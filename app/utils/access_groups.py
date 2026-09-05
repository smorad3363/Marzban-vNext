"""Owner-managed network access groups, independent from commercial Plans."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import xray
from app.db.models import (
    AccessGroup,
    AccessGroupHost,
    AccessGroupInbound,
    AccessGroupNode,
    Admin,
    MarzhelpAdminSettings,
    Node,
    ProxyHost,
    User,
)
from app.models.admin_hierarchy import AccessGroupInput, AccessGroupResponse
from app.models.user import UserStatus
from app.utils import admin_hierarchy


def _require_owner(db: Session, actor: Admin) -> None:
    if not admin_hierarchy.is_owner(db, actor):
        raise admin_hierarchy.HierarchyError(
            "access_group_management_forbidden", "Only Owner can manage Access Groups"
        )


def _scope(db: Session, group_id: int) -> tuple[set[str], dict[str, set[int]], set[int]]:
    inbounds = {
        row.inbound_tag
        for row in db.query(AccessGroupInbound).filter(AccessGroupInbound.access_group_id == group_id)
    }
    hosts = {tag: set() for tag in inbounds}
    for row in db.query(AccessGroupHost).filter(AccessGroupHost.access_group_id == group_id):
        hosts.setdefault(row.inbound_tag, set()).add(row.host_id)
    nodes = {
        row.node_id
        for row in db.query(AccessGroupNode).filter(AccessGroupNode.access_group_id == group_id)
    }
    return inbounds, hosts, nodes


def _validate_scope(db: Session, values: AccessGroupInput) -> None:
    configured = set(xray.config.inbounds_by_tag)
    unknown = set(values.inbounds) - configured
    if unknown:
        raise admin_hierarchy.HierarchyError(
            "unknown_inbound", f"Unknown Access Group inbounds: {sorted(unknown)}"
        )
    selected_hosts = {host_id for ids in values.hosts.values() for host_id in ids}
    host_rows = (
        db.query(ProxyHost.id, ProxyHost.inbound_tag)
        .filter(ProxyHost.id.in_(selected_hosts), ProxyHost.is_legacy.is_(False))
        .all()
        if selected_hosts
        else []
    )
    host_map = {row.id: row.inbound_tag for row in host_rows}
    missing = selected_hosts - set(host_map)
    mismatched = {
        host_id
        for tag, ids in values.hosts.items()
        for host_id in ids
        if host_map.get(host_id) != tag
    }
    if missing or mismatched:
        raise admin_hierarchy.HierarchyError(
            "access_group_host_invalid",
            f"Unavailable hosts: {sorted(missing)}; inbound mismatch: {sorted(mismatched)}",
        )
    if values.node_ids:
        existing_nodes = {
            row[0] for row in db.query(Node.id).filter(Node.id.in_(values.node_ids)).all()
        }
        if existing_nodes != set(values.node_ids):
            raise admin_hierarchy.HierarchyError("access_group_node_invalid", "Unknown node selected")


def _replace_scope(db: Session, group: AccessGroup, values: AccessGroupInput) -> None:
    for model in (AccessGroupHost, AccessGroupInbound, AccessGroupNode):
        db.query(model).filter(model.access_group_id == group.id).delete(synchronize_session=False)
    db.add_all(
        AccessGroupInbound(access_group_id=group.id, inbound_tag=tag) for tag in values.inbounds
    )
    db.add_all(
        AccessGroupHost(access_group_id=group.id, inbound_tag=tag, host_id=host_id)
        for tag, ids in values.hosts.items()
        for host_id in ids
    )
    db.add_all(
        AccessGroupNode(access_group_id=group.id, node_id=node_id) for node_id in values.node_ids
    )


def response(db: Session, group: AccessGroup) -> AccessGroupResponse:
    inbounds, hosts, nodes = _scope(db, group.id)
    active_count = (
        db.query(func.count(User.id))
        .filter(User.access_group_id == group.id, User.status == UserStatus.active)
        .scalar()
        or 0
    )
    return AccessGroupResponse(
        id=group.id,
        owner_admin_id=group.owner_admin_id,
        name=group.name,
        description=group.description,
        node_ids=sorted(nodes),
        inbounds=sorted(inbounds),
        hosts={tag: sorted(hosts[tag]) for tag in sorted(inbounds)},
        archived_at=group.archived_at,
        active_user_count=active_count,
    )


def list_groups(db: Session, actor: Admin) -> list[AccessGroup]:
    # Admins may select groups while creating users, but cannot manage them.
    return (
        db.query(AccessGroup)
        .filter(AccessGroup.archived_at.is_(None))
        .order_by(AccessGroup.name, AccessGroup.id)
        .all()
    )


def create(db: Session, actor: Admin, values: AccessGroupInput) -> AccessGroup:
    _require_owner(db, actor)
    _validate_scope(db, values)
    group = AccessGroup(
        owner_admin_id=actor.id,
        name=values.name.strip(),
        description=values.description,
    )
    db.add(group)
    db.flush()
    _replace_scope(db, group, values)
    db.commit()
    db.refresh(group)
    return group


def apply_to_user(db: Session, user: User, group_id: int) -> None:
    group = db.get(AccessGroup, group_id)
    if group is None or group.archived_at is not None:
        raise admin_hierarchy.HierarchyError("access_group_unavailable", "Access Group is unavailable")
    inbounds, hosts, _ = _scope(db, group.id)
    if not inbounds or set(hosts) != inbounds or any(not hosts[tag] for tag in inbounds):
        raise admin_hierarchy.HierarchyError("access_group_invalid", "Access Group has incomplete network scope")
    settings = db.get(MarzhelpAdminSettings, user.admin_id)
    if settings is not None and not settings.all_inbounds:
        forbidden = inbounds - set(settings.allowed_inbounds or [])
        if forbidden:
            raise admin_hierarchy.HierarchyError(
                "access_group_scope_forbidden", f"Access Group exceeds Admin scope: {sorted(forbidden)}"
            )
    from app.utils.admin_plans import _apply_plan_network_to_user

    _apply_plan_network_to_user(db, user, inbounds)
    user.access_group_id = group.id


def update(db: Session, actor: Admin, group: AccessGroup, values: AccessGroupInput) -> list[int]:
    _require_owner(db, actor)
    _validate_scope(db, values)
    group.name = values.name.strip()
    group.description = values.description
    _replace_scope(db, group, values)
    db.flush()
    users = (
        db.query(User)
        .filter(User.access_group_id == group.id, User.status == UserStatus.active)
        .order_by(User.id)
        .all()
    )
    for user in users:
        apply_to_user(db, user, group.id)
    db.commit()
    return [user.id for user in users]


def archive(db: Session, actor: Admin, group: AccessGroup) -> None:
    _require_owner(db, actor)
    active_users = (
        db.query(func.count(User.id))
        .filter(User.access_group_id == group.id, User.status == UserStatus.active)
        .scalar()
        or 0
    )
    if active_users:
        raise admin_hierarchy.HierarchyError(
            "access_group_in_use", "Access Group cannot be archived while active users reference it"
        )
    group.archived_at = datetime.utcnow()
    db.commit()


def host_scope(db: Session, user: User) -> dict[str, set[int]] | None:
    if user.access_group_id is None:
        return None
    group = db.get(AccessGroup, user.access_group_id)
    if group is None or group.archived_at is not None:
        return {}
    inbounds, hosts, _ = _scope(db, group.id)
    if not inbounds or set(hosts) != inbounds or any(not hosts[tag] for tag in inbounds):
        return {}
    return hosts


def propagate_host_changes(
    db: Session,
    *,
    replacement_ids: dict[int, int],
    removed_ids: set[int],
) -> list[int]:
    changed_ids = set(replacement_ids) | removed_ids
    if not changed_ids:
        return []
    rows = (
        db.query(AccessGroupHost)
        .filter(AccessGroupHost.host_id.in_(changed_ids))
        .with_for_update()
        .all()
    )
    affected_groups = {row.access_group_id for row in rows}
    for row in rows:
        replacement = replacement_ids.get(row.host_id)
        if replacement is not None:
            row.host_id = replacement
        elif row.host_id in removed_ids:
            db.delete(row)
    db.flush()
    synced = []
    for group_id in sorted(affected_groups):
        inbounds, hosts, _ = _scope(db, group_id)
        if set(hosts) != inbounds or any(not hosts[tag] for tag in inbounds):
            raise admin_hierarchy.HierarchyError(
                "host_change_would_invalidate_access_group",
                f"Host change would leave Access Group {group_id} without explicit host access",
            )
        users = (
            db.query(User)
            .filter(User.access_group_id == group_id, User.status == UserStatus.active)
            .order_by(User.id)
            .all()
        )
        for user in users:
            apply_to_user(db, user, group_id)
            synced.append(user.id)
    return synced
