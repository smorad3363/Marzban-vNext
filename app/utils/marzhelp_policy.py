"""Transactional Marzhelp policy and quota accounting.

All enforcement lives here so API, Telegram, CLI, jobs, and direct CRUD callers
apply the same rules. Marzhelp only edits the canonical settings rows.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import secrets
from typing import Any
from uuid import uuid4
from sqlalchemy import case, func, or_, update
from sqlalchemy.orm import Session

from app import logger, xray
from app.device_limit.constants import PenaltyStatus, SubscriptionMode
from app.db.models import (
    Admin,
    AdminHierarchy,
    AdminHierarchySettings,
    MarzhelpAccountingTransaction,
    MarzhelpAdminSettings,
    MarzhelpDeletedUser,
    SystemOwner,
    User,
    UserUsageResetLogs,
)
from app.utils import admin_billing


MAX_CUSTOMER_USERNAME_LENGTH = 32
RESTRICTED_CREATE_FIELDS = {
    "auto_delete_in_days",
    "concurrent_user_limit",
    "data_limit_reset_strategy",
    "inbounds",
    "next_plan",
    "on_hold_expire_duration",
    "on_hold_timeout",
    "proxies",
    "status",
}


class MarzhelpPolicyError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        audit_admin_id: int | None = None,
        audit_operation_type: str | None = None,
        audit_details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.audit_admin_id = audit_admin_id
        self.audit_operation_type = audit_operation_type
        self.audit_details = audit_details


class UserUpdateOperation(str, Enum):
    """Trusted server-side intent for policy-sensitive user updates."""

    edit = "edit"
    renew = "renew"


PLAN_ONLY_MODE_ID = 2


def _is_effective_owner(db: Session, actor: Admin | object | None) -> bool:
    if actor is None:
        return False
    hierarchy_on = bool(
        db.query(AdminHierarchySettings.enabled)
        .filter(AdminHierarchySettings.id == 1)
        .scalar()
    )
    if not hierarchy_on:
        return bool(getattr(actor, "is_sudo", False))
    return getattr(actor, "id", None) == db.query(SystemOwner.admin_id).filter(SystemOwner.id == 1).scalar()


def validate_plan_only_direct_edit(
    db: Session,
    dbuser: User,
    modify: Any,
    *,
    actor: Admin | object | None = None,
    operation: UserUpdateOperation = UserUpdateOperation.edit,
) -> None:
    """Block direct quota edits for Plan-only admins; explicit Plan renewals bypass."""

    if UserUpdateOperation(operation) != UserUpdateOperation.edit or _is_effective_owner(db, actor):
        return
    fields_set = set(getattr(modify, "model_fields_set", set()))
    if not fields_set:
        fields_set = {
            field
            for field in ("data_limit", "expire", "concurrent_user_limit")
            if getattr(modify, field, None) is not None
        }
    changed = set()
    if "data_limit" in fields_set and _effective_data_limit(getattr(modify, "data_limit", None)) != _effective_data_limit(dbuser.data_limit):
        changed.add("data_limit")
    if "expire" in fields_set and _effective_expire(getattr(modify, "expire", None)) != _effective_expire(dbuser.expire):
        changed.add("expire")
    if "concurrent_user_limit" in fields_set and getattr(modify, "concurrent_user_limit", None) != dbuser.concurrent_user_limit:
        changed.add("concurrent_user_limit")
    if not changed:
        return
    target_settings = _settings(db, dbuser.admin_id)
    actor_settings = _settings(db, getattr(actor, "id", None)) if actor is not None else target_settings
    if not any(
        settings is not None and settings.user_creation_mode_id == PLAN_ONLY_MODE_ID
        for settings in (target_settings, actor_settings)
    ):
        return
    raise MarzhelpPolicyError(
        "plan_only_direct_edit_forbidden",
        "Plan-only administrators must change traffic, expiry, and device limits through a Plan",
    )


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    while value:
        value, remainder = divmod(value, 36)
        encoded = alphabet[remainder] + encoded
    return encoded or "0"


def ensure_admin_namespace_prefix(db: Session, admin: Admin) -> str:
    """Return one persisted, concurrency-safe namespace for an Admin/Owner."""

    locked = db.query(Admin).filter(Admin.id == admin.id).with_for_update().one()
    if locked.user_namespace_prefix:
        return locked.user_namespace_prefix
    # Admin ID makes concurrent prefixes unique; random material hides a plain
    # sequential namespace while the DB unique constraint remains final authority.
    locked.user_namespace_prefix = f"u{_base36(locked.id)}{secrets.token_hex(2)}"
    db.flush()
    return locked.user_namespace_prefix


def customer_username(db: Session, admin: Admin, requested_username: str) -> str:
    prefix = ensure_admin_namespace_prefix(db, admin)
    final_username = f"{prefix}_{requested_username}"
    if len(final_username) > MAX_CUSTOMER_USERNAME_LENGTH:
        maximum = MAX_CUSTOMER_USERNAME_LENGTH - len(prefix) - 1
        raise MarzhelpPolicyError(
            "customer_username_too_long",
            f"Requested username may contain at most {maximum} characters for this namespace",
        )
    return final_username


def restricted_create_payload(settings: MarzhelpAdminSettings, user: Any):
    """Replace restricted raw-create network/device input with Admin policy."""

    mode = admin_billing.billing_mode(settings)
    if mode not in {
        admin_billing.BillingMode.USED_TRAFFIC,
        admin_billing.BillingMode.ALLOCATED_TRAFFIC,
    }:
        return user
    injected = sorted(RESTRICTED_CREATE_FIELDS & set(user.model_fields_set))
    if injected:
        raise MarzhelpPolicyError(
            "protected_create_fields",
            "Restricted creation does not accept protected fields: " + ", ".join(injected),
        )

    from app.models.proxy import ProxySettings, ProxyTypes
    from app.models.user import UserCreate, UserDataLimitResetStrategy, UserStatusCreate

    configured = set(xray.config.inbounds_by_tag)
    if settings.all_inbounds:
        selected = configured
    else:
        selected = set(settings.allowed_inbounds)
        missing = sorted(selected - configured)
        if missing:
            raise MarzhelpPolicyError(
                "inbound_unavailable",
                "Configured Admin inbound is unavailable: " + ", ".join(missing),
            )
    if not selected:
        raise MarzhelpPolicyError(
            "inbound_scope_empty",
            "Restricted creation requires at least one configured Admin inbound",
        )

    inbounds: dict[ProxyTypes, list[str]] = {}
    for tag in sorted(selected):
        protocol = ProxyTypes(xray.config.inbounds_by_tag[tag]["protocol"])
        inbounds.setdefault(protocol, []).append(tag)
    proxies = {
        protocol: ProxySettings.from_dict(protocol, {})
        for protocol in inbounds
    }
    concurrent_user_limit = None
    if not settings.all_user_limits:
        allowed_limits = list(settings.allowed_user_limits)
        if not allowed_limits:
            raise MarzhelpPolicyError(
                "user_limit_scope_empty",
                "Restricted creation requires at least one Admin device limit",
            )
        # Least-privilege deterministic policy; client cannot select or inject it.
        concurrent_user_limit = min(allowed_limits)

    return UserCreate(
        username=user.username,
        data_limit=user.data_limit,
        expire=user.expire,
        note=user.note,
        status=UserStatusCreate.active,
        proxies=proxies,
        inbounds=inbounds,
        concurrent_user_limit=concurrent_user_limit,
        data_limit_reset_strategy=UserDataLimitResetStrategy.no_reset,
    )


def _billing_strategy(settings: MarzhelpAdminSettings) -> admin_billing.BillingStrategy:
    try:
        return admin_billing.strategy_for(settings)
    except admin_billing.BillingModeError as exc:
        raise MarzhelpPolicyError(exc.code, str(exc)) from exc


def _validate_billing_plan(
    settings: MarzhelpAdminSettings,
    concurrent_user_limit: int | None,
) -> None:
    try:
        _billing_strategy(settings).validate_plan(concurrent_user_limit)
    except admin_billing.BillingModeError as exc:
        raise MarzhelpPolicyError(exc.code, str(exc)) from exc


def calculate_delete_refund(data_limit: int | None, actual_used_traffic: int | None) -> int:
    """Allocated credit is final; deleting a user never restores admin credit."""

    return 0


def _settings(db: Session, admin_id: int | None, lock: bool = False) -> MarzhelpAdminSettings | None:
    if admin_id is None:
        return None
    admin = db.get(Admin, admin_id)
    if admin is not None:
        hierarchy_on = bool(
            db.query(AdminHierarchySettings.enabled)
            .filter(AdminHierarchySettings.id == 1)
            .scalar()
        )
        real_owner_id = (
            db.query(SystemOwner.admin_id)
            .filter(SystemOwner.id == 1)
            .scalar()
            if hierarchy_on
            else None
        )
        if admin.role_id == 1 or real_owner_id == admin.id:
            return None
    if admin is not None and admin.is_sudo:
        if not hierarchy_on:
            return None
    query = db.query(MarzhelpAdminSettings).filter(MarzhelpAdminSettings.admin_id == admin_id)
    if lock:
        query = query.with_for_update()
    return query.first()


def _effective_data_limit(value: Any) -> int | None:
    if value in (None, 0):
        return None
    return int(value)


def _effective_expire(value: Any) -> int | None:
    if value in (None, 0):
        return None
    return int(value)


def subscription_mode_for(
    data_limit: int | None,
    concurrent_user_limit: int | None,
) -> SubscriptionMode:
    finite_traffic = _effective_data_limit(data_limit) is not None
    finite_devices = concurrent_user_limit is not None
    if finite_traffic and finite_devices:
        return SubscriptionMode.limited_traffic_limited_devices
    if finite_traffic:
        return SubscriptionMode.limited_traffic_unlimited_devices
    if finite_devices:
        return SubscriptionMode.unlimited_traffic_limited_devices
    return SubscriptionMode.unlimited_traffic_unlimited_devices


def _validate_subscription_mode(
    settings: MarzhelpAdminSettings,
    data_limit: int | None,
    concurrent_user_limit: int | None,
) -> None:
    mode = subscription_mode_for(data_limit, concurrent_user_limit)
    allowed = set(settings.allowed_subscription_modes)
    # Legacy rows created before mode permissions existed remain unrestricted.
    # The migration and every new admin explicitly receive the safe defaults.
    if not allowed:
        return
    if mode.value not in allowed:
        raise MarzhelpPolicyError(
            "subscription_mode_forbidden",
            f"MarzHelp: subscription mode '{mode.value}' is not allowed for the admin",
        )


def capacity_weight(concurrent_user_limit: int | None) -> int:
    """Map one account to active capacity units.

    Legacy/unlimited accounts predate weighted quotas and keep their former
    one-row cost. New finite values consume their exact positive limit.
    """

    if concurrent_user_limit is None:
        return 1
    value = int(concurrent_user_limit)
    if value < 1:
        raise MarzhelpPolicyError(
            "invalid_user_limit",
            "MarzHelp: concurrent user limit must be a positive integer",
        )
    return value


def capacity_used(db: Session, admin_id: int, excluded_user_id: int | None = None) -> int:
    filters = [User.admin_id == admin_id]
    if excluded_user_id is not None:
        filters.append(User.id != excluded_user_id)
    weight = case(
        (User.concurrent_user_limit.is_(None), 1),
        (User.concurrent_user_limit < 1, 1),
        else_=User.concurrent_user_limit,
    )
    return int(db.query(func.coalesce(func.sum(weight), 0)).filter(*filters).scalar() or 0)


def user_count_used(db: Session, admin_id: int, excluded_user_id: int | None = None) -> int:
    filters = [User.admin_id == admin_id]
    if excluded_user_id is not None:
        filters.append(User.id != excluded_user_id)
    return int(db.query(func.count(User.id)).filter(*filters).scalar() or 0)


def allocated_credit_baseline(db: Session, admin_id: int) -> int:
    """Best available non-refundable allocation total for legacy rows."""

    current = (
        db.query(func.coalesce(func.sum(func.coalesce(User.data_limit, User.used_traffic, 0)), 0))
        .filter(User.admin_id == admin_id)
        .scalar()
        or 0
    )
    deleted = (
        db.query(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        MarzhelpDeletedUser.allocated_traffic,
                        MarzhelpDeletedUser.used_traffic_total,
                        0,
                    )
                ),
                0,
            )
        )
        .filter(MarzhelpDeletedUser.admin_id == admin_id)
        .scalar()
        or 0
    )
    return int(current) + int(deleted)


def quota_summary(db: Session, admin_id: int) -> dict[str, Any]:
    return quota_summaries(db, [admin_id]).get(admin_id, _quota_summary_values(None))


def _quota_summary_values(
    settings: MarzhelpAdminSettings | None,
    *,
    zero_is_finite: bool = False,
    current_users: int = 0,
    current_usage: int = 0,
    reset_usage: int = 0,
    deleted_usage: int = 0,
    current_allocated: int = 0,
    deleted_allocated: int = 0,
) -> dict[str, Any]:
    lifetime_consumed = int(current_usage) + int(reset_usage) + int(deleted_usage)
    lifetime_created = max(
        int(settings.provisioning_volume_used or 0) if settings is not None else 0,
        int(current_allocated) + int(deleted_allocated),
    )
    legacy_mode = (settings.calculate_volume if settings is not None else None) or "used_traffic"
    billing_mode = admin_billing.billing_mode(settings).value if settings is not None else "LEGACY_COMPAT"
    if settings is not None and billing_mode == admin_billing.BillingMode.SEAT_CREDIT.value:
        used = int(settings.capacity_used or 0)
        configured_limit = settings.device_capacity_limit
    elif settings is not None and billing_mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC.value:
        used = int(settings.used_traffic or 0)
        configured_limit = settings.total_traffic
    elif settings is not None and billing_mode == admin_billing.BillingMode.USED_TRAFFIC.value:
        used = int(current_usage) + int(reset_usage) + int(deleted_usage)
        configured_limit = settings.total_traffic
    elif settings is not None and billing_mode == admin_billing.BillingMode.USER_CREDIT.value:
        used = int(current_users)
        configured_limit = settings.max_users
    else:
        used = (
            int(settings.used_traffic or 0)
            if settings is not None and legacy_mode == "created_traffic"
            else int(current_usage) + int(reset_usage) + int(deleted_usage)
        )
        configured_limit = settings.total_traffic if settings is not None else None
    limit = (
        int(configured_limit)
        if configured_limit is not None and (configured_limit > 0 or zero_is_finite)
        else None
    )
    percent = (
        None
        if limit is None
        else 100.0
        if limit == 0
        else round((used * 100) / limit, 2)
    )
    admin_threshold = int(
        settings.admin_traffic_warning_percent or 80
        if settings is not None
        else 80
    )
    sudo_threshold = int(
        settings.sudo_traffic_warning_percent or 80
        if settings is not None
        else 80
    )
    usage_warning_enabled = (
        billing_mode == admin_billing.BillingMode.USED_TRAFFIC.value
        or (
            billing_mode == admin_billing.BillingMode.LEGACY_COMPAT.value
            and legacy_mode == "used_traffic"
        )
    ) and percent is not None
    maximum_users = settings.max_users if settings is not None else None
    return {
        "current_users": current_users,
        "lifetime_consumed_traffic": lifetime_consumed,
        "lifetime_created_traffic": lifetime_created,
        "max_users": maximum_users,
        "remaining_user_slots": (
            max(int(maximum_users) - current_users, 0)
            if maximum_users is not None
            else None
        ),
        "credit_limit": limit,
        "credit_used": used,
        "credit_remaining": max(limit - used, 0) if limit is not None else None,
        "credit_usage_percent": percent,
        "credit_calculation_mode": legacy_mode,
        "billing_mode": billing_mode,
        "operation_allowance_remaining": settings.user_limit if settings is not None else None,
        "admin_warning_percent": admin_threshold,
        "sudo_warning_percent": sudo_threshold,
        "admin_warning_active": bool(usage_warning_enabled and percent >= admin_threshold),
        "sudo_warning_active": bool(usage_warning_enabled and percent >= sudo_threshold),
    }


def quota_summaries(
    db: Session,
    admin_ids: list[int],
    settings_by_admin: dict[int, MarzhelpAdminSettings] | None = None,
) -> dict[int, dict[str, Any]]:
    """Return quota state for many admins with three grouped queries."""

    ids = sorted(set(admin_ids))
    if not ids:
        return {}
    if settings_by_admin is None:
        settings_by_admin = {
            row.admin_id: row
            for row in db.query(MarzhelpAdminSettings)
            .filter(MarzhelpAdminSettings.admin_id.in_(ids))
            .all()
        }
    zero_is_finite = bool(
        db.query(AdminHierarchySettings.enabled)
        .filter(AdminHierarchySettings.id == 1)
        .scalar()
    )

    user_totals = {
        int(admin_id): (int(count or 0), int(used or 0), int(allocated or 0))
        for admin_id, count, used, allocated in db.query(
            User.admin_id,
            func.count(User.id),
            func.coalesce(func.sum(func.coalesce(User.used_traffic, 0)), 0),
            func.coalesce(func.sum(func.coalesce(User.data_limit, 0)), 0),
        )
        .filter(User.admin_id.in_(ids))
        .group_by(User.admin_id)
        .all()
    }
    reset_totals = {
        int(admin_id): int(used or 0)
        for admin_id, used in db.query(
            User.admin_id,
            func.coalesce(func.sum(UserUsageResetLogs.used_traffic_at_reset), 0),
        )
        .join(User, User.id == UserUsageResetLogs.user_id)
        .filter(User.admin_id.in_(ids))
        .group_by(User.admin_id)
        .all()
    }
    deleted_totals = {
        int(admin_id): (int(used or 0), int(allocated or 0))
        for admin_id, used, allocated in db.query(
            MarzhelpDeletedUser.admin_id,
            func.coalesce(func.sum(MarzhelpDeletedUser.used_traffic_total), 0),
            func.coalesce(func.sum(func.coalesce(MarzhelpDeletedUser.allocated_traffic, 0)), 0),
        )
        .filter(MarzhelpDeletedUser.admin_id.in_(ids))
        .group_by(MarzhelpDeletedUser.admin_id)
        .all()
    }
    subtree_current: dict[int, int] = {}
    subtree_reset: dict[int, int] = {}
    subtree_deleted: dict[int, int] = {}
    used_mode_ids = [
        admin_id
        for admin_id in ids
        if settings_by_admin.get(admin_id) is not None
        and (
            admin_billing.billing_mode(settings_by_admin[admin_id])
            == admin_billing.BillingMode.USED_TRAFFIC
            or (
                admin_billing.billing_mode(settings_by_admin[admin_id])
                == admin_billing.BillingMode.LEGACY_COMPAT
                and (settings_by_admin[admin_id].calculate_volume or "used_traffic")
                == "used_traffic"
            )
        )
    ]
    if zero_is_finite and used_mode_ids:
        subtree_current = {
            int(ancestor_id): int(used or 0)
            for ancestor_id, used in db.query(
                AdminHierarchy.ancestor_id,
                func.coalesce(func.sum(func.coalesce(User.used_traffic, 0)), 0),
            )
            .join(User, User.admin_id == AdminHierarchy.descendant_id)
            .filter(AdminHierarchy.ancestor_id.in_(used_mode_ids))
            .group_by(AdminHierarchy.ancestor_id)
            .all()
        }
        subtree_reset = {
            int(ancestor_id): int(used or 0)
            for ancestor_id, used in db.query(
                AdminHierarchy.ancestor_id,
                func.coalesce(func.sum(UserUsageResetLogs.used_traffic_at_reset), 0),
            )
            .join(User, User.admin_id == AdminHierarchy.descendant_id)
            .join(UserUsageResetLogs, UserUsageResetLogs.user_id == User.id)
            .filter(AdminHierarchy.ancestor_id.in_(used_mode_ids))
            .group_by(AdminHierarchy.ancestor_id)
            .all()
        }
        subtree_deleted = {
            int(ancestor_id): int(used or 0)
            for ancestor_id, used in db.query(
                AdminHierarchy.ancestor_id,
                func.coalesce(func.sum(MarzhelpDeletedUser.used_traffic_total), 0),
            )
            .join(
                MarzhelpDeletedUser,
                MarzhelpDeletedUser.admin_id == AdminHierarchy.descendant_id,
            )
            .filter(AdminHierarchy.ancestor_id.in_(used_mode_ids))
            .group_by(AdminHierarchy.ancestor_id)
            .all()
        }
    return {
        admin_id: _quota_summary_values(
            settings_by_admin.get(admin_id),
            zero_is_finite=zero_is_finite,
            current_users=user_totals.get(admin_id, (0, 0, 0))[0],
            current_usage=subtree_current.get(
                admin_id, user_totals.get(admin_id, (0, 0, 0))[1]
            ),
            reset_usage=subtree_reset.get(admin_id, reset_totals.get(admin_id, 0)),
            deleted_usage=subtree_deleted.get(admin_id, deleted_totals.get(admin_id, (0, 0))[0]),
            current_allocated=user_totals.get(admin_id, (0, 0, 0))[2],
            deleted_allocated=deleted_totals.get(admin_id, (0, 0))[1],
        )
        for admin_id in ids
    }


def allowed_inbound_tags(db: Session, admin: Admin) -> set[str] | None:
    """Return None for unrestricted access, otherwise exact allowed tags."""

    if admin.is_sudo:
        return None
    settings = _settings(db, admin.id)
    if settings is None or settings.all_inbounds:
        return None
    return set(settings.allowed_inbounds)


def allowed_user_limits(db: Session, admin: Admin) -> set[int] | None:
    if admin.is_sudo:
        return None
    settings = _settings(db, admin.id)
    if settings is None or settings.all_user_limits:
        return None
    return set(settings.allowed_user_limits)


def user_inbound_tags(dbuser: User) -> set[str]:
    return {tag for tags in dbuser.inbounds.values() for tag in tags}


def can_access_user(db: Session, admin: Admin, dbuser: User) -> bool:
    if admin.is_sudo:
        return True
    if dbuser.admin_id != admin.id:
        return False
    allowed = allowed_inbound_tags(db, admin)
    return allowed is None or user_inbound_tags(dbuser).issubset(allowed)


def _requested_inbound_tags(user: Any) -> set[str] | None:
    inbounds = getattr(user, "inbounds", None)
    if inbounds is None:
        return None
    return {tag for tags in inbounds.values() for tag in tags}


def _validate_inbounds(settings: MarzhelpAdminSettings, inbound_tags: set[str] | None) -> None:
    if settings.all_inbounds or inbound_tags is None:
        return
    unauthorized = sorted(inbound_tags - set(settings.allowed_inbounds))
    if unauthorized:
        raise MarzhelpPolicyError(
            "inbound_forbidden",
            "MarzHelp: unauthorized inbound(s): " + ", ".join(unauthorized),
        )


def _validate_concurrent_user_limit(
    settings: MarzhelpAdminSettings,
    concurrent_user_limit: int | None,
) -> None:
    # Unlimited devices are authorized by the subscription-mode permission.
    # The exact-limit allow-list applies only when a finite value is requested.
    if concurrent_user_limit is None:
        return
    if settings.all_user_limits:
        capacity_weight(concurrent_user_limit)
        return
    if int(concurrent_user_limit) not in settings.allowed_user_limits:
        raise MarzhelpPolicyError(
            "user_limit_forbidden",
            "MarzHelp: this concurrent user limit is not allowed for the admin",
        )


def _adjust_capacity(
    db: Session,
    settings: MarzhelpAdminSettings,
    delta: int,
) -> None:
    if delta == 0:
        return

    # Optimistic compare-and-swap complements SELECT FOR UPDATE and also keeps
    # SQLite tests safe, where row-level FOR UPDATE is unavailable.
    for _ in range(3):
        db.expire(settings, ["capacity_used"])
        stored = int(settings.capacity_used or 0)
        actual = capacity_used(db, settings.admin_id)
        baseline = max(stored, actual)
        target = max(baseline + delta, 0)
        if (
            settings.device_capacity_limit is not None
            and target > int(settings.device_capacity_limit)
        ):
            remaining = max(int(settings.device_capacity_limit) - baseline, 0)
            raise MarzhelpPolicyError(
                "weighted_capacity_exceeded",
                (
                    "MarzHelp: insufficient user capacity; "
                    f"requested {max(delta, 0)}, remaining {remaining}"
                ),
            )
        result = db.execute(
            update(MarzhelpAdminSettings)
            .where(
                MarzhelpAdminSettings.admin_id == settings.admin_id,
                MarzhelpAdminSettings.capacity_used == stored,
                or_(
                    MarzhelpAdminSettings.device_capacity_limit.is_(None),
                    target <= MarzhelpAdminSettings.device_capacity_limit,
                ),
            )
            .values(capacity_used=target, updated_at=func.now())
        )
        if result.rowcount == 1:
            settings.capacity_used = target
            return
        db.expire(settings, ["capacity_used", "device_capacity_limit"])

    raise MarzhelpPolicyError(
        "capacity_conflict",
        "MarzHelp: user capacity changed concurrently; retry the request",
    )


def _adjust_user_count(db: Session, settings: MarzhelpAdminSettings, delta: int) -> None:
    if delta == 0:
        return
    for _ in range(3):
        db.expire(settings, ["user_count_used"])
        stored = int(settings.user_count_used or 0)
        actual = user_count_used(db, settings.admin_id)
        baseline = max(stored, actual)
        target = max(baseline + delta, 0)
        if settings.max_users is not None and target > int(settings.max_users):
            logger.warning(
                "quota_rejected admin_id=%s dimension=max_users requested_delta=%s used=%s limit=%s",
                settings.admin_id,
                delta,
                baseline,
                settings.max_users,
            )
            raise MarzhelpPolicyError(
                "max_users_exceeded",
                "MarzHelp: maximum user-account quota is exhausted",
                audit_admin_id=settings.admin_id,
                audit_operation_type="max_users",
                audit_details={
                    "requested_delta": delta,
                    "used": baseline,
                    "limit": int(settings.max_users),
                },
            )
        result = db.execute(
            update(MarzhelpAdminSettings)
            .where(
                MarzhelpAdminSettings.admin_id == settings.admin_id,
                MarzhelpAdminSettings.user_count_used == stored,
                or_(
                    MarzhelpAdminSettings.max_users.is_(None),
                    target <= MarzhelpAdminSettings.max_users,
                ),
            )
            .values(user_count_used=target, updated_at=func.now())
        )
        if result.rowcount == 1:
            settings.user_count_used = target
            logger.info(
                "quota_consumed admin_id=%s dimension=max_users delta=%s used=%s",
                settings.admin_id,
                delta,
                target,
            )
            return
        db.expire(settings, ["user_count_used", "max_users"])
    raise MarzhelpPolicyError(
        "user_count_conflict",
        "MarzHelp: user quota changed concurrently; retry the request",
    )


def _validate_account(settings: MarzhelpAdminSettings) -> None:
    if int(getattr(settings, "account_status_id", 1) or 1) != 1:
        raise MarzhelpPolicyError(
            "admin_account_read_only",
            "MarzHelp: administrative account is suspended or disabled",
        )
    if settings.expiry_date is not None and settings.expiry_date < date.today():
        raise MarzhelpPolicyError("admin_expired", "MarzHelp: admin account is expired")


def _consume_renewal(db: Session, settings: MarzhelpAdminSettings) -> None:
    if not bool(getattr(settings, "renewal_enabled", True)):
        raise MarzhelpPolicyError(
            "renewal_disabled",
            "MarzHelp: renewal is disabled for this admin",
        )
    remaining = getattr(settings, "renewal_remaining", None)
    if remaining is None:
        return
    result = db.execute(
        update(MarzhelpAdminSettings)
        .where(
            MarzhelpAdminSettings.admin_id == settings.admin_id,
            MarzhelpAdminSettings.renewal_remaining > 0,
        )
        .values(
            renewal_remaining=MarzhelpAdminSettings.renewal_remaining - 1,
            renewals_used=MarzhelpAdminSettings.renewals_used + 1,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        raise MarzhelpPolicyError(
            "renewal_quota_exhausted",
            "MarzHelp: renewal quota is exhausted",
        )
    db.expire(settings, ["renewal_remaining", "renewals_used"])


def _validate_data_limit(settings: MarzhelpAdminSettings, data_limit: int | None) -> None:
    if settings.prevent_unlimited_traffic and data_limit is None:
        raise MarzhelpPolicyError(
            "unlimited_traffic_forbidden",
            "MarzHelp: unlimited traffic is not allowed for this admin",
        )


def _validate_expiration(
    settings: MarzhelpAdminSettings,
    expire: int | None,
    on_hold_duration: int | None = None,
    now: datetime | None = None,
) -> None:
    maximum_days = settings.max_user_duration_days
    if maximum_days is None or maximum_days <= 0:
        return

    maximum_seconds = int(maximum_days) * 86400
    if on_hold_duration not in (None, 0):
        if int(on_hold_duration) > maximum_seconds:
            raise MarzhelpPolicyError(
                "duration_exceeded",
                f"MarzHelp: account duration cannot exceed {maximum_days} days",
            )
        return

    if expire is None:
        raise MarzhelpPolicyError(
            "unlimited_expiration_forbidden",
            "MarzHelp: no-expiry accounts are not allowed for this admin",
        )

    now_timestamp = int((now or datetime.now(timezone.utc)).timestamp())
    if int(expire) - now_timestamp > maximum_seconds:
        raise MarzhelpPolicyError(
            "duration_exceeded",
            f"MarzHelp: account duration cannot exceed {maximum_days} days",
        )


def used_traffic_spend(db: Session, admin_id: int) -> int:
    hierarchy_on = bool(
        db.query(AdminHierarchySettings.enabled)
        .filter(AdminHierarchySettings.id == 1)
        .scalar()
    )
    scoped_admin_ids = (
        db.query(AdminHierarchy.descendant_id).filter(AdminHierarchy.ancestor_id == admin_id)
        if hierarchy_on
        else [admin_id]
    )
    current_usage = (
        db.query(func.coalesce(func.sum(User.used_traffic), 0))
        .filter(User.admin_id.in_(scoped_admin_ids))
        .scalar()
        or 0
    )
    reset_usage = (
        db.query(func.coalesce(func.sum(UserUsageResetLogs.used_traffic_at_reset), 0))
        .join(User, User.id == UserUsageResetLogs.user_id)
        .filter(User.admin_id.in_(scoped_admin_ids))
        .scalar()
        or 0
    )
    deleted = (
        db.query(func.coalesce(func.sum(MarzhelpDeletedUser.used_traffic_total), 0))
        .filter(MarzhelpDeletedUser.admin_id.in_(scoped_admin_ids))
        .scalar()
        or 0
    )
    return int(current_usage) + int(reset_usage) + int(deleted)


def _current_spend(db: Session, settings: MarzhelpAdminSettings) -> int:
    mode = admin_billing.billing_mode(settings)
    if mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC:
        return int(settings.used_traffic or 0)
    if mode == admin_billing.BillingMode.USED_TRAFFIC:
        return used_traffic_spend(db, settings.admin_id)
    if mode == admin_billing.BillingMode.SEAT_CREDIT:
        return 0
    if (settings.calculate_volume or "used_traffic") == "created_traffic":
        return int(settings.used_traffic or 0)
    return used_traffic_spend(db, settings.admin_id)


def _validate_traffic_credit(
    db: Session,
    settings: MarzhelpAdminSettings,
    *,
    allocated_charge: int = 0,
    unlimited_requested: bool = False,
) -> None:
    billing_mode = admin_billing.billing_mode(settings)
    legacy_mode = settings.calculate_volume or "used_traffic"
    if billing_mode == admin_billing.BillingMode.SEAT_CREDIT:
        return
    if billing_mode == admin_billing.BillingMode.USER_CREDIT:
        return
    hierarchy_on = bool(
        db.query(AdminHierarchySettings.enabled)
        .filter(AdminHierarchySettings.id == 1)
        .scalar()
    )
    limit = (
        int(settings.total_traffic)
        if settings.total_traffic is not None
        and (settings.total_traffic > 0 or hierarchy_on)
        else None
    )
    if limit is not None:
        limit = max(limit - int(getattr(settings, "delegated_traffic", 0) or 0), 0)
    allocated_mode = (
        billing_mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC
        or (
            billing_mode == admin_billing.BillingMode.LEGACY_COMPAT
            and legacy_mode == "created_traffic"
        )
    )
    if allocated_mode:
        if unlimited_requested and limit is not None:
            raise MarzhelpPolicyError(
                "unlimited_traffic_forbidden",
                "MarzHelp: unlimited traffic is not allowed with finite admin credit",
            )
        spent = int(settings.used_traffic or 0)
        target = spent + max(int(allocated_charge), 0)
        if limit is not None and target > limit:
            raise MarzhelpPolicyError(
                "traffic_exhausted",
                "MarzHelp: admin traffic credit is exhausted",
                audit_admin_id=settings.admin_id,
                audit_operation_type="traffic_credit",
                audit_details={"requested_delta": allocated_charge, "used": spent, "limit": limit},
            )
        settings.used_traffic = target
        return

    if limit is not None:
        spent = _current_spend(db, settings)
        if spent >= limit:
            raise MarzhelpPolicyError(
                "traffic_exhausted",
                "MarzHelp: admin traffic credit is exhausted",
                audit_admin_id=settings.admin_id,
                audit_operation_type="traffic_credit",
                audit_details={"requested_delta": 0, "used": spent, "limit": limit},
            )


def _consume_allowance(db: Session, settings: MarzhelpAdminSettings) -> None:
    if settings.user_limit is None:
        return
    result = db.execute(
        update(MarzhelpAdminSettings)
        .where(
            MarzhelpAdminSettings.admin_id == settings.admin_id,
            MarzhelpAdminSettings.user_limit > 0,
        )
        .values(user_limit=MarzhelpAdminSettings.user_limit - 1, updated_at=func.now())
    )
    if result.rowcount != 1:
        raise MarzhelpPolicyError(
            "operation_allowance_exhausted",
            "MarzHelp: admin create/renew/time-change allowance is exhausted",
        )


def _record(
    db: Session,
    operation_key: str,
    operation_type: str,
    admin_id: int,
    user_id: int | None,
    username: str | None,
    traffic_delta: int = 0,
    allowance_delta: int = 0,
    volume_delta: int = 0,
    renewal_delta: int = 0,
    result: str = "consumed",
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        MarzhelpAccountingTransaction(
            operation_key=operation_key,
            operation_type=operation_type,
            admin_id=admin_id,
            user_id=user_id,
            username=username,
            traffic_delta=traffic_delta,
            allowance_delta=allowance_delta,
            volume_delta=volume_delta,
            renewal_delta=renewal_delta,
            result=result,
            details=details,
        )
    )


def record_lifetime_created(
    settings: MarzhelpAdminSettings | None,
    allocated_volume: int | None,
) -> None:
    """Increase the non-refundable lifetime provisioned-volume counter."""

    if settings is None:
        return
    amount = max(int(allocated_volume or 0), 0)
    settings.provisioning_volume_used = int(settings.provisioning_volume_used or 0) + amount


def record_quota_rejection(error: MarzhelpPolicyError, db: Session | None = None) -> None:
    """Persist API quota rejections after the failed request transaction rolls back."""

    if error.audit_admin_id is None or error.audit_operation_type is None:
        return
    owns_session = db is None
    if owns_session:
        from app.db import SessionLocal

        db = SessionLocal()
    assert db is not None
    try:
        _record(
            db,
            f"rejected:{uuid4().hex}",
            error.audit_operation_type,
            error.audit_admin_id,
            None,
            None,
            result="rejected",
            details={"code": error.code, **(error.audit_details or {})},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "quota_rejection_audit_failed admin_id=%s code=%s",
            error.audit_admin_id,
            error.code,
        )
    finally:
        if owns_session:
            db.close()


def validate_create(db: Session, admin_id: int | None, user: Any) -> MarzhelpAdminSettings | None:
    settings = _settings(db, admin_id, lock=True)
    if settings is None:
        return None
    _validate_account(settings)
    if settings.prevent_user_creation:
        raise MarzhelpPolicyError(
            "creation_forbidden", "MarzHelp: user creation is disabled for this admin"
        )

    concurrent_user_limit = getattr(user, "concurrent_user_limit", None)
    _validate_inbounds(settings, _requested_inbound_tags(user))
    _validate_concurrent_user_limit(settings, concurrent_user_limit)
    _validate_billing_plan(settings, concurrent_user_limit)
    _adjust_user_count(db, settings, 1)
    strategy = _billing_strategy(settings)
    _adjust_capacity(db, settings, strategy.create_capacity_charge(concurrent_user_limit))

    data_limit = _effective_data_limit(user.data_limit)
    expire = _effective_expire(user.expire)
    _validate_data_limit(settings, data_limit)
    _validate_subscription_mode(settings, data_limit, concurrent_user_limit)
    _validate_expiration(settings, expire, getattr(user, "on_hold_expire_duration", None))
    _validate_traffic_credit(
        db,
        settings,
        allocated_charge=strategy.allocated_charge(None, data_limit, renewal=False),
        unlimited_requested=data_limit is None,
    )
    record_lifetime_created(settings, data_limit)

    next_plan = getattr(user, "next_plan", None)
    if next_plan is not None:
        next_data_limit = _effective_data_limit(next_plan.data_limit)
        _validate_data_limit(settings, next_data_limit)
        _validate_subscription_mode(settings, next_data_limit, concurrent_user_limit)
        _validate_expiration(settings, _effective_expire(next_plan.expire))

    _consume_allowance(db, settings)
    return settings


def record_create(db: Session, dbuser: User, quota_enforced: bool) -> None:
    if not quota_enforced or dbuser.admin_id is None:
        return
    settings = _settings(db, dbuser.admin_id)
    _record(
        db,
        f"create:{dbuser.id}",
        "create",
        dbuser.admin_id,
        dbuser.id,
        dbuser.username,
        allowance_delta=-1 if settings is not None and settings.user_limit is not None else 0,
        volume_delta=int(dbuser.data_limit or 0),
        details={"data_limit": dbuser.data_limit, "expire": dbuser.expire},
    )


def consume_seat_renewal(
    db: Session,
    settings: MarzhelpAdminSettings,
    *,
    user: User,
    seat_cost: int,
    idempotency_key: str,
    plan_id: int,
    version_id: int,
) -> None:
    """Consume one Plan renewal's Seat Credit inside the caller transaction."""

    _adjust_capacity(db, settings, seat_cost)
    operation_digest = sha256(idempotency_key.encode("utf-8")).hexdigest()
    _record(
        db,
        f"seat-renew:{settings.admin_id}:{operation_digest}",
        "plan_renew_seat",
        settings.admin_id,
        user.id,
        user.username,
        renewal_delta=1,
        details={
            "seat_credit_delta": -seat_cost,
            "seat_cost": seat_cost,
            "plan_id": plan_id,
            "plan_version_id": version_id,
            "idempotency_key_sha256": operation_digest,
        },
    )


def validate_update(
    db: Session,
    dbuser: User,
    modify: Any,
    operation: UserUpdateOperation = UserUpdateOperation.edit,
    actor: Admin | object | None = None,
) -> tuple[bool, bool]:
    if getattr(getattr(modify, "status", None), "value", getattr(modify, "status", None)) == "active":
        validate_no_active_penalty(dbuser)
    settings = _settings(db, dbuser.admin_id, lock=True)
    if settings is None:
        return False, False

    operation = UserUpdateOperation(operation)
    validate_plan_only_direct_edit(db, dbuser, modify, actor=actor, operation=operation)
    renewal = operation == UserUpdateOperation.renew
    fields_set = getattr(modify, "model_fields_set", set())
    expire_requested = "expire" in fields_set if fields_set else modify.expire is not None
    expiration_changed = expire_requested and (
        _effective_expire(modify.expire) != _effective_expire(dbuser.expire)
    )
    allowance_operation = renewal or expiration_changed
    concurrent_limit_changed = (
        "concurrent_user_limit" in fields_set
        and getattr(modify, "concurrent_user_limit", None) != dbuser.concurrent_user_limit
    )
    inbound_changed = bool(getattr(modify, "inbounds", None)) or bool(getattr(modify, "proxies", None))
    plan_change = (
        renewal
        or modify.data_limit is not None
        or modify.expire is not None
        or modify.next_plan is not None
        or concurrent_limit_changed
        or inbound_changed
    )
    if not plan_change:
        return False, False

    _validate_account(settings)
    concurrent_user_limit = (
        getattr(modify, "concurrent_user_limit", None)
        if concurrent_limit_changed
        else dbuser.concurrent_user_limit
    )
    final_inbounds = {key: list(value) for key, value in dbuser.inbounds.items()}
    modified_proxies = getattr(modify, "proxies", None) or {}
    modified_inbounds = getattr(modify, "inbounds", None) or {}
    if modified_proxies:
        final_inbounds = {
            proxy_type: modified_inbounds.get(
                proxy_type,
                final_inbounds.get(
                    proxy_type,
                    [item["tag"] for item in xray.config.inbounds_by_protocol.get(proxy_type, [])],
                ),
            )
            for proxy_type in modified_proxies
        }
    else:
        final_inbounds.update(modified_inbounds)
    _validate_inbounds(
        settings,
        {tag for tags in final_inbounds.values() for tag in tags},
    )
    _validate_concurrent_user_limit(settings, concurrent_user_limit)
    _validate_billing_plan(settings, concurrent_user_limit)
    strategy = _billing_strategy(settings)
    if renewal and strategy.mode == admin_billing.BillingMode.SEAT_CREDIT:
        raise MarzhelpPolicyError(
            "seat_plan_renewal_required",
            "SEAT_CREDIT renewals must use an explicit Plan and idempotency key",
        )
    _adjust_capacity(
        db,
        settings,
        strategy.update_capacity_charge(
            dbuser.concurrent_user_limit,
            concurrent_user_limit,
        ),
    )
    old_data_limit = _effective_data_limit(dbuser.data_limit)
    data_limit = (
        _effective_data_limit(modify.data_limit)
        if modify.data_limit is not None
        else dbuser.data_limit
    )
    if strategy.mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC:
        if data_limit is None:
            raise MarzhelpPolicyError(
                "unlimited_form_traffic_forbidden", "Allocated Form cannot set unlimited traffic"
            )
        if old_data_limit is not None and int(data_limit) < int(old_data_limit):
            raise MarzhelpPolicyError(
                "allocated_traffic_reduction_forbidden", "Admin cannot reduce allocated user traffic"
            )
    expire = _effective_expire(modify.expire) if modify.expire is not None else dbuser.expire
    on_hold_duration = (
        modify.on_hold_expire_duration
        if modify.on_hold_expire_duration is not None
        else dbuser.on_hold_expire_duration
    )
    _validate_data_limit(settings, data_limit)
    _validate_subscription_mode(settings, data_limit, concurrent_user_limit)
    _validate_expiration(settings, expire, on_hold_duration)
    volume_delta = int(data_limit or 0) - int(old_data_limit or 0)
    _validate_traffic_credit(
        db,
        settings,
        allocated_charge=strategy.allocated_charge(
            old_data_limit,
            data_limit,
            renewal=renewal,
        ),
        unlimited_requested=data_limit is None,
    )
    record_lifetime_created(settings, data_limit if renewal else max(volume_delta, 0))
    dbuser._marzhelp_volume_delta = volume_delta
    dbuser._marzhelp_is_renewal = renewal
    dbuser._marzhelp_allowance_delta = 0

    if modify.next_plan is not None:
        next_data_limit = _effective_data_limit(modify.next_plan.data_limit)
        _validate_data_limit(settings, next_data_limit)
        _validate_subscription_mode(settings, next_data_limit, concurrent_user_limit)
        _validate_expiration(settings, _effective_expire(modify.next_plan.expire))

    if allowance_operation:
        if renewal:
            _consume_renewal(db, settings)
        limited_allowance = settings.user_limit is not None
        _consume_allowance(db, settings)
        dbuser._marzhelp_allowance_delta = -1 if limited_allowance else 0
    return renewal, True


def record_renewal(db: Session, dbuser: User, quota_enforced: bool) -> None:
    if not quota_enforced or dbuser.admin_id is None:
        return
    # Absolute plan updates are naturally idempotent: retrying an already-applied
    # value is not classified as another renewal.
    renewal = bool(getattr(dbuser, "_marzhelp_is_renewal", True))
    allowance_delta = int(getattr(dbuser, "_marzhelp_allowance_delta", 0))
    operation = "renew" if renewal else ("plan_change" if allowance_delta else "volume_adjustment")
    sequence = (
        db.query(func.count(MarzhelpAccountingTransaction.id))
        .filter(
            MarzhelpAccountingTransaction.admin_id == dbuser.admin_id,
            MarzhelpAccountingTransaction.user_id == dbuser.id,
            MarzhelpAccountingTransaction.operation_type == operation,
        )
        .scalar()
        or 0
    ) + 1
    key = f"{operation}:{dbuser.id}:{sequence}:{dbuser.data_limit}:{dbuser.expire}"
    _record(
        db,
        key,
        operation,
        dbuser.admin_id,
        dbuser.id,
        dbuser.username,
        allowance_delta=allowance_delta,
        volume_delta=int(getattr(dbuser, "_marzhelp_volume_delta", 0)),
        renewal_delta=1 if renewal else 0,
        details={"data_limit": dbuser.data_limit, "expire": dbuser.expire},
    )


def resulting_next_plan_data_limit(dbuser: User) -> int | None:
    remaining = 0
    if not dbuser.next_plan.add_remaining_traffic:
        remaining = max(
            int(dbuser.data_limit or 0) - int(dbuser.used_traffic or 0),
            0,
        )
    result = int(dbuser.next_plan.data_limit or 0) + remaining
    return result or None


def validate_next_plan_activation(db: Session, dbuser: User) -> bool:
    if dbuser.next_plan is None:
        return False
    validate_no_active_penalty(dbuser)
    settings = _settings(db, dbuser.admin_id, lock=True)
    if settings is None:
        return False
    _validate_account(settings)
    if admin_billing.billing_mode(settings) == admin_billing.BillingMode.SEAT_CREDIT:
        raise MarzhelpPolicyError(
            "seat_plan_renewal_required",
            "SEAT_CREDIT renewals must use an explicit Plan and idempotency key",
        )
    data_limit = resulting_next_plan_data_limit(dbuser)
    expire = _effective_expire(dbuser.next_plan.expire)
    _validate_data_limit(settings, data_limit)
    _validate_subscription_mode(settings, data_limit, dbuser.concurrent_user_limit)
    _validate_billing_plan(settings, dbuser.concurrent_user_limit)
    _validate_expiration(settings, expire)
    next_allocation = _effective_data_limit(dbuser.next_plan.data_limit)
    _validate_traffic_credit(
        db,
        settings,
        allocated_charge=_billing_strategy(settings).allocated_charge(
            _effective_data_limit(dbuser.data_limit),
            next_allocation,
            renewal=True,
        ),
        unlimited_requested=next_allocation is None,
    )
    record_lifetime_created(settings, next_allocation)
    volume_delta = int(data_limit or 0) - int(_effective_data_limit(dbuser.data_limit) or 0)
    _consume_renewal(db, settings)
    limited_allowance = settings.user_limit is not None
    _consume_allowance(db, settings)
    dbuser._marzhelp_volume_delta = volume_delta
    dbuser._marzhelp_is_renewal = True
    dbuser._marzhelp_allowance_delta = -1 if limited_allowance else 0
    return True


def validate_reset(db: Session, dbuser: User) -> None:
    settings = _settings(db, dbuser.admin_id, lock=True)
    if settings is not None and settings.prevent_user_reset:
        raise MarzhelpPolicyError("reset_forbidden", "MarzHelp: resetting user traffic is disabled")


def validate_revoke(db: Session, dbuser: User) -> None:
    settings = _settings(db, dbuser.admin_id, lock=True)
    if settings is not None and settings.prevent_revoke_subscription:
        raise MarzhelpPolicyError("revoke_forbidden", "MarzHelp: revoking subscriptions is disabled")


def validate_activation(db: Session, dbuser: User) -> None:
    """Revalidate the effective plan before any alternate activation path."""

    validate_no_active_penalty(dbuser)
    settings = _settings(db, dbuser.admin_id, lock=True)
    if settings is None:
        return
    _validate_account(settings)
    _validate_data_limit(settings, dbuser.data_limit)
    _validate_subscription_mode(
        settings,
        dbuser.data_limit,
        dbuser.concurrent_user_limit,
    )
    _validate_expiration(
        settings,
        dbuser.expire,
        dbuser.on_hold_expire_duration,
    )
    _validate_traffic_credit(
        db,
        settings,
        unlimited_requested=_effective_data_limit(dbuser.data_limit) is None,
    )


def validate_no_active_penalty(dbuser: User) -> None:
    state = dbuser.device_limit_state
    if state is not None and state.penalty_status in {
        PenaltyStatus.temporarily_disabled.value,
        PenaltyStatus.permanently_disabled.value,
    }:
        raise MarzhelpPolicyError(
            "device_limit_penalty_active",
            "Active Device Limit penalty must be released by the Owner",
        )


def validate_start_expiration(db: Session, dbuser: User, expire: int) -> None:
    settings = _settings(db, dbuser.admin_id, lock=True)
    if settings is None:
        return
    _validate_account(settings)
    _validate_expiration(settings, expire)


def validate_transfer(db: Session, dbuser: User, new_admin_id: int) -> None:
    owner_changes = dbuser.admin_id != new_admin_id
    settings = _settings(db, new_admin_id, lock=True)
    if settings is not None:
        _validate_account(settings)
        _validate_inbounds(settings, user_inbound_tags(dbuser))
        _validate_concurrent_user_limit(settings, dbuser.concurrent_user_limit)
        _validate_billing_plan(settings, dbuser.concurrent_user_limit)
        _validate_data_limit(settings, dbuser.data_limit)
        _validate_subscription_mode(
            settings,
            dbuser.data_limit,
            dbuser.concurrent_user_limit,
        )
        _validate_expiration(settings, dbuser.expire, dbuser.on_hold_expire_duration)
        data_limit = _effective_data_limit(dbuser.data_limit)
        _validate_traffic_credit(
            db,
            settings,
            allocated_charge=int(data_limit or 0) if owner_changes else 0,
            unlimited_requested=data_limit is None,
        )
        if owner_changes:
            _adjust_user_count(db, settings, 1)
            _adjust_capacity(
                db,
                settings,
                _billing_strategy(settings).create_capacity_charge(
                    dbuser.concurrent_user_limit
                ),
            )
    previous_settings = _settings(db, dbuser.admin_id, lock=True)
    if owner_changes and previous_settings is not None:
        _adjust_user_count(db, previous_settings, -1)
        _adjust_capacity(
            db,
            previous_settings,
            _billing_strategy(previous_settings).delete_capacity_charge(
                dbuser.concurrent_user_limit
            ),
        )


def capture_delete(db: Session, dbuser: User) -> int:
    if dbuser.admin_id is None:
        return 0
    settings = _settings(db, dbuser.admin_id, lock=True)
    if settings is not None and settings.prevent_user_deletion:
        raise MarzhelpPolicyError("deletion_forbidden", "MarzHelp: user deletion is disabled")
    existing = (
        db.query(MarzhelpDeletedUser)
        .filter(MarzhelpDeletedUser.user_id == dbuser.id)
        .first()
    )
    if existing is not None:
        return 0

    if settings is not None:
        _adjust_user_count(db, settings, -1)
        _adjust_capacity(
            db,
            settings,
            _billing_strategy(settings).delete_capacity_charge(
                dbuser.concurrent_user_limit
            ),
        )

    used = max(int(dbuser.lifetime_used_traffic or 0), 0)
    refund = 0
    ledger = MarzhelpDeletedUser(
        user_id=dbuser.id,
        admin_id=dbuser.admin_id,
        username=dbuser.username,
        used_traffic_total=used,
        allocated_traffic=dbuser.data_limit,
        refunded_traffic=refund,
    )
    db.add(ledger)
    _record(
        db,
        f"delete:{dbuser.id}",
        "delete",
        dbuser.admin_id,
        dbuser.id,
        dbuser.username,
        traffic_delta=0,
        volume_delta=0,
        details={
            "allocated_traffic": dbuser.data_limit,
            "actual_used_traffic": used,
            "refundable_traffic": 0,
            "credit_retained": (
                int(dbuser.data_limit or 0)
                if settings is not None
                and (
                    admin_billing.billing_mode(settings)
                    == admin_billing.BillingMode.ALLOCATED_TRAFFIC
                    or (
                        admin_billing.billing_mode(settings)
                        == admin_billing.BillingMode.LEGACY_COMPAT
                        and settings.calculate_volume == "created_traffic"
                    )
                )
                else used
            ),
        },
    )
    return refund
