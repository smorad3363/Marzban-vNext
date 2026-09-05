from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
)
from sqlalchemy.exc import IntegrityError

from app import logger, xray
from app.db import Session, crud, get_db
from app.db.models import Admin as DBAdmin, AdminHierarchy, MarzhelpAdminSettings
from app.dependencies import get_expired_users_list, get_validated_user, validate_dates
from app.models.admin import Admin
from app.models.user import (
    BulkUserActionRequest,
    BulkUserActionResponse,
    BulkUserOperation,
    UserCreate,
    UserModify,
    UserResponse,
    UsersResponse,
    UserStatus,
    UsersUsagesResponse,
    UserUsagesResponse,
)
from app.utils import (
    admin_billing,
    admin_hierarchy,
    admin_plans,
    marzhelp_policy,
    money_billing,
    owner_pricing,
    report,
    responses,
)
from app.utils.audit import (
    AuditLogService,
    changed_fields,
    classify_user_change,
    summarize_targets,
    user_audit_state,
)

router = APIRouter(tags=["User"], prefix="/api", responses={401: responses._401})


@router.post("/user", response_model=UserResponse, responses={400: responses._400, 409: responses._409})
def add_user(
    request: Request,
    new_user: UserCreate,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Add a new user

    - **username**: 3 to 32 characters, can include a-z, 0-9, and underscores.
    - **status**: User's status, defaults to `active`. Special rules if `on_hold`.
    - **expire**: UTC timestamp for account expiration. Use `0` for unlimited.
    - **data_limit**: Max data usage in bytes (e.g., `1073741824` for 1GB). `0` means unlimited.
    - **data_limit_reset_strategy**: Defines how/if data limit resets. `no_reset` means it never resets.
    - **proxies**: Dictionary of protocol settings (e.g., `vmess`, `vless`).
    - **inbounds**: Dictionary of protocol tags to specify inbound connections.
    - **note**: Optional text field for additional user information or notes.
    - **on_hold_timeout**: UTC timestamp when `on_hold` status should start or end.
    - **on_hold_expire_duration**: Duration (in seconds) for how long the user should stay in `on_hold` status.
    - **next_plan**: Next user plan (resets after use).
    """

    # TODO expire should be datetime instead of timestamp

    dbadmin = crud.get_admin(db, admin.username)
    form_cost_toman = 0
    if dbadmin is not None and admin_hierarchy.hierarchy_enabled(db):
        settings = db.get(MarzhelpAdminSettings, dbadmin.id)
        billing_mode = admin_billing.billing_mode(settings) if settings is not None else None
        if (
            billing_mode == admin_billing.BillingMode.USER_CREDIT
            or not admin_hierarchy.allows_form_creation(settings)
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "plan_only", "message": "Use /api/users/from-plan for this administrator"},
            )
        if settings is not None:
            new_user = marzhelp_policy.restricted_create_payload(settings, new_user)
            if billing_mode in {
                admin_billing.BillingMode.USED_TRAFFIC,
                admin_billing.BillingMode.ALLOCATED_TRAFFIC,
            }:
                try:
                    form_cost_toman = owner_pricing.form_price(
                        db,
                        settings,
                        data_limit=new_user.data_limit or None,
                        expire=new_user.expire or None,
                    )
                except admin_hierarchy.HierarchyError as exc:
                    raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})

    if not new_user.proxies:
        raise HTTPException(
            status_code=422,
            detail={"proxies": "Each user needs at least one proxy"},
        )

    for proxy_type in new_user.proxies:
        if not xray.config.inbounds_by_protocol.get(proxy_type):
            raise HTTPException(
                status_code=400,
                detail=f"Protocol {proxy_type} is disabled on your server",
            )

    try:
        dbuser = crud.create_user(db, new_user, admin=dbadmin, commit=False)
        if dbadmin is not None:
            money_billing.charge_form_purchase(
                db,
                buyer=dbadmin,
                actor=dbadmin,
                user_id=dbuser.id,
                amount_toman=form_cost_toman,
            )
        db.commit()
        db.refresh(dbuser)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="User already exists")
    except admin_hierarchy.HierarchyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})

    bg.add_task(xray.operations.add_user_by_id, user_id=dbuser.id)
    user = admin_plans.scoped_user_response(db, dbuser, actor=dbadmin or admin)
    report.user_created(user=user, user_id=dbuser.id, by=admin, user_admin=dbuser.admin)
    AuditLogService.log(
        db,
        admin,
        "user.create",
        "user",
        f'Admin {admin.username} created user {dbuser.username}',
        target_id=dbuser.id,
        target_name=dbuser.username,
        new_value=user_audit_state(dbuser),
        request=request,
    )
    logger.info(f'New user "{dbuser.username}" added')
    return user


@router.get("/user/{username}", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
def get_user(
    db: Session = Depends(get_db),
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Get user information"""
    return admin_plans.scoped_user_response(
        db, dbuser, actor=crud.get_admin(db, admin.username) or admin
    )


@router.put("/user/{username}", response_model=UserResponse, responses={400: responses._400, 403: responses._403, 404: responses._404})
def modify_user(
    request: Request,
    modified_user: UserModify,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    dbuser: UsersResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Modify an existing user

    - **username**: Cannot be changed. Used to identify the user.
    - **status**: User's new status. Can be 'active', 'disabled', 'on_hold', 'limited', or 'expired'.
    - **expire**: UTC timestamp for new account expiration. Set to `0` for unlimited, `null` for no change.
    - **data_limit**: New max data usage in bytes (e.g., `1073741824` for 1GB). Set to `0` for unlimited, `null` for no change.
    - **data_limit_reset_strategy**: New strategy for data limit reset. Options include 'daily', 'weekly', 'monthly', or 'no_reset'.
    - **proxies**: Dictionary of new protocol settings (e.g., `vmess`, `vless`). Empty dictionary means no change.
    - **inbounds**: Dictionary of new protocol tags to specify inbound connections. Empty dictionary means no change.
    - **note**: New optional text for additional user information or notes. `null` means no change.
    - **on_hold_timeout**: New UTC timestamp for when `on_hold` status should start or end. Only applicable if status is changed to 'on_hold'.
    - **on_hold_expire_duration**: New duration (in seconds) for how long the user should stay in `on_hold` status. Only applicable if status is changed to 'on_hold'.
    - **next_plan**: Next user plan (resets after use).

    Note: Fields set to `null` or omitted will not be modified.
    """

    for proxy_type in modified_user.proxies:
        if not xray.config.inbounds_by_protocol.get(proxy_type):
            raise HTTPException(
                status_code=400,
                detail=f"Protocol {proxy_type} is disabled on your server",
            )

    previous_value = user_audit_state(dbuser)
    old_status = dbuser.status
    dbactor = crud.get_admin(db, admin.username) or admin
    try:
        dbuser = crud.update_user(db, dbuser, modified_user, actor=dbactor)
    except admin_hierarchy.HierarchyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc
    user = admin_plans.scoped_user_response(db, dbuser, actor=dbactor)
    new_value = user_audit_state(dbuser)
    audit_action = classify_user_change(previous_value, new_value)

    if user.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.update_user_by_id, user_id=dbuser.id)
    elif user.status == UserStatus.disabled and old_status != UserStatus.disabled:
        # HandlerService removes credentials for new connections only.
        # Reloading the cores also terminates already-established streams.
        bg.add_task(xray.operations.restart_all_cores)
    else:
        bg.add_task(xray.operations.remove_user_by_id, user_id=dbuser.id)

    bg.add_task(report.user_updated, user=user, user_admin=dbuser.admin, by=admin)

    logger.info(f'User "{user.username}" modified')
    AuditLogService.log(
        db,
        admin,
        audit_action,
        "user",
        f'Admin {admin.username} modified user {user.username}',
        target_id=dbuser.id,
        target_name=user.username,
        previous_value=previous_value,
        new_value=new_value,
        details={
            "changed_fields": changed_fields(
                previous_value,
                new_value,
            )
        },
        request=request,
    )

    if user.status != old_status:
        bg.add_task(
            report.status_change,
            username=user.username,
            status=user.status,
            user=user,
            user_admin=dbuser.admin,
            by=admin,
        )
        logger.info(
            f'User "{dbuser.username}" status changed from {old_status} to {user.status}'
        )

    return user


@router.delete("/user/{username}", responses={403: responses._403, 404: responses._404})
def remove_user(
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Remove a user"""
    previous_value = user_audit_state(dbuser)
    user_id = dbuser.id
    username = dbuser.username
    crud.remove_user(db, dbuser)
    bg.add_task(xray.operations.remove_user, dbuser=dbuser)

    bg.add_task(
        report.user_deleted, username=dbuser.username, user_admin=Admin.model_validate(dbuser.admin), by=admin
    )
    AuditLogService.log(
        db,
        admin,
        "user.delete",
        "user",
        f'Admin {admin.username} deleted user {username}',
        target_id=user_id,
        target_name=username,
        previous_value=previous_value,
        request=request,
    )

    logger.info(f'User "{dbuser.username}" deleted')
    return {"detail": "User successfully deleted"}


@router.post("/user/{username}/reset", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
def reset_user_data_usage(
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Reset user data usage"""
    previous_value = user_audit_state(dbuser)
    dbuser = crud.reset_user_data_usage(db=db, dbuser=dbuser)
    if dbuser.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.add_user, dbuser=dbuser)

    user = admin_plans.scoped_user_response(
        db, dbuser, actor=crud.get_admin(db, admin.username) or admin
    )
    bg.add_task(
        report.user_data_usage_reset, user=user, user_admin=dbuser.admin, by=admin
    )
    AuditLogService.log(
        db,
        admin,
        "user.traffic_reset",
        "user",
        f'Admin {admin.username} reset traffic for user {dbuser.username}',
        target_id=dbuser.id,
        target_name=dbuser.username,
        previous_value=previous_value,
        new_value=user_audit_state(dbuser),
        request=request,
    )

    logger.info(f'User "{dbuser.username}"\'s usage was reset')
    return user


@router.post("/user/{username}/revoke_sub", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
def revoke_user_subscription(
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Revoke users subscription (Subscription link and proxies)"""
    dbuser = crud.revoke_user_sub(db=db, dbuser=dbuser)

    if dbuser.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.update_user, dbuser=dbuser)
    user = admin_plans.scoped_user_response(
        db, dbuser, actor=crud.get_admin(db, admin.username) or admin
    )
    bg.add_task(
        report.user_subscription_revoked, user=user, user_admin=dbuser.admin, by=admin
    )
    AuditLogService.log(
        db,
        admin,
        "user.subscription_revoke",
        "user",
        f'Admin {admin.username} revoked the subscription for user {dbuser.username}',
        target_id=dbuser.id,
        target_name=dbuser.username,
        request=request,
    )

    logger.info(f'User "{dbuser.username}" subscription revoked')

    return user


@router.get("/users", response_model=UsersResponse, responses={400: responses._400, 403: responses._403, 404: responses._404})
def get_users(
    offset: int = 0,
    limit: int = 10,
    username: List[str] = Query(None),
    search: Union[str, None] = None,
    owner: Union[List[str], None] = Query(None, alias="admin"),
    status: UserStatus = None,
    sort: str = None,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Get all users"""
    if limit not in {10, 25, 50}:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "pagination_size_invalid",
                "message": "Page size must be one of 10, 25, or 50",
            },
        )
    if offset < 0 or offset % limit != 0:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "pagination_offset_invalid",
                "message": "Offset must be non-negative and aligned to page size",
            },
        )
    if sort is not None:
        opts = sort.strip(",").split(",")
        sort = []
        for opt in opts:
            try:
                sort.append(crud.UsersSortingOptions[opt])
            except KeyError:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "sort_option_invalid",
                        "message": f'"{opt}" is not a valid sort option',
                    },
                )

    dbadmin = crud.get_admin(db, admin.username)
    hierarchy_on = dbadmin is not None and admin_hierarchy.hierarchy_enabled(db)
    owner_role = dbadmin is not None and admin_hierarchy.is_owner(db, dbadmin)
    if hierarchy_on or admin.is_sudo:
        selected_admins = owner
    else:
        selected_admins = (
            [admin.username]
            if owner is None or admin.username in owner
            else []
        )
    users, count = crud.get_users(
        db=db,
        offset=offset,
        limit=limit,
        search=search,
        usernames=username,
        status=status,
        sort=sort,
        admins=selected_admins,
        scope_admin_id=(dbadmin.id if hierarchy_on and not owner_role else None),
        allowed_inbounds=marzhelp_policy.allowed_inbound_tags(
            db, crud.get_admin(db, admin.username) or admin
        ),
        return_with_count=True,
    )

    page = offset // limit + 1
    return {
        "users": admin_plans.scoped_user_responses(
            db, users, actor=dbadmin or admin
        ),
        "total": count,
        "page": page,
        "page_size": limit,
        "pages": (count + limit - 1) // limit,
    }


@router.post(
    "/users/bulk",
    response_model=BulkUserActionResponse,
    responses={
        400: responses._400,
        403: responses._403,
        404: responses._404,
    },
)
def bulk_user_action(
    request: Request,
    payload: BulkUserActionRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Apply one validated operation to a selected set of users."""

    usernames = list(dict.fromkeys(payload.usernames))
    dbusers = crud.get_users(db=db, usernames=usernames)
    users_by_username = {user.username: user for user in dbusers}
    missing = [username for username in usernames if username not in users_by_username]
    if missing:
        raise HTTPException(status_code=404, detail={"missing_users": missing})

    effective_admin = crud.get_admin(db, admin.username) or admin
    forbidden = [
        user.username
        for user in dbusers
        if not admin_hierarchy.can_access_user(db, effective_admin, user)
    ]
    if forbidden:
        raise HTTPException(status_code=403, detail={"forbidden_users": forbidden})

    raise HTTPException(
        status_code=409,
        detail={
            "code": "bulk_scope_required",
            "message": (
                "Legacy username-list bulk execution is disabled; create a scoped "
                "persistent job through /api/users/bulk/jobs"
            ),
        },
    )

    ordered_users = [users_by_username[username] for username in usernames]

    if payload.operation == BulkUserOperation.delete:
        crud.remove_users(db, ordered_users)
        for dbuser in ordered_users:
            bg.add_task(xray.operations.remove_user, dbuser=dbuser)
            bg.add_task(
                report.user_deleted,
                username=dbuser.username,
                user_admin=dbuser.admin,
                by=admin,
            )
            logger.info(f'User "{dbuser.username}" deleted by bulk action')
        AuditLogService.log(
            db,
            admin,
            "bulk.delete",
            "user",
            f"Admin {admin.username} deleted {len(usernames)} users in a bulk action",
            details={
                "operation": payload.operation,
                **summarize_targets(usernames),
            },
            request=request,
        )
        return BulkUserActionResponse(operation=payload.operation, updated=usernames)

    updated = []
    skipped = []
    updated_users = []

    try:
        for dbuser in ordered_users:
            changes = {}

            if payload.operation == BulkUserOperation.activate:
                marzhelp_policy.validate_activation(db, dbuser)
                changes["status"] = UserStatus.active
            elif payload.operation == BulkUserOperation.deactivate:
                changes["status"] = UserStatus.disabled
            elif payload.operation in (
                BulkUserOperation.add_data,
                BulkUserOperation.subtract_data,
            ):
                if dbuser.data_limit is None:
                    skipped.append(dbuser.username)
                    continue
                delta = payload.amount or 0
                changes["data_limit"] = (
                    dbuser.data_limit + delta
                    if payload.operation == BulkUserOperation.add_data
                    else max(1, dbuser.data_limit - delta)
                )
            elif payload.operation in (
                BulkUserOperation.add_days,
                BulkUserOperation.subtract_days,
            ):
                if dbuser.expire is None:
                    skipped.append(dbuser.username)
                    continue
                delta = (payload.amount or 0) * 86400
                changes["expire"] = (
                    int(dbuser.expire) + delta
                    if payload.operation == BulkUserOperation.add_days
                    else max(1, int(dbuser.expire) - delta)
                )

            next_plan = None
            if dbuser.next_plan is not None:
                next_plan = {
                    "data_limit": dbuser.next_plan.data_limit,
                    "expire": dbuser.next_plan.expire,
                    "add_remaining_traffic": dbuser.next_plan.add_remaining_traffic,
                    "fire_on_either": dbuser.next_plan.fire_on_either,
                }

            old_status = dbuser.status
            modified_user = UserModify(next_plan=next_plan, **changes)
            dbuser = crud.update_user(db, dbuser, modified_user, commit=False, actor=effective_admin)
            updated.append(dbuser.username)
            updated_users.append((dbuser, old_status))

        db.commit()
    except Exception:
        db.rollback()
        raise

    if payload.operation == BulkUserOperation.deactivate and updated:
        # One reload handles the whole batch and disconnects active streams.
        bg.add_task(xray.operations.restart_all_cores)

    for dbuser, old_status in updated_users:
        db.refresh(dbuser)
        user = admin_plans.scoped_user_response(db, dbuser, actor=effective_admin)
        if user.status in [UserStatus.active, UserStatus.on_hold]:
            bg.add_task(xray.operations.update_user_by_id, user_id=dbuser.id)
        elif payload.operation != BulkUserOperation.deactivate:
            bg.add_task(xray.operations.remove_user_by_id, user_id=dbuser.id)
        bg.add_task(report.user_updated, user=user, user_admin=dbuser.admin, by=admin)
        if user.status != old_status:
            bg.add_task(
                report.status_change,
                username=user.username,
                status=user.status,
                user=user,
                user_admin=dbuser.admin,
                by=admin,
            )

    AuditLogService.log(
        db,
        admin,
        f"bulk.{payload.operation.value}",
        "user",
        (
            f"Admin {admin.username} applied {payload.operation.value} "
            f"to {len(updated)} users"
        ),
        details={
            "operation": payload.operation,
            "amount": payload.amount,
            "updated": summarize_targets(updated),
            "skipped": summarize_targets(skipped),
        },
        request=request,
    )
    return BulkUserActionResponse(
        operation=payload.operation,
        updated=updated,
        skipped=skipped,
    )


@router.post("/users/reset", responses={403: responses._403, 404: responses._404})
def reset_users_data_usage(
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Reset all users data usage"""
    dbadmin = crud.get_admin(db, admin.username)
    usernames = [
        user.username
        for user in crud.get_users(db=db, admin=dbadmin)
    ]
    crud.reset_all_users_data_usage(db=db, admin=dbadmin)
    startup_config = xray.config.include_db_users()
    xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)
    AuditLogService.log(
        db,
        admin,
        "bulk.traffic_reset",
        "user",
        f"Admin {admin.username} reset traffic for {len(usernames)} users",
        details=summarize_targets(usernames),
        request=request,
    )
    return {"detail": "Users successfully reset."}


@router.get("/user/{username}/usage", response_model=UserUsagesResponse, responses={403: responses._403, 404: responses._404})
def get_user_usage(
    dbuser: UserResponse = Depends(get_validated_user),
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Get users usage"""
    start, end = validate_dates(start, end)

    actor = crud.get_admin(db, admin.username) or admin
    usages = (
        crud.get_user_usages(db, dbuser, start, end)
        if admin_plans.usage_is_visible(db, actor)
        else None
    )

    return {"usages": usages, "username": dbuser.username}


@router.post("/user/{username}/active-next", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
def active_next_plan(
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Reset user by next plan"""
    if dbuser.next_plan is None:
        raise HTTPException(
            status_code=404,
            detail="User doesn't have next plan",
        )

    previous_value = user_audit_state(dbuser)
    dbuser = crud.reset_user_by_next(db=db, dbuser=dbuser)

    if dbuser.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.add_user, dbuser=dbuser)

    user = admin_plans.scoped_user_response(
        db, dbuser, actor=crud.get_admin(db, admin.username) or admin
    )
    bg.add_task(
        report.user_data_reset_by_next, user=user, user_admin=dbuser.admin,
    )
    AuditLogService.log(
        db,
        admin,
        "user.next_plan_activate",
        "user",
        f'Admin {admin.username} activated the next plan for user {dbuser.username}',
        target_id=dbuser.id,
        target_name=dbuser.username,
        previous_value=previous_value,
        new_value=user_audit_state(dbuser),
        request=request,
    )

    logger.info(f'User "{dbuser.username}"\'s usage was reset by next plan')
    return user


@router.get("/users/usage", response_model=UsersUsagesResponse)
def get_users_usage(
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
    owner: Union[List[str], None] = Query(None, alias="admin"),
    admin: Admin = Depends(Admin.get_current),
):
    """Get all users usage"""
    start, end = validate_dates(start, end)

    actor = crud.get_admin(db, admin.username)
    hierarchy_on = admin_hierarchy.hierarchy_enabled(db)
    if hierarchy_on and actor is not None and not admin_hierarchy.is_owner(db, actor):
        scoped_admins = [
            row[0]
            for row in db.query(DBAdmin.username)
            .join(AdminHierarchy, AdminHierarchy.descendant_id == DBAdmin.id)
            .filter(AdminHierarchy.ancestor_id == actor.id)
            .all()
        ]
        selected_admins = (
            sorted(set(owner).intersection(scoped_admins)) if owner else scoped_admins
        )
    elif not hierarchy_on and not admin.is_sudo:
        selected_admins = (
            [admin.username]
            if owner is None or admin.username in owner
            else []
        )
    else:
        selected_admins = owner
    usages = None
    if admin_plans.usage_is_visible(db, actor or admin):
        usages = crud.get_all_users_usages(
            db=db,
            start=start,
            end=end,
            admin=selected_admins,
            allowed_inbounds=marzhelp_policy.allowed_inbound_tags(
                db, actor or admin
            ),
        )

    return {"usages": usages}


@router.put("/user/{username}/set-owner", response_model=UserResponse)
def set_owner(
    request: Request,
    admin_username: str,
    dbuser: UserResponse = Depends(get_validated_user),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Set a new owner (admin) for a user."""
    new_admin = crud.get_admin(db, username=admin_username)
    if not new_admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    previous_owner = (
        dbuser.admin.username if dbuser.admin is not None else None
    )
    try:
        dbuser = crud.set_owner(db, dbuser, new_admin, commit=False)
        user = admin_plans.scoped_user_response(
            db, dbuser, actor=crud.get_admin(db, admin.username) or admin
        )
        AuditLogService.log(
            db,
            admin,
            "user.owner_change",
            "user",
            (
                f"Admin {admin.username} changed owner of user "
                f"{user.username} to {new_admin.username}"
            ),
            target_id=dbuser.id,
            target_name=user.username,
            previous_value={"admin": previous_owner},
            new_value={"admin": new_admin.username},
            request=request,
            commit=False,
        )
        db.commit()
        db.refresh(dbuser)
    except Exception:
        db.rollback()
        raise

    logger.info(
        f'User "{user.username}" owner successfully set to '
        f'"{new_admin.username}" by "{admin.username}"'
    )

    return user


@router.get("/users/expired", response_model=List[str])
def get_expired_users(
    expired_after: Optional[datetime] = Query(None, example="2024-01-01T00:00:00"),
    expired_before: Optional[datetime] = Query(None, example="2024-01-31T23:59:59"),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Get users who have expired within the specified date range.

    - **expired_after** UTC datetime (optional)
    - **expired_before** UTC datetime (optional)
    - At least one of expired_after or expired_before must be provided for filtering
    - If both are omitted, returns all expired users
    """

    expired_after, expired_before = validate_dates(expired_after, expired_before)

    expired_users = get_expired_users_list(db, admin, expired_after, expired_before)
    return [u.username for u in expired_users]


@router.delete("/users/expired", response_model=List[str])
def delete_expired_users(
    request: Request,
    bg: BackgroundTasks,
    expired_after: Optional[datetime] = Query(None, example="2024-01-01T00:00:00"),
    expired_before: Optional[datetime] = Query(None, example="2024-01-31T23:59:59"),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Delete users who have expired within the specified date range.

    - **expired_after** UTC datetime (optional)
    - **expired_before** UTC datetime (optional)
    - At least one of expired_after or expired_before must be provided
    """
    expired_after, expired_before = validate_dates(expired_after, expired_before)

    expired_users = get_expired_users_list(db, admin, expired_after, expired_before)
    removed_users = [u.username for u in expired_users]

    if not removed_users:
        raise HTTPException(
            status_code=404, detail="No expired users found in the specified date range"
        )

    crud.remove_users(db, expired_users)

    for removed_user in removed_users:
        logger.info(f'User "{removed_user}" deleted')
        bg.add_task(
            report.user_deleted,
            username=removed_user,
            user_admin=next(
                (u.admin for u in expired_users if u.username == removed_user), None
            ),
            by=admin,
        )

    AuditLogService.log(
        db,
        admin,
        "bulk.delete_expired",
        "user",
        (
            f"Admin {admin.username} deleted {len(removed_users)} "
            "expired users"
        ),
        details={
            **summarize_targets(removed_users),
            "expired_after": expired_after,
            "expired_before": expired_before,
        },
        request=request,
    )
    return removed_users
