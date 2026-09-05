from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from app import logger, xray
from app.db import GetDB, crud
from app.models.node import NodeStatus
from app.models.user import UserResponse
from app.device_limit.slots import enabled_device_slots, slot_email
from app.utils.concurrency import threaded_function
from app.xray.node import XRayNode
from xray_api import XRay as XRayAPI
from xray_api.types.account import Account, XTLSFlows

if TYPE_CHECKING:
    from app.db import User as DBUser
    from app.db.models import Node as DBNode


@lru_cache(maxsize=None)
def get_tls():
    from app.db import GetDB, get_tls_certificate
    with GetDB() as db:
        tls = get_tls_certificate(db)
        return {
            "key": tls.key,
            "certificate": tls.certificate
        }


@threaded_function
def _add_user_to_inbound(api: XRayAPI, inbound_tag: str, account: Account):
    try:
        api.add_inbound_user(tag=inbound_tag, user=account, timeout=30)
    except (xray.exc.EmailExistsError, xray.exc.ConnectionError):
        pass


@threaded_function
def _remove_user_from_inbound(api: XRayAPI, inbound_tag: str, email: str):
    try:
        api.remove_inbound_user(tag=inbound_tag, email=email, timeout=30)
    except (xray.exc.EmailNotFoundError, xray.exc.ConnectionError):
        pass


@threaded_function
def _replace_inbound_users(
    api: XRayAPI,
    inbound_tag: str,
    emails: tuple[str, ...],
    accounts: tuple[Account, ...],
):
    """Replace all slot accounts in one ordered HandlerService operation."""

    for email in emails:
        try:
            api.remove_inbound_user(tag=inbound_tag, email=email, timeout=30)
        except (xray.exc.EmailNotFoundError, xray.exc.ConnectionError):
            pass
    for account in accounts:
        try:
            api.add_inbound_user(tag=inbound_tag, user=account, timeout=30)
        except (xray.exc.EmailExistsError, xray.exc.ConnectionError):
            pass


def add_user(dbuser: "DBUser"):
    from app.utils.access_groups import user_node_scope
    node_scope = user_node_scope(dbuser)
    user = UserResponse.model_validate(dbuser)

    slots = enabled_device_slots(dbuser)
    credential_sets = (
        [(slot.slot_index, slot.credentials) for slot in slots]
        if slots
        else [(1, {key.value: value.dict(no_obj=True) for key, value in user.proxies.items()})]
    )

    for proxy_type, inbound_tags in user.inbounds.items():
        for inbound_tag in inbound_tags:
            inbound = xray.config.inbounds_by_tag.get(inbound_tag, {})
            for slot_index, credentials in credential_sets:
                proxy_settings = credentials.get(proxy_type.value)
                if not proxy_settings:
                    continue
                account = proxy_type.account_model(
                    email=slot_email(dbuser.id, dbuser.username, slot_index),
                    **proxy_settings,
                )

                # XTLS currently only supports transmission methods of TCP and mKCP
                if getattr(account, 'flow', None) and (
                    inbound.get('network', 'tcp') not in ('tcp', 'kcp')
                    or
                    (
                        inbound.get('network', 'tcp') in ('tcp', 'kcp')
                        and
                        inbound.get('tls') not in ('tls', 'reality')
                    )
                    or
                    inbound.get('header_type') == 'http'
                ):
                    account.flow = XTLSFlows.NONE

                if node_scope is None:
                    _add_user_to_inbound(xray.api, inbound_tag, account)  # main core
                for node_id, node in list(xray.nodes.items()):
                    if (node_scope is None or node_id in node_scope) and node.connected and node.started:
                        _add_user_to_inbound(node.api, inbound_tag, account)


def add_user_by_id(user_id: int):
    """Load committed relationships in a fresh session before background sync.

    Passing request-scoped ORM objects to a FastAPI background task can leave
    proxies, slots or ownership detached and previously produced incomplete
    delegated-admin accounts/EOF failures.
    """

    with GetDB() as db:
        dbuser = crud.get_user_by_id(db, user_id)
        if dbuser is not None:
            add_user(dbuser)


def remove_user(dbuser: "DBUser"):
    emails = {f"{dbuser.id}.{dbuser.username}"}
    emails.update(
        slot_email(dbuser.id, dbuser.username, slot.slot_index)
        for slot in dbuser.device_slots
    )

    for inbound_tag in xray.config.inbounds_by_tag:
        for email in emails:
            _remove_user_from_inbound(xray.api, inbound_tag, email)
            for node in list(xray.nodes.values()):
                if node.connected and node.started:
                    _remove_user_from_inbound(node.api, inbound_tag, email)


def remove_user_by_id(user_id: int):
    """Load the user in a fresh session before background removal."""

    with GetDB() as db:
        dbuser = crud.get_user_by_id(db, user_id)
        if dbuser is not None:
            remove_user(dbuser)


def restart_all_cores(config=None):
    """Reload all running cores, terminating already-established streams."""

    if config is None:
        config = xray.config.include_db_users()

    if xray.core.started:
        xray.core.restart(config)

    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            restart_node(node_id, config)


def update_user(dbuser: "DBUser"):
    """Atomically replace a user's slot accounts without restarting Xray."""

    from app.utils.access_groups import user_node_scope
    node_scope = user_node_scope(dbuser)
    user = UserResponse.model_validate(dbuser)
    all_emails = tuple({
        f"{dbuser.id}.{dbuser.username}",
        *(
            slot_email(dbuser.id, dbuser.username, slot.slot_index)
            for slot in dbuser.device_slots
        ),
    })
    slots = enabled_device_slots(dbuser)
    credential_sets = (
        [(slot.slot_index, slot.credentials) for slot in slots]
        if slots
        else [(1, {key.value: value.dict(no_obj=True) for key, value in user.proxies.items()})]
    )

    accounts_by_inbound: dict[str, list[Account]] = {
        inbound_tag: [] for inbound_tag in xray.config.inbounds_by_tag
    }
    for proxy_type, inbound_tags in user.inbounds.items():
        for inbound_tag in inbound_tags:
            inbound = xray.config.inbounds_by_tag.get(inbound_tag, {})
            for slot_index, credentials in credential_sets:
                proxy_settings = credentials.get(proxy_type.value)
                if not proxy_settings:
                    continue
                account = proxy_type.account_model(
                    email=slot_email(dbuser.id, dbuser.username, slot_index),
                    **proxy_settings,
                )
                if getattr(account, "flow", None) and (
                    inbound.get("network", "tcp") not in ("tcp", "kcp")
                    or inbound.get("tls") not in ("tls", "reality")
                    or inbound.get("header_type") == "http"
                ):
                    account.flow = XTLSFlows.NONE
                accounts_by_inbound.setdefault(inbound_tag, []).append(account)

    for inbound_tag, accounts in accounts_by_inbound.items():
        account_tuple = tuple(accounts)
        _replace_inbound_users(xray.api, inbound_tag, all_emails, account_tuple if node_scope is None else ())
        for node_id, node in list(xray.nodes.items()):
            if node.connected and node.started:
                _replace_inbound_users(node.api, inbound_tag, all_emails,
                                       account_tuple if node_scope is None or node_id in node_scope else ())


def update_user_by_id(user_id: int):
    with GetDB() as db:
        dbuser = crud.get_user_by_id(db, user_id)
        if dbuser is not None:
            update_user(dbuser)


def remove_node(node_id: int):
    if node_id in xray.nodes:
        try:
            xray.nodes[node_id].disconnect()
        except Exception:
            pass
        finally:
            try:
                del xray.nodes[node_id]
            except KeyError:
                pass


def add_node(dbnode: "DBNode"):
    remove_node(dbnode.id)

    tls = get_tls()
    xray.nodes[dbnode.id] = XRayNode(address=dbnode.address,
                                     port=dbnode.port,
                                     api_port=dbnode.api_port,
                                     ssl_key=tls['key'],
                                     ssl_cert=tls['certificate'],
                                     usage_coefficient=dbnode.usage_coefficient)
    xray.nodes[dbnode.id].node_id = dbnode.id

    return xray.nodes[dbnode.id]


def _change_node_status(node_id: int, status: NodeStatus, message: str = None, version: str = None):
    with GetDB() as db:
        try:
            dbnode = crud.get_node_by_id(db, node_id)
            if not dbnode:
                return

            if dbnode.status == NodeStatus.disabled:
                remove_node(dbnode.id)
                return

            crud.update_node_status(db, dbnode, status, message, version)
        except SQLAlchemyError:
            db.rollback()


global _connecting_nodes
_connecting_nodes = {}


@threaded_function
def connect_node(node_id, config=None):
    global _connecting_nodes

    if _connecting_nodes.get(node_id):
        return

    with GetDB() as db:
        dbnode = crud.get_node_by_id(db, node_id)

    if not dbnode:
        return

    try:
        node = xray.nodes[dbnode.id]
        assert node.connected
    except (KeyError, AssertionError):
        node = xray.operations.add_node(dbnode)

    try:
        _connecting_nodes[node_id] = True

        _change_node_status(node_id, NodeStatus.connecting)
        logger.info(f"Connecting to \"{dbnode.name}\" node")

        if config is None:
            config = xray.config.include_db_users()

        node.start(config)
        version = node.get_version()
        _change_node_status(node_id, NodeStatus.connected, version=version)
        logger.info(f"Connected to \"{dbnode.name}\" node, xray run on v{version}")

    except Exception as e:
        _change_node_status(node_id, NodeStatus.error, message=str(e))
        logger.info(f"Unable to connect to \"{dbnode.name}\" node")

    finally:
        try:
            del _connecting_nodes[node_id]
        except KeyError:
            pass


@threaded_function
def restart_node(node_id, config=None):
    with GetDB() as db:
        dbnode = crud.get_node_by_id(db, node_id)

    if not dbnode:
        return

    try:
        node = xray.nodes[dbnode.id]
    except KeyError:
        node = xray.operations.add_node(dbnode)

    if not node.connected:
        return connect_node(node_id, config)

    try:
        logger.info(f"Restarting Xray core of \"{dbnode.name}\" node")

        if config is None:
            config = xray.config.include_db_users()

        node.restart(config)
        logger.info(f"Xray core of \"{dbnode.name}\" node restarted")
    except Exception as e:
        _change_node_status(node_id, NodeStatus.error, message=str(e))
        logger.info(f"Unable to restart node {node_id}")
        try:
            node.disconnect()
        except Exception:
            pass


__all__ = [
    "add_user",
    "remove_user",
    "remove_user_by_id",
    "restart_all_cores",
    "add_node",
    "remove_node",
    "connect_node",
    "restart_node",
]
