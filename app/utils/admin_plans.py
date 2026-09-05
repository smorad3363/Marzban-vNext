"""Versioned, scoped administrator plans and immutable user assignments."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import xray
from app.db import crud
from app.db.models import (
    Admin,
    AdminHierarchy,
    AdminPlanCategory,
    AdminPlanCategoryAccess,
    AdminUserPlan,
    AdminUserPlanAccess,
    AdminUserPlanHost,
    AdminUserPlanInbound,
    AdminUserPlanPrice,
    AdminUserPlanVersion,
    MarzhelpAdminSettings,
    Proxy,
    ProxyHost,
    User,
    UserPlanAssignment,
    UserUsageResetLogs,
)
from app.models.admin_hierarchy import (
    PlanCategoryCreate,
    PlanCategoryResponse,
    PlanCategoryUpdate,
    PlanCreate,
    PlanResponse,
    PlanUpdate,
    PlanVersionInput,
    PlanVersionResponse,
)
from app.models.proxy import ProxySettings, ProxyTypes
from app.models.user import UserCreate, UserDataLimitResetStrategy, UserResponse, UserStatus, UserStatusCreate
from app.device_limit.slots import sync_device_slots
from app.utils import admin_billing, admin_hierarchy, marzhelp_policy, money_billing


def _can_manage_plans(db: Session, actor: Admin) -> bool:
    return admin_hierarchy.is_owner(db, actor)


def effective_categories_query(db: Session, actor: Admin):
    query = db.query(AdminPlanCategory).filter(AdminPlanCategory.archived_at.is_(None))
    if admin_hierarchy.is_owner(db, actor):
        return query
    assigned = exists().where(
        and_(
            AdminPlanCategoryAccess.category_id == AdminPlanCategory.id,
            AdminPlanCategoryAccess.admin_id == actor.id,
        )
    )
    return query.filter(or_(AdminPlanCategory.owner_admin_id == actor.id, assigned))


def category_response(
    db: Session,
    category: AdminPlanCategory,
    plan_count: int | None = None,
) -> PlanCategoryResponse:
    return PlanCategoryResponse(
        id=category.id,
        owner_admin_id=category.owner_admin_id,
        name=category.name,
        description=category.description,
        archived_at=category.archived_at,
        plan_count=plan_count if plan_count is not None else (
            db.query(func.count(AdminUserPlan.id))
            .filter(
                AdminUserPlan.category_id == category.id,
                AdminUserPlan.archived_at.is_(None),
            )
            .scalar()
            or 0
        ),
    )


def category_responses(
    db: Session,
    categories: list[AdminPlanCategory],
) -> list[PlanCategoryResponse]:
    category_ids = [category.id for category in categories]
    counts = (
        dict(
            db.query(AdminUserPlan.category_id, func.count(AdminUserPlan.id))
            .filter(
                AdminUserPlan.category_id.in_(category_ids),
                AdminUserPlan.archived_at.is_(None),
            )
            .group_by(AdminUserPlan.category_id)
            .all()
        )
        if category_ids
        else {}
    )
    return [
        category_response(db, category, int(counts.get(category.id, 0)))
        for category in categories
    ]


def create_category(
    db: Session,
    actor: Admin,
    values: PlanCategoryCreate,
) -> AdminPlanCategory:
    if not _can_manage_plans(db, actor):
        raise admin_hierarchy.HierarchyError("plan_management_forbidden", "Plan management is not enabled")
    category = AdminPlanCategory(
        owner_admin_id=actor.id,
        name=values.name.strip(),
        description=values.description,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def update_category(
    db: Session,
    actor: Admin,
    category: AdminPlanCategory,
    values: PlanCategoryUpdate,
) -> AdminPlanCategory:
    if not admin_hierarchy.is_owner(db, actor) and category.owner_admin_id != actor.id:
        raise admin_hierarchy.HierarchyError("category_update_forbidden", "Only category owner can update it")
    if not _can_manage_plans(db, actor):
        raise admin_hierarchy.HierarchyError("plan_management_forbidden", "Plan management is not enabled")
    category.name = values.name.strip()
    category.description = values.description
    db.commit()
    db.refresh(category)
    return category


def archive_category(db: Session, actor: Admin, category: AdminPlanCategory) -> None:
    if not admin_hierarchy.is_owner(db, actor) and category.owner_admin_id != actor.id:
        raise admin_hierarchy.HierarchyError("category_archive_forbidden", "Only category owner can archive it")
    active_plans = db.query(AdminUserPlan.id).filter(
        AdminUserPlan.category_id == category.id,
        AdminUserPlan.archived_at.is_(None),
    ).first()
    if active_plans:
        raise admin_hierarchy.HierarchyError(
            "category_in_use",
            "Archive or move active plans before archiving this category",
        )
    category.archived_at = admin_hierarchy.utc_now_naive()
    db.commit()


def admin_category_ids(db: Session, admin_id: int) -> list[int]:
    return [
        row[0]
        for row in db.query(AdminPlanCategoryAccess.category_id)
        .filter(AdminPlanCategoryAccess.admin_id == admin_id)
        .order_by(AdminPlanCategoryAccess.category_id)
        .all()
    ]


def admin_category_ids_map(db: Session, admin_ids: list[int]) -> dict[int, list[int]]:
    result = {admin_id: [] for admin_id in admin_ids}
    if not admin_ids:
        return result
    for admin_id, category_id in (
        db.query(AdminPlanCategoryAccess.admin_id, AdminPlanCategoryAccess.category_id)
        .filter(AdminPlanCategoryAccess.admin_id.in_(admin_ids))
        .order_by(AdminPlanCategoryAccess.admin_id, AdminPlanCategoryAccess.category_id)
        .all()
    ):
        result[admin_id].append(category_id)
    return result


def replace_admin_categories(
    db: Session,
    *,
    actor: Admin,
    target: Admin,
    category_ids: list[int],
) -> None:
    if not admin_hierarchy.admin_in_scope(db, actor, target.id):
        raise admin_hierarchy.HierarchyError("admin_scope_forbidden", "Administrator is outside actor scope")
    wanted = sorted(set(category_ids))
    available = {
        row[0]
        for row in effective_categories_query(db, actor)
        .with_entities(AdminPlanCategory.id)
        .filter(AdminPlanCategory.id.in_(wanted))
        .all()
    } if wanted else set()
    if available != set(wanted):
        raise admin_hierarchy.HierarchyError(
            "category_access_forbidden",
            "One or more plan categories are unavailable to the assigning administrator",
        )
    db.query(AdminPlanCategoryAccess).filter(
        AdminPlanCategoryAccess.admin_id == target.id
    ).delete(synchronize_session=False)
    db.add_all(
        AdminPlanCategoryAccess(
            category_id=category_id,
            admin_id=target.id,
            assigned_by_admin_id=actor.id,
        )
        for category_id in wanted
    )
    db.flush()


def _validate_category(db: Session, actor: Admin, category_id: int | None) -> None:
    if category_id is None:
        return
    category = effective_categories_query(db, actor).filter(AdminPlanCategory.id == category_id).first()
    if category is None:
        raise admin_hierarchy.HierarchyError("category_access_forbidden", "Plan category is unavailable")


def _validate_network_scope(
    db: Session,
    settings: MarzhelpAdminSettings,
    inbounds: set[str],
    hosts: dict[str, set[int]],
) -> None:
    if not inbounds:
        raise admin_hierarchy.HierarchyError(
            "plan_inbound_required", "Plan requires at least one allowed inbound"
        )
    if set(hosts) != inbounds or any(not hosts[tag] for tag in inbounds):
        raise admin_hierarchy.HierarchyError(
            "plan_host_required", "Every selected inbound requires at least one explicit host"
        )
    if not settings.all_inbounds:
        unauthorized = inbounds - set(settings.allowed_inbounds)
        if unauthorized:
            raise admin_hierarchy.HierarchyError(
                "inbound_forbidden", f"Plan contains unauthorized inbounds: {sorted(unauthorized)}"
            )
    unknown = inbounds - set(xray.config.inbounds_by_tag)
    if unknown:
        raise admin_hierarchy.HierarchyError(
            "unknown_inbound", f"Unknown or unavailable inbounds: {sorted(unknown)}"
        )

    selected_ids = {host_id for host_ids in hosts.values() for host_id in host_ids}
    rows = (
        db.query(ProxyHost.id, ProxyHost.inbound_tag, ProxyHost.is_disabled, ProxyHost.address)
        .filter(ProxyHost.id.in_(selected_ids))
        .all()
    )
    active_by_id = {
        host_id: inbound_tag
        for host_id, inbound_tag, is_disabled, address in rows
        if not bool(is_disabled) and bool((address or "").strip())
    }
    missing = sorted(selected_ids - set(active_by_id))
    if missing:
        raise admin_hierarchy.HierarchyError(
            "plan_host_unavailable", f"Plan contains disabled, deleted, or unavailable hosts: {missing}"
        )
    mismatched = sorted(
        host_id
        for inbound_tag, host_ids in hosts.items()
        for host_id in host_ids
        if active_by_id.get(host_id) != inbound_tag
    )
    if mismatched:
        raise admin_hierarchy.HierarchyError(
            "plan_host_inbound_mismatch", f"Plan hosts do not belong to selected inbounds: {mismatched}"
        )


def network_options(db: Session, actor: Admin) -> list[dict]:
    settings = db.get(MarzhelpAdminSettings, actor.id)
    if settings is None:
        raise admin_hierarchy.HierarchyError("policy_missing", "Administrator policy is missing")
    allowed = set(xray.config.inbounds_by_tag)
    if not settings.all_inbounds:
        allowed &= set(settings.allowed_inbounds)
    host_rows = (
        db.query(ProxyHost.id, ProxyHost.inbound_tag, ProxyHost.remark)
        .filter(
            ProxyHost.inbound_tag.in_(allowed),
            or_(ProxyHost.is_disabled.is_(False), ProxyHost.is_disabled.is_(None)),
            ProxyHost.is_legacy.is_(False),
            ProxyHost.address != "",
        )
        .order_by(ProxyHost.inbound_tag, ProxyHost.id)
        .all()
        if allowed
        else []
    )
    hosts_by_tag = {tag: [] for tag in allowed}
    for host_id, inbound_tag, remark in host_rows:
        hosts_by_tag[inbound_tag].append({"id": host_id, "remark": remark})
    return [
        {
            "tag": tag,
            "protocol": inbound.get("protocol", ""),
            "network": inbound.get("network", ""),
            "tls": inbound.get("tls", ""),
            "port": inbound.get("port"),
            "hosts": hosts_by_tag.get(tag, []),
        }
        for tag, inbound in sorted(xray.config.inbounds_by_tag.items())
        if tag in allowed
    ]


def _version_network_scope(
    db: Session, version_id: int
) -> tuple[set[str], dict[str, set[int]]]:
    inbounds = {
        row[0]
        for row in db.query(AdminUserPlanInbound.inbound_tag)
        .filter(AdminUserPlanInbound.version_id == version_id)
        .all()
    }
    hosts = {tag: set() for tag in inbounds}
    for inbound_tag, host_id in (
        db.query(AdminUserPlanHost.inbound_tag, AdminUserPlanHost.host_id)
        .filter(AdminUserPlanHost.version_id == version_id)
        .all()
    ):
        hosts.setdefault(inbound_tag, set()).add(host_id)
    return inbounds, hosts


def version_network_scope(
    db: Session, version_id: int
) -> tuple[set[str], dict[str, set[int]]]:
    return _version_network_scope(db, version_id)


def _validate_version(db: Session, actor: Admin, version: PlanVersionInput) -> None:
    settings = db.get(MarzhelpAdminSettings, actor.id)
    if settings is None:
        raise admin_hierarchy.HierarchyError("policy_missing", "Administrator policy is missing")
    try:
        admin_billing.strategy_for(settings).validate_plan(version.concurrent_user_limit)
    except admin_billing.BillingModeError as exc:
        raise admin_hierarchy.HierarchyError(exc.code, str(exc)) from exc
    if settings.max_user_duration_days and version.duration_days > settings.max_user_duration_days:
        raise admin_hierarchy.HierarchyError("duration_exceeded", "Plan duration exceeds administrator limit")
    if not settings.all_user_limits and version.concurrent_user_limit is not None:
        if version.concurrent_user_limit not in settings.allowed_user_limits:
            raise admin_hierarchy.HierarchyError("user_limit_forbidden", "Plan device limit is not allowed")
    # Legacy versions keep their immutable network snapshot for rollback. New
    # commercial Plans omit it because Access Groups own network access.
    if version.inbounds or version.hosts:
        _validate_network_scope(
            db,
            settings,
            set(version.inbounds),
            {tag: set(host_ids) for tag, host_ids in version.hosts.items()},
        )
    mode = admin_billing.billing_mode(settings)
    available = admin_hierarchy.available_credit(db, settings)
    if mode == admin_billing.BillingMode.SEAT_CREDIT:
        seat_cost = admin_billing.finite_seat_cost(version.concurrent_user_limit)
        if available is not None and seat_cost > available:
            raise admin_hierarchy.HierarchyError(
                "credit_exhausted", "Plan seat cost exceeds available seat credit"
            )
        return
    if mode == admin_billing.BillingMode.USED_TRAFFIC and not admin_hierarchy.is_owner(db, actor):
        raise admin_hierarchy.HierarchyError(
            "plan_for_used_traffic_forbidden",
            "Actual-usage Admins use custom users and cannot create Plans",
        )
    if version.data_limit == 0 and available is not None:
        raise admin_hierarchy.HierarchyError("unlimited_traffic_forbidden", "Finite credit cannot create unlimited plans")
    if available is not None and version.data_limit > available:
        raise admin_hierarchy.HierarchyError("credit_exhausted", "Plan volume exceeds available credit")


def _validate_access_targets(db: Session, actor: Admin, admin_ids: list[int]) -> None:
    existing = {
        row[0] for row in db.query(Admin.id).filter(Admin.id.in_(set(admin_ids))).all()
    } if admin_ids else set()
    if existing != set(admin_ids):
        raise admin_hierarchy.HierarchyError("admin_not_found", "One or more plan access targets do not exist")
    for admin_id in existing:
        if not admin_hierarchy.admin_in_scope(db, actor, admin_id):
            raise admin_hierarchy.HierarchyError("plan_access_scope_forbidden", "Plan access target is outside scope")


def _validate_access_network_targets(
    db: Session,
    version: PlanVersionInput,
    admin_ids: list[int],
    include_subtree: bool,
) -> None:
    target_ids = set(admin_ids)
    if include_subtree and target_ids:
        target_ids.update(
            row[0]
            for row in db.query(AdminHierarchy.descendant_id)
            .filter(AdminHierarchy.ancestor_id.in_(target_ids))
            .all()
        )
    if not target_ids:
        return
    if not version.inbounds:
        return
    settings_rows = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id.in_(target_ids))
        .all()
    )
    settings_by_admin = {settings.admin_id: settings for settings in settings_rows}
    missing = sorted(target_ids - set(settings_by_admin))
    if missing:
        raise admin_hierarchy.HierarchyError(
            "plan_access_policy_missing", f"Plan access targets have no policy: {missing}"
        )
    plan_inbounds = set(version.inbounds)
    forbidden = sorted(
        settings.admin_id
        for settings in settings_rows
        if not settings.all_inbounds
        and not plan_inbounds.issubset(set(settings.allowed_inbounds))
    )
    if forbidden:
        raise admin_hierarchy.HierarchyError(
            "plan_access_network_forbidden",
            f"Plan network exceeds target administrator scope: {forbidden}",
        )


def _replace_access(
    db: Session,
    plan: AdminUserPlan,
    admin_ids: list[int],
    include_subtree: bool,
) -> None:
    db.query(AdminUserPlanAccess).filter(AdminUserPlanAccess.plan_id == plan.id).delete(
        synchronize_session=False
    )
    db.add_all(
        AdminUserPlanAccess(
            admin_id=admin_id,
            plan_id=plan.id,
            include_subtree=include_subtree,
        )
        for admin_id in sorted(set(admin_ids))
    )


def _add_version(
    db: Session,
    plan: AdminUserPlan,
    actor: Admin,
    values: PlanVersionInput,
) -> AdminUserPlanVersion:
    number = (
        db.query(func.max(AdminUserPlanVersion.version_number))
        .filter(AdminUserPlanVersion.plan_id == plan.id)
        .scalar()
        or 0
    ) + 1
    version = AdminUserPlanVersion(
        plan_id=plan.id,
        version_number=number,
        price_toman=0 if plan.is_trial else values.price_toman,
        data_limit=values.data_limit,
        duration_days=values.duration_days,
        concurrent_user_limit=values.concurrent_user_limit,
        reset_strategy=values.reset_strategy,
        renewal_volume_strategy=values.renewal_volume_strategy,
        renewal_time_strategy=values.renewal_time_strategy,
        created_by_admin_id=actor.id,
    )
    db.add(version)
    db.flush()
    db.add_all(
        AdminUserPlanInbound(version_id=version.id, inbound_tag=tag)
        for tag in values.inbounds
    )
    db.add_all(
        AdminUserPlanHost(version_id=version.id, inbound_tag=tag, host_id=host_id)
        for tag, host_ids in values.hosts.items()
        for host_id in host_ids
    )
    plan.current_version_id = version.id
    return version


def add_network_revision(
    db: Session,
    *,
    actor: Admin,
    plan: AdminUserPlan,
    inbounds: set[str],
    hosts: dict[str, set[int]],
) -> tuple[AdminUserPlanVersion, AdminUserPlanVersion]:
    """Create a network-only Plan revision without rewriting financial history."""
    current = db.get(AdminUserPlanVersion, plan.current_version_id)
    if current is None:
        raise admin_hierarchy.HierarchyError("plan_version_missing", "Current Plan version is missing")
    revision = _add_version(
        db,
        plan,
        actor,
        PlanVersionInput(
            price_toman=current.price_toman,
            data_limit=current.data_limit,
            duration_days=current.duration_days,
            concurrent_user_limit=current.concurrent_user_limit,
            reset_strategy=current.reset_strategy,
            renewal_volume_strategy=current.renewal_volume_strategy,
            renewal_time_strategy=current.renewal_time_strategy,
            inbounds=sorted(inbounds),
            hosts={tag: sorted(hosts[tag]) for tag in sorted(inbounds)},
        ),
    )
    return current, revision


def sync_active_users_to_network_revision(
    db: Session,
    *,
    actor: Admin,
    plan: AdminUserPlan,
    previous_version: AdminUserPlanVersion,
    revision: AdminUserPlanVersion,
) -> list[int]:
    """Move only currently assigned active Users to a network-only revision."""
    latest = (
        db.query(
            UserPlanAssignment.user_id.label("user_id"),
            func.max(UserPlanAssignment.id).label("assignment_id"),
        )
        .group_by(UserPlanAssignment.user_id)
        .subquery()
    )
    users = (
        db.query(User)
        .join(latest, latest.c.user_id == User.id)
        .join(UserPlanAssignment, UserPlanAssignment.id == latest.c.assignment_id)
        .filter(
            User.status == UserStatus.active,
            UserPlanAssignment.plan_id == plan.id,
            UserPlanAssignment.version_id == previous_version.id,
        )
        .order_by(User.id)
        .all()
    )
    inbounds, _ = _version_network_scope(db, revision.id)
    for user in users:
        _apply_plan_network_to_user(db, user, inbounds)
        db.add(UserPlanAssignment(
            user_id=user.id,
            plan_id=plan.id,
            version_id=revision.id,
            actor_admin_id=actor.id,
            operation_type="network_sync",
            is_trial=bool(plan.is_trial),
            idempotency_key=f"network-sync:{revision.id}:{user.id}",
        ))
    db.flush()
    return [user.id for user in users]


def create_plan(db: Session, actor: Admin, values: PlanCreate) -> AdminUserPlan:
    if not _can_manage_plans(db, actor):
        raise admin_hierarchy.HierarchyError("plan_management_forbidden", "Plan management is not enabled")
    _validate_version(db, actor, values.version)
    _validate_category(db, actor, values.category_id)
    _validate_access_targets(db, actor, values.allowed_admin_ids)
    _validate_access_network_targets(
        db, values.version, values.allowed_admin_ids, values.include_subtree
    )
    if values.is_trial and not admin_hierarchy.is_owner(db, actor):
        raise admin_hierarchy.HierarchyError(
            "trial_plan_owner_required", "Only Owner can create Trial plans"
        )
    plan = AdminUserPlan(
        owner_admin_id=actor.id,
        category_id=values.category_id,
        name=values.name.strip(),
        description=values.description,
        is_trial=values.is_trial,
    )
    try:
        db.add(plan)
        db.flush()
        _add_version(db, plan, actor, values.version)
        _replace_access(db, plan, values.allowed_admin_ids, values.include_subtree)
        db.commit()
        db.refresh(plan)
        return plan
    except Exception:
        db.rollback()
        raise


def update_plan(db: Session, actor: Admin, plan: AdminUserPlan, values: PlanUpdate) -> AdminUserPlan:
    if not admin_hierarchy.is_owner(db, actor) and plan.owner_admin_id != actor.id:
        raise admin_hierarchy.HierarchyError("plan_update_forbidden", "Only plan owner can update this plan")
    if not _can_manage_plans(db, actor):
        raise admin_hierarchy.HierarchyError("plan_management_forbidden", "Plan management is not enabled")
    _validate_version(db, actor, values.version)
    _validate_category(db, actor, values.category_id)
    _validate_access_targets(db, actor, values.allowed_admin_ids)
    _validate_access_network_targets(
        db, values.version, values.allowed_admin_ids, values.include_subtree
    )
    plan = db.query(AdminUserPlan).filter(AdminUserPlan.id == plan.id).with_for_update().one()
    plan.description = values.description
    plan.category_id = values.category_id
    _add_version(db, plan, actor, values.version)
    _replace_access(db, plan, values.allowed_admin_ids, values.include_subtree)
    db.commit()
    db.refresh(plan)
    return plan


def effective_plans_query(db: Session, actor: Admin):
    query = db.query(AdminUserPlan).filter(AdminUserPlan.archived_at.is_(None))
    if admin_hierarchy.is_owner(db, actor):
        return query
    settings = db.get(MarzhelpAdminSettings, actor.id)
    if (
        settings is not None
        and settings.money_billing_enabled
        and admin_billing.billing_mode(settings) in money_billing.PRICED_PLAN_MODES
    ):
        return query
    direct = exists().where(
        and_(
            AdminUserPlanAccess.plan_id == AdminUserPlan.id,
            AdminUserPlanAccess.admin_id == actor.id,
        )
    )
    inherited = exists().where(
        and_(
            AdminUserPlanAccess.plan_id == AdminUserPlan.id,
            AdminUserPlanAccess.include_subtree.is_(True),
            exists().where(
                and_(
                    AdminHierarchy.ancestor_id == AdminUserPlanAccess.admin_id,
                    AdminHierarchy.descendant_id == actor.id,
                )
            ),
        )
    )
    category_access = exists().where(
        and_(
            AdminPlanCategoryAccess.category_id == AdminUserPlan.category_id,
            AdminPlanCategoryAccess.admin_id == actor.id,
        )
    )
    query = query.filter(
        or_(AdminUserPlan.owner_admin_id == actor.id, category_access, direct, inherited)
    )
    return query


def can_use_plan(db: Session, actor: Admin, plan_id: int) -> bool:
    return bool(effective_plans_query(db, actor).filter(AdminUserPlan.id == plan_id).first())


def plan_responses(
    db: Session,
    plans: list[AdminUserPlan],
    actor: Admin | None = None,
) -> list[PlanResponse]:
    if not plans:
        return []
    version_ids = [plan.current_version_id for plan in plans if plan.current_version_id]
    versions = {
        version.id: version
        for version in db.query(AdminUserPlanVersion)
        .filter(AdminUserPlanVersion.id.in_(version_ids))
        .all()
    }
    inbounds_by_version = {version_id: [] for version_id in version_ids}
    for version_id, inbound_tag in (
        db.query(AdminUserPlanInbound.version_id, AdminUserPlanInbound.inbound_tag)
        .filter(AdminUserPlanInbound.version_id.in_(version_ids))
        .order_by(AdminUserPlanInbound.version_id, AdminUserPlanInbound.inbound_tag)
        .all()
    ):
        inbounds_by_version[version_id].append(inbound_tag)
    hosts_by_version = {
        version_id: {tag: [] for tag in inbounds_by_version.get(version_id, [])}
        for version_id in version_ids
    }
    for version_id, inbound_tag, host_id in (
        db.query(
            AdminUserPlanHost.version_id,
            AdminUserPlanHost.inbound_tag,
            AdminUserPlanHost.host_id,
        )
        .filter(AdminUserPlanHost.version_id.in_(version_ids))
        .order_by(
            AdminUserPlanHost.version_id,
            AdminUserPlanHost.inbound_tag,
            AdminUserPlanHost.host_id,
        )
        .all()
    ):
        hosts_by_version[version_id].setdefault(inbound_tag, []).append(host_id)
    plan_ids = [plan.id for plan in plans]
    actor_is_owner = bool(actor is not None and admin_hierarchy.is_owner(db, actor))
    actor_prices = {
        row.plan_id: int(row.price_toman)
        for row in db.query(AdminUserPlanPrice)
        .filter(
            AdminUserPlanPrice.admin_id == actor.id,
            AdminUserPlanPrice.plan_id.in_(plan_ids),
        )
        .all()
    } if actor is not None and not actor_is_owner else {}
    access_by_plan = {plan_id: [] for plan_id in plan_ids}
    for row in (
        db.query(AdminUserPlanAccess)
        .filter(AdminUserPlanAccess.plan_id.in_(plan_ids))
        .order_by(AdminUserPlanAccess.plan_id, AdminUserPlanAccess.admin_id)
        .all()
    ):
        access_by_plan[row.plan_id].append(row)

    responses = []
    for plan in plans:
        version = versions.get(plan.current_version_id)
        if version is None:
            raise admin_hierarchy.HierarchyError(
                "plan_version_missing", "Plan current version is missing"
            )
        inbounds = inbounds_by_version.get(version.id, [])
        access = access_by_plan.get(plan.id, [])
        responses.append(
            PlanResponse(
                id=plan.id,
                owner_admin_id=plan.owner_admin_id,
                name=plan.name,
                description=plan.description,
                category_id=plan.category_id,
                category_name=plan.category.name if plan.category is not None else None,
                current_version_id=version.id,
                version_number=version.version_number,
                archived_at=plan.archived_at,
                version=PlanVersionResponse(
                    price_toman=int(version.price_toman or 0),
                    data_limit=version.data_limit,
                    duration_days=version.duration_days,
                    concurrent_user_limit=version.concurrent_user_limit,
                    reset_strategy=version.reset_strategy,
                    renewal_volume_strategy=version.renewal_volume_strategy,
                    renewal_time_strategy=version.renewal_time_strategy,
                    inbounds=inbounds,
                    hosts=hosts_by_version.get(version.id, {}),
                ),
                allowed_admin_ids=[row.admin_id for row in access],
                include_subtree=any(row.include_subtree for row in access),
                is_trial=bool(plan.is_trial),
                effective_price_toman=(
                    0
                    if plan.is_trial
                    else actor_prices.get(plan.id, int(version.price_toman or 0))
                ),
                base_price_toman=(
                    int(version.price_toman or 0)
                    if actor_is_owner
                    else None
                ),
            )
        )
    return responses


def plan_response(db: Session, plan: AdminUserPlan, actor: Admin | None = None) -> PlanResponse:
    return plan_responses(db, [plan], actor=actor)[0]


def _plan_user_payload(plan: AdminUserPlan, version: AdminUserPlanVersion, username: str, status, note):
    tags = []
    # Caller loaded these into the transient attribute to avoid an extra query here.
    tags.extend(getattr(version, "_inbound_tags", []))
    inbounds: dict[ProxyTypes, list[str]] = {}
    for tag in tags:
        protocol = xray.config.inbounds_by_tag[tag]["protocol"]
        proxy_type = ProxyTypes(protocol)
        inbounds.setdefault(proxy_type, []).append(tag)
    proxies = {proxy_type: {} for proxy_type in inbounds}
    expire = int((datetime.now(timezone.utc) + timedelta(days=version.duration_days)).timestamp())
    return UserCreate(
        username=username,
        status=UserStatusCreate(status),
        proxies=proxies,
        inbounds=inbounds,
        data_limit=version.data_limit,
        concurrent_user_limit=version.concurrent_user_limit,
        data_limit_reset_strategy=UserDataLimitResetStrategy(version.reset_strategy),
        expire=expire,
        note=note,
    )


def _apply_plan_network_to_user(
    db: Session,
    user: User,
    inbound_tags: set[str],
) -> None:
    desired: dict[ProxyTypes, list[str]] = {}
    for tag in sorted(inbound_tags):
        proxy_type = ProxyTypes(xray.config.inbounds_by_tag[tag]["protocol"])
        desired.setdefault(proxy_type, []).append(tag)

    existing = {ProxyTypes(proxy.type): proxy for proxy in list(user.proxies)}
    for proxy_type, tags in desired.items():
        proxy = existing.pop(proxy_type, None)
        if proxy is None:
            settings = ProxySettings.from_dict(proxy_type, {})
            proxy = Proxy(type=proxy_type, settings=settings.dict(no_obj=True))
            user.proxies.append(proxy)
        allowed = set(tags)
        proxy.excluded_inbounds = [
            crud.get_or_create_inbound(db, inbound["tag"])
            for inbound in xray.config.inbounds_by_protocol.get(proxy_type, [])
            if inbound["tag"] not in allowed
        ]
    for proxy in existing.values():
        db.delete(proxy)


def subscription_host_scope(db: Session, user: User) -> dict[str, set[int]] | None:
    if user.access_group_id is not None:
        from app.utils import access_groups

        return access_groups.host_scope(db, user)
    assignment = (
        db.query(UserPlanAssignment)
        .filter(UserPlanAssignment.user_id == user.id)
        .order_by(UserPlanAssignment.created_at.desc(), UserPlanAssignment.id.desc())
        .first()
    )
    if assignment is None:
        return None
    settings = db.get(MarzhelpAdminSettings, user.admin_id)
    if settings is None:
        return {}
    inbounds, hosts = _version_network_scope(db, assignment.version_id)
    try:
        _validate_network_scope(db, settings, inbounds, hosts)
    except admin_hierarchy.HierarchyError:
        return {}
    return hosts


def subscription_host_scopes(
    db: Session, users: list[User]
) -> dict[int, dict[str, set[int]] | None]:
    """Resolve Plan Host snapshots for a bounded User page without N+1 queries."""
    if not users:
        return {}
    user_ids = [user.id for user in users]
    latest = (
        db.query(
            UserPlanAssignment.user_id.label("user_id"),
            func.max(UserPlanAssignment.id).label("assignment_id"),
        )
        .filter(UserPlanAssignment.user_id.in_(user_ids))
        .group_by(UserPlanAssignment.user_id)
        .subquery()
    )
    assignments = {
        assignment.user_id: assignment
        for assignment in (
            db.query(UserPlanAssignment)
            .join(latest, latest.c.assignment_id == UserPlanAssignment.id)
            .all()
        )
    }
    version_ids = sorted({row.version_id for row in assignments.values()})
    inbounds = {version_id: set() for version_id in version_ids}
    for version_id, tag in (
        db.query(AdminUserPlanInbound.version_id, AdminUserPlanInbound.inbound_tag)
        .filter(AdminUserPlanInbound.version_id.in_(version_ids))
        .all()
    ):
        inbounds[version_id].add(tag)
    hosts = {version_id: {} for version_id in version_ids}
    host_ids: set[int] = set()
    for version_id, tag, host_id in (
        db.query(
            AdminUserPlanHost.version_id,
            AdminUserPlanHost.inbound_tag,
            AdminUserPlanHost.host_id,
        )
        .filter(AdminUserPlanHost.version_id.in_(version_ids))
        .all()
    ):
        hosts[version_id].setdefault(tag, set()).add(host_id)
        host_ids.add(host_id)
    active_hosts = {
        row.id: row.inbound_tag
        for row in db.query(ProxyHost.id, ProxyHost.inbound_tag)
        .filter(ProxyHost.id.in_(host_ids), ProxyHost.is_disabled.is_not(True))
        .all()
    }
    admin_ids = sorted({user.admin_id for user in users if user.admin_id is not None})
    settings = {
        row.admin_id: row
        for row in db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id.in_(admin_ids))
        .all()
    }
    result: dict[int, dict[str, set[int]] | None] = {}
    configured_tags = set(xray.config.inbounds_by_tag)
    for user in users:
        assignment = assignments.get(user.id)
        if assignment is None:
            result[user.id] = None
            continue
        user_settings = settings.get(user.admin_id)
        version_inbounds = inbounds.get(assignment.version_id, set())
        version_hosts = hosts.get(assignment.version_id, {})
        allowed_tags = (
            configured_tags
            if user_settings and user_settings.all_inbounds
            else set(user_settings.allowed_inbounds or []) if user_settings else set()
        )
        valid = (
            bool(version_inbounds)
            and set(version_hosts) == version_inbounds
            and all(version_hosts.get(tag) for tag in version_inbounds)
            and version_inbounds <= allowed_tags
            and all(
                active_hosts.get(host_id) == tag
                for tag, ids in version_hosts.items()
                for host_id in ids
            )
        )
        result[user.id] = version_hosts if valid else {}
    group_ids = {user.access_group_id for user in users if user.access_group_id is not None}
    if group_ids:
        from app.db.models import AccessGroup, AccessGroupInbound, AccessGroupHost
        active_groups = {row[0] for row in db.query(AccessGroup.id).filter(
            AccessGroup.id.in_(group_ids), AccessGroup.archived_at.is_(None))}
        group_inbounds = {group_id: set() for group_id in active_groups}
        group_hosts = {group_id: {} for group_id in active_groups}
        for group_id, tag in db.query(AccessGroupInbound.access_group_id, AccessGroupInbound.inbound_tag).filter(
            AccessGroupInbound.access_group_id.in_(active_groups)):
            group_inbounds[group_id].add(tag)
        for group_id, tag, host_id in db.query(AccessGroupHost.access_group_id, AccessGroupHost.inbound_tag, AccessGroupHost.host_id).filter(
            AccessGroupHost.access_group_id.in_(active_groups)):
            group_hosts[group_id].setdefault(tag, set()).add(host_id)
        for user in users:
            if user.access_group_id is not None:
                tags = group_inbounds.get(user.access_group_id, set())
                selected = group_hosts.get(user.access_group_id, {})
                result[user.id] = selected if tags and set(selected) == tags and all(selected.values()) else {}
    return result


def usage_is_visible(db: Session, actor: Admin | None) -> bool:
    if actor is None or admin_hierarchy.is_owner(db, actor):
        return True
    settings = db.get(MarzhelpAdminSettings, actor.id)
    return bool(
        settings is None
        or admin_billing.billing_mode(settings)
        not in {admin_billing.BillingMode.SEAT_CREDIT, admin_billing.BillingMode.USER_CREDIT}
    )


def _redact_usage(response: UserResponse, visible: bool) -> UserResponse:
    if not visible:
        response.used_traffic = None
        response.lifetime_used_traffic = None
        response.reset_history = []
    return response


def scoped_user_response(
    db: Session, user: User, *, actor: Admin | None = None
) -> UserResponse:
    response = UserResponse.model_validate(
        user,
        context={"host_scope": subscription_host_scope(db, user)},
    )
    return _redact_usage(response, usage_is_visible(db, actor))


def scoped_user_responses(
    db: Session, users: list[User], *, actor: Admin | None = None
) -> list[UserResponse]:
    scopes = subscription_host_scopes(db, users)
    visible = usage_is_visible(db, actor)
    return [
        _redact_usage(
            UserResponse.model_validate(user, context={"host_scope": scopes[user.id]}),
            visible,
        )
        for user in users
    ]


def _assignment_replay(
    db: Session,
    *,
    actor: Admin,
    plan_id: int,
    username: str,
    operation_type: str,
    idempotency_key: str,
) -> tuple[User, UserPlanAssignment] | None:
    assignment = db.query(UserPlanAssignment).filter(
        UserPlanAssignment.idempotency_key == idempotency_key
    ).one_or_none()
    if assignment is None:
        return None
    user = db.get(User, assignment.user_id)
    if (
        user is None
        or assignment.actor_admin_id != actor.id
        or assignment.plan_id != plan_id
        or assignment.operation_type != operation_type
        or user.username != username
    ):
        raise admin_hierarchy.HierarchyError(
            "idempotency_conflict",
            "Idempotency key belongs to another plan operation",
        )
    if not admin_hierarchy.can_access_user(db, actor, user):
        raise admin_hierarchy.HierarchyError(
            "user_scope_forbidden",
            "The prior plan operation is outside actor scope",
        )
    return user, assignment


def create_user_from_plan(
    db: Session,
    *,
    actor: Admin,
    plan_id: int,
    username: str,
    status: str,
    note: str | None,
    idempotency_key: str,
    access_group_id: int | None = None,
) -> tuple[User, UserPlanAssignment, bool]:
    username = marzhelp_policy.customer_username(db, actor, username)
    replay = _assignment_replay(
        db,
        actor=actor,
        plan_id=plan_id,
        username=username,
        operation_type="create",
        idempotency_key=idempotency_key,
    )
    if replay:
        return replay[0], replay[1], False
    if not can_use_plan(db, actor, plan_id):
        raise admin_hierarchy.HierarchyError("plan_access_forbidden", "Plan is unavailable in this scope")
    settings = db.get(MarzhelpAdminSettings, actor.id)
    if settings and settings.user_creation_mode_id not in (1, 2):
        if settings.user_creation_mode_id != admin_hierarchy.USER_CREATION_MODE_IDS[admin_hierarchy.BOTH]:
            raise admin_hierarchy.HierarchyError("invalid_creation_mode", "Unknown user creation mode")
    if not admin_hierarchy.is_owner(db, actor) and not admin_hierarchy.allows_plan_creation(settings):
        raise admin_hierarchy.HierarchyError("form_only", "This administrator can create users only with Form")
    plan = db.get(AdminUserPlan, plan_id)
    version = db.get(AdminUserPlanVersion, plan.current_version_id)
    if settings is None:
        raise admin_hierarchy.HierarchyError("policy_missing", "Administrator policy is missing")
    if plan.is_trial:
        replay = _assignment_replay(
            db,
            actor=actor,
            plan_id=plan_id,
            username=username,
            operation_type="create",
            idempotency_key=idempotency_key,
        )
        if replay:
            return replay[0], replay[1], False
        consumed = db.execute(
            update(MarzhelpAdminSettings)
            .where(
                MarzhelpAdminSettings.admin_id == actor.id,
                MarzhelpAdminSettings.trial_quota > 0,
            )
            .values(
                trial_quota=MarzhelpAdminSettings.trial_quota - 1,
                trials_used=MarzhelpAdminSettings.trials_used + 1,
                updated_at=func.now(),
            )
        )
        if consumed.rowcount != 1:
            raise admin_hierarchy.HierarchyError(
                "trial_quota_exhausted", "Trial creation quota is exhausted"
            )
        db.expire(settings, ["trial_quota", "trials_used"])
    version_inbounds, version_hosts = _version_network_scope(db, version.id)
    if access_group_id is not None:
        from app.utils import access_groups

        group = db.get(access_groups.AccessGroup, access_group_id)
        if group is None or group.archived_at is not None:
            raise admin_hierarchy.HierarchyError("access_group_unavailable", "Access Group is unavailable")
        version_inbounds, version_hosts, _ = access_groups._scope(db, access_group_id)
    else:
        _validate_network_scope(db, settings, version_inbounds, version_hosts)
    version._inbound_tags = sorted(version_inbounds)
    payload = _plan_user_payload(plan, version, username, status, note)
    try:
        user = crud.create_user(
            db,
            payload,
            admin=actor,
            commit=False,
            apply_namespace=False,
        )
        if access_group_id is not None:
            access_groups.apply_to_user(db, user, access_group_id)
        assignment = UserPlanAssignment(
            user_id=user.id,
            plan_id=plan.id,
            version_id=version.id,
            actor_admin_id=actor.id,
            operation_type="create",
            is_trial=bool(plan.is_trial),
            idempotency_key=idempotency_key,
        )
        db.add(assignment)
        db.flush()
        money_billing.charge_plan_purchase(
            db,
            buyer=actor,
            actor=actor,
            plan=plan,
            version=version,
            operation_type="create",
            idempotency_key=idempotency_key,
            user_id=user.id,
        )
        db.commit()
        db.refresh(user)
        db.refresh(assignment)
        return user, assignment, True
    except IntegrityError:
        db.rollback()
        replay = _assignment_replay(
            db,
            actor=actor,
            plan_id=plan_id,
            username=username,
            operation_type="create",
            idempotency_key=idempotency_key,
        )
        if replay:
            return replay[0], replay[1], False
        raise


def renew_user_from_plan(
    db: Session,
    *,
    actor: Admin,
    user: User,
    plan_id: int,
    idempotency_key: str,
    access_group_id: int | None = None,
) -> tuple[User, UserPlanAssignment, bool]:
    replay = _assignment_replay(
        db,
        actor=actor,
        plan_id=plan_id,
        username=user.username,
        operation_type="renew",
        idempotency_key=idempotency_key,
    )
    if replay:
        return replay[0], replay[1], False
    user = (
        db.query(User)
        .filter(User.id == user.id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    replay = _assignment_replay(
        db,
        actor=actor,
        plan_id=plan_id,
        username=user.username,
        operation_type="renew",
        idempotency_key=idempotency_key,
    )
    if replay:
        return replay[0], replay[1], False
    if access_group_id is None:
        access_group_id = user.access_group_id
    marzhelp_policy.validate_no_active_penalty(user)
    if not admin_hierarchy.can_access_user(db, actor, user):
        raise admin_hierarchy.HierarchyError("user_scope_forbidden", "User is outside actor scope")
    if not can_use_plan(db, actor, plan_id):
        raise admin_hierarchy.HierarchyError("plan_access_forbidden", "Plan is unavailable in this scope")
    plan = db.get(AdminUserPlan, plan_id)
    if plan.archived_at is not None:
        raise admin_hierarchy.HierarchyError("plan_archived", "Archived plan cannot be renewed")
    if plan.is_trial:
        raise admin_hierarchy.HierarchyError(
            "trial_renewal_forbidden", "Trial plans can only create new Trial users"
        )
    version = db.get(AdminUserPlanVersion, plan.current_version_id)
    settings = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id == user.admin_id)
        .with_for_update()
        .one()
    )
    mode = admin_billing.billing_mode(settings)
    try:
        admin_billing.strategy_for(settings).validate_plan(version.concurrent_user_limit)
    except admin_billing.BillingModeError as exc:
        raise admin_hierarchy.HierarchyError(exc.code, str(exc)) from exc
    if not settings.renewal_enabled:
        raise admin_hierarchy.HierarchyError("renewal_disabled", "Renewal is disabled")
    if settings.renewal_remaining is not None and settings.renewal_remaining <= 0:
        raise admin_hierarchy.HierarchyError("renewal_quota_exhausted", "Renewal quota is exhausted")
    if settings.max_user_duration_days and version.duration_days > settings.max_user_duration_days:
        raise admin_hierarchy.HierarchyError("duration_exceeded", "Plan duration exceeds user owner limit")
    if (
        not settings.all_user_limits
        and version.concurrent_user_limit is not None
        and version.concurrent_user_limit not in settings.allowed_user_limits
    ):
        raise admin_hierarchy.HierarchyError("user_limit_forbidden", "Plan device limit is not allowed")
    version_inbounds, version_hosts = _version_network_scope(db, version.id)
    if access_group_id is not None:
        from app.utils import access_groups

        group = db.get(access_groups.AccessGroup, access_group_id)
        if group is None or group.archived_at is not None:
            raise admin_hierarchy.HierarchyError("access_group_unavailable", "Access Group is unavailable")
    elif user.access_group_id is None:
        _validate_network_scope(db, settings, version_inbounds, version_hosts)
    available = admin_hierarchy.available_credit(db, settings)
    seat_cost = 0
    if mode == admin_billing.BillingMode.SEAT_CREDIT:
        seat_cost = admin_billing.finite_seat_cost(version.concurrent_user_limit)
        if available is not None and seat_cost > available:
            raise admin_hierarchy.HierarchyError(
                "credit_exhausted",
                "Renewal Seat cost exceeds available Seat Credit",
            )
    elif mode != admin_billing.BillingMode.USED_TRAFFIC:
        if version.data_limit == 0 and available is not None:
            raise admin_hierarchy.HierarchyError("unlimited_traffic_forbidden", "Finite credit cannot renew unlimited")
        if available is not None and version.data_limit > available:
            raise admin_hierarchy.HierarchyError("credit_exhausted", "Renewal exceeds available credit")
    else:
        marzhelp_policy._validate_traffic_credit(db, settings)

    if user.used_traffic:
        db.add(UserUsageResetLogs(user_id=user.id, used_traffic_at_reset=user.used_traffic))
    user.used_traffic = 0
    user.data_limit = version.data_limit or None
    user.status = UserStatus.active
    user.concurrent_user_limit = version.concurrent_user_limit
    user.data_limit_reset_strategy = UserDataLimitResetStrategy(version.reset_strategy)
    if access_group_id is not None:
        access_groups.apply_to_user(db, user, access_group_id)
    elif user.access_group_id is None:
        _apply_plan_network_to_user(db, user, version_inbounds)
    sync_device_slots(db, user)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    user.expire = max(now_ts, int(user.expire or 0)) + version.duration_days * 86400
    if mode == admin_billing.BillingMode.ALLOCATED_TRAFFIC:
        marzhelp_policy._validate_traffic_credit(
            db,
            settings,
            allocated_charge=int(version.data_limit or 0),
            unlimited_requested=version.data_limit == 0,
        )
    elif (
        mode == admin_billing.BillingMode.LEGACY_COMPAT
        and (settings.calculate_volume or "used_traffic") == "created_traffic"
    ):
        settings.used_traffic = int(settings.used_traffic or 0) + int(version.data_limit or 0)
    marzhelp_policy.record_lifetime_created(settings, version.data_limit)
    if settings.renewal_remaining is not None:
        settings.renewal_remaining -= 1
    settings.renewals_used = int(settings.renewals_used or 0) + 1
    if mode == admin_billing.BillingMode.SEAT_CREDIT:
        marzhelp_policy.consume_seat_renewal(
            db,
            settings,
            user=user,
            seat_cost=seat_cost,
            idempotency_key=idempotency_key,
            plan_id=plan.id,
            version_id=version.id,
        )
    assignment = UserPlanAssignment(
        user_id=user.id,
        plan_id=plan.id,
        version_id=version.id,
        actor_admin_id=actor.id,
        operation_type="renew",
        is_trial=False,
        idempotency_key=idempotency_key,
    )
    try:
        db.add(assignment)
        buyer = db.get(Admin, user.admin_id)
        if buyer is None:
            raise admin_hierarchy.HierarchyError("user_owner_missing", "User owner Admin is missing")
        money_billing.charge_plan_purchase(
            db,
            buyer=buyer,
            actor=actor,
            plan=plan,
            version=version,
            operation_type="renew",
            idempotency_key=idempotency_key,
            user_id=user.id,
        )
        db.commit()
        db.refresh(user)
        db.refresh(assignment)
        return user, assignment, True
    except IntegrityError:
        db.rollback()
        replay = _assignment_replay(
            db,
            actor=actor,
            plan_id=plan_id,
            username=user.username,
            operation_type="renew",
            idempotency_key=idempotency_key,
        )
        if replay:
            return replay[0], replay[1], False
        raise
