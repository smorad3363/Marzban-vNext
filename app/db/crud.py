"""
Functions for managing proxy hosts, users, user templates, nodes, and administrative tasks.
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.orm import Query, Session, joinedload
from sqlalchemy.sql.functions import coalesce

from app import xray
from app.device_limit.constants import (
    DEFAULT_ADMIN_SUBSCRIPTION_MODES,
    PenaltyStatus,
)
from app.db.models import (
    JWT,
    TLS,
    Admin,
    AdminApiToken,
    AdminAccountStatus,
    AdminHierarchy,
    AdminRole,
    AdminUserPlanAccess,
    AdminUsageLogs,
    DeviceLimitUserState,
    MarzhelpAdminSettings,
    MarzhelpAdminInboundPermission,
    MarzhelpAdminSubscriptionModePermission,
    MarzhelpAdminUserLimitPermission,
    MarzhelpAdminUsage,
    MarzhelpLimit,
    NextPlan,
    Node,
    NodeWatchdogSettings,
    NodeUsage,
    NodeUserUsage,
    NotificationReminder,
    Proxy,
    ProxyHost,
    ProxyInbound,
    ProxyTypes,
    System,
    User,
    UserTemplate,
    UserUsageResetLogs,
    excluded_inbounds_association,
)
from app.models.admin import (
    AdminCreate,
    AdminModify,
    AdminPartialModify,
    MarzhelpAdminPolicy,
)
from app.models.node import (NodeCreate, NodeModify, NodeStatus,
                             NodeUsageResponse, NodeWatchdogSettingsUpdate)
from app.models.proxy import ProxyHost as ProxyHostModify
from app.models.user import (
    ReminderType,
    UserCreate,
    UserDataLimitResetStrategy,
    UserModify,
    UserResponse,
    UserStatus,
    UserUsageResponse,
)
from app.models.user_template import UserTemplateCreate, UserTemplateModify
from app.utils.helpers import calculate_expiration_days, calculate_usage_percent
from app.utils import marzhelp_policy
from app.device_limit.slots import sync_device_slots
from config import NOTIFY_DAYS_LEFT, NOTIFY_REACHED_USAGE_PERCENT, USERS_AUTODELETE_DAYS


def add_default_host(db: Session, inbound: ProxyInbound):
    """
    Adds a default host to a proxy inbound.

    Args:
        db (Session): Database session.
        inbound (ProxyInbound): Proxy inbound to add the default host to.
    """
    host = ProxyHost(remark="🚀 Marz ({USERNAME}) [{PROTOCOL} - {TRANSPORT}]", address="{SERVER_IP}", inbound=inbound)
    db.add(host)
    db.commit()


def get_or_create_inbound(db: Session, inbound_tag: str) -> ProxyInbound:
    """
    Retrieves or creates a proxy inbound based on the given tag.

    Args:
        db (Session): Database session.
        inbound_tag (str): The tag of the inbound.

    Returns:
        ProxyInbound: The retrieved or newly created proxy inbound.
    """
    inbound = db.query(ProxyInbound).filter(ProxyInbound.tag == inbound_tag).first()
    if not inbound:
        inbound = ProxyInbound(tag=inbound_tag)
        db.add(inbound)
        db.commit()
        add_default_host(db, inbound)
        db.refresh(inbound)
    return inbound


def get_hosts(db: Session, inbound_tag: str, *, include_legacy: bool = True) -> List[ProxyHost]:
    """
    Retrieves hosts for a given inbound tag.

    Args:
        db (Session): Database session.
        inbound_tag (str): The tag of the inbound.

    Returns:
        List[ProxyHost]: List of hosts for the inbound.
    """
    inbound = get_or_create_inbound(db, inbound_tag)
    query = db.query(ProxyHost).filter(ProxyHost.inbound_tag == inbound.tag)
    if not include_legacy:
        query = query.filter(ProxyHost.is_legacy.is_(False))
    return query.order_by(ProxyHost.id).all()


def add_host(db: Session, inbound_tag: str, host: ProxyHostModify) -> List[ProxyHost]:
    """
    Adds a new host to a proxy inbound.

    Args:
        db (Session): Database session.
        inbound_tag (str): The tag of the inbound.
        host (ProxyHostModify): Host details to be added.

    Returns:
        List[ProxyHost]: Updated list of hosts for the inbound.
    """
    inbound = get_or_create_inbound(db, inbound_tag)
    inbound.hosts.append(
        ProxyHost(
            remark=host.remark,
            address=host.address,
            port=host.port,
            path=host.path,
            sni=host.sni,
            host=host.host,
            inbound=inbound,
            security=host.security,
            alpn=host.alpn,
            fingerprint=host.fingerprint
        )
    )
    db.commit()
    db.refresh(inbound)
    return inbound.hosts


def update_hosts(db: Session, inbound_tag: str, modified_hosts: List[ProxyHostModify]) -> List[ProxyHost]:
    """
    Updates hosts for a given inbound tag.

    Args:
        db (Session): Database session.
        inbound_tag (str): The tag of the inbound.
        modified_hosts (List[ProxyHostModify]): List of modified hosts.

    Returns:
        List[ProxyHost]: Updated list of hosts for the inbound.
    """
    inbound = get_or_create_inbound(db, inbound_tag)
    existing_by_id = {host.id: host for host in inbound.hosts if not host.is_legacy}
    retained_ids: set[int] = set()
    created: list[tuple[ProxyHostModify, ProxyHost]] = []
    fields = (
        "remark", "address", "port", "path", "sni", "host", "security", "alpn",
        "fingerprint", "allowinsecure", "is_disabled", "mux_enable",
        "fragment_setting", "noise_setting", "random_user_agent", "use_sni_as_host",
    )
    for modified in modified_hosts:
        if modified.id is not None:
            dbhost = existing_by_id.get(modified.id)
            if dbhost is None:
                raise ValueError(f"Host {modified.id} does not belong to inbound {inbound_tag}")
            if modified.id in retained_ids:
                raise ValueError(f"Host {modified.id} is duplicated")
            retained_ids.add(modified.id)
        else:
            dbhost = ProxyHost(inbound=inbound)
            db.add(dbhost)
            created.append((modified, dbhost))
        for field in fields:
            setattr(dbhost, field, getattr(modified, field))
    for host_id, dbhost in existing_by_id.items():
        if host_id not in retained_ids:
            db.delete(dbhost)
    db.flush()
    for modified, dbhost in created:
        modified.id = dbhost.id
    db.refresh(inbound)
    return inbound.hosts


def get_user_queryset(db: Session) -> Query:
    """
    Retrieves the base user query with joined admin details.

    Args:
        db (Session): Database session.

    Returns:
        Query: Base user query.
    """
    return db.query(User).options(joinedload(User.admin)).options(joinedload(User.next_plan))


def apply_inbound_access_filter(query: Query, allowed_inbounds: set[str] | None) -> Query:
    """Exclude users connected to any configured inbound outside allowed set."""

    if allowed_inbounds is None:
        return query
    for proxy_type, inbounds in xray.config.inbounds_by_protocol.items():
        for inbound in inbounds:
            tag = inbound["tag"]
            if tag in allowed_inbounds:
                continue
            tag_is_excluded = exists().where(
                excluded_inbounds_association.c.proxy_id == Proxy.id,
                excluded_inbounds_association.c.inbound_tag == tag,
            )
            uses_forbidden_inbound = exists().where(
                Proxy.user_id == User.id,
                Proxy.type == proxy_type,
                ~tag_is_excluded,
            )
            query = query.filter(~uses_forbidden_inbound)
    return query


def get_user(db: Session, username: str) -> Optional[User]:
    """
    Retrieves a user by username.

    Args:
        db (Session): Database session.
        username (str): The username of the user.

    Returns:
        Optional[User]: The user object if found, else None.
    """
    return get_user_queryset(db).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """
    Retrieves a user by user ID.

    Args:
        db (Session): Database session.
        user_id (int): The ID of the user.

    Returns:
        Optional[User]: The user object if found, else None.
    """
    return get_user_queryset(db).filter(User.id == user_id).first()


_admin_username_sort = (
    select(Admin.username).where(Admin.id == User.admin_id).scalar_subquery()
)


UsersSortingOptions = Enum('UsersSortingOptions', {
    'username': User.username.asc(),
    'used_traffic': User.used_traffic.asc(),
    'data_limit': User.data_limit.asc(),
    'expire': User.expire.asc(),
    'created_at': User.created_at.asc(),
    'admin': _admin_username_sort.asc(),
    '-username': User.username.desc(),
    '-used_traffic': User.used_traffic.desc(),
    '-data_limit': User.data_limit.desc(),
    '-expire': User.expire.desc(),
    '-created_at': User.created_at.desc(),
    '-admin': _admin_username_sort.desc(),
})


def get_users(db: Session,
              offset: Optional[int] = None,
              limit: Optional[int] = None,
              usernames: Optional[List[str]] = None,
              search: Optional[str] = None,
              status: Optional[Union[UserStatus, list]] = None,
              sort: Optional[List[UsersSortingOptions]] = None,
              admin: Optional[Admin] = None,
              admins: Optional[List[str]] = None,
              reset_strategy: Optional[Union[UserDataLimitResetStrategy, list]] = None,
              return_with_count: bool = False,
              allowed_inbounds: set[str] | None = None,
              scope_admin_id: Optional[int] = None) -> Union[List[User], Tuple[List[User], int]]:
    """
    Retrieves users based on various filters and options.

    Args:
        db (Session): Database session.
        offset (Optional[int]): Number of records to skip.
        limit (Optional[int]): Number of records to retrieve.
        usernames (Optional[List[str]]): List of usernames to filter by.
        search (Optional[str]): Search term to filter by username or note.
        status (Optional[Union[UserStatus, list]]): User status or list of statuses to filter by.
        sort (Optional[List[UsersSortingOptions]]): Sorting options.
        admin (Optional[Admin]): Admin to filter users by.
        admins (Optional[List[str]]): List of admin usernames to filter users by.
        reset_strategy (Optional[Union[UserDataLimitResetStrategy, list]]): Data limit reset strategy to filter by.
        return_with_count (bool): Whether to return the total count of users.

    Returns:
        Union[List[User], Tuple[List[User], int]]: List of users or tuple of users and total count.
    """
    # Keep count query narrow; eager relationships belong only to page payload.
    query = db.query(User)
    query = apply_inbound_access_filter(query, allowed_inbounds)
    if scope_admin_id is not None:
        query = query.filter(
            exists().where(
                and_(
                    AdminHierarchy.ancestor_id == scope_admin_id,
                    AdminHierarchy.descendant_id == User.admin_id,
                )
            )
        )

    if search:
        query = query.filter(or_(User.username.ilike(f"%{search}%"), User.note.ilike(f"%{search}%")))

    if usernames:
        query = query.filter(User.username.in_(usernames))

    if status:
        if isinstance(status, list):
            query = query.filter(User.status.in_(status))
        else:
            query = query.filter(User.status == status)

    if reset_strategy:
        if isinstance(reset_strategy, list):
            query = query.filter(User.data_limit_reset_strategy.in_(reset_strategy))
        else:
            query = query.filter(User.data_limit_reset_strategy == reset_strategy)

    if admin:
        query = query.filter(User.admin == admin)

    if admins is not None:
        query = query.filter(User.admin.has(Admin.username.in_(admins)))

    if return_with_count:
        count = query.order_by(None).with_entities(func.count(User.id)).scalar() or 0

    if sort:
        query = query.order_by(*(opt.value for opt in sort))
        primary_descending = sort[0].name.startswith("-")
        query = query.order_by(User.id.desc() if primary_descending else User.id.asc())
    else:
        query = query.order_by(User.created_at.desc(), User.id.desc())

    if offset:
        query = query.offset(offset)
    if limit:
        query = query.limit(limit)

    query = query.options(joinedload(User.admin)).options(joinedload(User.next_plan))
    if return_with_count:
        return query.all(), count

    return query.all()


def get_user_usages(db: Session, dbuser: User, start: datetime, end: datetime) -> List[UserUsageResponse]:
    """
    Retrieves user usages within a specified date range.

    Args:
        db (Session): Database session.
        dbuser (User): The user object.
        start (datetime): Start date for usage retrieval.
        end (datetime): End date for usage retrieval.

    Returns:
        List[UserUsageResponse]: List of user usage responses.
    """

    usages = {0: UserUsageResponse(  # Main Core
        node_id=None,
        node_name="Master",
        used_traffic=0
    )}

    for node in db.query(Node).all():
        usages[node.id] = UserUsageResponse(
            node_id=node.id,
            node_name=node.name,
            used_traffic=0
        )

    cond = and_(NodeUserUsage.user_id == dbuser.id,
                NodeUserUsage.created_at >= start,
                NodeUserUsage.created_at <= end)

    for v in db.query(NodeUserUsage).filter(cond):
        try:
            usages[v.node_id or 0].used_traffic += v.used_traffic
        except KeyError:
            pass

    return list(usages.values())


def get_users_count(
    db: Session,
    status: UserStatus = None,
    admin: Admin = None,
    allowed_inbounds: set[str] | None = None,
    scope_admin_id: Optional[int] = None,
) -> int:
    """
    Retrieves the count of users based on status and admin filters.

    Args:
        db (Session): Database session.
        status (UserStatus, optional): Status to filter users by.
        admin (Admin, optional): Admin to filter users by.

    Returns:
        int: Count of users matching the criteria.
    """
    query = db.query(User.id)
    query = apply_inbound_access_filter(query, allowed_inbounds)
    if scope_admin_id is not None:
        query = query.filter(
            exists().where(
                and_(
                    AdminHierarchy.ancestor_id == scope_admin_id,
                    AdminHierarchy.descendant_id == User.admin_id,
                )
            )
        )
    if admin:
        query = query.filter(User.admin == admin)
    if status:
        query = query.filter(User.status == status)
    return query.count()


def create_user(
    db: Session,
    user: UserCreate,
    admin: Admin = None,
    commit: bool = True,
    apply_namespace: bool = True,
) -> User:
    """
    Creates a new user with provided details.

    Args:
        db (Session): Database session.
        user (UserCreate): User creation details.
        admin (Admin, optional): Admin associated with the user.

    Returns:
        User: The created user object.
    """
    excluded_inbounds_tags = user.excluded_inbounds
    proxies = []
    for proxy_type, settings in user.proxies.items():
        excluded_inbounds = [
            get_or_create_inbound(db, tag) for tag in excluded_inbounds_tags[proxy_type]
        ]
        proxies.append(
            Proxy(type=proxy_type.value,
                  settings=settings.dict(no_obj=True),
                  excluded_inbounds=excluded_inbounds)
        )

    policy_settings = marzhelp_policy.validate_create(
        db, admin.id if admin is not None else None, user
    )

    username = (
        marzhelp_policy.customer_username(db, admin, user.username)
        if admin is not None and apply_namespace
        else user.username
    )
    dbuser = User(
        username=username,
        proxies=proxies,
        status=user.status,
        data_limit=(user.data_limit or None),
        concurrent_user_limit=user.concurrent_user_limit,
        expire=(user.expire or None),
        admin=admin,
        data_limit_reset_strategy=user.data_limit_reset_strategy,
        note=user.note,
        on_hold_expire_duration=(user.on_hold_expire_duration or None),
        on_hold_timeout=(user.on_hold_timeout or None),
        auto_delete_in_days=user.auto_delete_in_days,
        next_plan=NextPlan(
            data_limit=user.next_plan.data_limit,
            expire=user.next_plan.expire,
            add_remaining_traffic=user.next_plan.add_remaining_traffic,
            fire_on_either=user.next_plan.fire_on_either,
        ) if user.next_plan else None
    )
    db.add(dbuser)
    db.flush()
    sync_device_slots(db, dbuser)
    marzhelp_policy.record_create(
        db, dbuser, policy_settings is not None
    )
    if commit:
        db.commit()
        db.refresh(dbuser)
    else:
        db.flush()
    return dbuser


def remove_user(db: Session, dbuser: User) -> User:
    """
    Removes a user from the database.

    Args:
        db (Session): Database session.
        dbuser (User): The user object to be removed.

    Returns:
        User: The removed user object.
    """
    marzhelp_policy.capture_delete(db, dbuser)
    db.delete(dbuser)
    db.commit()
    return dbuser


def remove_users(db: Session, dbusers: List[User]):
    """
    Removes multiple users from the database.

    Args:
        db (Session): Database session.
        dbusers (List[User]): List of user objects to be removed.
    """
    for dbuser in dbusers:
        marzhelp_policy.capture_delete(db, dbuser)
        db.delete(dbuser)
    db.commit()
    return


def update_user(
    db: Session,
    dbuser: User,
    modify: UserModify,
    commit: bool = True,
    operation: marzhelp_policy.UserUpdateOperation = marzhelp_policy.UserUpdateOperation.edit,
    actor: Admin | object | None = None,
) -> User:
    """
    Updates a user with new details.

    Args:
        db (Session): Database session.
        dbuser (User): The user object to be updated.
        modify (UserModify): New details for the user.

    Returns:
        User: The updated user object.
    """
    # Requests may have loaded this user before another transaction committed.
    # Price and apply the modification against the same locked current state.
    db.refresh(dbuser, with_for_update=True)
    renewal, allowance_consumed = marzhelp_policy.validate_update(
        db,
        dbuser,
        modify,
        operation=operation,
        actor=actor,
    )
    added_proxies: Dict[ProxyTypes, Proxy] = {}
    if modify.proxies:
        for proxy_type, settings in modify.proxies.items():
            dbproxy = db.query(Proxy) \
                .where(Proxy.user == dbuser, Proxy.type == proxy_type) \
                .first()
            if dbproxy:
                dbproxy.settings = settings.dict(no_obj=True)
            else:
                new_proxy = Proxy(type=proxy_type, settings=settings.dict(no_obj=True))
                dbuser.proxies.append(new_proxy)
                added_proxies.update({proxy_type: new_proxy})
        for proxy in dbuser.proxies:
            if proxy.type not in modify.proxies:
                db.delete(proxy)
    if modify.inbounds:
        for proxy_type, tags in modify.excluded_inbounds.items():
            dbproxy = db.query(Proxy) \
                .where(Proxy.user == dbuser, Proxy.type == proxy_type) \
                .first() or added_proxies.get(proxy_type)
            if dbproxy:
                dbproxy.excluded_inbounds = [get_or_create_inbound(db, tag) for tag in tags]

    if modify.status is not None:
        dbuser.status = modify.status
        dbuser.last_status_change = datetime.utcnow()
        device_limit_state = dbuser.device_limit_state
        if (
            getattr(modify.status, "value", modify.status)
            == UserStatus.disabled.value
            and device_limit_state is not None
            and device_limit_state.penalty_status
            in {
                PenaltyStatus.temporarily_disabled.value,
                PenaltyStatus.permanently_disabled.value,
            }
        ):
            # An explicit disable during a temporary device penalty is an
            # operator lock, not a state the penalty timer may undo.
            device_limit_state.status_before_penalty = UserStatus.disabled.value

    if modify.data_limit is not None:
        dbuser.data_limit = (modify.data_limit or None)
        if dbuser.status not in (UserStatus.expired, UserStatus.disabled):
            if not dbuser.data_limit or dbuser.used_traffic < dbuser.data_limit:
                if dbuser.status != UserStatus.on_hold:
                    dbuser.status = UserStatus.active

                for percent in sorted(NOTIFY_REACHED_USAGE_PERCENT, reverse=True):
                    if not dbuser.data_limit or (calculate_usage_percent(
                            dbuser.used_traffic, dbuser.data_limit) < percent):
                        reminder = get_notification_reminder(db, dbuser.id, ReminderType.data_usage, threshold=percent)
                        if reminder:
                            delete_notification_reminder(db, reminder, commit=False)

            else:
                dbuser.status = UserStatus.limited

    if "concurrent_user_limit" in modify.model_fields_set:
        dbuser.concurrent_user_limit = modify.concurrent_user_limit

    if modify.expire is not None:
        dbuser.expire = (modify.expire or None)
        if dbuser.status in (UserStatus.active, UserStatus.expired):
            if not dbuser.expire or dbuser.expire > datetime.utcnow().timestamp():
                dbuser.status = UserStatus.active
                for days_left in sorted(NOTIFY_DAYS_LEFT):
                    if not dbuser.expire or (calculate_expiration_days(
                            dbuser.expire) > days_left):
                        reminder = get_notification_reminder(
                            db, dbuser.id, ReminderType.expiration_date, threshold=days_left)
                        if reminder:
                            delete_notification_reminder(db, reminder, commit=False)
            else:
                dbuser.status = UserStatus.expired

    if modify.note is not None:
        dbuser.note = modify.note or None

    if modify.data_limit_reset_strategy is not None:
        dbuser.data_limit_reset_strategy = modify.data_limit_reset_strategy.value

    if modify.on_hold_timeout is not None:
        dbuser.on_hold_timeout = modify.on_hold_timeout

    if modify.on_hold_expire_duration is not None:
        dbuser.on_hold_expire_duration = modify.on_hold_expire_duration

    if modify.next_plan is not None:
        dbuser.next_plan = NextPlan(
            data_limit=modify.next_plan.data_limit,
            expire=modify.next_plan.expire,
            add_remaining_traffic=modify.next_plan.add_remaining_traffic,
            fire_on_either=modify.next_plan.fire_on_either,
        )
    elif dbuser.next_plan is not None:
        db.delete(dbuser.next_plan)

    dbuser.edit_at = datetime.utcnow()

    sync_device_slots(db, dbuser)

    if (
        renewal
        or int(getattr(dbuser, "_marzhelp_volume_delta", 0)) != 0
        or int(getattr(dbuser, "_marzhelp_allowance_delta", 0)) != 0
    ):
        db.flush()
        marzhelp_policy.record_renewal(db, dbuser, allowance_consumed)

    if commit:
        db.commit()
        db.refresh(dbuser)
    else:
        db.flush()
    return dbuser


def reset_user_data_usage(db: Session, dbuser: User, commit: bool = True) -> User:
    """
    Resets the data usage of a user and logs the reset.

    Args:
        db (Session): Database session.
        dbuser (User): The user object whose data usage is to be reset.
        commit (bool): Commit immediately, or flush into the caller's transaction.

    Returns:
        User: The updated user object.
    """
    marzhelp_policy.validate_reset(db, dbuser)
    usage_log = UserUsageResetLogs(
        user=dbuser,
        used_traffic_at_reset=dbuser.used_traffic,
    )
    db.add(usage_log)

    dbuser.used_traffic = 0
    dbuser.node_usages.clear()
    if dbuser.status not in (
        UserStatus.on_hold,
        UserStatus.expired,
        UserStatus.disabled,
    ):
        dbuser.status = UserStatus.active.value

    if dbuser.next_plan:
        db.delete(dbuser.next_plan)
        dbuser.next_plan = None
    db.add(dbuser)

    if commit:
        db.commit()
        db.refresh(dbuser)
    else:
        db.flush()
    return dbuser


def reset_user_by_next(db: Session, dbuser: User) -> User:
    """
    Resets the data usage of a user based on next user.

    Args:
        db (Session): Database session.
        dbuser (User): The user object whose data usage is to be reset.

    Returns:
        User: The updated user object.
    """

    if (dbuser.next_plan is None):
        return

    allowance_consumed = marzhelp_policy.validate_next_plan_activation(db, dbuser)

    usage_log = UserUsageResetLogs(
        user=dbuser,
        used_traffic_at_reset=dbuser.used_traffic,
    )
    db.add(usage_log)

    dbuser.node_usages.clear()
    dbuser.status = UserStatus.active.value

    dbuser.data_limit = marzhelp_policy.resulting_next_plan_data_limit(dbuser)
    dbuser.expire = dbuser.next_plan.expire

    dbuser.used_traffic = 0
    db.delete(dbuser.next_plan)
    dbuser.next_plan = None
    db.add(dbuser)
    db.flush()
    marzhelp_policy.record_renewal(db, dbuser, allowance_consumed)

    db.commit()
    db.refresh(dbuser)
    return dbuser


def revoke_user_sub(db: Session, dbuser: User) -> User:
    """
    Revokes the subscription of a user and updates proxies settings.

    Args:
        db (Session): Database session.
        dbuser (User): The user object whose subscription is to be revoked.

    Returns:
        User: The updated user object.
    """
    marzhelp_policy.validate_revoke(db, dbuser)
    dbuser.sub_revoked_at = datetime.utcnow()

    user = UserResponse.model_validate(dbuser)
    for proxy_type, settings in user.proxies.copy().items():
        settings.revoke()
        user.proxies[proxy_type] = settings
    dbuser = update_user(db, dbuser, user)

    db.commit()
    db.refresh(dbuser)
    return dbuser


def update_user_sub(db: Session, dbuser: User, user_agent: str) -> User:
    """
    Updates the user's subscription details.

    Args:
        db (Session): Database session.
        dbuser (User): The user object whose subscription is to be updated.
        user_agent (str): The user agent string to update.

    Returns:
        User: The updated user object.
    """
    from app.device_limit.clients import MAX_RAW_USER_AGENT, observe_subscription_client

    safe_user_agent = (user_agent or "")[:MAX_RAW_USER_AGENT]
    dbuser.sub_updated_at = datetime.utcnow()
    dbuser.sub_last_user_agent = safe_user_agent
    observe_subscription_client(db, dbuser, safe_user_agent)

    db.commit()
    db.refresh(dbuser)
    return dbuser


def reset_all_users_data_usage(db: Session, admin: Optional[Admin] = None):
    """
    Resets the data usage for all users or users under a specific admin.

    Args:
        db (Session): Database session.
        admin (Optional[Admin]): Admin to filter users by, if any.
    """
    query = get_user_queryset(db)

    if admin:
        query = query.filter(User.admin == admin)

    users = query.all()
    for dbuser in users:
        marzhelp_policy.validate_reset(db, dbuser)

    for dbuser in users:
        dbuser.used_traffic = 0
        if dbuser.status not in [UserStatus.on_hold, UserStatus.expired, UserStatus.disabled]:
            dbuser.status = UserStatus.active
        dbuser.usage_logs.clear()
        dbuser.node_usages.clear()
        if dbuser.next_plan:
            db.delete(dbuser.next_plan)
            dbuser.next_plan = None
        db.add(dbuser)

    db.commit()


def disable_all_active_users(db: Session, admin: Optional[Admin] = None):
    """
    Disable all active users or users under a specific admin.

    Args:
        db (Session): Database session.
        admin (Optional[Admin]): Admin to filter users by, if any.
    """
    query = db.query(User).filter(User.status.in_((UserStatus.active, UserStatus.on_hold)))
    if admin:
        query = query.filter(User.admin == admin)

    query.update({User.status: UserStatus.disabled, User.last_status_change: datetime.utcnow()}, synchronize_session=False)

    db.commit()


def activate_all_disabled_users(db: Session, admin: Optional[Admin] = None):
    """
    Activate all disabled users or users under a specific admin.

    Args:
        db (Session): Database session.
        admin (Optional[Admin]): Admin to filter users by, if any.
    """
    query_for_active_users = db.query(User).filter(User.status == UserStatus.disabled)
    query_for_on_hold_users = db.query(User).filter(
        and_(
            User.status == UserStatus.disabled, User.expire.is_(
                None), User.on_hold_expire_duration.isnot(None), User.online_at.is_(None)
        )
    )
    active_penalty = exists().where(
        and_(
            DeviceLimitUserState.user_id == User.id,
            DeviceLimitUserState.penalty_status.in_(
                (
                    PenaltyStatus.temporarily_disabled.value,
                    PenaltyStatus.permanently_disabled.value,
                )
            ),
        )
    )
    query_for_active_users = query_for_active_users.filter(~active_penalty)
    query_for_on_hold_users = query_for_on_hold_users.filter(~active_penalty)
    if admin:
        query_for_active_users = query_for_active_users.filter(User.admin == admin)
        query_for_on_hold_users = query_for_on_hold_users.filter(User.admin == admin)

    query_for_on_hold_users.update(
        {User.status: UserStatus.on_hold, User.last_status_change: datetime.utcnow()}, synchronize_session=False)
    query_for_active_users.update(
        {User.status: UserStatus.active, User.last_status_change: datetime.utcnow()}, synchronize_session=False)

    db.commit()


def autodelete_expired_users(db: Session,
                             include_limited_users: bool = False) -> List[User]:
    """
    Deletes expired (optionally also limited) users whose auto-delete time has passed.

    Args:
        db (Session): Database session
        include_limited_users (bool, optional): Whether to delete limited users as well.
            Defaults to False.

    Returns:
        list[User]: List of deleted users.
    """
    target_status = (
        [UserStatus.expired] if not include_limited_users
        else [UserStatus.expired, UserStatus.limited]
    )

    auto_delete = coalesce(User.auto_delete_in_days, USERS_AUTODELETE_DAYS)

    query = db.query(
        User, auto_delete,  # Use global auto-delete days as fallback
    ).filter(
        auto_delete >= 0,  # Negative values prevent auto-deletion
        User.status.in_(target_status),
    ).options(joinedload(User.admin))

    # TODO: Handle time filter in query itself (NOTE: Be careful with sqlite's strange datetime handling)
    expired_users = [
        user
        for (user, auto_delete) in query
        if user.last_status_change + timedelta(days=auto_delete) <= datetime.utcnow()
    ]

    if expired_users:
        remove_users(db, expired_users)

    return expired_users


def get_all_users_usages(
        db: Session,
        admin: Admin,
        start: datetime,
        end: datetime,
        allowed_inbounds: set[str] | None = None,
) -> List[UserUsageResponse]:
    """
    Retrieves usage data for all users associated with an admin within a specified time range.

    This function calculates the total traffic used by users across different nodes,
    including a "Master" node that represents the main core.

    Args:
        db (Session): Database session for querying.
        admin (Admin): The admin user for which to retrieve user usage data.
        start (datetime): The start date and time of the period to consider.
        end (datetime): The end date and time of the period to consider.

    Returns:
        List[UserUsageResponse]: A list of UserUsageResponse objects, each representing
        the usage data for a specific node or the main core.
    """
    usages = {0: UserUsageResponse(  # Main Core
        node_id=None,
        node_name="Master",
        used_traffic=0
    )}

    for node in db.query(Node).all():
        usages[node.id] = UserUsageResponse(
            node_id=node.id,
            node_name=node.name,
            used_traffic=0
        )

    admin_users = set(
        user.id
        for user in get_users(db=db, admins=admin, allowed_inbounds=allowed_inbounds)
    )

    cond = and_(
        NodeUserUsage.created_at >= start,
        NodeUserUsage.created_at <= end,
        NodeUserUsage.user_id.in_(admin_users)
    )

    for v in db.query(NodeUserUsage).filter(cond):
        try:
            usages[v.node_id or 0].used_traffic += v.used_traffic
        except KeyError:
            pass

    return list(usages.values())


def update_user_status(db: Session, dbuser: User, status: UserStatus) -> User:
    """
    Updates a user's status and records the time of change.

    Args:
        db (Session): Database session.
        dbuser (User): The user to update.
        status (UserStatus): The new status.

    Returns:
        User: The updated user object.
    """
    if status == UserStatus.active:
        marzhelp_policy.validate_activation(db, dbuser)
    dbuser.status = status
    dbuser.last_status_change = datetime.utcnow()
    db.commit()
    db.refresh(dbuser)
    return dbuser


def set_owner(
    db: Session,
    dbuser: User,
    admin: Admin,
    commit: bool = True,
) -> User:
    """
    Sets the owner (admin) of a user.

    Args:
        db (Session): Database session.
        dbuser (User): The user object whose owner is to be set.
        admin (Admin): The admin to set as owner.

    Returns:
        User: The updated user object.
    """
    marzhelp_policy.validate_transfer(db, dbuser, admin.id)
    dbuser.admin = admin
    if commit:
        db.commit()
        db.refresh(dbuser)
    else:
        db.flush()
    return dbuser


def start_user_expire(db: Session, dbuser: User) -> User:
    """
    Starts the expiration timer for a user.

    Args:
        db (Session): Database session.
        dbuser (User): The user object whose expiration timer is to be started.

    Returns:
        User: The updated user object.
    """
    expire = int(datetime.utcnow().timestamp()) + dbuser.on_hold_expire_duration
    marzhelp_policy.validate_start_expiration(db, dbuser, expire)
    dbuser.expire = expire
    dbuser.on_hold_expire_duration = None
    dbuser.on_hold_timeout = None
    db.commit()
    db.refresh(dbuser)
    return dbuser


def get_system_usage(db: Session) -> System:
    """
    Retrieves system usage information.

    Args:
        db (Session): Database session.

    Returns:
        System: System usage information.
    """
    return db.query(System).first()


def get_jwt_secret_key(db: Session) -> str:
    """
    Retrieves the JWT secret key.

    Args:
        db (Session): Database session.

    Returns:
        str: JWT secret key.
    """
    return db.query(JWT).first().secret_key


def get_tls_certificate(db: Session) -> TLS:
    """
    Retrieves the TLS certificate.

    Args:
        db (Session): Database session.

    Returns:
        TLS: TLS certificate information.
    """
    return db.query(TLS).first()


def get_admin(db: Session, username: str) -> Admin:
    """
    Retrieves an admin by username.

    Args:
        db (Session): Database session.
        username (str): The username of the admin.

    Returns:
        Admin: The admin object.
    """
    return db.query(Admin).filter(Admin.username == username).first()


def create_admin(db: Session, admin: AdminCreate, commit: bool = True) -> Admin:
    """
    Creates a new admin in the database.

    Args:
        db (Session): Database session.
        admin (AdminCreate): The admin creation data.

    Returns:
        Admin: The created admin object.
    """
    dbadmin = Admin(
        username=admin.username,
        hashed_password=admin.hashed_password,
        is_sudo=admin.is_sudo,
        telegram_id=admin.telegram_id if admin.telegram_id else None,
        phone=admin.phone,
        discord_webhook=admin.discord_webhook if admin.discord_webhook else None
    )
    db.add(dbadmin)
    db.flush()
    marzhelp_policy.ensure_admin_namespace_prefix(db, dbadmin)
    settings = MarzhelpAdminSettings(admin_id=dbadmin.id)
    settings.subscription_mode_permissions = [
        MarzhelpAdminSubscriptionModePermission(
            admin_id=dbadmin.id,
            mode=mode.value,
        )
        for mode in DEFAULT_ADMIN_SUBSCRIPTION_MODES
    ]
    db.add(settings)
    if commit:
        db.commit()
        db.refresh(dbadmin)
    else:
        db.flush()
    return dbadmin


def update_admin(
    db: Session, dbadmin: Admin, modified_admin: AdminModify, commit: bool = True
) -> Admin:
    """
    Updates an admin's details.

    Args:
        db (Session): Database session.
        dbadmin (Admin): The admin object to be updated.
        modified_admin (AdminModify): The modified admin data.

    Returns:
        Admin: The updated admin object.
    """
    if modified_admin.is_sudo is not None:
        dbadmin.is_sudo = modified_admin.is_sudo
    if modified_admin.password is not None and dbadmin.hashed_password != modified_admin.hashed_password:
        dbadmin.hashed_password = modified_admin.hashed_password
        dbadmin.password_reset_at = datetime.utcnow()
    if "telegram_id" in modified_admin.model_fields_set:
        dbadmin.telegram_id = modified_admin.telegram_id
    if "phone" in modified_admin.model_fields_set:
        dbadmin.phone = modified_admin.phone
    if "discord_webhook" in modified_admin.model_fields_set:
        dbadmin.discord_webhook = modified_admin.discord_webhook

    if commit:
        db.commit()
        db.refresh(dbadmin)
    else:
        db.flush()
    return dbadmin


def upsert_marzhelp_admin_policy(
    db: Session,
    admin_id: int,
    policy: MarzhelpAdminPolicy,
    commit: bool = True,
) -> MarzhelpAdminSettings:
    """Create or replace the dashboard-editable MarzHelp policy fields."""
    unknown_inbounds = sorted(set(policy.allowed_inbounds) - set(xray.config.inbounds_by_tag))
    if unknown_inbounds:
        raise marzhelp_policy.MarzhelpPolicyError(
            "unknown_inbound",
            "MarzHelp: unknown inbound(s): " + ", ".join(unknown_inbounds),
        )
    settings = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id == admin_id)
        .with_for_update()
        .first()
    )
    is_new = settings is None
    if settings is None:
        settings = MarzhelpAdminSettings(admin_id=admin_id)
        db.add(settings)

    previous_mode = "used_traffic" if is_new else (settings.calculate_volume or "used_traffic")
    switching_to_allocated = (
        policy.calculate_volume == "created_traffic"
        and previous_mode != "created_traffic"
    )

    used_capacity = marzhelp_policy.capacity_used(db, admin_id)
    current_users = marzhelp_policy.user_count_used(db, admin_id)
    if policy.max_users is not None and current_users > policy.max_users:
        raise marzhelp_policy.MarzhelpPolicyError(
            "user_limit_below_usage",
            (
                "MarzHelp: maximum users cannot be lower than current user count "
                f"({current_users})"
            ),
        )
    if (
        policy.device_capacity_limit is not None
        and used_capacity > policy.device_capacity_limit
    ):
        raise marzhelp_policy.MarzhelpPolicyError(
            "capacity_below_usage",
            (
                "MarzHelp: total user capacity cannot be lower than current usage "
                f"({used_capacity})"
            ),
        )
    settings.capacity_used = used_capacity
    settings.user_count_used = current_users

    if policy.calculate_volume == "created_traffic":
        credit_used = (
            max(
                int(settings.used_traffic or 0),
                marzhelp_policy.allocated_credit_baseline(db, admin_id),
            )
            if switching_to_allocated
            else int(settings.used_traffic or 0)
        )
    else:
        credit_used = marzhelp_policy.used_traffic_spend(db, admin_id)
    if (
        policy.total_traffic is not None
        and policy.total_traffic > 0
        and credit_used > policy.total_traffic
    ):
        raise marzhelp_policy.MarzhelpPolicyError(
            "credit_limit_below_usage",
            (
                "MarzHelp: traffic credit cannot be lower than current usage "
                f"({credit_used})"
            ),
        )

    values = policy.model_dump(
        exclude={
            "billing_mode",
            "money_balance_toman",
            "allowed_inbounds",
            "allowed_user_limits",
            "allowed_subscription_modes",
        }
    )
    values.update(
        view_full_client_ip=True,
        prevent_user_creation=False,
        prevent_revoke_subscription=False,
    )
    for field, value in values.items():
        setattr(settings, field, value)
    if switching_to_allocated:
        settings.used_traffic = credit_used

    settings.inbound_permissions = [
        MarzhelpAdminInboundPermission(admin_id=admin_id, inbound_tag=tag)
        for tag in policy.allowed_inbounds
    ]
    settings.user_limit_permissions = [
        MarzhelpAdminUserLimitPermission(
            admin_id=admin_id,
            concurrent_user_limit=limit,
        )
        for limit in policy.allowed_user_limits
    ]
    settings.subscription_mode_permissions = [
        MarzhelpAdminSubscriptionModePermission(
            admin_id=admin_id,
            mode=mode.value,
        )
        for mode in policy.allowed_subscription_modes
    ]

    if commit:
        db.commit()
        db.refresh(settings)
    else:
        db.flush()
    return settings


def partial_update_admin(
    db: Session,
    dbadmin: Admin,
    modified_admin: AdminPartialModify,
    commit: bool = True,
) -> Admin:
    """
    Partially updates an admin's details.

    Args:
        db (Session): Database session.
        dbadmin (Admin): The admin object to be updated.
        modified_admin (AdminPartialModify): The modified admin data.

    Returns:
        Admin: The updated admin object.
    """
    if modified_admin.is_sudo is not None:
        dbadmin.is_sudo = modified_admin.is_sudo
    if modified_admin.password is not None and dbadmin.hashed_password != modified_admin.hashed_password:
        dbadmin.hashed_password = modified_admin.hashed_password
        dbadmin.password_reset_at = datetime.utcnow()
    if modified_admin.telegram_id is not None:
        dbadmin.telegram_id = modified_admin.telegram_id
    if modified_admin.discord_webhook is not None:
        dbadmin.discord_webhook = modified_admin.discord_webhook

    if commit:
        db.commit()
        db.refresh(dbadmin)
    else:
        db.flush()
    return dbadmin


def remove_admin(
    db: Session,
    dbadmin: Admin,
    strategy: str = "keep_users",
) -> int:
    """
    Removes an admin from the database.

    Args:
        db (Session): Database session.
        dbadmin (Admin): The admin object to be removed.

    Returns:
        int: Number of users affected by the selected strategy.
    """
    if strategy not in {"delete_users", "disable_users", "keep_users"}:
        raise ValueError("Unknown admin deletion strategy")
    owned_users = (
        db.query(User)
        .filter(User.admin_id == dbadmin.id)
        .order_by(User.id)
        .with_for_update()
        .all()
    )
    if strategy == "delete_users":
        for dbuser in owned_users:
            db.delete(dbuser)
    else:
        for dbuser in owned_users:
            if strategy == "disable_users":
                dbuser.status = UserStatus.disabled
                dbuser.last_status_change = datetime.utcnow()
            dbuser.admin = None
            dbuser.admin_id = None

    # Canonical MarzHelp rows with admin foreign keys must be removed in the
    # same transaction before the Marzban admin row. Audit/deletion ledgers are
    # intentionally retained because they have no destructive foreign key.
    db.query(MarzhelpAdminUsage).filter(MarzhelpAdminUsage.admin_id == dbadmin.id).delete()
    db.query(MarzhelpLimit).filter(MarzhelpLimit.admin_id == dbadmin.id).delete()
    db.query(MarzhelpAdminInboundPermission).filter(
        MarzhelpAdminInboundPermission.admin_id == dbadmin.id
    ).delete()
    db.query(MarzhelpAdminUserLimitPermission).filter(
        MarzhelpAdminUserLimitPermission.admin_id == dbadmin.id
    ).delete()
    db.query(MarzhelpAdminSubscriptionModePermission).filter(
        MarzhelpAdminSubscriptionModePermission.admin_id == dbadmin.id
    ).delete()
    db.query(AdminApiToken).filter(AdminApiToken.admin_id == dbadmin.id).delete()
    db.query(AdminUserPlanAccess).filter(AdminUserPlanAccess.admin_id == dbadmin.id).delete()
    db.query(AdminHierarchy).filter(
        (AdminHierarchy.ancestor_id == dbadmin.id)
        | (AdminHierarchy.descendant_id == dbadmin.id)
    ).delete(synchronize_session=False)
    db.query(MarzhelpAdminSettings).filter(
        MarzhelpAdminSettings.admin_id == dbadmin.id
    ).delete()
    db.delete(dbadmin)
    db.commit()
    return len(owned_users)


def get_admin_by_id(db: Session, id: int) -> Admin:
    """
    Retrieves an admin by their ID.

    Args:
        db (Session): Database session.
        id (int): The ID of the admin.

    Returns:
        Admin: The admin object.
    """
    return db.query(Admin).filter(Admin.id == id).first()


def get_admin_by_telegram_id(db: Session, telegram_id: int) -> Admin:
    """
    Retrieves an admin by their Telegram ID.

    Args:
        db (Session): Database session.
        telegram_id (int): The Telegram ID of the admin.

    Returns:
        Admin: The admin object.
    """
    return db.query(Admin).filter(Admin.telegram_id == telegram_id).first()


def get_admins(db: Session,
               offset: Optional[int] = None,
               limit: Optional[int] = None,
               username: Optional[str] = None,
               scope_admin_id: Optional[int] = None) -> List[Admin]:
    """
    Retrieves a list of admins with optional filters and pagination.

    Args:
        db (Session): Database session.
        offset (Optional[int]): The number of records to skip (for pagination).
        limit (Optional[int]): The maximum number of records to return.
        username (Optional[str]): The username to filter by.

    Returns:
        List[Admin]: A list of admin objects.
    """
    query = db.query(Admin).order_by(Admin.created_at.desc(), Admin.id.desc())
    if scope_admin_id is not None:
        query = query.filter(
            exists().where(
                and_(
                    AdminHierarchy.ancestor_id == scope_admin_id,
                    AdminHierarchy.descendant_id == Admin.id,
                )
            )
        )
    if username:
        query = query.filter(Admin.username.ilike(f'%{username}%'))
    if offset:
        query = query.offset(offset)
    if limit:
        query = query.limit(limit)
    return query.all()


def get_admins_with_count(
    db: Session,
    offset: int = 0,
    limit: int = 20,
    username: Optional[str] = None,
    scope_admin_id: Optional[int] = None,
    role: Optional[str] = None,
    billing_mode: Optional[str] = None,
    account_status: Optional[str] = None,
) -> Tuple[List[Admin], int]:
    query = db.query(Admin)
    if scope_admin_id is not None:
        query = query.filter(
            exists().where(
                and_(
                    AdminHierarchy.ancestor_id == scope_admin_id,
                    AdminHierarchy.descendant_id == Admin.id,
                )
            )
        )
    if username:
        query = query.filter(Admin.username.ilike(f"%{username}%"))
    if role:
        query = query.filter(Admin.role.has(AdminRole.code == role))
    if billing_mode:
        query = query.filter(
            exists().where(
                and_(
                    MarzhelpAdminSettings.admin_id == Admin.id,
                    MarzhelpAdminSettings.billing_mode == billing_mode,
                )
            )
        )
    if account_status:
        query = query.filter(
            exists().where(
                and_(
                    MarzhelpAdminSettings.admin_id == Admin.id,
                    MarzhelpAdminSettings.account_status_id == AdminAccountStatus.id,
                    AdminAccountStatus.code == account_status,
                )
            )
        )
    total = query.count()
    admins = query.order_by(Admin.created_at.desc(), Admin.id.desc()).offset(offset).limit(limit).all()
    return admins, total


def reset_admin_usage(db: Session, dbadmin: Admin) -> int:
    """
    Retrieves an admin's usage by their username.
    Args:
        db (Session): Database session.
        dbadmin (Admin): The admin object to be updated.
    Returns:
        Admin: The updated admin.
    """
    if (dbadmin.users_usage == 0):
        return dbadmin

    usage_log = AdminUsageLogs(
        admin=dbadmin,
        used_traffic_at_reset=dbadmin.users_usage
    )
    db.add(usage_log)
    dbadmin.users_usage = 0

    db.commit()
    db.refresh(dbadmin)
    return dbadmin


def create_user_template(db: Session, user_template: UserTemplateCreate) -> UserTemplate:
    """
    Creates a new user template in the database.

    Args:
        db (Session): Database session.
        user_template (UserTemplateCreate): The user template creation data.

    Returns:
        UserTemplate: The created user template object.
    """
    inbound_tags: List[str] = []
    for _, i in user_template.inbounds.items():
        inbound_tags.extend(i)
    dbuser_template = UserTemplate(
        name=user_template.name,
        data_limit=user_template.data_limit,
        expire_duration=user_template.expire_duration,
        username_prefix=user_template.username_prefix,
        username_suffix=user_template.username_suffix,
        inbounds=db.query(ProxyInbound).filter(ProxyInbound.tag.in_(inbound_tags)).all()
    )
    db.add(dbuser_template)
    db.commit()
    db.refresh(dbuser_template)
    return dbuser_template


def update_user_template(
        db: Session, dbuser_template: UserTemplate, modified_user_template: UserTemplateModify) -> UserTemplate:
    """
    Updates a user template's details.

    Args:
        db (Session): Database session.
        dbuser_template (UserTemplate): The user template object to be updated.
        modified_user_template (UserTemplateModify): The modified user template data.

    Returns:
        UserTemplate: The updated user template object.
    """
    if modified_user_template.name is not None:
        dbuser_template.name = modified_user_template.name
    if modified_user_template.data_limit is not None:
        dbuser_template.data_limit = modified_user_template.data_limit
    if modified_user_template.expire_duration is not None:
        dbuser_template.expire_duration = modified_user_template.expire_duration
    if modified_user_template.username_prefix is not None:
        dbuser_template.username_prefix = modified_user_template.username_prefix
    if modified_user_template.username_suffix is not None:
        dbuser_template.username_suffix = modified_user_template.username_suffix

    if modified_user_template.inbounds:
        inbound_tags: List[str] = []
        for _, i in modified_user_template.inbounds.items():
            inbound_tags.extend(i)
        dbuser_template.inbounds = db.query(ProxyInbound).filter(ProxyInbound.tag.in_(inbound_tags)).all()

    db.commit()
    db.refresh(dbuser_template)
    return dbuser_template


def remove_user_template(db: Session, dbuser_template: UserTemplate):
    """
    Removes a user template from the database.

    Args:
        db (Session): Database session.
        dbuser_template (UserTemplate): The user template object to be removed.
    """
    db.delete(dbuser_template)
    db.commit()


def get_user_template(db: Session, user_template_id: int) -> UserTemplate:
    """
    Retrieves a user template by its ID.

    Args:
        db (Session): Database session.
        user_template_id (int): The ID of the user template.

    Returns:
        UserTemplate: The user template object.
    """
    return db.query(UserTemplate).filter(UserTemplate.id == user_template_id).first()


def get_user_templates(
        db: Session, offset: Union[int, None] = None, limit: Union[int, None] = None) -> List[UserTemplate]:
    """
    Retrieves a list of user templates with optional pagination.

    Args:
        db (Session): Database session.
        offset (Union[int, None]): The number of records to skip (for pagination).
        limit (Union[int, None]): The maximum number of records to return.

    Returns:
        List[UserTemplate]: A list of user template objects.
    """
    dbuser_templates = db.query(UserTemplate)
    if offset:
        dbuser_templates = dbuser_templates.offset(offset)
    if limit:
        dbuser_templates = dbuser_templates.limit(limit)

    return dbuser_templates.all()


def get_node(db: Session, name: str) -> Optional[Node]:
    """
    Retrieves a node by its name.

    Args:
        db (Session): The database session.
        name (str): The name of the node to retrieve.

    Returns:
        Optional[Node]: The Node object if found, None otherwise.
    """
    return db.query(Node).filter(Node.name == name).first()


def get_node_by_id(db: Session, node_id: int) -> Optional[Node]:
    """
    Retrieves a node by its ID.

    Args:
        db (Session): The database session.
        node_id (int): The ID of the node to retrieve.

    Returns:
        Optional[Node]: The Node object if found, None otherwise.
    """
    return db.query(Node).filter(Node.id == node_id).first()


def get_nodes(db: Session,
              status: Optional[Union[NodeStatus, list]] = None,
              enabled: bool = None) -> List[Node]:
    """
    Retrieves nodes based on optional status and enabled filters.

    Args:
        db (Session): The database session.
        status (Optional[Union[NodeStatus, list]]): The status or list of statuses to filter by.
        enabled (bool): If True, excludes disabled nodes.

    Returns:
        List[Node]: A list of Node objects matching the criteria.
    """
    query = db.query(Node)

    if status:
        if isinstance(status, list):
            query = query.filter(Node.status.in_(status))
        else:
            query = query.filter(Node.status == status)

    if enabled:
        query = query.filter(Node.status != NodeStatus.disabled)

    return query.all()


def get_nodes_usage(db: Session, start: datetime, end: datetime) -> List[NodeUsageResponse]:
    """
    Retrieves usage data for all nodes within a specified time range.

    Args:
        db (Session): The database session.
        start (datetime): The start time of the usage period.
        end (datetime): The end time of the usage period.

    Returns:
        List[NodeUsageResponse]: A list of NodeUsageResponse objects containing usage data.
    """
    usages = {0: NodeUsageResponse(  # Main Core
        node_id=None,
        node_name="Master",
        uplink=0,
        downlink=0
    )}

    for node in db.query(Node).all():
        usages[node.id] = NodeUsageResponse(
            node_id=node.id,
            node_name=node.name,
            uplink=0,
            downlink=0
        )

    cond = and_(NodeUsage.created_at >= start, NodeUsage.created_at <= end)

    for v in db.query(NodeUsage).filter(cond):
        try:
            usages[v.node_id or 0].uplink += v.uplink
            usages[v.node_id or 0].downlink += v.downlink
        except KeyError:
            pass

    return list(usages.values())


def create_node(db: Session, node: NodeCreate) -> Node:
    """
    Creates a new node in the database.

    Args:
        db (Session): The database session.
        node (NodeCreate): The node creation model containing node details.

    Returns:
        Node: The newly created Node object.
    """
    dbnode = Node(name=node.name,
                  address=node.address,
                  port=node.port,
                  api_port=node.api_port,
                  usage_coefficient=node.usage_coefficient,
                  watchdog_enabled=node.watchdog_enabled)

    db.add(dbnode)
    db.commit()
    db.refresh(dbnode)
    return dbnode


def remove_node(db: Session, dbnode: Node) -> Node:
    """
    Removes a node from the database.

    Args:
        db (Session): The database session.
        dbnode (Node): The Node object to be removed.

    Returns:
        Node: The removed Node object.
    """
    db.delete(dbnode)
    db.commit()
    return dbnode


def update_node(db: Session, dbnode: Node, modify: NodeModify) -> Node:
    """
    Updates an existing node with new information.

    Args:
        db (Session): The database session.
        dbnode (Node): The Node object to be updated.
        modify (NodeModify): The modification model containing updated node details.

    Returns:
        Node: The updated Node object.
    """
    if modify.name is not None:
        dbnode.name = modify.name

    if modify.address is not None:
        dbnode.address = modify.address

    if modify.port is not None:
        dbnode.port = modify.port

    if modify.api_port is not None:
        dbnode.api_port = modify.api_port

    if modify.status is NodeStatus.disabled:
        dbnode.status = modify.status
        dbnode.xray_version = None
        dbnode.message = None
    else:
        dbnode.status = NodeStatus.connecting

    if modify.usage_coefficient:
        dbnode.usage_coefficient = modify.usage_coefficient

    if modify.watchdog_enabled is not None:
        dbnode.watchdog_enabled = modify.watchdog_enabled

    db.commit()
    db.refresh(dbnode)
    return dbnode


def get_node_watchdog_settings(db: Session) -> NodeWatchdogSettings:
    settings = db.query(NodeWatchdogSettings).filter(NodeWatchdogSettings.id == 1).first()
    if settings is None:
        settings = NodeWatchdogSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def update_node_watchdog_settings(
        db: Session, update: NodeWatchdogSettingsUpdate) -> NodeWatchdogSettings:
    settings = get_node_watchdog_settings(db)
    settings.enabled = update.enabled
    settings.telegram_chat_id = update.telegram_chat_id or None
    settings.check_interval = update.check_interval
    settings.backoff_cap = update.backoff_cap
    settings.remind_every = update.remind_every
    if update.telegram_bot_token:
        settings.telegram_bot_token = update.telegram_bot_token
    db.commit()
    db.refresh(settings)
    return settings


def update_node_status(db: Session, dbnode: Node, status: NodeStatus, message: str = None, version: str = None) -> Node:
    """
    Updates the status of a node.

    Args:
        db (Session): The database session.
        dbnode (Node): The Node object to be updated.
        status (NodeStatus): The new status of the node.
        message (str, optional): A message associated with the status update.
        version (str, optional): The version of the node software.

    Returns:
        Node: The updated Node object.
    """
    dbnode.status = status
    dbnode.message = message
    dbnode.xray_version = version
    dbnode.last_status_change = datetime.utcnow()
    db.commit()
    db.refresh(dbnode)
    return dbnode


def create_notification_reminder(
        db: Session, reminder_type: ReminderType, expires_at: datetime, user_id: int, threshold: Optional[int] = None) -> NotificationReminder:
    """
    Creates a new notification reminder.

    Args:
        db (Session): The database session.
        reminder_type (ReminderType): The type of reminder.
        expires_at (datetime): The expiration time of the reminder.
        user_id (int): The ID of the user associated with the reminder.
        threshold (Optional[int]): The threshold value to check for (e.g., days left or usage percent).

    Returns:
        NotificationReminder: The newly created NotificationReminder object.
    """
    reminder = NotificationReminder(type=reminder_type, expires_at=expires_at, user_id=user_id)
    if threshold is not None:
        reminder.threshold = threshold
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def get_notification_reminder(
        db: Session, user_id: int, reminder_type: ReminderType, threshold: Optional[int] = None
) -> Union[NotificationReminder, None]:
    """
    Retrieves a notification reminder for a user.

    Args:
        db (Session): The database session.
        user_id (int): The ID of the user.
        reminder_type (ReminderType): The type of reminder to retrieve.
        threshold (Optional[int]): The threshold value to check for (e.g., days left or usage percent).

    Returns:
        Union[NotificationReminder, None]: The NotificationReminder object if found and not expired, None otherwise.
    """
    query = db.query(NotificationReminder).filter(
        NotificationReminder.user_id == user_id,
        NotificationReminder.type == reminder_type
    )

    # If a threshold is provided, filter for reminders with this threshold
    if threshold is not None:
        query = query.filter(NotificationReminder.threshold == threshold)

    reminder = query.first()

    if reminder is None:
        return None

    # Check if the reminder has expired
    if reminder.expires_at and reminder.expires_at < datetime.utcnow():
        db.delete(reminder)
        db.flush()
        return None

    return reminder


def delete_notification_reminder_by_type(
        db: Session, user_id: int, reminder_type: ReminderType, threshold: Optional[int] = None
) -> None:
    """
    Deletes a notification reminder for a user based on the reminder type and optional threshold.

    Args:
        db (Session): The database session.
        user_id (int): The ID of the user.
        reminder_type (ReminderType): The type of reminder to delete.
        threshold (Optional[int]): The threshold to delete (e.g., days left or usage percent). If not provided, deletes all reminders of that type.
    """
    stmt = delete(NotificationReminder).where(
        NotificationReminder.user_id == user_id,
        NotificationReminder.type == reminder_type
    )

    # If a threshold is provided, include it in the filter
    if threshold is not None:
        stmt = stmt.where(NotificationReminder.threshold == threshold)

    db.execute(stmt)
    db.commit()


def delete_notification_reminder(
    db: Session, dbreminder: NotificationReminder, commit: bool = True
) -> None:
    """
    Deletes a specific notification reminder.

    Args:
        db (Session): The database session.
        dbreminder (NotificationReminder): The NotificationReminder object to delete.
    """
    db.delete(dbreminder)
    if commit:
        db.commit()
    else:
        db.flush()
    return


def count_online_users(
    db: Session,
    hours: int = 24,
    admin: Admin | None = None,
    allowed_inbounds: set[str] | None = None,
    scope_admin_id: int | None = None,
):
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=hours)
    query = db.query(func.count(User.id)).filter(User.online_at.isnot(
        None), User.online_at >= twenty_four_hours_ago)
    if admin is not None:
        query = query.filter(User.admin_id == admin.id)
    if scope_admin_id is not None:
        query = query.filter(
            exists().where(
                and_(
                    AdminHierarchy.ancestor_id == scope_admin_id,
                    AdminHierarchy.descendant_id == User.admin_id,
                )
            )
        )
    query = apply_inbound_access_filter(query, allowed_inbounds)
    return query.scalar()
