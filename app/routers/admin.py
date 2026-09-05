from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError

from app import xray
from app.db import Session, crud, get_db
from app.dependencies import get_admin_by_username, validate_admin
from app.db.models import (
    Admin as DBAdmin,
    AdminBulkJob,
    AdminCreditTransfer,
    AdminReferralAttribution,
    AdminReferralEvent,
    AdminSuspensionAdmin,
    AdminSuspensionEvent,
    AdminUserCreationMode,
    AdminUserPlan,
    AdminUserPlanPrice,
    AdminUserPlanVersion,
    MarzhelpAccountingTransaction,
    MarzhelpAdminSettings,
    User,
    UserPlanAssignment,
)
from app.models.admin import (
    Admin,
    AdminCapabilities,
    AdminDeleteRequest,
    AdminQuotaSummary,
    AdminCreate,
    AdminModify,
    ManagedAdmin,
    ManagedAdminCreate,
    ManagedAdminList,
    ManagedAdminModify,
    MarzhelpAdminPolicy,
    Token,
)
from app.models.user import UserStatus
from app.device_limit.constants import SubscriptionMode
from app.utils import admin_hierarchy, admin_plans, marzhelp_policy, money_billing, report, responses
from app.utils.admin_billing import BillingMode
from app.utils.audit import (
    AuditLogService,
    AuditStatus,
    admin_audit_state,
    get_client_ip,
    summarize_targets,
)
from app.utils.jwt import create_admin_token
from config import LOGIN_NOTIFY_WHITE_LIST

router = APIRouter(tags=["Admin"], prefix="/api", responses={401: responses._401})


def require_initialized_admin_hierarchy(db: Session) -> None:
    if admin_hierarchy.hierarchy_enabled(db):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "admin_hierarchy_not_initialized",
            "message": (
                "Admin hierarchy is not initialized. Run "
                "marzban set-owner <username> on the server first"
            ),
        },
    )


def managed_admin_response(
    db: Session,
    dbadmin,
    settings=None,
    user_count: int = 0,
    capacity_used: int = 0,
    quota: AdminQuotaSummary | None = None,
    plan_category_ids: list[int] | None = None,
    plan_prices: list[dict] | None = None,
    parent_username: str | None = None,
) -> ManagedAdmin:
    policy = (
        MarzhelpAdminPolicy.model_validate(settings)
        if settings is not None
        else MarzhelpAdminPolicy()
    )
    policy = policy.model_copy(
        update={
            "view_full_client_ip": True,
            "prevent_user_creation": False,
            "prevent_revoke_subscription": False,
            "money_balance_toman": int(settings.money_balance_toman or 0) if settings else 0,
        }
    )
    creation_mode = "PLAN_ONLY"
    if settings is not None:
        creation_mode = (
            db.query(AdminUserCreationMode.code)
            .filter(AdminUserCreationMode.id == settings.user_creation_mode_id)
            .scalar()
            or "PLAN_ONLY"
        )
    return ManagedAdmin(
        id=dbadmin.id,
        username=dbadmin.username,
        is_sudo=dbadmin.is_sudo,
        role=admin_hierarchy.role_code(dbadmin),
        parent_admin_id=dbadmin.parent_admin_id,
        external_api_enabled=bool(dbadmin.external_api_enabled),
        telegram_id=dbadmin.telegram_id,
        phone=dbadmin.phone,
        dashboard_theme=dbadmin.dashboard_theme or "heisenberg",
        logo_url=dbadmin.logo_url,
        account_status=(
            {value: code for code, value in admin_hierarchy.ACCOUNT_STATUS_IDS.items()}.get(
                settings.account_status_id if settings is not None else 1,
                admin_hierarchy.ACTIVE,
            )
        ),
        parent_username=parent_username,
        active_owner_freeze_event_id=(
            settings.suspension_event_id if settings is not None else None
        ),
        trial_quota=int(settings.trial_quota or 0) if settings else 0,
        trial_quota_limit=int(settings.trial_quota_limit or 0) if settings else 0,
        trials_used=int(settings.trials_used or 0) if settings else 0,
        discord_webhook=dbadmin.discord_webhook,
        users_usage=dbadmin.users_usage,
        user_count=user_count,
        capacity_used=capacity_used,
        policy=policy,
        quota=quota
        or AdminQuotaSummary.model_validate(
            marzhelp_policy.quota_summary(db, dbadmin.id)
        ),
        plan_category_ids=(
            admin_plans.admin_category_ids(db, dbadmin.id)
            if plan_category_ids is None
            else plan_category_ids
        ),
        plan_prices=(
            [
                {"plan_id": row.plan_id, "price_toman": int(row.price_toman)}
                for row in db.query(AdminUserPlanPrice)
                .filter(AdminUserPlanPrice.admin_id == dbadmin.id)
                .order_by(AdminUserPlanPrice.plan_id)
                .all()
            ]
            if plan_prices is None
            else plan_prices
        ),
        user_creation_mode=creation_mode,
        can_manage_plans=bool(settings.can_manage_plans) if settings else False,
        can_create_admins=bool(settings.can_create_admins) if settings else False,
        can_delegate_admin_creation=(
            bool(settings.can_delegate_admin_creation) if settings else False
        ),
        can_create_allocated_children=(
            bool(settings.can_create_allocated_children) if settings else True
        ),
        admin_creation_limit=settings.admin_creation_limit if settings else 0,
        admin_creations_used=int(settings.admin_creations_used or 0) if settings else 0,
        delegated_admin_creation_limit=(
            int(settings.delegated_admin_creation_limit or 0) if settings else 0
        ),
        admin_creation_remaining=(
            admin_hierarchy.admin_creation_remaining(db, dbadmin, settings)
            if settings is not None
            else 0
        ),
    )


@router.post("/admin/token", response_model=Token)
def admin_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Authenticate an admin and issue a token."""
    client_ip = get_client_ip(request) or "Unknown"

    authenticated_admin = validate_admin(db, form_data.username, form_data.password)
    if not authenticated_admin:
        report.login(form_data.username, client_ip, False)
        AuditLogService.log(
            db,
            form_data.username,
            "auth.login",
            "admin",
            f"Failed login attempt for admin {form_data.username}",
            target_name=form_data.username,
            request=request,
            status=AuditStatus.failed,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if client_ip not in LOGIN_NOTIFY_WHITE_LIST:
        report.login(form_data.username, client_ip, True)

    dbadmin = crud.get_admin(db, authenticated_admin.username)
    AuditLogService.log(
        db,
        authenticated_admin,
        "auth.login",
        "admin",
        f"Admin {authenticated_admin.username} logged in",
        target_id=dbadmin.id if dbadmin is not None else None,
        target_name=authenticated_admin.username,
        request=request,
    )
    return Token(
        access_token=create_admin_token(
            authenticated_admin.username,
            authenticated_admin.is_sudo,
        )
    )


@router.post("/admin/logout")
def admin_logout(
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Record a client-side logout before its token is discarded."""
    AuditLogService.log(
        db,
        admin,
        "auth.logout",
        "admin",
        f"Admin {admin.username} logged out",
        target_name=admin.username,
        request=request,
    )
    return {"detail": "Logout recorded"}


@router.post(
    "/admin",
    response_model=Admin,
    responses={403: responses._403, 409: responses._409},
)
def create_admin(
    request: Request,
    new_admin: AdminCreate,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Create a new admin if the current admin has sudo privileges."""
    if new_admin.is_sudo:
        raise HTTPException(
            status_code=403,
            detail=(
                "Owner can only be selected from the server. Use "
                "marzban set-owner <username>, or on older scripts: "
                "marzban cli admin set-owner --username <username>"
            ),
        )
    try:
        dbadmin = crud.create_admin(db, new_admin)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Admin already exists")

    AuditLogService.log(
        db,
        admin,
        "admin.create",
        "admin",
        f"Admin {admin.username} created admin {dbadmin.username}",
        target_id=dbadmin.id,
        target_name=dbadmin.username,
        new_value=admin_audit_state(dbadmin),
        request=request,
    )
    return dbadmin


@router.put(
    "/admin/{username}",
    response_model=Admin,
    responses={403: responses._403},
)
def modify_admin(
    request: Request,
    modified_admin: AdminModify,
    dbadmin: Admin = Depends(get_admin_by_username),
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Modify an existing admin's details."""
    if modified_admin.is_sudo is not None and modified_admin.is_sudo != dbadmin.is_sudo:
        raise HTTPException(
            status_code=403,
            detail="Owner/role cannot be changed through the legacy admin endpoint",
        )
    if (dbadmin.username != current_admin.username) and dbadmin.is_sudo:
        raise HTTPException(
            status_code=403,
            detail="You're not allowed to edit another sudoer's account. Use marzban-cli instead.",
        )

    previous_value = admin_audit_state(dbadmin)
    updated_admin = crud.update_admin(db, dbadmin, modified_admin)
    AuditLogService.log(
        db,
        current_admin,
        "admin.update",
        "admin",
        f"Admin {current_admin.username} updated admin {updated_admin.username}",
        target_id=updated_admin.id,
        target_name=updated_admin.username,
        previous_value=previous_value,
        new_value=admin_audit_state(updated_admin),
        details={"password_changed": modified_admin.password is not None},
        request=request,
    )

    return updated_admin


@router.delete(
    "/admin/{username}",
    responses={403: responses._403},
)
def remove_admin(
    request: Request,
    values: AdminDeleteRequest = Body(default=AdminDeleteRequest()),
    dbadmin: Admin = Depends(get_admin_by_username),
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(Admin.check_admin_manager),
):
    """Remove an admin from the database."""
    actor = crud.get_admin(db, current_admin.username)
    if actor is None or not admin_hierarchy.admin_in_scope(db, actor, dbadmin.id):
        raise HTTPException(status_code=403, detail="Admin is outside your scope")
    if dbadmin.id == actor.id or admin_hierarchy.role_code(dbadmin) == admin_hierarchy.OWNER:
        raise HTTPException(
            status_code=403,
            detail="Owner/self deletion is not allowed",
        )
    if dbadmin.children:
        raise HTTPException(status_code=409, detail="Only a leaf admin can be deleted")
    settings = db.get(MarzhelpAdminSettings, dbadmin.id)
    if settings and (
        int(settings.delegated_traffic or 0) > 0
        or int(settings.total_traffic or 0) > 0
        or admin_hierarchy.own_credit_spend(db, settings) > 0
    ):
        raise HTTPException(status_code=409, detail="Resolve administrator credit before deletion")
    historical_rows = sum(
        query.count()
        for query in (
            db.query(AdminCreditTransfer).filter(
                (AdminCreditTransfer.from_admin_id == dbadmin.id)
                | (AdminCreditTransfer.to_admin_id == dbadmin.id)
                | (AdminCreditTransfer.actor_admin_id == dbadmin.id)
            ),
            db.query(AdminSuspensionEvent).filter(
                (AdminSuspensionEvent.admin_id == dbadmin.id)
                | (AdminSuspensionEvent.actor_admin_id == dbadmin.id)
                | (AdminSuspensionEvent.resolved_by_admin_id == dbadmin.id)
            ),
            db.query(AdminSuspensionAdmin).filter(AdminSuspensionAdmin.admin_id == dbadmin.id),
            db.query(AdminReferralAttribution).filter(
                (AdminReferralAttribution.referred_admin_id == dbadmin.id)
                | (AdminReferralAttribution.referrer_admin_id == dbadmin.id)
                | (AdminReferralAttribution.created_by_admin_id == dbadmin.id)
                | (AdminReferralAttribution.updated_by_admin_id == dbadmin.id)
            ),
            db.query(AdminReferralEvent).filter(
                (AdminReferralEvent.actor_admin_id == dbadmin.id)
                | (AdminReferralEvent.referred_admin_id == dbadmin.id)
                | (AdminReferralEvent.previous_referrer_admin_id == dbadmin.id)
                | (AdminReferralEvent.new_referrer_admin_id == dbadmin.id)
            ),
            db.query(AdminBulkJob).filter(
                (AdminBulkJob.actor_admin_id == dbadmin.id)
                | (AdminBulkJob.target_admin_id == dbadmin.id)
            ),
            db.query(AdminUserPlan).filter(AdminUserPlan.owner_admin_id == dbadmin.id),
            db.query(AdminUserPlanVersion).filter(AdminUserPlanVersion.created_by_admin_id == dbadmin.id),
            db.query(UserPlanAssignment).filter(UserPlanAssignment.actor_admin_id == dbadmin.id),
        )
    )
    if historical_rows:
        raise HTTPException(
            status_code=409,
            detail="Administrator has immutable history; suspend the account instead",
        )

    target_id = dbadmin.id
    target_name = dbadmin.username
    previous_value = admin_audit_state(dbadmin)
    affected_users = crud.remove_admin(db, dbadmin, values.strategy)
    startup_config = xray.config.include_db_users()
    if xray.core.started:
        xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)
    AuditLogService.log(
        db,
        current_admin,
        "admin.delete",
        "admin",
        f"Admin {current_admin.username} deleted admin {target_name}",
        target_id=target_id,
        target_name=target_name,
        previous_value=previous_value,
        details={"strategy": values.strategy, "affected_users": affected_users},
        request=request,
    )
    return {"detail": "Admin removed successfully"}


@router.get("/admin", response_model=Admin)
def get_current_admin(admin: Admin = Depends(Admin.get_current)):
    """Retrieve the current authenticated admin."""
    return admin


@router.get("/admin/capabilities", response_model=AdminCapabilities)
def get_admin_capabilities(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Return effective inbound, device-limit, and weighted-capacity rules."""

    dbadmin = crud.get_admin(db, admin.username)
    hierarchy_on = admin_hierarchy.hierarchy_enabled(db)
    if admin.is_sudo or (dbadmin is not None and admin_hierarchy.is_owner(db, dbadmin)):
        return AdminCapabilities(
            hierarchy_enabled=hierarchy_on,
            allowed_subscription_modes=list(SubscriptionMode),
            view_full_client_ip=True,
            can_manage_admins=True,
            can_create_admins=True,
            can_delegate_admin_creation=True,
            admin_creation_remaining=None,
            allowed_child_roles=[admin_hierarchy.ADMIN],
            allowed_child_billing_modes=admin_hierarchy.allowed_child_billing_modes(db, dbadmin),
            allowed_child_user_creation_modes=[admin_hierarchy.PLAN_ONLY, admin_hierarchy.FREE_FORM],
            can_delegate_plan_management=True,
        )
    settings = db.get(MarzhelpAdminSettings, dbadmin.id)
    if settings is None:
        return AdminCapabilities(hierarchy_enabled=hierarchy_on)
    account_active = admin_hierarchy.account_status_code(db, dbadmin.id) == admin_hierarchy.ACTIVE
    used = marzhelp_policy.capacity_used(db, dbadmin.id)
    maximum = settings.device_capacity_limit
    return AdminCapabilities(
        hierarchy_enabled=hierarchy_on,
        all_inbounds=settings.all_inbounds,
        allowed_inbounds=settings.allowed_inbounds,
        all_user_limits=settings.all_user_limits,
        allowed_user_limits=settings.allowed_user_limits,
        allowed_subscription_modes=settings.allowed_subscription_modes,
        view_full_client_ip=True,
        capacity_used=used,
        capacity_limit=maximum,
        capacity_remaining=(max(int(maximum) - used, 0) if maximum is not None else None),
        quota=AdminQuotaSummary.model_validate(
            marzhelp_policy.quota_summary(db, dbadmin.id)
        ),
        can_manage_admins=account_active and admin_hierarchy.can_manage_children(db, dbadmin),
        can_create_admins=account_active and bool(settings.can_create_admins),
        can_delegate_admin_creation=account_active and bool(settings.can_delegate_admin_creation),
        can_create_allocated_children=bool(settings.can_create_allocated_children),
        admin_creation_limit=settings.admin_creation_limit,
        admin_creations_used=int(settings.admin_creations_used or 0),
        delegated_admin_creation_limit=int(settings.delegated_admin_creation_limit or 0),
        admin_creation_remaining=admin_hierarchy.admin_creation_remaining(db, dbadmin, settings),
        allowed_child_roles=admin_hierarchy.allowed_child_roles(dbadmin),
        allowed_child_billing_modes=admin_hierarchy.allowed_child_billing_modes(
            db, dbadmin, settings
        ),
        allowed_child_user_creation_modes=(
            [admin_hierarchy.PLAN_ONLY, admin_hierarchy.FREE_FORM]
            if settings.user_creation_mode_id == admin_hierarchy.USER_CREATION_MODE_IDS[admin_hierarchy.FREE_FORM]
            else [admin_hierarchy.PLAN_ONLY]
        ),
        can_delegate_plan_management=account_active and bool(settings.can_manage_plans),
    )


@router.get(
    "/admins",
    response_model=List[Admin],
    responses={403: responses._403},
)
def get_admins(
    offset: Optional[int] = None,
    limit: Optional[int] = None,
    username: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_admin_manager),
):
    """Fetch a list of admins with optional filters for pagination and username."""
    actor = crud.get_admin(db, admin.username)
    scope_admin_id = (
        actor.id
        if actor is not None
        and admin_hierarchy.hierarchy_enabled(db)
        and not admin_hierarchy.is_owner(db, actor)
        else None
    )
    return crud.get_admins(db, offset, limit, username, scope_admin_id=scope_admin_id)


@router.get(
    "/admin-management",
    response_model=ManagedAdminList,
    responses={403: responses._403},
)
def get_managed_admins(
    offset: int = 0,
    limit: int = 20,
    username: Optional[str] = None,
    role: Optional[str] = None,
    billing_mode: Optional[BillingMode] = None,
    account_status: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_admin_manager),
):
    """Return a stable, paginated view of admins and their MarzHelp limits."""
    limit = max(1, min(limit, 100))
    offset = max(offset, 0)
    actor = crud.get_admin(db, admin.username)
    scope_admin_id = (
        actor.id
        if actor is not None
        and admin_hierarchy.hierarchy_enabled(db)
        and not admin_hierarchy.is_owner(db, actor)
        else None
    )
    dbadmins, total = crud.get_admins_with_count(
        db,
        offset,
        limit,
        username,
        scope_admin_id=scope_admin_id,
        role=role,
        billing_mode=billing_mode.value if billing_mode else None,
        account_status=account_status,
    )
    settings_by_admin = (
        {
            row.admin_id: row
            for row in db.query(MarzhelpAdminSettings)
            .filter(MarzhelpAdminSettings.admin_id.in_([item.id for item in dbadmins]))
            .all()
        }
        if dbadmins
        else {}
    )
    quota_by_admin = {
        admin_id: AdminQuotaSummary.model_validate(summary)
        for admin_id, summary in marzhelp_policy.quota_summaries(
            db,
            [item.id for item in dbadmins],
            settings_by_admin,
        ).items()
    }
    capacity_weight = case(
        (User.concurrent_user_limit.is_(None), 1),
        (User.concurrent_user_limit < 1, 1),
        else_=User.concurrent_user_limit,
    )
    capacity_usage = (
        dict(
            db.query(User.admin_id, func.coalesce(func.sum(capacity_weight), 0))
            .filter(User.admin_id.in_([item.id for item in dbadmins]))
            .group_by(User.admin_id)
            .all()
        )
        if dbadmins
        else {}
    )
    category_ids_by_admin = admin_plans.admin_category_ids_map(
        db,
        [item.id for item in dbadmins],
    )
    plan_prices_by_admin = {item.id: [] for item in dbadmins}
    if dbadmins:
        for row in (
            db.query(AdminUserPlanPrice)
            .filter(AdminUserPlanPrice.admin_id.in_([item.id for item in dbadmins]))
            .order_by(AdminUserPlanPrice.admin_id, AdminUserPlanPrice.plan_id)
            .all()
        ):
            plan_prices_by_admin[row.admin_id].append(
                {"plan_id": row.plan_id, "price_toman": int(row.price_toman)}
            )
    parent_ids = {item.parent_admin_id for item in dbadmins if item.parent_admin_id is not None}
    parent_names = (
        dict(db.query(DBAdmin.id, DBAdmin.username).filter(DBAdmin.id.in_(parent_ids)).all())
        if parent_ids
        else {}
    )
    return ManagedAdminList(
        admins=[
            managed_admin_response(
                db,
                item,
                settings_by_admin.get(item.id),
                quota_by_admin[item.id].current_users,
                int(capacity_usage.get(item.id, 0)),
                quota_by_admin[item.id],
                category_ids_by_admin[item.id],
                plan_prices_by_admin[item.id],
                parent_names.get(item.parent_admin_id),
            )
            for item in dbadmins
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/admin-management/repair-orphans")
def repair_orphaned_users(
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    valid_admin_ids = db.query(DBAdmin.id)
    orphaned = (
        db.query(User)
        .filter(
            User.admin_id.is_not(None),
            ~User.admin_id.in_(valid_admin_ids),
        )
        .with_for_update()
        .all()
    )
    for dbuser in orphaned:
        dbuser.admin_id = None
        dbuser.admin = None
    db.commit()
    AuditLogService.log(
        db,
        admin,
        "admin.orphan_repair",
        "users",
        f"Admin {admin.username} repaired {len(orphaned)} orphaned users",
        details={"count": len(orphaned)},
        request=request,
    )
    return {"repaired": len(orphaned)}


@router.post(
    "/admin-management",
    response_model=ManagedAdmin,
    responses={403: responses._403, 409: responses._409},
)
def create_managed_admin(
    request: Request,
    new_admin: ManagedAdminCreate,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_admin_manager),
):
    """Create an admin and its MarzHelp policy in one transaction."""
    if new_admin.is_sudo:
        raise HTTPException(
            status_code=403,
            detail=(
                "Owner can only be selected from the server. Use "
                "marzban set-owner <username>, or on older scripts: "
                "marzban cli admin set-owner --username <username>"
            ),
        )
    require_initialized_admin_hierarchy(db)
    actor = crud.get_admin(db, admin.username)
    requested_mode = new_admin.policy.billing_mode
    hierarchy_on = actor is not None and admin_hierarchy.hierarchy_enabled(db)
    if hierarchy_on and requested_mode == BillingMode.LEGACY_COMPAT:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "billing_mode_required",
                "message": "Select a commercial billing mode for the new Admin",
            },
        )
    try:
        dbadmin = crud.create_admin(db, new_admin, commit=False)
        policy = new_admin.policy
        if hierarchy_on:
            policy = policy.model_copy(
                update={
                    "money_billing_enabled": True,
                    "money_balance_toman": 0,
                }
            )
            if requested_mode == BillingMode.USED_TRAFFIC:
                money_billing.validate_child_usage_price(
                    db,
                    parent=actor,
                    child_price_per_gib_toman=policy.used_traffic_price_per_gib_toman,
                )
        initial_credit = None if hierarchy_on and requested_mode in {
            BillingMode.USED_TRAFFIC,
            BillingMode.ALLOCATED_TRAFFIC,
            BillingMode.USER_CREDIT,
        } else (
            policy.device_capacity_limit
            if requested_mode == BillingMode.SEAT_CREDIT
            else policy.max_users
            if requested_mode == BillingMode.USER_CREDIT
            else policy.total_traffic
        ) if hierarchy_on else None
        if (
            hierarchy_on
            and requested_mode not in {
                BillingMode.USED_TRAFFIC,
                BillingMode.ALLOCATED_TRAFFIC,
                BillingMode.USER_CREDIT,
            }
            and actor is not None
            and not admin_hierarchy.is_owner(db, actor)
        ):
            parent_settings = db.get(MarzhelpAdminSettings, actor.id)
            parent_available = (
                admin_hierarchy.available_credit(db, parent_settings)
                if parent_settings is not None
                else 0
            )
            if initial_credit is None and parent_available is not None:
                raise admin_hierarchy.HierarchyError(
                    "unlimited_child_credit_forbidden",
                    "A finite parent cannot create an unlimited-credit child",
                )
        initial_transfer = None
        initial_money_result = None
        if hierarchy_on:
            policy = policy.model_copy(update={
                "total_traffic": (
                    None
                    if requested_mode in {BillingMode.USED_TRAFFIC, BillingMode.ALLOCATED_TRAFFIC}
                    else policy.total_traffic
                ),
                "device_capacity_limit": (
                    0 if requested_mode == BillingMode.SEAT_CREDIT else policy.device_capacity_limit
                ),
                "max_users": None if requested_mode == BillingMode.USER_CREDIT else policy.max_users,
            })
        settings = crud.upsert_marzhelp_admin_policy(
            db, dbadmin.id, policy, commit=False
        )
        if not hierarchy_on:
            settings.billing_mode = requested_mode.value
        if hierarchy_on:
            admin_hierarchy.configure_new_child_admin_creation(
                db,
                actor=actor,
                parent=actor,
                child=dbadmin,
                child_settings=settings,
                child_role=new_admin.role or admin_hierarchy.ADMIN,
                child_billing_mode=requested_mode,
                can_create_admins=new_admin.can_create_admins,
                can_delegate_admin_creation=new_admin.can_delegate_admin_creation,
                can_create_allocated_children=new_admin.can_create_allocated_children,
                admin_creation_limit=new_admin.admin_creation_limit,
            )
            admin_hierarchy.attach_new_child(
                db,
                actor=actor,
                parent=actor,
                child=dbadmin,
                child_role=new_admin.role or admin_hierarchy.ADMIN,
                commit=False,
            )
            if new_admin.plan_prices:
                money_billing.replace_child_plan_prices(
                    db,
                    parent=actor,
                    child=dbadmin,
                    prices=new_admin.plan_prices,
                )
            admin_hierarchy.configure_child_user_creation_access(
                db,
                actor=actor,
                parent=actor,
                child_settings=settings,
                mode=(
                    admin_hierarchy.PLAN_ONLY
                    if requested_mode == BillingMode.USER_CREDIT
                    else new_admin.user_creation_mode
                ),
                can_manage_plans=False,
            )
            if new_admin.initial_money_credit_toman:
                initial_money_result, _ = money_billing.transfer_money(
                    db,
                    actor=actor,
                    parent=actor,
                    child=dbadmin,
                    amount_toman=new_admin.initial_money_credit_toman,
                    operation_type="grant",
                    idempotency_key=f"admin-create-{dbadmin.id}-money-credit",
                    note="Initial admin money credit",
                )
                AuditLogService.log(
                    db,
                    actor,
                    "money.grant",
                    "admin_money",
                    f"Admin {actor.username} granted initial money credit to {dbadmin.username}",
                    target_id=dbadmin.id,
                    target_name=dbadmin.username,
                    new_value={"money_balance_toman": initial_money_result["target_balance_toman"]},
                    details={
                        "amount_toman": new_admin.initial_money_credit_toman,
                        "idempotency_key": f"admin-create-{dbadmin.id}-money-credit",
                    },
                    request=request,
                    commit=False,
                )
            if initial_credit:
                initial_transfer, _ = admin_hierarchy.transfer_credit(
                    db,
                    actor=actor,
                    source=actor,
                    target=dbadmin,
                    amount=int(initial_credit),
                    operation_type="grant",
                    idempotency_key=f"admin-create-{dbadmin.id}-traffic-credit",
                    note="Initial admin traffic credit",
                    commit=False,
                    return_created=True,
                )
                AuditLogService.log(
                    db,
                    actor,
                    "credit.grant",
                    "admin_credit",
                    f"Admin {actor.username} granted initial traffic credit to {dbadmin.username}",
                    target_id=dbadmin.id,
                    target_name=dbadmin.username,
                    previous_value={
                        "traffic_credit": initial_transfer.balance_before,
                        "source_delegated": initial_transfer.source_delegated_before,
                    },
                    new_value={
                        "traffic_credit": initial_transfer.balance_after,
                        "source_delegated": initial_transfer.source_delegated_after,
                    },
                    details={
                        "resource": initial_transfer.resource,
                        "transfer_id": initial_transfer.id,
                        "delta": initial_transfer.delta,
                        "actor_admin_id": actor.id,
                        "adjusted_admin_id": dbadmin.id,
                        "reason": initial_transfer.note,
                        "idempotency_key": initial_transfer.idempotency_key,
                    },
                    request=request,
                    commit=False,
                )
            db.add(
                MarzhelpAccountingTransaction(
                    operation_key=f"admin-create-billing-mode:{dbadmin.id}",
                    operation_type="billing_mode",
                    admin_id=dbadmin.id,
                    result="consumed",
                    details={
                        "previous_mode": None,
                        "mode": requested_mode.value,
                        "reason": "Initial Admin billing mode",
                        "actor_admin_id": actor.id,
                    },
                )
            )
        AuditLogService.log(
            db,
            admin,
            "admin.create",
            "admin",
            f"Admin {admin.username} created managed admin {dbadmin.username}",
            target_id=dbadmin.id,
            target_name=dbadmin.username,
            new_value=admin_audit_state(
                dbadmin,
                MarzhelpAdminPolicy.model_validate(settings),
            ),
            details={
                "initial_credit_transfer_id": initial_transfer.id if initial_transfer else None,
                "initial_money_balance_toman": (
                    initial_money_result["target_balance_toman"] if initial_money_result else None
                ),
            },
            request=request,
            commit=False,
        )
        db.commit()
        db.refresh(dbadmin)
        db.refresh(settings)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Admin already exists")
    except admin_hierarchy.HierarchyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})
    except Exception:
        db.rollback()
        raise
    response = managed_admin_response(db, dbadmin, settings, 0, 0)
    return response


@router.put(
    "/admin-management/{username}",
    response_model=ManagedAdmin,
    responses={403: responses._403, 404: responses._404},
)
def modify_managed_admin(
    request: Request,
    modified_admin: ManagedAdminModify,
    dbadmin: Admin = Depends(get_admin_by_username),
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(Admin.check_admin_manager),
):
    """Update an admin account and its MarzHelp policy atomically."""
    actor = crud.get_admin(db, current_admin.username)
    require_initialized_admin_hierarchy(db)
    if actor is not None and not admin_hierarchy.admin_in_scope(db, actor, dbadmin.id):
        raise HTTPException(status_code=403, detail="Admin is outside your scope")
    if modified_admin.is_sudo is not None and modified_admin.is_sudo != dbadmin.is_sudo:
        raise HTTPException(status_code=403, detail="Role cannot be changed through this endpoint")
    if (dbadmin.username != current_admin.username) and dbadmin.is_sudo:
        raise HTTPException(
            status_code=403,
            detail="You're not allowed to edit another sudoer's account. Use marzban-cli instead.",
        )

    current_settings = db.get(MarzhelpAdminSettings, dbadmin.id)
    if (
        actor is not None
        and actor.id == dbadmin.id
        and not admin_hierarchy.is_owner(db, actor)
        and current_settings is not None
    ):
        current_contract = managed_admin_response(db, dbadmin, current_settings)
        forbidden_changes = []
        fields_set = modified_admin.model_fields_set
        if (
            "policy" in fields_set
            and modified_admin.policy.model_dump(mode="json")
            != current_contract.policy.model_dump(mode="json")
        ):
            forbidden_changes.append("policy")
        for field in (
            "plan_category_ids",
            "user_creation_mode",
            "can_manage_plans",
            "can_create_admins",
            "can_delegate_admin_creation",
            "can_create_allocated_children",
            "admin_creation_limit",
            "plan_prices",
        ):
            if field not in fields_set:
                continue
            requested = getattr(modified_admin, field)
            current = getattr(current_contract, field)
            if field == "plan_category_ids":
                requested = sorted(requested or [])
                current = sorted(current or [])
            elif field == "plan_prices":
                requested = sorted(
                    (item.plan_id, item.price_toman) for item in (requested or [])
                )
                current = sorted(
                    (item.plan_id, item.price_toman) for item in (current or [])
                )
            if requested != current:
                forbidden_changes.append(field)
        if forbidden_changes:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "self_commercial_edit_forbidden",
                    "message": "Commercial and delegated access are controlled by the parent",
                    "fields": sorted(forbidden_changes),
                },
            )
        previous_value = admin_audit_state(dbadmin, current_contract.policy)
        dbadmin = crud.update_admin(db, dbadmin, modified_admin, commit=False)
        db.commit()
        db.refresh(dbadmin)
        db.refresh(current_settings)
        response = managed_admin_response(
            db,
            dbadmin,
            current_settings,
            db.query(func.count(User.id)).filter(User.admin_id == dbadmin.id).scalar() or 0,
            marzhelp_policy.capacity_used(db, dbadmin.id),
        )
        AuditLogService.log(
            db,
            current_admin,
            "admin.update",
            "admin",
            f"Admin {current_admin.username} updated managed admin {dbadmin.username}",
            target_id=dbadmin.id,
            target_name=dbadmin.username,
            previous_value=previous_value,
            new_value=admin_audit_state(dbadmin, response.policy),
            details={"password_changed": modified_admin.password is not None},
            request=request,
        )
        return response
    previous_calculation_mode = (
        current_settings.calculate_volume if current_settings is not None else None
    )
    previous_value = admin_audit_state(
        dbadmin,
        MarzhelpAdminPolicy.model_validate(current_settings)
        if current_settings is not None
        else MarzhelpAdminPolicy(),
    )
    dbadmin = crud.update_admin(db, dbadmin, modified_admin, commit=False)
    policy = modified_admin.policy
    if admin_hierarchy.hierarchy_enabled(db) and current_settings is not None:
        current_mode = BillingMode(current_settings.billing_mode or BillingMode.LEGACY_COMPAT.value)
        policy = policy.model_copy(
            update={
                "total_traffic": (
                    None
                    if current_mode in {BillingMode.USED_TRAFFIC, BillingMode.ALLOCATED_TRAFFIC}
                    else current_settings.total_traffic
                ),
                "billing_mode": current_mode,
                "money_balance_toman": int(current_settings.money_balance_toman or 0),
                "money_billing_enabled": True,
                "device_capacity_limit": (
                    current_settings.device_capacity_limit
                    if current_mode == BillingMode.SEAT_CREDIT
                    else policy.device_capacity_limit
                ),
                "max_users": (
                    None if current_mode == BillingMode.USER_CREDIT else policy.max_users
                ),
            }
        )
        if current_mode == BillingMode.USED_TRAFFIC and actor is not None and actor.id != dbadmin.id:
            money_billing.validate_child_usage_price(
                db,
                parent=actor,
                child_price_per_gib_toman=policy.used_traffic_price_per_gib_toman,
            )
            money_billing.validate_existing_usage_resale_floor(
                db,
                admin=dbadmin,
                new_price_per_gib_toman=policy.used_traffic_price_per_gib_toman,
            )
    settings = crud.upsert_marzhelp_admin_policy(db, dbadmin.id, policy, commit=False)
    if (
        actor is not None
        and admin_hierarchy.hierarchy_enabled(db)
        and (
            modified_admin.user_creation_mode is not None
            or modified_admin.can_manage_plans is not None
        )
    ):
        requested_mode = modified_admin.user_creation_mode or (
            db.query(AdminUserCreationMode.code)
            .filter(AdminUserCreationMode.id == settings.user_creation_mode_id)
            .scalar()
            or admin_hierarchy.PLAN_ONLY
        )
        requested_plan_management = (
            bool(modified_admin.can_manage_plans)
            if modified_admin.can_manage_plans is not None
            else bool(settings.can_manage_plans)
        )
        commercial_mode = BillingMode(settings.billing_mode)
        if commercial_mode == BillingMode.USED_TRAFFIC:
            money_billing.validate_child_usage_price(
                db,
                parent=actor,
                child_price_per_gib_toman=settings.used_traffic_price_per_gib_toman,
            )
        if commercial_mode == BillingMode.USER_CREDIT:
            requested_mode = admin_hierarchy.PLAN_ONLY
        requested_plan_management = False
        current_mode = (
            db.query(AdminUserCreationMode.code)
            .filter(AdminUserCreationMode.id == current_settings.user_creation_mode_id)
            .scalar()
            if current_settings is not None
            else admin_hierarchy.PLAN_ONLY
        )
        if actor.id == dbadmin.id and not admin_hierarchy.is_owner(db, actor):
            if requested_mode != current_mode or requested_plan_management != bool(current_settings.can_manage_plans):
                db.rollback()
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "self_user_creation_access_forbidden",
                        "message": "User creation access is controlled by the parent",
                    },
                )
        else:
            try:
                admin_hierarchy.configure_child_user_creation_access(
                    db,
                    actor=actor,
                    parent=actor,
                    child_settings=settings,
                    mode=requested_mode,
                    can_manage_plans=requested_plan_management,
                )
            except admin_hierarchy.HierarchyError as exc:
                db.rollback()
                raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})
    if (
        actor is not None
        and admin_hierarchy.hierarchy_enabled(db)
        and not admin_hierarchy.is_owner(db, dbadmin)
    ):
        requested_creation_policy = (
            bool(modified_admin.can_create_admins),
            bool(modified_admin.can_delegate_admin_creation),
            bool(modified_admin.can_create_allocated_children),
            modified_admin.admin_creation_limit,
        )
        current_creation_policy = (
            bool(current_settings.can_create_admins),
            bool(current_settings.can_delegate_admin_creation),
            bool(current_settings.can_create_allocated_children),
            current_settings.admin_creation_limit,
        ) if current_settings is not None else (False, False, True, 0)
        if actor.id == dbadmin.id and not admin_hierarchy.is_owner(db, actor):
            if requested_creation_policy != current_creation_policy:
                db.rollback()
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "self_permission_change_forbidden",
                        "message": "Admin creation permissions are controlled by the parent",
                    },
                )
        else:
            try:
                settings = admin_hierarchy.update_child_admin_creation(
                    db,
                    actor=actor,
                    child=dbadmin,
                    can_create_admins=modified_admin.can_create_admins,
                    can_delegate_admin_creation=modified_admin.can_delegate_admin_creation,
                    can_create_allocated_children=modified_admin.can_create_allocated_children,
                    admin_creation_limit=modified_admin.admin_creation_limit,
                )
            except admin_hierarchy.HierarchyError as exc:
                db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail={"code": exc.code, "message": str(exc)},
                )
    if (
        previous_calculation_mode is not None
        and previous_calculation_mode != policy.calculate_volume
        and policy.calculate_volume == "created_traffic"
    ):
        settings.used_traffic = max(
            int(settings.used_traffic or 0),
            marzhelp_policy.allocated_credit_baseline(db, dbadmin.id),
        )
    if modified_admin.plan_prices is not None:
        try:
            money_billing.replace_child_plan_prices(
                db,
                parent=actor,
                child=dbadmin,
                prices=modified_admin.plan_prices,
            )
        except admin_hierarchy.HierarchyError as exc:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": str(exc)},
            )
    db.commit()
    db.refresh(dbadmin)
    db.refresh(settings)
    user_count = db.query(func.count(User.id)).filter(User.admin_id == dbadmin.id).scalar() or 0
    response = managed_admin_response(
        db,
        dbadmin,
        settings,
        user_count,
        marzhelp_policy.capacity_used(db, dbadmin.id),
    )
    AuditLogService.log(
        db,
        current_admin,
        "admin.update",
        "admin",
        f"Admin {current_admin.username} updated managed admin {dbadmin.username}",
        target_id=dbadmin.id,
        target_name=dbadmin.username,
        previous_value=previous_value,
        new_value=admin_audit_state(dbadmin, response.policy),
        details={"password_changed": modified_admin.password is not None},
        request=request,
    )
    return response


@router.post("/admin/{username}/users/disable", responses={403: responses._403, 404: responses._404})
def disable_all_active_users(
    request: Request,
    dbadmin: Admin = Depends(get_admin_by_username),
    db: Session = Depends(get_db), admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Disable all active users under a specific admin"""
    usernames = [
        row[0]
        for row in db.query(User.username)
        .filter(
            User.admin_id == dbadmin.id,
            User.status.in_((UserStatus.active, UserStatus.on_hold)),
        )
        .all()
    ]
    crud.disable_all_active_users(db=db, admin=dbadmin)
    startup_config = xray.config.include_db_users()
    xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)
    AuditLogService.log(
        db,
        admin,
        "bulk.deactivate",
        "admin_users",
        f"Admin {admin.username} disabled {len(usernames)} users owned by {dbadmin.username}",
        target_id=dbadmin.id,
        target_name=dbadmin.username,
        details=summarize_targets(usernames),
        request=request,
    )
    return {"detail": "Users successfully disabled"}


@router.post("/admin/{username}/users/activate", responses={403: responses._403, 404: responses._404})
def activate_all_disabled_users(
    request: Request,
    dbadmin: Admin = Depends(get_admin_by_username),
    db: Session = Depends(get_db), admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Activate all disabled users under a specific admin"""
    usernames = [
        row[0]
        for row in db.query(User.username)
        .filter(
            User.admin_id == dbadmin.id,
            User.status == UserStatus.disabled,
        )
        .all()
    ]
    crud.activate_all_disabled_users(db=db, admin=dbadmin)
    startup_config = xray.config.include_db_users()
    xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)
    AuditLogService.log(
        db,
        admin,
        "bulk.activate",
        "admin_users",
        f"Admin {admin.username} activated {len(usernames)} users owned by {dbadmin.username}",
        target_id=dbadmin.id,
        target_name=dbadmin.username,
        details=summarize_targets(usernames),
        request=request,
    )
    return {"detail": "Users successfully activated"}


@router.post(
    "/admin/usage/reset/{username}",
    response_model=Admin,
    responses={403: responses._403},
)
def reset_admin_usage(
    request: Request,
    dbadmin: Admin = Depends(get_admin_by_username),
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Resets usage of admin."""
    previous_value = {"users_usage": dbadmin.users_usage}
    updated_admin = crud.reset_admin_usage(db, dbadmin)
    AuditLogService.log(
        db,
        current_admin,
        "admin.usage_reset",
        "admin",
        f"Admin {current_admin.username} reset usage for admin {dbadmin.username}",
        target_id=dbadmin.id,
        target_name=dbadmin.username,
        previous_value=previous_value,
        new_value={"users_usage": updated_admin.users_usage},
        request=request,
    )
    return updated_admin


@router.get(
    "/admin/usage/{username}",
    response_model=int,
    responses={403: responses._403},
)
def get_admin_usage(
    dbadmin: Admin = Depends(get_admin_by_username),
    current_admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Retrieve the usage of given admin."""
    return dbadmin.users_usage
