from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import exists, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import noload

from app import xray
from app.db import Session, crud, get_db
from app.db.models import (
    AccessGroup,
    Admin as DBAdmin,
    AdminAccountStatus,
    AdminApiToken,
    AdminAuditLog,
    AdminCreditTransfer,
    AdminHierarchy,
    AdminPlanCategory,
    AdminRole,
    AdminReferralAttribution,
    AdminReferralEvent,
    AdminSuspensionEvent,
    AdminSuspensionReason,
    AdminUserCreationMode,
    AdminUserPlan,
    AllocatedTrafficRefundEvent,
    AllocatedTrafficRefundRequest,
    MarzhelpAdminSettings,
    User,
)
from app.models.admin import Admin, AdminCreate, MarzhelpAdminPolicy
from app.models.admin_hierarchy import (
    AccessGroupInput,
    AccessGroupResponse,
    AccountSummary,
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenSummary,
    AllocatedTrafficRefundCreate,
    AllocatedTrafficRefundDecision,
    AllocatedTrafficRefundEventResponse,
    AllocatedTrafficRefundResponse,
    BillingModeUpdate,
    BulkDisableRequest,
    CreditTransferRequest,
    CreditTransferResponse,
    ExternalApiPolicy,
    HierarchyAdminNode,
    HierarchyChildCreate,
    MoneyTransferRequest,
    MoneyTransferResponse,
    PlanCreate,
    PlanNetworkOption,
    PlanCategoryCreate,
    PlanCategoryResponse,
    PlanCategoryUpdate,
    PlanRenewRequest,
    PlanResponse,
    PlanSummary,
    PlanUpdate,
    PlanUserCreate,
    OwnerFreezeRequest,
    OwnerPricingResponse,
    OwnerPricingUpdate,
    OwnerUnfreezeRequest,
    ReferralAttributionRemove,
    ReferralAttributionResponse,
    ReferralAttributionUpdate,
    RenewalPolicyUpdate,
    ReparentRequest,
    SuspendRequest,
    TrialCleanupRequest,
    TrialCleanupResponse,
    TrialQuotaAdjustmentRequest,
    TrialQuotaResetRequest,
    UserCreationModeUpdate,
)
from app.models.user import UserResponse
from app.utils import (
    access_groups,
    admin_billing,
    admin_hierarchy,
    admin_plans,
    billing_service,
    marzhelp_policy,
    money_billing,
    owner_pricing,
    responses,
    trials,
)
from app.utils.audit import AuditLogService, get_client_ip


router = APIRouter(
    tags=["Admin hierarchy"],
    prefix="/api",
    responses={401: responses._401, 403: responses._403},
)


def _db_actor(db: Session, admin: Admin) -> DBAdmin:
    actor = crud.get_admin(db, admin.username)
    if actor is None:
        raise HTTPException(status_code=401, detail="Database administrator record is required")
    return actor


def _target(db: Session, username: str) -> DBAdmin:
    target = crud.get_admin(db, username)
    if target is None:
        raise HTTPException(status_code=404, detail="Admin not found")
    return target


def _raise_domain(exc: Exception):
    if isinstance(exc, admin_hierarchy.HierarchyError):
        code = 404 if exc.code.endswith("not_found") else 409 if "conflict" in exc.code or exc.code == "category_in_use" else 403
        if exc.code.startswith("invalid_") or exc.code in {
            "cycle_detected",
            "credit_exhausted",
            "reclaim_exceeds_available",
            "reclaim_exceeds_delegated",
            "reclaim_unlimited_credit",
            "credit_concurrent_conflict",
            "renewal_quota_exhausted",
            "renewal_disabled",
            "plan_archived",
            "seat_plan_requires_finite_devices",
            "seat_plan_renewal_required",
            "billing_mode_transition_requires_settlement",
            "refund_exceeds_remaining",
            "refund_exceeds_allocated_spend",
        }:
            code = 400
        raise HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)})
    raise exc


@router.put("/admin-management/{username}/billing-mode")
def update_billing_mode(
    username: str,
    values: BillingModeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        settings, created = billing_service.assign_billing_mode(
            db,
            actor=actor,
            target=target,
            mode=values.mode,
            idempotency_key=values.idempotency_key,
            reason=values.reason,
        )
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.billing_mode_assign",
        "admin_billing",
        f"Billing mode {settings.billing_mode} assigned to {target.username}",
        target_id=target.id,
        target_name=target.username,
        details={"mode": settings.billing_mode, "created": created, "reason": values.reason},
        request=request,
    )
    return {"admin_id": target.id, "billing_mode": settings.billing_mode, "created": created}


@router.post(
    "/users/{username}/allocated-traffic-refunds",
    response_model=AllocatedTrafficRefundResponse,
)
def request_allocated_traffic_refund(
    username: str,
    values: AllocatedTrafficRefundCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    user = crud.get_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        row, created = billing_service.create_refund_request(
            db,
            actor=actor,
            user=user,
            requested_refund_amount=values.requested_refund_amount,
            request_reason=values.request_reason,
            request_note=values.request_note,
            correlation_id=values.correlation_id,
            idempotency_key=values.idempotency_key,
        )
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "refund.request_create",
        "allocated_traffic_refund",
        f"Allocated-traffic refund requested for {user.username}",
        target_id=row.id,
        target_name=user.username,
        details={"created": created, "amount": row.requested_refund_amount, "correlation_id": row.correlation_id},
        request=request,
    )
    return row


@router.get(
    "/admin-management/allocated-traffic-refunds",
    response_model=list[AllocatedTrafficRefundResponse],
)
def list_allocated_traffic_refunds(
    status: str | None = Query(default=None),
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=25, ge=1, le=50),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    query = billing_service.refund_requests_query(db, actor)
    if status is not None:
        query = query.filter(AllocatedTrafficRefundRequest.status == status.upper())
    if before_id is not None:
        query = query.filter(AllocatedTrafficRefundRequest.id < before_id)
    return query.order_by(AllocatedTrafficRefundRequest.id.desc()).limit(limit).all()


def _refund_decision_endpoint(
    request_id: int,
    decision: str,
    values: AllocatedTrafficRefundDecision,
    request: Request,
    db: Session,
    admin: Admin,
):
    actor = _db_actor(db, admin)
    try:
        row, created = billing_service.decide_refund_request(
            db,
            actor=actor,
            request_id=request_id,
            decision=decision,
            idempotency_key=values.idempotency_key,
            explanation=values.explanation,
        )
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        f"refund.{decision.lower()}",
        "allocated_traffic_refund",
        f"Refund request {request_id} changed to {decision}",
        target_id=request_id,
        target_name=row.target_username,
        details={"created": created, "correlation_id": row.correlation_id},
        request=request,
    )
    return row


@router.post("/admin-management/allocated-traffic-refunds/{request_id}/approve", response_model=AllocatedTrafficRefundResponse)
def approve_allocated_traffic_refund(request_id: int, values: AllocatedTrafficRefundDecision, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(Admin.get_current)):
    return _refund_decision_endpoint(request_id, "APPROVED", values, request, db, admin)


@router.post("/admin-management/allocated-traffic-refunds/{request_id}/reject", response_model=AllocatedTrafficRefundResponse)
def reject_allocated_traffic_refund(request_id: int, values: AllocatedTrafficRefundDecision, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(Admin.get_current)):
    return _refund_decision_endpoint(request_id, "REJECTED", values, request, db, admin)


@router.post("/admin-management/allocated-traffic-refunds/{request_id}/cancel", response_model=AllocatedTrafficRefundResponse)
def cancel_allocated_traffic_refund(request_id: int, values: AllocatedTrafficRefundDecision, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(Admin.get_current)):
    return _refund_decision_endpoint(request_id, "CANCELLED", values, request, db, admin)


@router.get("/admin-management/allocated-traffic-refunds/{request_id}/events", response_model=list[AllocatedTrafficRefundEventResponse])
def allocated_traffic_refund_events(request_id: int, db: Session = Depends(get_db), admin: Admin = Depends(Admin.get_current)):
    actor = _db_actor(db, admin)
    visible = billing_service.refund_requests_query(db, actor).filter(
        AllocatedTrafficRefundRequest.id == request_id
    ).first()
    if visible is None:
        raise HTTPException(status_code=404, detail="Refund request not found")
    return db.query(AllocatedTrafficRefundEvent).filter(
        AllocatedTrafficRefundEvent.request_id == request_id
    ).order_by(AllocatedTrafficRefundEvent.id).all()


def _restart_runtime() -> None:
    startup_config = xray.config.include_db_users()
    if xray.core.started:
        xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)


def _node_response(
    db: Session,
    row: DBAdmin,
    depth: int,
    settings: MarzhelpAdminSettings | None = None,
    status: str | None = None,
    role: str | None = None,
    preloaded: bool = False,
    referral: AdminReferralAttribution | None = None,
    active_owner_freeze_event_id: int | None = None,
    quota_summary: dict | None = None,
    owner_row: bool = False,
) -> HierarchyAdminNode:
    if settings is None and not preloaded:
        settings = db.get(MarzhelpAdminSettings, row.id)
    if status is None and settings is not None and not preloaded:
        status = db.query(AdminAccountStatus.code).filter(
            AdminAccountStatus.id == settings.account_status_id
        ).scalar()
    spend = (
        int(quota_summary.get("credit_used", 0))
        if quota_summary is not None
        else admin_hierarchy.own_credit_spend(db, settings) if settings else 0
    )
    available = None
    if settings is not None and not owner_row:
        if quota_summary is not None:
            remaining = quota_summary.get("credit_remaining")
            available = (
                None
                if remaining is None
                else max(int(remaining) - int(settings.delegated_traffic or 0), 0)
            )
        else:
            available = admin_hierarchy.available_credit(db, settings)
    creation_remaining = None if owner_row else 0
    if settings is not None and not owner_row and settings.can_create_admins:
        creation_remaining = (
            None
            if settings.admin_creation_limit is None
            else max(
                int(settings.admin_creation_limit)
                - int(settings.admin_creations_used or 0)
                - int(settings.delegated_admin_creation_limit or 0),
                0,
            )
        )
    return HierarchyAdminNode(
        id=row.id,
        username=row.username,
        role=(admin_hierarchy.ADMIN if role == admin_hierarchy.SUPER_ADMIN else role)
        or admin_hierarchy.role_code(row),
        parent_admin_id=row.parent_admin_id,
        depth=depth,
        external_api_enabled=bool(row.external_api_enabled),
        account_status=status or admin_hierarchy.ACTIVE,
        total_traffic=settings.total_traffic if settings else None,
        delegated_traffic=int(settings.delegated_traffic or 0) if settings else 0,
        own_spend=spend,
        available_traffic=available,
        renewal_enabled=bool(settings.renewal_enabled) if settings else True,
        renewal_remaining=settings.renewal_remaining if settings else None,
        trial_quota=int(settings.trial_quota or 0) if settings else 0,
        trials_used=int(settings.trials_used or 0) if settings else 0,
        referral_referrer_admin_id=referral.referrer_admin_id if referral else None,
        referral_rate_bps=referral.rate_bps if referral else None,
        active_owner_freeze_event_id=active_owner_freeze_event_id,
        billing_mode=admin_billing.billing_mode(settings) if settings else admin_billing.BillingMode.LEGACY_COMPAT,
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
        admin_creation_remaining=creation_remaining,
    )


@router.get("/admin-management/tree", response_model=list[HierarchyAdminNode])
def get_admin_tree(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    if not admin_hierarchy.hierarchy_enabled(db):
        nodes = [_node_response(db, actor, 0)]
    else:
        rows = (
            db.query(
                DBAdmin,
                AdminHierarchy.depth,
                MarzhelpAdminSettings,
                AdminAccountStatus.code,
                AdminRole.code,
                AdminReferralAttribution,
                AdminSuspensionEvent.id,
            )
            .join(AdminHierarchy, AdminHierarchy.descendant_id == DBAdmin.id)
            .outerjoin(MarzhelpAdminSettings, MarzhelpAdminSettings.admin_id == DBAdmin.id)
            .outerjoin(
                AdminAccountStatus,
                AdminAccountStatus.id == MarzhelpAdminSettings.account_status_id,
            )
            .outerjoin(AdminRole, AdminRole.id == DBAdmin.role_id)
            .outerjoin(
                AdminReferralAttribution,
                AdminReferralAttribution.referred_admin_id == DBAdmin.id,
            )
            .outerjoin(
                AdminSuspensionEvent,
                (AdminSuspensionEvent.id == MarzhelpAdminSettings.suspension_event_id)
                & (AdminSuspensionEvent.operation_type == "owner_freeze")
                & (AdminSuspensionEvent.status == "complete"),
            )
            .options(
                noload(MarzhelpAdminSettings.inbound_permissions),
                noload(MarzhelpAdminSettings.user_limit_permissions),
                noload(MarzhelpAdminSettings.subscription_mode_permissions),
            )
            .filter(AdminHierarchy.ancestor_id == actor.id)
            .order_by(AdminHierarchy.depth, DBAdmin.username)
            .all()
        )
        owner_view = admin_hierarchy.can_manage_children(db, actor)
        settings_map = {row.id: settings for row, _, settings, *_ in rows if settings is not None}
        quota_map = marzhelp_policy.quota_summaries(
            db,
            [row.id for row, *_ in rows],
            settings_map,
        )
        nodes = [
            _node_response(
                db,
                row,
                depth,
                settings,
                status,
                role,
                preloaded=True,
                referral=referral if owner_view else None,
                active_owner_freeze_event_id=freeze_event_id if owner_view else None,
                quota_summary=quota_map.get(row.id),
                owner_row=role == admin_hierarchy.OWNER,
            )
            for row, depth, settings, status, role, referral, freeze_event_id in rows
        ]
    by_id = {node.id: node for node in nodes}
    roots: list[HierarchyAdminNode] = []
    for node in nodes:
        if node.parent_admin_id in by_id:
            by_id[node.parent_admin_id].children.append(node)
        else:
            roots.append(node)
    return roots


@router.post("/admin-management/{username}/children", response_model=HierarchyAdminNode)
def create_child(
    username: str,
    values: HierarchyChildCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    parent = _target(db, username)
    try:
        child = crud.create_admin(
            db,
            AdminCreate(
                username=values.username,
                password=values.password,
                phone=values.phone,
                is_sudo=False,
            ),
            commit=False,
        )
        if values.billing_mode == admin_billing.BillingMode.LEGACY_COMPAT:
            raise admin_hierarchy.HierarchyError(
                "billing_mode_required", "Select a commercial billing mode"
            )
        if values.billing_mode == admin_billing.BillingMode.USER_CREDIT and not values.initial_credit:
            raise admin_hierarchy.HierarchyError(
                "user_credit_limit_required",
                "Unlimited traffic Admin requires a positive account limit",
            )
        policy = MarzhelpAdminPolicy(
            billing_mode=values.billing_mode,
            total_traffic=0,
            max_users=0 if values.billing_mode == admin_billing.BillingMode.USER_CREDIT else None,
            device_capacity_limit=(
                0 if values.billing_mode == admin_billing.BillingMode.SEAT_CREDIT else None
            ),
        )
        settings = crud.upsert_marzhelp_admin_policy(db, child.id, policy, commit=False)
        admin_hierarchy.configure_new_child_admin_creation(
            db,
            actor=actor,
            parent=parent,
            child=child,
            child_settings=settings,
            child_role=values.role,
            child_billing_mode=values.billing_mode,
            can_create_admins=values.can_create_admins,
            can_delegate_admin_creation=values.can_delegate_admin_creation,
            can_create_allocated_children=values.can_create_allocated_children,
            admin_creation_limit=values.admin_creation_limit,
        )
        admin_hierarchy.attach_new_child(
            db,
            actor=actor,
            parent=parent,
            child=child,
            child_role=values.role,
            commit=False,
        )
        admin_hierarchy.configure_child_user_creation_access(
            db,
            actor=actor,
            parent=parent,
            child_settings=settings,
            mode=values.user_creation_mode,
            can_manage_plans=values.can_manage_plans,
        )
        if values.initial_credit:
            admin_hierarchy.transfer_credit(
                db,
                actor=actor,
                source=parent,
                target=child,
                amount=int(values.initial_credit),
                operation_type="grant",
                idempotency_key=f"child-create-{child.id}-credit",
                note="system:initial_child_credit",
                commit=False,
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Admin already exists")
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.child_create",
        "admin",
        f"Admin {actor.username} created {values.role} {child.username}",
        target_id=child.id,
        target_name=child.username,
        details={
            "parent_admin_id": parent.id,
            "role": values.role,
            "billing_mode": values.billing_mode.value,
        },
        request=request,
    )
    return _node_response(db, child, 1)


@router.put("/admin-management/{username}/parent")
def reparent_admin(
    username: str,
    values: ReparentRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    parent = _target(db, values.parent_username)
    previous_parent = target.parent_admin_id
    try:
        admin_hierarchy.reparent_subtree(db, actor, target, parent)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.reparent",
        "admin",
        f"Owner {actor.username} reparented {target.username}",
        target_id=target.id,
        target_name=target.username,
        previous_value={"parent_admin_id": previous_parent},
        new_value={"parent_admin_id": parent.id},
        request=request,
    )
    return {"detail": "Admin subtree reparented"}


def _credit_move(
    username: str,
    values: CreditTransferRequest,
    operation: str,
    request: Request,
    db: Session,
    admin: Admin,
):
    actor = _db_actor(db, admin)
    child = _target(db, username)
    parent = db.get(DBAdmin, child.parent_admin_id) if child.parent_admin_id else None
    if parent is None:
        raise HTTPException(status_code=400, detail="Target has no parent credit account")
    try:
        row, created = admin_hierarchy.transfer_credit(
            db,
            actor=actor,
            source=parent,
            target=child,
            amount=values.amount,
            operation_type=operation,
            idempotency_key=values.idempotency_key,
            note=values.note,
            commit=False,
            return_created=True,
        )
        if created:
            AuditLogService.log(
                db,
                actor,
                f"credit.{operation}",
                "admin_credit",
                f"Admin {actor.username} {operation} {values.amount} bytes for {child.username}",
                target_id=child.id,
                target_name=child.username,
                previous_value={
                    "traffic_credit": row.balance_before,
                    "source_delegated": row.source_delegated_before,
                },
                new_value={
                    "traffic_credit": row.balance_after,
                    "source_delegated": row.source_delegated_after,
                },
                details={
                    "resource": row.resource,
                    "transfer_id": row.id,
                    "delta": row.delta,
                    "actor_admin_id": actor.id,
                    "adjusted_admin_id": child.id,
                    "reason": row.note,
                    "idempotency_key": row.idempotency_key,
                },
                request=request,
                commit=False,
            )
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
    return row


@router.post("/admin-management/{username}/credit/grant", response_model=CreditTransferResponse)
def grant_credit(
    username: str,
    values: CreditTransferRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _credit_move(username, values, "grant", request, db, admin)


@router.post("/admin-management/{username}/credit/reclaim", response_model=CreditTransferResponse)
def reclaim_credit(
    username: str,
    values: CreditTransferRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _credit_move(username, values, "reclaim", request, db, admin)


def _money_move(
    username: str,
    values: MoneyTransferRequest,
    operation: str,
    request: Request,
    db: Session,
    admin: Admin,
):
    actor = _db_actor(db, admin)
    child = _target(db, username)
    parent = db.get(DBAdmin, child.parent_admin_id) if child.parent_admin_id else None
    if parent is None:
        raise HTTPException(status_code=400, detail="Target has no parent money account")
    try:
        result, created = money_billing.transfer_money(
            db,
            actor=actor,
            parent=parent,
            child=child,
            amount_toman=values.amount_toman,
            operation_type=operation,
            idempotency_key=values.idempotency_key,
            note=values.note,
        )
        if created:
            AuditLogService.log(
                db,
                actor,
                f"money.{operation}",
                "admin_money",
                f"Admin {actor.username} {operation} {values.amount_toman} Toman for {child.username}",
                target_id=child.id,
                target_name=child.username,
                details={"amount_toman": values.amount_toman, "idempotency_key": values.idempotency_key},
                request=request,
                commit=False,
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
    return MoneyTransferResponse(**result, replayed=not created)


@router.post("/admin-management/{username}/money/grant", response_model=MoneyTransferResponse)
def grant_money(
    username: str,
    values: MoneyTransferRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _money_move(username, values, "grant", request, db, admin)


@router.post("/admin-management/{username}/money/reclaim", response_model=MoneyTransferResponse)
def reclaim_money(
    username: str,
    values: MoneyTransferRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _money_move(username, values, "reclaim", request, db, admin)


def _trial_quota_adjustment(
    username: str,
    values: TrialQuotaAdjustmentRequest,
    operation: str,
    request: Request,
    db: Session,
    admin: Admin,
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        row, created = trials.adjust_quota(
            db,
            actor=actor,
            target=target,
            amount=values.amount,
            operation=operation,
            idempotency_key=values.idempotency_key,
            note=values.note,
        )
        if created:
            AuditLogService.log(
                db,
                actor,
                f"trial_quota.{operation}",
                "admin_trial_quota",
                f"Owner {actor.username} adjusted Trial quota for {target.username}",
                target_id=target.id,
                target_name=target.username,
                previous_value={"trial_quota": row.balance_before},
                new_value={"trial_quota": row.balance_after},
                details={
                    "resource": "trial_quota",
                    "transfer_id": row.id,
                    "idempotency_key": row.idempotency_key,
                    "reason": row.note,
                },
                request=request,
                commit=False,
            )
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)


@router.post(
    "/admin-management/{username}/trial-quota/grant",
    response_model=CreditTransferResponse,
)
def grant_trial_quota(
    username: str,
    values: TrialQuotaAdjustmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _trial_quota_adjustment(username, values, "grant", request, db, admin)


@router.post(
    "/admin-management/{username}/trial-quota/reclaim",
    response_model=CreditTransferResponse,
)
def reclaim_trial_quota(
    username: str,
    values: TrialQuotaAdjustmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _trial_quota_adjustment(username, values, "reclaim", request, db, admin)


@router.post(
    "/admin-management/{username}/trial-quota/reset",
    response_model=CreditTransferResponse,
)
def reset_trial_quota(
    username: str,
    values: TrialQuotaResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        row, created = trials.reset_quota(
            db,
            actor=actor,
            target=target,
            idempotency_key=values.idempotency_key,
            note=values.note,
        )
        if created:
            AuditLogService.log(
                db,
                actor,
                "trial_quota.reset",
                "admin_trial_quota",
                f"Admin {actor.username} reset Trial quota for {target.username}",
                target_id=target.id,
                target_name=target.username,
                previous_value={"trial_quota": row.balance_before},
                new_value={"trial_quota": row.balance_after},
                details={"idempotency_key": row.idempotency_key, "reason": row.note},
                request=request,
                commit=False,
            )
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)


@router.get("/admin-management/{username}/credit/ledger", response_model=list[CreditTransferResponse])
def credit_ledger(
    username: str,
    before_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    if not admin_hierarchy.admin_in_scope(db, actor, target.id):
        raise HTTPException(status_code=403, detail="Admin is outside your scope")
    query = db.query(AdminCreditTransfer).filter(
        (AdminCreditTransfer.from_admin_id == target.id)
        | (AdminCreditTransfer.to_admin_id == target.id)
    )
    if before_id is not None:
        query = query.filter(AdminCreditTransfer.id < before_id)
    return query.order_by(AdminCreditTransfer.id.desc()).limit(limit).all()


@router.put("/admin-management/{username}/external-api")
def set_external_api(
    username: str,
    values: ExternalApiPolicy,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        if not admin_hierarchy.is_owner(db, actor):
            raise admin_hierarchy.HierarchyError("owner_required", "Only Owner can change external API")
        revoked = 0
        if values.enabled:
            target.external_api_enabled = True
            target.external_api_updated_by = actor.id
            target.external_api_updated_at = admin_hierarchy.utc_now_naive()
            db.commit()
        else:
            revoked = admin_hierarchy.revoke_api_access(db, actor, target)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.external_api",
        "admin",
        f"Owner {actor.username} set external API for {target.username} to {values.enabled}",
        target_id=target.id,
        target_name=target.username,
        details={"enabled": values.enabled, "revoked_tokens": revoked},
        request=request,
    )
    return {"enabled": values.enabled, "revoked_tokens": revoked}


@router.post("/admin-management/{username}/api-tokens", response_model=ApiTokenCreated)
def create_api_token(
    username: str,
    values: ApiTokenCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        row, plaintext = admin_hierarchy.issue_api_token(
            db,
            owner=actor,
            target=target,
            name=values.name,
            scopes=values.scopes,
            expires_at=values.expires_at,
        )
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.api_token_create",
        "admin_api_token",
        f"Owner {actor.username} created an automation token for {target.username}",
        target_id=row.id,
        target_name=target.username,
        details={"scopes": sorted(row.scopes), "expires_at": row.expires_at},
        request=request,
    )
    return ApiTokenCreated(
        id=row.id,
        name=row.name,
        scopes=row.scopes,
        expires_at=row.expires_at,
        token=plaintext,
    )


@router.get("/admin-management/{username}/api-tokens", response_model=list[ApiTokenSummary])
def list_api_tokens(
    username: str,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    if not admin_hierarchy.is_owner(db, actor):
        raise HTTPException(status_code=403, detail="Only Owner can list automation tokens")
    return db.query(AdminApiToken).filter(AdminApiToken.admin_id == target.id).order_by(
        AdminApiToken.id.desc()
    ).all()


@router.delete("/admin-management/{username}/api-tokens/{token_id}")
def revoke_api_token(
    username: str,
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    if not admin_hierarchy.is_owner(db, actor):
        raise HTTPException(status_code=403, detail="Only Owner can revoke automation tokens")
    token = db.query(AdminApiToken).filter(
        AdminApiToken.id == token_id,
        AdminApiToken.admin_id == target.id,
    ).one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="API token not found")
    token.revoked_at = admin_hierarchy.utc_now_naive()
    db.commit()
    AuditLogService.log(
        db,
        actor,
        "admin.api_token_revoke",
        "admin_api_token",
        f"Owner {actor.username} revoked an automation token for {target.username}",
        target_id=token.id,
        target_name=target.username,
        details={"token_name": token.name},
        request=request,
    )
    return {"detail": "API token revoked"}


def _parent_or_owner(db: Session, actor: DBAdmin, target: DBAdmin) -> bool:
    return admin_hierarchy.is_owner(db, actor) or target.parent_admin_id == actor.id


@router.put("/admin-management/{username}/renewal-policy")
def update_renewal_policy(
    username: str,
    values: RenewalPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    if not _parent_or_owner(db, actor, target):
        raise HTTPException(status_code=403, detail="Only parent or Owner can set renewal policy")
    settings = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id == target.id)
        .with_for_update()
        .one_or_none()
    )
    if settings is None:
        raise HTTPException(status_code=409, detail="Target credit settings are missing")
    previous = {
        "enabled": settings.renewal_enabled,
        "remaining": settings.renewal_remaining,
        "limit": settings.renewal_limit,
        "used": settings.renewals_used,
    }
    settings.renewal_enabled = values.enabled
    settings.renewal_remaining = values.remaining
    settings.renewal_limit = values.remaining
    settings.renewals_used = 0
    try:
        AuditLogService.log(
            db,
            actor,
            "admin.renewal_policy_update",
            "admin",
            f"Admin {actor.username} updated renewal policy for {target.username}",
            target_id=target.id,
            target_name=target.username,
            previous_value=previous,
            new_value={
                "enabled": values.enabled,
                "remaining": values.remaining,
                "limit": values.remaining,
                "used": 0,
            },
            request=request,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"enabled": values.enabled, "remaining": values.remaining}


@router.put("/admin-management/{username}/user-creation-mode")
def update_user_creation_mode(
    username: str,
    values: UserCreationModeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    if not _parent_or_owner(db, actor, target):
        raise HTTPException(status_code=403, detail="Only parent or Owner can set creation mode")
    settings = db.get(MarzhelpAdminSettings, target.id)
    previous = {
        "user_creation_mode_id": settings.user_creation_mode_id,
        "can_manage_plans": settings.can_manage_plans,
    }
    try:
        admin_hierarchy.configure_child_user_creation_access(
            db,
            actor=actor,
            parent=actor,
            child_settings=settings,
            mode=values.mode,
            can_manage_plans=values.can_manage_plans,
        )
    except admin_hierarchy.HierarchyError as exc:
        db.rollback()
        _raise_domain(exc)
    db.commit()
    AuditLogService.log(
        db,
        actor,
        "admin.user_creation_mode_update",
        "admin",
        f"Admin {actor.username} updated creation mode for {target.username}",
        target_id=target.id,
        target_name=target.username,
        previous_value=previous,
        new_value={"mode": values.mode, "can_manage_plans": values.can_manage_plans},
        request=request,
    )
    return {"mode": values.mode, "can_manage_plans": values.can_manage_plans}


@router.post("/admin-management/{username}/suspend")
def suspend_admin(
    username: str,
    values: SuspendRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        event = admin_hierarchy.suspend_admin(
            db,
            actor=actor,
            target=target,
            reason_id=values.reason_id,
            include_subtree=values.include_subtree,
        )
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.suspend",
        "admin",
        f"Admin {actor.username} suspended {target.username}",
        target_id=target.id,
        target_name=target.username,
        details={"event_id": event.id, "reason_id": values.reason_id, "include_subtree": values.include_subtree},
        request=request,
    )
    bg.add_task(_restart_runtime)
    return {"event_id": event.id, "status": event.status}


def _referral_response(
    db: Session,
    target: DBAdmin,
    *,
    event: AdminReferralEvent | None = None,
    replayed: bool = False,
) -> ReferralAttributionResponse:
    attribution = db.get(AdminReferralAttribution, target.id)
    referrer = db.get(DBAdmin, attribution.referrer_admin_id) if attribution else None
    return ReferralAttributionResponse(
        referred_admin_id=target.id,
        referred_username=target.username,
        referrer_admin_id=referrer.id if referrer else None,
        referrer_username=referrer.username if referrer else None,
        rate_bps=attribution.rate_bps if attribution else None,
        last_event_id=event.id if event else None,
        replayed=replayed,
    )


@router.get(
    "/admin-management/{username}/referral",
    response_model=ReferralAttributionResponse,
)
def get_referral_attribution(
    username: str,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    if not admin_hierarchy.is_owner(db, actor):
        raise HTTPException(status_code=403, detail="Only Owner can view referral configuration")
    return _referral_response(db, _target(db, username))


@router.put(
    "/admin-management/{username}/referral",
    response_model=ReferralAttributionResponse,
)
def update_referral_attribution(
    username: str,
    values: ReferralAttributionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    referrer = _target(db, values.referrer_username)
    try:
        event, created = admin_hierarchy.set_referral_attribution(
            db,
            actor=actor,
            referred=target,
            referrer=referrer,
            rate_bps=values.rate_bps,
            idempotency_key=values.idempotency_key,
            note=values.note,
        )
    except Exception as exc:
        _raise_domain(exc)
    if created:
        AuditLogService.log(
            db,
            actor,
            "admin.referral_attribution_update",
            "admin_referral_attribution",
            f"Owner {actor.username} updated referral attribution for {target.username}",
            target_id=target.id,
            target_name=target.username,
            previous_value={
                "referrer_admin_id": event.previous_referrer_admin_id,
                "rate_bps": event.previous_rate_bps,
            },
            new_value={"referrer_admin_id": referrer.id, "rate_bps": values.rate_bps},
            details={"event_id": event.id, "idempotency_key": values.idempotency_key},
            request=request,
        )
    return _referral_response(db, target, event=event, replayed=not created)


@router.delete(
    "/admin-management/{username}/referral",
    response_model=ReferralAttributionResponse,
)
def remove_referral_attribution(
    username: str,
    values: ReferralAttributionRemove,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        event, created = admin_hierarchy.set_referral_attribution(
            db,
            actor=actor,
            referred=target,
            referrer=None,
            rate_bps=None,
            idempotency_key=values.idempotency_key,
            note=values.note,
        )
    except Exception as exc:
        _raise_domain(exc)
    if created:
        AuditLogService.log(
            db,
            actor,
            "admin.referral_attribution_remove",
            "admin_referral_attribution",
            f"Owner {actor.username} removed referral attribution for {target.username}",
            target_id=target.id,
            target_name=target.username,
            previous_value={
                "referrer_admin_id": event.previous_referrer_admin_id,
                "rate_bps": event.previous_rate_bps,
            },
            new_value=None,
            details={"event_id": event.id, "idempotency_key": values.idempotency_key},
            request=request,
        )
    return _referral_response(db, target, event=event, replayed=not created)


@router.post("/admin-management/{username}/freeze")
def freeze_admin(
    username: str,
    values: OwnerFreezeRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        event, created = admin_hierarchy.freeze_admin(
            db,
            actor=actor,
            target=target,
            reason_id=values.reason_id,
            idempotency_key=values.idempotency_key,
            note=values.note,
        )
    except Exception as exc:
        _raise_domain(exc)
    if created:
        AuditLogService.log(
            db,
            actor,
            "admin.owner_freeze",
            "admin_suspension_event",
            f"Owner {actor.username} froze subtree {target.username}",
            target_id=target.id,
            target_name=target.username,
            new_value={"account_status": "SUSPENDED", "scope": "full_subtree"},
            details={"event_id": event.id, "idempotency_key": values.idempotency_key},
            request=request,
        )
        bg.add_task(_restart_runtime)
    return {"event_id": event.id, "status": event.status, "replayed": not created}


@router.post("/admin-management/{username}/unfreeze")
def unfreeze_admin(
    username: str,
    values: OwnerUnfreezeRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        event, restored_admins, restored_users, created = admin_hierarchy.unfreeze_admin(
            db,
            actor=actor,
            target=target,
            idempotency_key=values.idempotency_key,
        )
    except Exception as exc:
        _raise_domain(exc)
    if created:
        AuditLogService.log(
            db,
            actor,
            "admin.owner_unfreeze",
            "admin_suspension_event",
            f"Owner {actor.username} unfroze subtree {target.username}",
            target_id=target.id,
            target_name=target.username,
            details={
                "event_id": event.id,
                "restored_admins": restored_admins,
                "restored_users": restored_users,
                "idempotency_key": values.idempotency_key,
            },
            request=request,
        )
        bg.add_task(_restart_runtime)
    return {
        "event_id": event.id,
        "status": event.status,
        "restored_admins": restored_admins,
        "restored_users": restored_users,
        "replayed": not created,
    }


@router.post("/admin-management/{username}/resume")
def resume_admin(
    username: str,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        restored = admin_hierarchy.resume_admin(db, actor=actor, target=target)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.resume",
        "admin",
        f"Admin {actor.username} resumed {target.username}",
        target_id=target.id,
        target_name=target.username,
        details={"restored_users": restored},
        request=request,
    )
    bg.add_task(_restart_runtime)
    return {"restored_users": restored}


@router.post("/admin-management/{username}/activate")
def activate_disabled_admin(
    username: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        admin_hierarchy.activate_disabled_admin(db, actor=actor, target=target)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.activate",
        "admin",
        f"Admin {actor.username} activated {target.username}",
        target_id=target.id,
        target_name=target.username,
        previous_value={"account_status": "DISABLED"},
        new_value={"account_status": "ACTIVE"},
        request=request,
    )
    return {"account_status": "ACTIVE"}


@router.post("/admin-management/{username}/users/disable")
def disable_users_job(
    username: str,
    values: BulkDisableRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    target = _target(db, username)
    try:
        job = admin_hierarchy.run_disable_job(
            db,
            actor=actor,
            target=target,
            include_subtree=values.include_subtree,
            idempotency_key=values.idempotency_key,
            batch_size=values.batch_size,
        )
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "admin.users_disable_bulk",
        "admin_bulk_job",
        f"Admin {actor.username} disabled users for {target.username}",
        target_id=job.id,
        target_name=target.username,
        details={
            "include_subtree": values.include_subtree,
            "total_count": job.total_count,
            "processed_count": job.processed_count,
            "idempotency_key": values.idempotency_key,
        },
        request=request,
    )
    bg.add_task(_restart_runtime)
    return {
        "job_id": job.id,
        "status": job.status,
        "total_count": job.total_count,
        "processed_count": job.processed_count,
    }


@router.get("/account/summary", response_model=AccountSummary)
def account_summary(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    settings = db.get(MarzhelpAdminSettings, actor.id)
    account_status = admin_hierarchy.account_status_code(db, actor.id)
    reason = (
        db.query(AdminSuspensionReason.code)
        .filter(AdminSuspensionReason.id == settings.suspended_reason_id)
        .scalar()
        if settings and settings.suspended_reason_id
        else None
    )
    if settings and settings.suspension_event_id:
        freeze_event = db.get(AdminSuspensionEvent, settings.suspension_event_id)
        freeze_note = (
            (freeze_event.limits_snapshot or {}).get("note")
            if freeze_event is not None
            else None
        )
        if freeze_note:
            reason = str(freeze_note)
    own_users = db.query(func.count(User.id)).filter(User.admin_id == actor.id).scalar() or 0
    subtree_users = (
        db.query(func.count(User.id))
        .filter(
            exists().where(
                (AdminHierarchy.ancestor_id == actor.id)
                & (AdminHierarchy.descendant_id == User.admin_id)
            )
        )
        .scalar()
        or own_users
    )
    mode = (
        db.query(AdminUserCreationMode.code)
        .filter(AdminUserCreationMode.id == settings.user_creation_mode_id)
        .scalar()
        if settings
        else admin_hierarchy.FREE_FORM
    )
    namespace_prefix = actor.user_namespace_prefix
    if not namespace_prefix:
        namespace_prefix = marzhelp_policy.ensure_admin_namespace_prefix(db, actor)
        db.commit()
    return AccountSummary(
        username=actor.username,
        user_namespace_prefix=namespace_prefix,
        role=admin_hierarchy.role_code(actor),
        account_status=account_status,
        suspended_reason=reason,
        suspended_at=settings.suspended_at if settings else None,
        own_users=own_users,
        subtree_users=subtree_users,
        total_traffic=settings.total_traffic if settings else None,
        delegated_traffic=int(settings.delegated_traffic or 0) if settings else 0,
        own_spend=admin_hierarchy.own_credit_spend(db, settings) if settings else 0,
        available_traffic=admin_hierarchy.available_credit(db, settings) if settings else None,
        renewal_enabled=bool(settings.renewal_enabled) if settings else True,
        renewal_remaining=settings.renewal_remaining if settings else None,
        billing_mode=admin_billing.billing_mode(settings),
        money_billing_enabled=bool(settings.money_billing_enabled) if settings else False,
        money_balance_toman=int(settings.money_balance_toman or 0) if settings else 0,
        used_traffic_price_per_gib_toman=(
            settings.used_traffic_price_per_gib_toman if settings else None
        ),
        user_creation_mode=mode or admin_hierarchy.FREE_FORM,
        can_manage_plans=bool(settings.can_manage_plans) if settings else False,
        trial_quota=int(settings.trial_quota or 0) if settings else 0,
        trials_used=int(settings.trials_used or 0) if settings else 0,
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
            admin_hierarchy.admin_creation_remaining(db, actor, settings)
            if settings is not None
            else 0
        ),
    )


@router.get("/account/activity")
def account_activity(
    before_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    query = db.query(AdminAuditLog)
    if not admin_hierarchy.is_owner(db, actor):
        query = query.filter(
            exists().where(
                (AdminHierarchy.ancestor_id == actor.id)
                & (AdminHierarchy.descendant_id == AdminAuditLog.admin_id)
            )
        )
    if before_id is not None:
        query = query.filter(AdminAuditLog.id < before_id)
    rows = query.order_by(AdminAuditLog.id.desc()).limit(limit).all()
    return [
        {
            "id": row.id,
            "admin_username": row.admin_username,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "target_name": row.target_name,
            "description": row.description,
            "status": row.status,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/user-plans", response_model=list[PlanResponse])
def get_user_plans(
    before_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    if not admin_hierarchy.is_owner(db, actor):
        raise HTTPException(status_code=403, detail="Plan management is Owner-only")
    query = admin_plans.effective_plans_query(db, actor)
    if before_id is not None:
        query = query.filter(AdminUserPlan.id < before_id)
    plans = query.order_by(AdminUserPlan.id.desc()).limit(limit).all()
    return admin_plans.plan_responses(db, plans, actor=actor)


@router.get("/available-user-plans", response_model=list[PlanSummary])
def get_available_user_plan_summaries(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    plans = admin_plans.effective_plans_query(db, actor).order_by(AdminUserPlan.name, AdminUserPlan.id).all()
    return [
        PlanSummary(
            id=plan.id,
            name=plan.name,
            data_limit=plan.version.data_limit,
            duration_days=plan.version.duration_days,
            price_toman=plan.effective_price_toman,
            concurrent_user_limit=plan.version.concurrent_user_limit,
        )
        for plan in admin_plans.plan_responses(db, plans, actor=actor)
    ]


@router.get("/plan-network-options", response_model=list[PlanNetworkOption])
def get_plan_network_options(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    try:
        return admin_plans.network_options(db, actor)
    except Exception as exc:
        _raise_domain(exc)


@router.get("/plan-categories", response_model=list[PlanCategoryResponse])
def get_plan_categories(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    categories = (
        admin_plans.effective_categories_query(db, actor)
        .order_by(AdminPlanCategory.name, AdminPlanCategory.id)
        .all()
    )
    return admin_plans.category_responses(db, categories)


@router.post("/plan-categories", response_model=PlanCategoryResponse)
def create_plan_category(
    values: PlanCategoryCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    try:
        category = admin_plans.create_category(db, actor, values)
        result = admin_plans.category_response(db, category)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category name already exists in this owner scope")
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "plan_category.create",
        "admin_plan_category",
        f"Admin {actor.username} created plan category {category.name}",
        target_id=category.id,
        target_name=category.name,
        request=request,
    )
    return result


@router.put("/plan-categories/{category_id}", response_model=PlanCategoryResponse)
def update_plan_category(
    category_id: int,
    values: PlanCategoryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    category = db.get(AdminPlanCategory, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Plan category not found")
    try:
        category = admin_plans.update_category(db, actor, category, values)
        result = admin_plans.category_response(db, category)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category name already exists in this owner scope")
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "plan_category.update",
        "admin_plan_category",
        f"Admin {actor.username} updated plan category {category.name}",
        target_id=category.id,
        target_name=category.name,
        request=request,
    )
    return result


@router.delete("/plan-categories/{category_id}")
def archive_plan_category(
    category_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    category = db.get(AdminPlanCategory, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Plan category not found")
    category_name = category.name
    try:
        admin_plans.archive_category(db, actor, category)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "plan_category.archive",
        "admin_plan_category",
        f"Admin {actor.username} archived plan category {category_name}",
        target_id=category_id,
        target_name=category_name,
        request=request,
    )
    return {"detail": "Plan category archived"}


@router.get("/access-groups", response_model=list[AccessGroupResponse])
def get_access_groups(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    return [access_groups.response(db, group) for group in access_groups.list_groups(db, actor)]


@router.get("/owner/pricing", response_model=OwnerPricingResponse)
def get_owner_pricing(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    if not admin_hierarchy.is_owner(db, actor):
        raise HTTPException(status_code=403, detail="Only Owner can view pricing settings")
    return owner_pricing.response(db)


@router.put("/owner/pricing", response_model=OwnerPricingResponse)
def update_owner_pricing(
    values: OwnerPricingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    try:
        result = owner_pricing.update(db, actor, values)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db, actor, "owner.pricing_update", "owner_commercial_policy",
        f"Owner {actor.username} updated pricing and duration presets",
        new_value=values.model_dump(), request=request,
    )
    return result


@router.post("/access-groups", response_model=AccessGroupResponse)
def create_access_group(
    values: AccessGroupInput,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    try:
        group = access_groups.create(db, actor, values)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Access Group name already exists")
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db, actor, "access_group.create", "access_group",
        f"Owner {actor.username} created Access Group {group.name}",
        target_id=group.id, target_name=group.name, request=request,
    )
    return access_groups.response(db, group)


@router.put("/access-groups/{group_id}", response_model=AccessGroupResponse)
def update_access_group(
    group_id: int,
    values: AccessGroupInput,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    group = db.get(AccessGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Access Group not found")
    try:
        synced_ids = access_groups.update(db, actor, group, values)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db, actor, "access_group.update", "access_group",
        f"Owner {actor.username} updated Access Group {group.name}",
        target_id=group.id, target_name=group.name,
        details={"synced_active_user_ids": synced_ids}, request=request,
    )
    return access_groups.response(db, group)


@router.delete("/access-groups/{group_id}")
def archive_access_group(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    group = db.get(AccessGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Access Group not found")
    try:
        access_groups.archive(db, actor, group)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db, actor, "access_group.archive", "access_group",
        f"Owner {actor.username} archived Access Group {group.name}",
        target_id=group.id, target_name=group.name, request=request,
    )
    return {"detail": "Access Group archived"}


@router.post("/user-plans", response_model=PlanResponse)
def create_user_plan(
    values: PlanCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    try:
        plan = admin_plans.create_plan(db, actor, values)
        result = admin_plans.plan_response(db, plan, actor=actor)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Plan name already exists in this owner scope")
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "plan.create",
        "admin_user_plan",
        f"Admin {actor.username} created plan {plan.name}",
        target_id=plan.id,
        target_name=plan.name,
        details={"version_id": plan.current_version_id},
        request=request,
    )
    return result


@router.put("/user-plans/{plan_id}", response_model=PlanResponse)
def update_user_plan(
    plan_id: int,
    values: PlanUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    plan = db.get(AdminUserPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    previous_version_id = plan.current_version_id
    try:
        updated = admin_plans.update_plan(db, actor, plan, values)
        result = admin_plans.plan_response(db, updated, actor=actor)
    except Exception as exc:
        _raise_domain(exc)
    AuditLogService.log(
        db,
        actor,
        "plan.version_create",
        "admin_user_plan",
        f"Admin {actor.username} created a new version of plan {plan.name}",
        target_id=plan.id,
        target_name=plan.name,
        previous_value={"version_id": previous_version_id},
        new_value={"version_id": updated.current_version_id},
        request=request,
    )
    return result


@router.delete("/user-plans/{plan_id}")
def archive_user_plan(
    plan_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    plan = db.get(AdminUserPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not admin_hierarchy.is_owner(db, actor):
        raise HTTPException(status_code=403, detail="Only Owner can archive Plans")
    plan.archived_at = admin_hierarchy.utc_now_naive()
    db.commit()
    AuditLogService.log(
        db,
        actor,
        "plan.archive",
        "admin_user_plan",
        f"Admin {actor.username} archived plan {plan.name}",
        target_id=plan.id,
        target_name=plan.name,
        request=request,
    )
    return {"detail": "Plan archived"}


@router.post("/users/from-plan", response_model=UserResponse)
def create_user_from_plan(
    values: PlanUserCreate,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    try:
        user, _, created = admin_plans.create_user_from_plan(
            db,
            actor=actor,
            plan_id=values.plan_id,
            username=values.username,
            status=values.status,
            note=values.note,
            idempotency_key=values.idempotency_key,
            access_group_id=values.access_group_id,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="User already exists")
    except Exception as exc:
        _raise_domain(exc)
    if created:
        AuditLogService.log(
            db,
            actor,
            "user.create_from_plan",
            "user",
            f"Admin {actor.username} created user {user.username} from plan",
            target_id=user.id,
            target_name=user.username,
            details={"plan_id": values.plan_id, "idempotency_key": values.idempotency_key},
            request=request,
        )
        bg.add_task(xray.operations.add_user_by_id, user_id=user.id)
    return admin_plans.scoped_user_response(db, user, actor=actor)


@router.post("/users/{username}/renew-from-plan", response_model=UserResponse)
def renew_user_from_plan(
    username: str,
    values: PlanRenewRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    user = crud.get_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        user, _, renewed = admin_plans.renew_user_from_plan(
            db,
            actor=actor,
            user=user,
            plan_id=values.plan_id,
            idempotency_key=values.idempotency_key,
            access_group_id=values.access_group_id,
        )
    except Exception as exc:
        _raise_domain(exc)
    if renewed:
        AuditLogService.log(
            db,
            actor,
            "user.renew_from_plan",
            "user",
            f"Admin {actor.username} renewed user {user.username} from plan",
            target_id=user.id,
            target_name=user.username,
            details={"plan_id": values.plan_id, "idempotency_key": values.idempotency_key},
            request=request,
        )
        bg.add_task(xray.operations.update_user_by_id, user_id=user.id)
    return admin_plans.scoped_user_response(db, user, actor=actor)


@router.get("/trials/cleanup/preview", response_model=TrialCleanupResponse)
def preview_trial_cleanup(
    expired_before: datetime,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    count, usernames = trials.cleanup_preview(db, actor, expired_before)
    return TrialCleanupResponse(count=count, usernames=usernames)


@router.post("/trials/cleanup", response_model=TrialCleanupResponse)
def execute_trial_cleanup(
    values: TrialCleanupRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    actor = _db_actor(db, admin)
    try:
        operation, created = trials.cleanup(
            db,
            actor=actor,
            expired_before=values.expired_before,
            idempotency_key=values.idempotency_key,
        )
        if created:
            AuditLogService.log(
                db,
                actor,
                "trial.cleanup",
                "trial_cleanup_operation",
                f"Admin {actor.username} deleted {operation.deleted_count} expired Trial users",
                target_id=operation.id,
                details={
                    "expired_before": operation.expired_before,
                    "deleted_count": operation.deleted_count,
                    "idempotency_key": operation.idempotency_key,
                },
                request=request,
                commit=False,
            )
        db.commit()
        if created and operation.deleted_count:
            bg.add_task(_restart_runtime)
        return TrialCleanupResponse(
            count=operation.deleted_count,
            usernames=list(operation.deleted_usernames or []),
            replayed=not created,
        )
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
