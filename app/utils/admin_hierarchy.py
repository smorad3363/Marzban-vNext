"""Central role, hierarchy, scope, credit, and account-state services."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import Counter
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import and_, exists, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.models import (
    Admin,
    AdminAccountStatus,
    AdminApiToken,
    AdminBulkJob,
    AdminCreditTransfer,
    AdminHierarchy,
    AdminHierarchySettings,
    AdminRole,
    AdminReferralAttribution,
    AdminReferralEvent,
    AdminSuspensionAdmin,
    AdminSuspensionEvent,
    AdminSuspensionReason,
    AdminSuspensionUser,
    AdminUserCreationMode,
    MarzhelpAdminSettings,
    SystemOwner,
    User,
)
from app.models.user import UserStatus
from app.utils import admin_billing, marzhelp_policy


OWNER = "OWNER"
SUPER_ADMIN = "SUPER_ADMIN"
ADMIN = "ADMIN"
ROLE_IDS = {OWNER: 1, SUPER_ADMIN: 2, ADMIN: 3}

ACTIVE = "ACTIVE"
SUSPENDED = "SUSPENDED"
DISABLED = "DISABLED"
ACCOUNT_STATUS_IDS = {ACTIVE: 1, SUSPENDED: 2, DISABLED: 3}

FREE_FORM = "FREE_FORM"  # legacy API alias for FORM_ONLY
FORM_ONLY = "FORM_ONLY"
PLAN_ONLY = "PLAN_ONLY"
BOTH = "BOTH"
USER_CREATION_MODE_IDS = {FREE_FORM: 1, FORM_ONLY: 1, PLAN_ONLY: 2, BOTH: 3}


def creation_mode_capabilities(settings: MarzhelpAdminSettings | None) -> set[str]:
    mode_id = settings.user_creation_mode_id if settings is not None else USER_CREATION_MODE_IDS[PLAN_ONLY]
    return creation_mode_capabilities_by_id(mode_id)


def creation_mode_capabilities_by_id(mode_id: int) -> set[str]:
    if mode_id == USER_CREATION_MODE_IDS[FORM_ONLY]:
        return {FORM_ONLY}
    if mode_id == USER_CREATION_MODE_IDS[BOTH]:
        return {FORM_ONLY, PLAN_ONLY}
    return {PLAN_ONLY}


def allows_form_creation(settings: MarzhelpAdminSettings | None) -> bool:
    return FORM_ONLY in creation_mode_capabilities(settings)


def allows_plan_creation(settings: MarzhelpAdminSettings | None) -> bool:
    return PLAN_ONLY in creation_mode_capabilities(settings)

ALLOWED_API_SCOPES = frozenset(
    {
        "account:read",
        "users:read",
        "users:write",
        "admins:read",
        "plans:read",
        "plans:write",
        "audit:read",
    }
)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class HierarchyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def hierarchy_settings(db: Session, *, lock: bool = False) -> AdminHierarchySettings | None:
    query = db.query(AdminHierarchySettings).filter(AdminHierarchySettings.id == 1)
    if lock:
        query = query.with_for_update()
    return query.one_or_none()


def hierarchy_enabled(db: Session) -> bool:
    settings = hierarchy_settings(db)
    return bool(settings and settings.enabled)


def owner_id(db: Session) -> int | None:
    return db.query(SystemOwner.admin_id).filter(SystemOwner.id == 1).scalar()


def role_code(admin: Admin | object) -> str:
    role = getattr(admin, "role", None)
    code = getattr(role, "code", None)
    if code:
        return ADMIN if str(code) == SUPER_ADMIN else str(code)
    role_id = getattr(admin, "role_id", None)
    for candidate, candidate_id in ROLE_IDS.items():
        if role_id == candidate_id:
            return ADMIN if candidate == SUPER_ADMIN else candidate
    return OWNER if bool(getattr(admin, "is_sudo", False)) else ADMIN


def is_owner(db: Session, admin: Admin | object) -> bool:
    if not hierarchy_enabled(db):
        return bool(getattr(admin, "is_sudo", False))
    return role_code(admin) == OWNER and getattr(admin, "id", None) == owner_id(db)


def can_manage_children(db: Session, admin: Admin | object) -> bool:
    if not hierarchy_enabled(db):
        return bool(getattr(admin, "is_sudo", False))
    if is_owner(db, admin):
        return True
    admin_id = getattr(admin, "id", None)
    if admin_id is None:
        return False
    return bool(
        db.query(MarzhelpAdminSettings.can_create_admins)
        .filter(MarzhelpAdminSettings.admin_id == int(admin_id))
        .scalar()
    )


def admin_creation_remaining(
    db: Session,
    admin: Admin | object,
    settings: MarzhelpAdminSettings | None = None,
) -> int | None:
    if is_owner(db, admin):
        return None
    admin_id = getattr(admin, "id", None)
    if admin_id is None:
        return 0
    settings = settings or db.get(MarzhelpAdminSettings, int(admin_id))
    if settings is None or not settings.can_create_admins:
        return 0
    if settings.admin_creation_limit is None:
        return None
    return max(
        int(settings.admin_creation_limit)
        - int(settings.admin_creations_used or 0)
        - int(settings.delegated_admin_creation_limit or 0),
        0,
    )


def allowed_child_roles(parent: Admin | object) -> list[str]:
    return [ADMIN]


def allowed_child_billing_modes(
    db: Session,
    parent: Admin | object,
    settings: MarzhelpAdminSettings | None = None,
) -> list[admin_billing.BillingMode]:
    if is_owner(db, parent):
        return [
            admin_billing.BillingMode.USED_TRAFFIC,
            admin_billing.BillingMode.ALLOCATED_TRAFFIC,
            admin_billing.BillingMode.USER_CREDIT,
        ]
    parent_id = getattr(parent, "id", None)
    settings = settings or (
        db.get(MarzhelpAdminSettings, int(parent_id)) if parent_id is not None else None
    )
    mode = admin_billing.billing_mode(settings)
    if mode == admin_billing.BillingMode.USED_TRAFFIC:
        modes = [admin_billing.BillingMode.USED_TRAFFIC]
        if settings is not None and settings.can_create_allocated_children:
            modes.append(admin_billing.BillingMode.ALLOCATED_TRAFFIC)
        return modes
    if mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC:
        return [admin_billing.BillingMode.ALLOCATED_TRAFFIC]
    if mode == admin_billing.BillingMode.USER_CREDIT:
        return [admin_billing.BillingMode.USER_CREDIT]
    if mode == admin_billing.BillingMode.SEAT_CREDIT:
        return [admin_billing.BillingMode.SEAT_CREDIT]
    return [admin_billing.BillingMode.LEGACY_COMPAT]


def configure_new_child_admin_creation(
    db: Session,
    *,
    actor: Admin,
    parent: Admin,
    child: Admin,
    child_settings: MarzhelpAdminSettings,
    child_role: str,
    child_billing_mode: admin_billing.BillingMode,
    can_create_admins: bool,
    can_delegate_admin_creation: bool,
    can_create_allocated_children: bool,
    admin_creation_limit: int | None,
) -> None:
    parent_settings = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id == parent.id)
        .with_for_update()
        .one_or_none()
    )
    if parent_settings is None:
        raise HierarchyError("policy_missing", "Parent policy is missing")
    if not admin_in_scope(db, actor, parent.id):
        raise HierarchyError("admin_scope_forbidden", "Parent is outside actor scope")
    if not is_owner(db, parent) and not parent_settings.can_create_admins:
        raise HierarchyError("admin_create_forbidden", "Parent cannot create administrators")
    if child_role not in allowed_child_roles(parent):
        raise HierarchyError("child_role_too_powerful", "Child cannot be more powerful than parent")
    if child_billing_mode not in allowed_child_billing_modes(db, parent, parent_settings):
        raise HierarchyError(
            "child_billing_mode_forbidden",
            "Child billing mode must follow the parent billing contract",
        )
    if can_delegate_admin_creation and not can_create_admins:
        raise HierarchyError(
            "delegation_requires_creation",
            "Delegating Admin creation requires Admin creation permission",
        )
    if can_create_admins and not is_owner(db, parent) and not parent_settings.can_delegate_admin_creation:
        raise HierarchyError(
            "admin_creation_delegation_forbidden",
            "Parent cannot delegate Admin creation permission",
        )
    if can_delegate_admin_creation and not is_owner(db, parent) and not parent_settings.can_delegate_admin_creation:
        raise HierarchyError(
            "admin_creation_delegation_forbidden",
            "Parent cannot delegate Admin creation permission",
        )
    if can_create_allocated_children and child_billing_mode != admin_billing.BillingMode.USED_TRAFFIC:
        can_create_allocated_children = False
    if (
        can_create_allocated_children
        and not is_owner(db, parent)
        and not parent_settings.can_create_allocated_children
    ):
        raise HierarchyError(
            "allocated_child_delegation_forbidden",
            "Parent cannot delegate Allocated Traffic child creation",
        )

    child_limit = admin_creation_limit if can_create_admins else 0
    if child_limit is not None:
        child_limit = int(child_limit)
        if child_limit < 0:
            raise HierarchyError("invalid_admin_creation_limit", "Admin creation limit cannot be negative")
    remaining = admin_creation_remaining(db, parent, parent_settings)
    delegated_cost = int(child_limit or 0)
    required = 1 + delegated_cost
    if child_limit is None and remaining is not None:
        raise HierarchyError(
            "unlimited_admin_creation_forbidden",
            "A finite parent cannot delegate unlimited Admin creation",
        )
    if remaining is not None and required > remaining:
        raise HierarchyError(
            "admin_creation_quota_exhausted",
            "Parent has insufficient Admin creation quota",
        )

    if not is_owner(db, parent):
        parent_settings.admin_creations_used = int(parent_settings.admin_creations_used or 0) + 1
        parent_settings.delegated_admin_creation_limit = (
            int(parent_settings.delegated_admin_creation_limit or 0) + delegated_cost
        )
    child_settings.can_create_admins = bool(can_create_admins)
    child_settings.can_delegate_admin_creation = bool(can_delegate_admin_creation)
    child_settings.can_create_allocated_children = bool(can_create_allocated_children)
    child_settings.admin_creation_limit = child_limit
    child_settings.admin_creations_used = 0
    child_settings.delegated_admin_creation_limit = 0
    child_settings.billing_mode = child_billing_mode.value


def update_child_admin_creation(
    db: Session,
    *,
    actor: Admin,
    child: Admin,
    can_create_admins: bool,
    can_delegate_admin_creation: bool,
    can_create_allocated_children: bool,
    admin_creation_limit: int | None,
) -> MarzhelpAdminSettings:
    if child.parent_admin_id is None or child.id == owner_id(db):
        raise HierarchyError("owner_policy_immutable", "Owner creation policy is unrestricted")
    if not admin_in_scope(db, actor, child.id):
        raise HierarchyError("admin_scope_forbidden", "Admin is outside actor scope")
    ids = sorted({child.id, int(child.parent_admin_id)})
    locked = {
        row.admin_id: row
        for row in db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id.in_(ids))
        .order_by(MarzhelpAdminSettings.admin_id)
        .with_for_update()
        .all()
    }
    if set(locked) != set(ids):
        raise HierarchyError("policy_missing", "Parent and child policies are required")
    parent = db.get(Admin, int(child.parent_admin_id))
    parent_settings = locked[int(child.parent_admin_id)]
    child_settings = locked[child.id]
    if parent is None:
        raise HierarchyError("parent_missing", "Parent administrator does not exist")
    if role_code(child) not in allowed_child_roles(parent):
        raise HierarchyError("child_role_too_powerful", "Child cannot be more powerful than parent")
    if can_delegate_admin_creation and not can_create_admins:
        raise HierarchyError(
            "delegation_requires_creation",
            "Delegating Admin creation requires Admin creation permission",
        )
    if can_create_admins and not is_owner(db, parent) and not parent_settings.can_delegate_admin_creation:
        raise HierarchyError(
            "admin_creation_delegation_forbidden",
            "Parent cannot delegate Admin creation permission",
        )
    if can_create_allocated_children and admin_billing.billing_mode(child_settings) != admin_billing.BillingMode.USED_TRAFFIC:
        can_create_allocated_children = False
    if (
        can_create_allocated_children
        and not is_owner(db, parent)
        and not parent_settings.can_create_allocated_children
    ):
        raise HierarchyError(
            "allocated_child_delegation_forbidden",
            "Parent cannot delegate Allocated Traffic child creation",
        )

    minimum_limit = int(child_settings.admin_creations_used or 0) + int(
        child_settings.delegated_admin_creation_limit or 0
    )
    new_limit = admin_creation_limit if can_create_admins else minimum_limit
    if new_limit is not None:
        new_limit = int(new_limit)
        if new_limit < minimum_limit:
            raise HierarchyError(
                "admin_creation_limit_below_committed",
                "Admin creation limit cannot be below used and delegated quota",
            )
    if not can_create_admins:
        can_delegate_admin_creation = False
        can_create_allocated_children = False

    old_limit = child_settings.admin_creation_limit
    old_reserved = int(old_limit or 0)
    new_reserved = int(new_limit or 0)
    if not is_owner(db, parent):
        parent_remaining = admin_creation_remaining(db, parent, parent_settings)
        available_with_current_reservation = (
            None if parent_remaining is None else parent_remaining + old_reserved
        )
        if new_limit is None and available_with_current_reservation is not None:
            raise HierarchyError(
                "unlimited_admin_creation_forbidden",
                "A finite parent cannot delegate unlimited Admin creation",
            )
        if (
            available_with_current_reservation is not None
            and new_reserved > available_with_current_reservation
        ):
            raise HierarchyError(
                "admin_creation_quota_exhausted",
                "Parent has insufficient Admin creation quota",
            )
        parent_settings.delegated_admin_creation_limit = max(
            int(parent_settings.delegated_admin_creation_limit or 0)
            - old_reserved
            + new_reserved,
            0,
        )

    child_settings.can_create_admins = bool(can_create_admins)
    child_settings.can_delegate_admin_creation = bool(can_delegate_admin_creation)
    child_settings.can_create_allocated_children = bool(can_create_allocated_children)
    child_settings.admin_creation_limit = new_limit
    db.flush()
    return child_settings


def account_status_code(db: Session, admin_id: int) -> str:
    value = (
        db.query(AdminAccountStatus.code)
        .join(MarzhelpAdminSettings, MarzhelpAdminSettings.account_status_id == AdminAccountStatus.id)
        .filter(MarzhelpAdminSettings.admin_id == admin_id)
        .scalar()
    )
    return str(value or ACTIVE)


def require_active_account(db: Session, admin: Admin | object) -> None:
    admin_id = getattr(admin, "id", None)
    if admin_id is None or not hierarchy_enabled(db):
        return
    state = account_status_code(db, int(admin_id))
    if state != ACTIVE:
        raise HierarchyError("account_read_only", f"Administrative account is {state.lower()}")


def admin_in_scope(db: Session, actor: Admin | object, target_admin_id: int) -> bool:
    actor_id = getattr(actor, "id", None)
    if actor_id is None:
        return bool(getattr(actor, "is_sudo", False)) and not hierarchy_enabled(db)
    if is_owner(db, actor):
        return True
    if not hierarchy_enabled(db):
        return int(actor_id) == int(target_admin_id)
    return bool(
        db.query(AdminHierarchy.ancestor_id)
        .filter(
            AdminHierarchy.ancestor_id == int(actor_id),
            AdminHierarchy.descendant_id == int(target_admin_id),
        )
        .first()
    )


def can_access_user(db: Session, actor: Admin | object, user: User) -> bool:
    if user.admin_id is None or not admin_in_scope(db, actor, user.admin_id):
        return False
    if is_owner(db, actor):
        return True
    allowed = marzhelp_policy.allowed_inbound_tags(db, actor)
    return allowed is None or marzhelp_policy.user_inbound_tags(user).issubset(allowed)


def scope_admin_column(query, db: Session, actor: Admin | object, column):
    """Apply subtree scope in SQL; never materialize an unbounded Python ID list."""

    actor_id = getattr(actor, "id", None)
    if is_owner(db, actor):
        return query
    if actor_id is None or not hierarchy_enabled(db):
        return query.filter(column == actor_id)
    scoped = exists().where(
        and_(
            AdminHierarchy.ancestor_id == int(actor_id),
            AdminHierarchy.descendant_id == column,
        )
    )
    return query.filter(scoped)


def subtree_admin_ids_query(db: Session, root_admin_id: int):
    return db.query(AdminHierarchy.descendant_id).filter(
        AdminHierarchy.ancestor_id == root_admin_id
    )


def _parent_cycle_nodes(admins: Iterable[Admin]) -> set[int]:
    parent_by_id = {item.id: item.parent_admin_id for item in admins}
    cycle_nodes: set[int] = set()
    for start in parent_by_id:
        seen: dict[int, int] = {}
        current = start
        while current in parent_by_id and parent_by_id[current] is not None:
            if current in seen:
                cycle_nodes.update(list(seen)[seen[current] :])
                break
            seen[current] = len(seen)
            current = parent_by_id[current]
    return cycle_nodes


def _rebuild_closure(db: Session, admins: list[Admin], max_depth: int) -> int:
    by_id = {item.id: item for item in admins}
    rows: list[AdminHierarchy] = []
    for descendant in admins:
        rows.append(AdminHierarchy(ancestor_id=descendant.id, descendant_id=descendant.id, depth=0))
        current = descendant
        visited = {descendant.id}
        depth = 0
        while current.parent_admin_id is not None:
            depth += 1
            if depth > max_depth:
                raise HierarchyError("max_depth_exceeded", f"Hierarchy exceeds maximum depth {max_depth}")
            parent_id = int(current.parent_admin_id)
            if parent_id in visited or parent_id not in by_id:
                raise HierarchyError("closure_invalid_parent", "Hierarchy contains a cycle or missing parent")
            visited.add(parent_id)
            rows.append(
                AdminHierarchy(
                    ancestor_id=parent_id,
                    descendant_id=descendant.id,
                    depth=depth,
                )
            )
            current = by_id[parent_id]
    db.query(AdminHierarchy).delete(synchronize_session=False)
    db.add_all(rows)
    db.flush()
    return len(rows)


def set_owner(db: Session, username: str) -> dict:
    """Atomically select Owner, repair legacy parentage, and enable hierarchy."""

    try:
        settings = hierarchy_settings(db, lock=True)
        if settings is None:
            raise HierarchyError("schema_not_ready", "Admin hierarchy migration is not installed")
        admins = db.query(Admin).order_by(Admin.id).with_for_update().all()
        selected = next((item for item in admins if item.username == username), None)
        if selected is None:
            raise HierarchyError("admin_not_found", f"Admin {username!r} does not exist")

        original_sudo = {item.id: bool(item.is_sudo) for item in admins}
        reason_counts: Counter[str] = Counter()
        cycle_nodes = _parent_cycle_nodes(admins)
        valid_ids = {item.id for item in admins}

        selected.role_id = ROLE_IDS[OWNER]
        selected.parent_admin_id = None
        selected.is_sudo = True

        for item in admins:
            if item.id == selected.id:
                continue
            item.role_id = ROLE_IDS[ADMIN]
            item.is_sudo = False
            item.external_api_enabled = False

            parent_id = item.parent_admin_id
            if item.id in cycle_nodes:
                item.parent_admin_id = selected.id
                reason_counts["cycle_broken_attached_to_owner"] += 1
            elif parent_id == item.id:
                item.parent_admin_id = selected.id
                reason_counts["self_parent_attached_to_owner"] += 1
            elif parent_id is not None and parent_id not in valid_ids:
                item.parent_admin_id = selected.id
                reason_counts["missing_parent_attached_to_owner"] += 1
            elif parent_id is None or parent_id == selected.id:
                item.parent_admin_id = selected.id
                reason_counts[
                    "legacy_sudo_attached_to_owner"
                    if original_sudo[item.id]
                    else "legacy_admin_missing_parent_attached_to_owner"
                ] += 1
            else:
                reason_counts["existing_valid_parent_preserved"] += 1

        db.flush()
        closure_rows = _rebuild_closure(db, admins, int(settings.max_depth or 64))

        owner_row = db.query(SystemOwner).filter(SystemOwner.id == 1).with_for_update().one_or_none()
        if owner_row is None:
            owner_row = SystemOwner(id=1, admin_id=selected.id, assigned_at=utc_now_naive())
            db.add(owner_row)
        else:
            owner_row.admin_id = selected.id
            owner_row.assigned_at = utc_now_naive()

        null_users = db.query(User).filter(User.admin_id.is_(None)).update(
            {User.admin_id: selected.id}, synchronize_session=False
        )
        reason_counts["null_user_owner_attached_to_owner"] += int(null_users or 0)

        existing_settings = {
            row.admin_id: row
            for row in db.query(MarzhelpAdminSettings)
            .filter(MarzhelpAdminSettings.admin_id.in_(valid_ids))
            .with_for_update()
            .all()
        }
        for item in admins:
            policy = existing_settings.get(item.id)
            if policy is None:
                policy = MarzhelpAdminSettings(
                    admin_id=item.id,
                    calculate_volume="created_traffic",
                    renewal_enabled=True,
                    user_creation_mode_id=USER_CREATION_MODE_IDS[FREE_FORM],
                    account_status_id=ACCOUNT_STATUS_IDS[ACTIVE],
                )
                db.add(policy)
            else:
                # Compatibility conversion happens only at the explicit cutover.
                # Legacy zero meant unlimited; canonical hierarchy uses NULL for it.
                if policy.total_traffic is not None and int(policy.total_traffic) <= 0:
                    policy.total_traffic = None
                policy.used_traffic = max(
                    int(policy.used_traffic or 0),
                    marzhelp_policy.allocated_credit_baseline(db, item.id),
                )
                policy.calculate_volume = "created_traffic"

            if item.id == selected.id:
                policy.total_traffic = None

        settings.enabled = True
        settings.updated_at = utc_now_naive()
        db.flush()

        owner_count = db.query(SystemOwner).count()
        orphan_count = db.query(Admin).filter(
            Admin.id != selected.id,
            or_(Admin.parent_admin_id.is_(None), ~Admin.parent_admin_id.in_(valid_ids)),
        ).count()
        if owner_count != 1 or orphan_count:
            raise HierarchyError("backfill_verification_failed", "Owner/parent invariants failed")

        db.commit()
        return {
            "owner": selected.username,
            "owner_id": selected.id,
            "admin_count": len(admins),
            "closure_rows": closure_rows,
            "reason_counts": dict(sorted(reason_counts.items())),
        }
    except Exception:
        db.rollback()
        raise


def reparent_subtree(db: Session, actor: Admin, target: Admin, new_parent: Admin) -> None:
    if not is_owner(db, actor):
        raise HierarchyError("owner_required", "Only Owner can reparent a subtree")
    if target.id == actor.id or target.id == new_parent.id:
        raise HierarchyError("invalid_parent", "Owner/self reparent is not allowed")
    if not can_manage_children(db, new_parent):
        raise HierarchyError("invalid_parent_permission", "New parent cannot manage child administrators")
    if role_code(target) not in allowed_child_roles(new_parent):
        raise HierarchyError("child_role_too_powerful", "Child cannot be more powerful than parent")
    if db.query(AdminHierarchy).filter(
        AdminHierarchy.ancestor_id == target.id,
        AdminHierarchy.descendant_id == new_parent.id,
    ).first():
        raise HierarchyError("cycle_detected", "The new parent is inside the target subtree")
    admins = db.query(Admin).order_by(Admin.id).with_for_update().all()
    target.parent_admin_id = new_parent.id
    settings = hierarchy_settings(db, lock=True)
    _rebuild_closure(db, admins, int(settings.max_depth if settings else 64))
    db.commit()


def attach_new_child(
    db: Session,
    *,
    actor: Admin,
    parent: Admin,
    child: Admin,
    child_role: str,
    commit: bool = True,
) -> None:
    if not can_manage_children(db, actor) or not admin_in_scope(db, actor, parent.id):
        raise HierarchyError("admin_create_forbidden", "Parent administrator is outside actor scope")
    if child_role not in allowed_child_roles(parent):
        raise HierarchyError("child_role_too_powerful", "Child cannot be more powerful than parent")
    child.role_id = ROLE_IDS[child_role]
    child.parent_admin_id = parent.id
    child.is_sudo = False
    child.external_api_enabled = False
    policy = db.get(MarzhelpAdminSettings, child.id)
    if policy is not None:
        policy.renewal_enabled = True
        # New descendants are Plan-only unless their parent explicitly grants
        # free-form creation. This keeps the raw endpoint fail-closed.
        policy.user_creation_mode_id = USER_CREATION_MODE_IDS[PLAN_ONLY]
        policy.account_status_id = ACCOUNT_STATUS_IDS[ACTIVE]
    admins = db.query(Admin).order_by(Admin.id).with_for_update().all()
    settings = hierarchy_settings(db, lock=True)
    _rebuild_closure(db, admins, int(settings.max_depth if settings else 64))
    if commit:
        db.commit()
    else:
        db.flush()


def configure_child_user_creation_access(
    db: Session,
    *,
    actor: Admin,
    parent: Admin,
    child_settings: MarzhelpAdminSettings,
    mode: str,
    can_manage_plans: bool,
) -> None:
    if mode not in USER_CREATION_MODE_IDS:
        raise HierarchyError("invalid_creation_mode", "Unknown user creation mode")
    if admin_billing.billing_mode(child_settings) == admin_billing.BillingMode.USER_CREDIT and mode != PLAN_ONLY:
        raise HierarchyError("user_credit_plan_only", "USER_CREDIT administrators are always Plan Only")
    if can_manage_plans:
        raise HierarchyError("plan_management_owner_only", "Plan management is Owner-only")
    if not is_owner(db, actor):
        parent_settings = db.get(MarzhelpAdminSettings, parent.id)
        if parent_settings is None:
            raise HierarchyError("parent_policy_missing", "Parent policy is missing")
        requested = creation_mode_capabilities_by_id(USER_CREATION_MODE_IDS[mode])
        parent_capabilities = creation_mode_capabilities(parent_settings)
        if not requested.issubset(parent_capabilities):
            raise HierarchyError(
                "child_creation_mode_too_powerful",
                "Parent cannot grant a user creation path it does not have",
            )
    child_settings.user_creation_mode_id = USER_CREATION_MODE_IDS[mode]
    child_settings.can_manage_plans = bool(can_manage_plans)


def own_credit_spend(db: Session, settings: MarzhelpAdminSettings) -> int:
    mode = admin_billing.billing_mode(settings)
    if mode == admin_billing.BillingMode.SEAT_CREDIT:
        return int(settings.capacity_used or 0)
    if mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC:
        return int(settings.used_traffic or 0)
    if mode == admin_billing.BillingMode.USED_TRAFFIC:
        return marzhelp_policy.used_traffic_spend(db, settings.admin_id)
    if mode == admin_billing.BillingMode.USER_CREDIT:
        return int(db.query(User.id).filter(User.admin_id == settings.admin_id).count())
    if (settings.calculate_volume or "used_traffic") == "created_traffic":
        return int(settings.used_traffic or 0)
    return marzhelp_policy.used_traffic_spend(db, settings.admin_id)


def available_credit(db: Session, settings: MarzhelpAdminSettings) -> int | None:
    mode = admin_billing.billing_mode(settings)
    configured_limit = (
        settings.device_capacity_limit
        if mode == admin_billing.BillingMode.SEAT_CREDIT
        else settings.max_users
        if mode == admin_billing.BillingMode.USER_CREDIT
        else settings.total_traffic
    )
    if configured_limit is None:
        return None
    admin = db.get(Admin, settings.admin_id)
    if admin is not None and is_owner(db, admin):
        return None
    delegated = (
        0
        if mode == admin_billing.BillingMode.USED_TRAFFIC
        else int(settings.delegated_traffic or 0)
    )
    return max(
        int(configured_limit or 0)
        - own_credit_spend(db, settings)
        - delegated,
        0,
    )


def automatic_suspension_reason(
    db: Session,
    settings: MarzhelpAdminSettings,
    *,
    today: date | None = None,
) -> int | None:
    admin = db.get(Admin, settings.admin_id)
    if (
        admin is not None
        and not is_owner(db, admin)
        and bool(settings.money_billing_enabled)
        and int(settings.money_balance_toman or 0) <= 0
    ):
        return 2
    if settings.expiry_date is not None and settings.expiry_date < (today or date.today()):
        return 3
    configured_limit = (
        settings.device_capacity_limit
        if admin_billing.billing_mode(settings) == admin_billing.BillingMode.SEAT_CREDIT
        else settings.max_users
        if admin_billing.billing_mode(settings) == admin_billing.BillingMode.USER_CREDIT
        else settings.total_traffic
    )
    if admin_billing.billing_mode(settings) == admin_billing.BillingMode.USER_CREDIT:
        return None
    available = available_credit(db, settings) if configured_limit is not None else None
    if available is not None and available <= 0:
        return 2
    return None


def transfer_credit(
    db: Session,
    *,
    actor: Admin,
    source: Admin,
    target: Admin,
    amount: int,
    operation_type: str,
    idempotency_key: str,
    note: str | None = None,
    commit: bool = True,
    return_created: bool = False,
) -> AdminCreditTransfer | tuple[AdminCreditTransfer, bool]:
    if amount <= 0:
        raise HierarchyError("invalid_amount", "Credit amount must be positive")
    if operation_type not in {"grant", "reclaim", "owner_adjustment", "migration"}:
        raise HierarchyError("invalid_operation", "Unsupported credit operation")
    if not idempotency_key or len(idempotency_key) > 128:
        raise HierarchyError("invalid_idempotency_key", "A bounded idempotency key is required")
    if not is_owner(db, actor) and actor.id != source.id:
        raise HierarchyError("credit_scope_forbidden", "Only source admin or Owner can transfer credit")
    if operation_type in {"grant", "reclaim"} and target.parent_admin_id != source.id:
        raise HierarchyError("direct_child_required", "Credit moves only between a parent and direct child")

    ledger_from_id = target.id if operation_type == "reclaim" else source.id
    ledger_to_id = source.id if operation_type == "reclaim" else target.id
    reason = (note or f"system:{operation_type}_credit").strip()

    def checked_existing(existing: AdminCreditTransfer):
        if (
            existing.from_admin_id != ledger_from_id
            or existing.to_admin_id != ledger_to_id
            or existing.actor_admin_id != actor.id
            or int(existing.amount) != amount
            or existing.operation_type != operation_type
            or existing.note != reason
        ):
            raise HierarchyError(
                "idempotency_conflict",
                "Idempotency key belongs to another credit operation",
            )
        return (existing, False) if return_created else existing

    attempts = 3 if commit else 1
    for attempt in range(attempts):
        try:
            existing = (
                db.query(AdminCreditTransfer)
                .filter(AdminCreditTransfer.idempotency_key == idempotency_key)
                .with_for_update()
                .one_or_none()
            )
            if existing is not None:
                return checked_existing(existing)

            ids = sorted({source.id, target.id})
            wallets = {
                item.admin_id: item
                for item in db.query(MarzhelpAdminSettings)
                .filter(MarzhelpAdminSettings.admin_id.in_(ids))
                .order_by(MarzhelpAdminSettings.admin_id)
                .with_for_update()
                .all()
            }
            if set(wallets) != set(ids):
                raise HierarchyError("wallet_missing", "Both administrators need credit settings")
            source_wallet = wallets[source.id]
            target_wallet = wallets[target.id]
            source_mode = admin_billing.billing_mode(source_wallet)
            target_mode = admin_billing.billing_mode(target_wallet)
            seat_resource = target_mode == admin_billing.BillingMode.SEAT_CREDIT
            user_resource = target_mode == admin_billing.BillingMode.USER_CREDIT
            compatible = (
                source_mode == target_mode
                or source_mode == admin_billing.BillingMode.USED_TRAFFIC
                and target_mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC
            )
            if not is_owner(db, source) and not compatible:
                raise HierarchyError(
                    "credit_resource_mismatch",
                    "Parent and child billing modes use incompatible credit resources",
                )
            resource = (
                "seat_credit"
                if seat_resource
                else "user_credit"
                if user_resource
                else "traffic_credit"
            )
            balance_column = (
                MarzhelpAdminSettings.device_capacity_limit
                if seat_resource
                else MarzhelpAdminSettings.max_users
                if user_resource
                else MarzhelpAdminSettings.total_traffic
            )
            balance_before = getattr(target_wallet, balance_column.key)
            source_delegated_before = int(source_wallet.delegated_traffic or 0)
            # Owner remains unlimited, but finite credit delegated from every
            # non-USED_TRAFFIC wallet must still be recorded for reconciliation.
            # USED_TRAFFIC parents are charged from actual descendant usage instead.
            tracks_delegated_credit = source_mode != admin_billing.BillingMode.USED_TRAFFIC

            if operation_type == "reclaim":
                reclaimable = available_credit(db, target_wallet)
                if reclaimable is None:
                    raise HierarchyError(
                        "reclaim_unlimited_credit",
                        "Unlimited child credit cannot be reclaimed as a finite amount",
                    )
                if amount > reclaimable:
                    raise HierarchyError("reclaim_exceeds_available", "Reclaim exceeds child available credit")
                if tracks_delegated_credit and amount > source_delegated_before:
                    raise HierarchyError(
                        "reclaim_exceeds_delegated",
                        "Reclaim exceeds credit delegated by the parent",
                    )
                balance_after = int(balance_before) - amount
                source_delegated_after = (
                    source_delegated_before - amount
                    if tracks_delegated_credit
                    else source_delegated_before
                )
            else:
                available = available_credit(db, source_wallet)
                if not is_owner(db, source) and available is not None and amount > available:
                    raise HierarchyError("credit_exhausted", "Parent has insufficient delegatable credit")
                source_delegated_after = (
                    source_delegated_before + amount
                    if tracks_delegated_credit
                    else source_delegated_before
                )
                balance_after = int(balance_before or 0) + amount

            updates = {
                source.id: (
                    MarzhelpAdminSettings.delegated_traffic,
                    source_delegated_before,
                    source_delegated_after,
                ),
                target.id: (
                    balance_column,
                    balance_before,
                    balance_after,
                ),
            }
            for admin_id in sorted(updates):
                column, before, after = updates[admin_id]
                changed = (
                    db.query(MarzhelpAdminSettings)
                    .filter(
                        MarzhelpAdminSettings.admin_id == admin_id,
                        column == before,
                    )
                    .update({column: after}, synchronize_session="fetch")
                )
                if changed != 1:
                    db.rollback()
                    raise HierarchyError(
                        "credit_concurrent_conflict",
                        "Credit balance changed concurrently; retry with the same idempotency key",
                    )

            transfer = AdminCreditTransfer(
                from_admin_id=ledger_from_id,
                to_admin_id=ledger_to_id,
                actor_admin_id=actor.id,
                adjusted_admin_id=target.id,
                resource=resource,
                amount=amount,
                delta=-amount if operation_type == "reclaim" else amount,
                balance_before=balance_before,
                balance_after=balance_after,
                source_delegated_before=source_delegated_before,
                source_delegated_after=source_delegated_after,
                operation_type=operation_type,
                idempotency_key=idempotency_key,
                note=reason,
            )
            db.add(transfer)
            if commit:
                db.commit()
                db.refresh(transfer)
            else:
                db.flush()
            return (transfer, True) if return_created else transfer
        except OperationalError as exc:
            db.rollback()
            mysql_code = getattr(getattr(exc, "orig", None), "args", [None])[0]
            if mysql_code != 1213 or attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))
        except IntegrityError:
            db.rollback()
            if not commit:
                raise
            existing = db.query(AdminCreditTransfer).filter(
                AdminCreditTransfer.idempotency_key == idempotency_key
            ).one_or_none()
            if existing is not None:
                return checked_existing(existing)
            raise
    raise AssertionError("unreachable")


def issue_api_token(
    db: Session,
    *,
    owner: Admin,
    target: Admin,
    name: str,
    scopes: set[str],
    expires_at: datetime,
) -> tuple[AdminApiToken, str]:
    if not is_owner(db, owner):
        raise HierarchyError("owner_required", "Only Owner can issue automation tokens")
    invalid = scopes - ALLOWED_API_SCOPES
    if invalid or not scopes:
        raise HierarchyError("invalid_scopes", f"Invalid API scopes: {sorted(invalid)}")
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    if expires_at <= utc_now_naive():
        raise HierarchyError("invalid_expiry", "Token expiry must be in the future")
    if not target.external_api_enabled:
        raise HierarchyError("external_api_disabled", "External API is disabled for this admin")
    plaintext = "mzapi_" + secrets.token_urlsafe(36)
    row = AdminApiToken(
        admin_id=target.id,
        token_hash=hashlib.sha256(plaintext.encode()).digest(),
        name=name.strip(),
        scopes=sorted(scopes),
        expires_at=expires_at,
        created_by_admin_id=owner.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def authenticate_api_token(db: Session, plaintext: str) -> tuple[Admin, set[str]] | None:
    if not plaintext.startswith("mzapi_"):
        return None
    digest = hashlib.sha256(plaintext.encode()).digest()
    now = utc_now_naive()
    row = (
        db.query(AdminApiToken)
        .join(Admin, Admin.id == AdminApiToken.admin_id)
        .filter(
            AdminApiToken.token_hash == digest,
            AdminApiToken.revoked_at.is_(None),
            AdminApiToken.expires_at > now,
            Admin.external_api_enabled.is_(True),
        )
        .one_or_none()
    )
    if row is None:
        return None
    row.last_used_at = now
    db.commit()
    return db.get(Admin, row.admin_id), set(row.scopes or [])


def revoke_api_access(db: Session, owner: Admin, target: Admin) -> int:
    if not is_owner(db, owner):
        raise HierarchyError("owner_required", "Only Owner can revoke external API access")
    now = utc_now_naive()
    target.external_api_enabled = False
    target.external_api_updated_by = owner.id
    target.external_api_updated_at = now
    count = db.query(AdminApiToken).filter(
        AdminApiToken.admin_id == target.id,
        AdminApiToken.revoked_at.is_(None),
    ).update({AdminApiToken.revoked_at: now}, synchronize_session=False)
    db.commit()
    return int(count or 0)


def _target_user_query(db: Session, target_admin_id: int, include_subtree: bool):
    query = db.query(User)
    if include_subtree:
        query = query.filter(
            exists().where(
                and_(
                    AdminHierarchy.ancestor_id == target_admin_id,
                    AdminHierarchy.descendant_id == User.admin_id,
                )
            )
        )
    else:
        query = query.filter(User.admin_id == target_admin_id)
    return query


def _operation_fingerprint(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()


def _set_referral_attribution_once(
    db: Session,
    *,
    actor: Admin,
    referred: Admin,
    referrer: Admin | None,
    rate_bps: int | None,
    idempotency_key: str,
    note: str | None = None,
) -> tuple[AdminReferralEvent, bool]:
    """Owner-only attribution mutation. Never touches credit/resource ledgers."""
    if not is_owner(db, actor):
        raise HierarchyError("owner_required", "Only Owner can modify referral attribution")
    if referred.id == actor.id:
        raise HierarchyError("invalid_referral_target", "Owner referral attribution is not supported")
    if referrer is not None and referrer.id == referred.id:
        raise HierarchyError("invalid_referrer", "An Admin cannot refer itself")
    if not idempotency_key or len(idempotency_key) > 128:
        raise HierarchyError("invalid_idempotency_key", "A bounded idempotency key is required")
    if referrer is not None and (rate_bps is None or not 0 <= rate_bps <= 10_000):
        raise HierarchyError("invalid_referral_rate", "Referral rate must be between 0 and 10000 bps")
    normalized_note = (note or "").strip() or None
    fingerprint = _operation_fingerprint(
        actor.id,
        referred.id,
        referrer.id if referrer else None,
        rate_bps if referrer else None,
        normalized_note,
    )

    existing_event = (
        db.query(AdminReferralEvent)
        .filter(AdminReferralEvent.idempotency_key == idempotency_key)
        .with_for_update()
        .one_or_none()
    )
    if existing_event is not None:
        if existing_event.payload_fingerprint != fingerprint:
            raise HierarchyError("idempotency_conflict", "Idempotency key belongs to another referral operation")
        return existing_event, False

    admin_ids = sorted({referred.id, *(set() if referrer is None else {referrer.id})})
    db.query(Admin).filter(Admin.id.in_(admin_ids)).order_by(Admin.id).with_for_update().all()
    current = (
        db.query(AdminReferralAttribution)
        .filter(AdminReferralAttribution.referred_admin_id == referred.id)
        .with_for_update()
        .one_or_none()
    )
    existing_event = (
        db.query(AdminReferralEvent)
        .filter(AdminReferralEvent.idempotency_key == idempotency_key)
        .with_for_update()
        .one_or_none()
    )
    if existing_event is not None:
        if existing_event.payload_fingerprint != fingerprint:
            raise HierarchyError("idempotency_conflict", "Idempotency key belongs to another referral operation")
        return existing_event, False

    previous_referrer_id = current.referrer_admin_id if current else None
    previous_rate = current.rate_bps if current else None
    if referrer is None:
        if current is not None:
            db.delete(current)
        operation_type = "remove"
    elif current is None:
        current = AdminReferralAttribution(
            referred_admin_id=referred.id,
            referrer_admin_id=referrer.id,
            rate_bps=rate_bps,
            created_by_admin_id=actor.id,
            updated_by_admin_id=actor.id,
        )
        db.add(current)
        operation_type = "set"
    else:
        current.referrer_admin_id = referrer.id
        current.rate_bps = rate_bps
        current.updated_by_admin_id = actor.id
        current.updated_at = utc_now_naive()
        operation_type = "update"

    event = AdminReferralEvent(
        actor_admin_id=actor.id,
        referred_admin_id=referred.id,
        previous_referrer_admin_id=previous_referrer_id,
        new_referrer_admin_id=referrer.id if referrer else None,
        previous_rate_bps=previous_rate,
        new_rate_bps=rate_bps if referrer else None,
        operation_type=operation_type,
        idempotency_key=idempotency_key,
        payload_fingerprint=fingerprint,
        note=normalized_note,
    )
    db.add(event)
    try:
        db.commit()
        db.refresh(event)
        return event, True
    except IntegrityError:
        db.rollback()
        replay = db.query(AdminReferralEvent).filter(
            AdminReferralEvent.idempotency_key == idempotency_key
        ).one_or_none()
        if replay is not None and replay.payload_fingerprint == fingerprint:
            return replay, False
        raise


def set_referral_attribution(
    db: Session,
    *,
    actor: Admin,
    referred: Admin,
    referrer: Admin | None,
    rate_bps: int | None,
    idempotency_key: str,
    note: str | None = None,
) -> tuple[AdminReferralEvent, bool]:
    """Retry MySQL deadlock victims without changing attribution semantics."""
    actor_id = actor.id
    referred_id = referred.id
    referrer_id = referrer.id if referrer else None
    for attempt in range(3):
        try:
            return _set_referral_attribution_once(
                db,
                actor=db.get(Admin, actor_id),
                referred=db.get(Admin, referred_id),
                referrer=db.get(Admin, referrer_id) if referrer_id else None,
                rate_bps=rate_bps,
                idempotency_key=idempotency_key,
                note=note,
            )
        except OperationalError as exc:
            db.rollback()
            mysql_code = getattr(getattr(exc, "orig", None), "args", [None])[0]
            if mysql_code != 1213 or attempt == 2:
                raise
            time.sleep(0.02 * (attempt + 1))
    raise AssertionError("unreachable")


def _freeze_admin_once(
    db: Session,
    *,
    actor: Admin,
    target: Admin,
    reason_id: int,
    idempotency_key: str,
    note: str | None = None,
    batch_size: int = 500,
) -> tuple[AdminSuspensionEvent, bool]:
    """Authorized parent Freeze snapshots and freezes the complete descendant subtree."""
    if not (
        is_owner(db, actor)
        or can_manage_children(db, actor)
        and actor.id != target.id
        and admin_in_scope(db, actor, target.id)
    ):
        raise HierarchyError(
            "freeze_forbidden", "Only Owner or an authorized parent can freeze this subtree"
        )
    if target.id == actor.id or role_code(target) == OWNER:
        raise HierarchyError("invalid_freeze_target", "Owner cannot be frozen")
    if not idempotency_key or len(idempotency_key) > 128:
        raise HierarchyError("invalid_idempotency_key", "A bounded idempotency key is required")
    if db.get(AdminSuspensionReason, reason_id) is None:
        raise HierarchyError("invalid_suspension_reason", "Suspension reason does not exist")
    normalized_note = (note or "").strip() or None
    fingerprint = _operation_fingerprint(actor.id, target.id, reason_id, normalized_note, "full_subtree")
    replay = db.query(AdminSuspensionEvent).filter(
        AdminSuspensionEvent.idempotency_key == idempotency_key
    ).with_for_update().one_or_none()
    if replay is not None:
        if replay.operation_type != "owner_freeze" or replay.payload_fingerprint != fingerprint:
            raise HierarchyError("idempotency_conflict", "Idempotency key belongs to another freeze operation")
        return replay, False

    subtree_ids = [
        row[0]
        for row in db.query(AdminHierarchy.descendant_id)
        .filter(AdminHierarchy.ancestor_id == target.id)
        .order_by(AdminHierarchy.descendant_id)
        .all()
    ]
    if target.id not in subtree_ids:
        subtree_ids.append(target.id)
        subtree_ids.sort()
    settings_rows = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id.in_(subtree_ids))
        .order_by(MarzhelpAdminSettings.admin_id)
        .with_for_update()
        .all()
    )
    if {row.admin_id for row in settings_rows} != set(subtree_ids):
        raise HierarchyError("settings_missing", "Every Admin in frozen subtree requires settings")
    replay = db.query(AdminSuspensionEvent).filter(
        AdminSuspensionEvent.idempotency_key == idempotency_key
    ).with_for_update().one_or_none()
    if replay is not None:
        if replay.operation_type != "owner_freeze" or replay.payload_fingerprint != fingerprint:
            raise HierarchyError("idempotency_conflict", "Idempotency key belongs to another freeze operation")
        return replay, False
    if any(
        row.suspension_event_id
        and db.query(AdminSuspensionEvent.id).filter(
            AdminSuspensionEvent.id == row.suspension_event_id,
            AdminSuspensionEvent.operation_type == "owner_freeze",
            AdminSuspensionEvent.status == "complete",
        ).first()
        for row in settings_rows
    ):
        raise HierarchyError("already_frozen", "Admin subtree already has an active Owner Freeze")

    event = AdminSuspensionEvent(
        admin_id=target.id,
        actor_admin_id=actor.id,
        reason_id=reason_id,
        operation_type="owner_freeze",
        idempotency_key=idempotency_key,
        payload_fingerprint=fingerprint,
        limits_snapshot={"scope": "full_subtree", "note": normalized_note},
        status="processing",
    )
    db.add(event)
    db.flush()
    frozen_at = utc_now_naive()
    for settings in settings_rows:
        db.add(
            AdminSuspensionAdmin(
                event_id=event.id,
                admin_id=settings.admin_id,
                previous_account_status_id=settings.account_status_id,
                previous_suspended_reason_id=settings.suspended_reason_id,
                previous_suspended_at=settings.suspended_at,
                previous_suspended_by_admin_id=settings.suspended_by_admin_id,
                previous_suspension_event_id=settings.suspension_event_id,
                applied_account_status_id=ACCOUNT_STATUS_IDS[SUSPENDED],
                restore_status="applied",
            )
        )
        settings.account_status_id = ACCOUNT_STATUS_IDS[SUSPENDED]
        settings.suspended_reason_id = reason_id
        settings.suspended_at = frozen_at
        settings.suspended_by_admin_id = actor.id
        settings.suspension_event_id = event.id

    last_id = 0
    while True:
        users = (
            db.query(User)
            .filter(
                User.admin_id.in_(subtree_ids),
                User.id > last_id,
                User.status.in_((UserStatus.active, UserStatus.on_hold)),
            )
            .order_by(User.id)
            .limit(max(1, min(batch_size, 2000)))
            .with_for_update()
            .all()
        )
        if not users:
            break
        for user in users:
            db.add(AdminSuspensionUser(
                event_id=event.id,
                user_id=user.id,
                previous_status=user.status.value,
                applied_status=UserStatus.disabled.value,
                sync_status="applied",
            ))
            user.status = UserStatus.disabled
            last_id = user.id
        db.flush()
    event.status = "complete"
    db.commit()
    db.refresh(event)
    return event, True


def freeze_admin(
    db: Session,
    *,
    actor: Admin,
    target: Admin,
    reason_id: int,
    idempotency_key: str,
    note: str | None = None,
    batch_size: int = 500,
) -> tuple[AdminSuspensionEvent, bool]:
    """Retry MySQL deadlock victims with the same idempotency key."""
    actor_id = actor.id
    target_id = target.id
    for attempt in range(3):
        try:
            return _freeze_admin_once(
                db,
                actor=db.get(Admin, actor_id),
                target=db.get(Admin, target_id),
                reason_id=reason_id,
                idempotency_key=idempotency_key,
                note=note,
                batch_size=batch_size,
            )
        except OperationalError as exc:
            db.rollback()
            mysql_code = getattr(getattr(exc, "orig", None), "args", [None])[0]
            if mysql_code != 1213 or attempt == 2:
                raise
            time.sleep(0.02 * (attempt + 1))
        except IntegrityError:
            db.rollback()
            replay = db.query(AdminSuspensionEvent).filter(
                AdminSuspensionEvent.idempotency_key == idempotency_key
            ).one_or_none()
            expected = _operation_fingerprint(
                actor_id,
                target_id,
                reason_id,
                (note or "").strip() or None,
                "full_subtree",
            )
            if replay is not None and replay.operation_type == "owner_freeze" and replay.payload_fingerprint == expected:
                return replay, False
            raise
    raise AssertionError("unreachable")


def unfreeze_admin(
    db: Session,
    *,
    actor: Admin,
    target: Admin,
    idempotency_key: str,
) -> tuple[AdminSuspensionEvent, int, int, bool]:
    """Restore only state still owned by the matching Freeze event."""
    if not (
        is_owner(db, actor)
        or can_manage_children(db, actor)
        and actor.id != target.id
        and admin_in_scope(db, actor, target.id)
    ):
        raise HierarchyError(
            "unfreeze_forbidden", "Only Owner or an authorized parent can unfreeze this subtree"
        )
    if not idempotency_key or len(idempotency_key) > 128:
        raise HierarchyError("invalid_idempotency_key", "A bounded idempotency key is required")
    replay = db.query(AdminSuspensionEvent).filter(
        AdminSuspensionEvent.resolved_idempotency_key == idempotency_key
    ).one_or_none()
    if replay is not None:
        if replay.admin_id != target.id or replay.operation_type != "owner_freeze":
            raise HierarchyError("idempotency_conflict", "Idempotency key belongs to another unfreeze operation")
        summary = replay.limits_snapshot or {}
        return (
            replay,
            int(summary.get("restored_admins", 0)),
            int(summary.get("restored_users", 0)),
            False,
        )
    target_settings = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id == target.id)
        .with_for_update()
        .one_or_none()
    )
    if target_settings is None or target_settings.suspension_event_id is None:
        raise HierarchyError("no_active_freeze", "Admin has no active Owner Freeze")
    event = (
        db.query(AdminSuspensionEvent)
        .filter(AdminSuspensionEvent.id == target_settings.suspension_event_id)
        .with_for_update()
        .one_or_none()
    )
    if event is None or event.operation_type != "owner_freeze" or event.status != "complete":
        raise HierarchyError("no_active_freeze", "Admin has no active Owner Freeze")
    snapshots = (
        db.query(AdminSuspensionAdmin, MarzhelpAdminSettings)
        .join(MarzhelpAdminSettings, MarzhelpAdminSettings.admin_id == AdminSuspensionAdmin.admin_id)
        .filter(AdminSuspensionAdmin.event_id == event.id)
        .order_by(AdminSuspensionAdmin.admin_id)
        .with_for_update()
        .all()
    )
    restored_admins = 0
    for snapshot, settings in snapshots:
        if settings.suspension_event_id == event.id:
            settings.account_status_id = snapshot.previous_account_status_id
            settings.suspended_reason_id = snapshot.previous_suspended_reason_id
            settings.suspended_at = snapshot.previous_suspended_at
            settings.suspended_by_admin_id = snapshot.previous_suspended_by_admin_id
            settings.suspension_event_id = snapshot.previous_suspension_event_id
            snapshot.restore_status = "restored"
            restored_admins += 1
        else:
            snapshot.restore_status = "skipped_changed"
    user_rows = (
        db.query(AdminSuspensionUser, User)
        .join(User, User.id == AdminSuspensionUser.user_id)
        .filter(AdminSuspensionUser.event_id == event.id, AdminSuspensionUser.sync_status == "applied")
        .order_by(User.id)
        .with_for_update()
        .all()
    )
    restored_users = 0
    for snapshot, user in user_rows:
        if user.status == UserStatus.disabled:
            user.status = UserStatus(snapshot.previous_status)
            snapshot.sync_status = "restored"
            restored_users += 1
        else:
            snapshot.sync_status = "skipped_changed"
    event.status = "resolved"
    event.resolved_at = utc_now_naive()
    event.resolved_by_admin_id = actor.id
    event.resolved_idempotency_key = idempotency_key
    event.limits_snapshot = {
        **(event.limits_snapshot or {}),
        "restored_admins": restored_admins,
        "restored_users": restored_users,
    }
    db.commit()
    db.refresh(event)
    return event, restored_admins, restored_users, True


def suspend_admin(
    db: Session,
    *,
    actor: Admin,
    target: Admin,
    reason_id: int = 1,
    include_subtree: bool = True,
    batch_size: int = 500,
    commit: bool = True,
) -> AdminSuspensionEvent:
    if not admin_in_scope(db, actor, target.id) or actor.id == target.id:
        raise HierarchyError("suspension_scope_forbidden", "Target admin is outside actor scope")
    event = AdminSuspensionEvent(
        admin_id=target.id,
        actor_admin_id=actor.id,
        reason_id=reason_id,
        status="processing",
    )
    db.add(event)
    db.flush()
    last_id = 0
    while True:
        batch = (
            _target_user_query(db, target.id, include_subtree)
            .filter(
                User.id > last_id,
                User.status.in_((UserStatus.active, UserStatus.on_hold)),
            )
            .order_by(User.id)
            .limit(max(1, min(batch_size, 2000)))
            .with_for_update()
            .all()
        )
        if not batch:
            break
        for user in batch:
            db.add(
                AdminSuspensionUser(
                    event_id=event.id,
                    user_id=user.id,
                    previous_status=user.status.value,
                    applied_status=UserStatus.disabled.value,
                    sync_status="applied",
                )
            )
            user.status = UserStatus.disabled
            last_id = user.id
        db.flush()

    settings_query = db.query(MarzhelpAdminSettings)
    if include_subtree:
        settings_query = settings_query.filter(
            MarzhelpAdminSettings.admin_id.in_(subtree_admin_ids_query(db, target.id))
        )
    else:
        settings_query = settings_query.filter(MarzhelpAdminSettings.admin_id == target.id)
    settings_query.update(
        {
            MarzhelpAdminSettings.account_status_id: ACCOUNT_STATUS_IDS[SUSPENDED],
            MarzhelpAdminSettings.suspended_reason_id: reason_id,
            MarzhelpAdminSettings.suspended_at: utc_now_naive(),
            MarzhelpAdminSettings.suspended_by_admin_id: actor.id,
            MarzhelpAdminSettings.suspension_event_id: event.id,
        },
        synchronize_session=False,
    )
    event.status = "complete"
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return event


def resume_admin(db: Session, *, actor: Admin, target: Admin) -> int:
    if not admin_in_scope(db, actor, target.id) or actor.id == target.id:
        raise HierarchyError("resume_scope_forbidden", "Target admin is outside actor scope")
    settings = db.get(MarzhelpAdminSettings, target.id)
    if settings is None or settings.account_status_id != ACCOUNT_STATUS_IDS[SUSPENDED]:
        raise HierarchyError("no_active_suspension", "Admin has no resumable suspension event")
    if settings.suspension_event_id is None:
        if automatic_suspension_reason(db, settings) is not None:
            raise HierarchyError(
                "suspension_condition_active",
                "Automatic suspension condition is still active",
            )
        settings.account_status_id = ACCOUNT_STATUS_IDS[ACTIVE]
        settings.suspended_reason_id = None
        settings.suspended_at = None
        settings.suspended_by_admin_id = None
        db.commit()
        return 0
    event_id = int(settings.suspension_event_id)
    rows = (
        db.query(AdminSuspensionUser, User)
        .join(User, User.id == AdminSuspensionUser.user_id)
        .filter(
            AdminSuspensionUser.event_id == event_id,
            AdminSuspensionUser.sync_status == "applied",
        )
        .order_by(User.id)
        .with_for_update()
        .all()
    )
    restored = 0
    for snapshot, user in rows:
        if user.status == UserStatus.disabled:
            user.status = UserStatus(snapshot.previous_status)
            restored += 1
        snapshot.sync_status = "restored"
    subtree_settings = db.query(MarzhelpAdminSettings).filter(
        MarzhelpAdminSettings.suspension_event_id == event_id
    )
    subtree_settings.update(
        {
            MarzhelpAdminSettings.account_status_id: ACCOUNT_STATUS_IDS[ACTIVE],
            MarzhelpAdminSettings.suspended_reason_id: None,
            MarzhelpAdminSettings.suspended_at: None,
            MarzhelpAdminSettings.suspended_by_admin_id: None,
            MarzhelpAdminSettings.suspension_event_id: None,
        },
        synchronize_session=False,
    )
    event = db.get(AdminSuspensionEvent, event_id)
    if event:
        event.status = "resolved"
        event.resolved_at = utc_now_naive()
    db.commit()
    return restored


def activate_disabled_admin(db: Session, *, actor: Admin, target: Admin) -> None:
    """Activate a disabled Admin without changing any User state or credit."""
    if actor.id == target.id or not (
        is_owner(db, actor)
        or can_manage_children(db, actor) and admin_in_scope(db, actor, target.id)
    ):
        raise HierarchyError(
            "activation_forbidden",
            "Only Owner or an authorized parent can activate this Admin",
        )
    settings = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id == target.id)
        .with_for_update()
        .one_or_none()
    )
    if settings is None or settings.account_status_id != ACCOUNT_STATUS_IDS[DISABLED]:
        raise HierarchyError("account_not_disabled", "Admin account is not disabled")
    if automatic_suspension_reason(db, settings) is not None:
        raise HierarchyError(
            "suspension_condition_active",
            "Automatic suspension condition is still active",
        )
    settings.account_status_id = ACCOUNT_STATUS_IDS[ACTIVE]
    settings.suspended_reason_id = None
    settings.suspended_at = None
    settings.suspended_by_admin_id = None
    settings.suspension_event_id = None
    db.commit()


def run_disable_job(
    db: Session,
    *,
    actor: Admin,
    target: Admin,
    include_subtree: bool,
    idempotency_key: str,
    batch_size: int = 500,
) -> AdminBulkJob:
    existing = db.query(AdminBulkJob).filter(
        AdminBulkJob.idempotency_key == idempotency_key
    ).one_or_none()
    if existing and existing.status == "complete":
        return existing
    if existing and (
        existing.actor_admin_id != actor.id
        or existing.target_admin_id != target.id
        or bool(existing.include_subtree) != bool(include_subtree)
    ):
        raise HierarchyError("idempotency_conflict", "Idempotency key belongs to another bulk job")
    if not admin_in_scope(db, actor, target.id):
        raise HierarchyError("bulk_scope_forbidden", "Target is outside actor scope")
    query = _target_user_query(db, target.id, include_subtree).filter(
        User.status.in_((UserStatus.active, UserStatus.on_hold))
    )
    if existing is None:
        job = AdminBulkJob(
            actor_admin_id=actor.id,
            target_admin_id=target.id,
            operation="disable",
            include_subtree=include_subtree,
            status="processing",
            total_count=query.count(),
            idempotency_key=idempotency_key,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
    else:
        job = existing
    last_id = int(job.last_user_id or 0)
    while True:
        ids = [
            row[0]
            for row in query.with_entities(User.id)
            .filter(User.id > last_id)
            .order_by(User.id)
            .limit(max(1, min(batch_size, 2000)))
            .all()
        ]
        if not ids:
            break
        db.query(User).filter(User.id.in_(ids)).update(
            {User.status: UserStatus.disabled}, synchronize_session=False
        )
        last_id = ids[-1]
        job.last_user_id = last_id
        job.processed_count = int(job.processed_count or 0) + len(ids)
        db.commit()
    job.status = "complete"
    db.commit()
    db.refresh(job)
    return job
