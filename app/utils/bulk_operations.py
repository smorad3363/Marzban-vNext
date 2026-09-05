"""Persistent, scoped and idempotent Stage 8 bulk operations."""

from __future__ import annotations

import json
import time
from datetime import datetime
from hashlib import sha256
from typing import Iterable

from sqlalchemy import and_, case, exists, func, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db import crud
from app.db.models import (
    Admin,
    AdminBulkJob,
    AdminBulkJobTarget,
    AdminHierarchy,
    MarzhelpAdminSettings,
    User,
)
from app.models.bulk import (
    BulkAdminJobCreateRequest,
    BulkAdminOperation,
    BulkAdminPreviewRequest,
    BulkJobResponse,
    BulkPreviewResponse,
    BulkTargetResultResponse,
    BulkTargetScope,
    BulkUserJobCreateRequest,
    BulkUserPreviewRequest,
    BulkSelectionPreview,
    BulkSelectionRequest,
    BulkSelectionResponse,
    BulkSelectionResult,
)
from app.models.user import BulkUserOperation, UserModify, UserStatus
from app.utils import admin_hierarchy, marzhelp_policy, owner_pricing
from app.utils.admin_billing import BillingMode, billing_mode


TARGET_INSERT_CHUNK = 1000
REPORT_LIMIT = 500
DEADLOCK_CODES = {1205, 1213}


class BulkOperationError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _selected_users(db: Session, actor: Admin | object, user_ids: list[int], *, lock: bool = False) -> tuple[Admin, list[User]]:
    """Resolve only the checked IDs and reject hidden/out-of-scope targets."""
    dbactor = _db_actor(db, actor)
    query = db.query(User).filter(User.id.in_(user_ids)).order_by(User.id)
    query = crud.apply_inbound_access_filter(query, marzhelp_policy.allowed_inbound_tags(db, dbactor))
    if lock:
        query = query.with_for_update()
    users = query.all()
    found = {user.id for user in users}
    missing = sorted(set(user_ids) - found)
    forbidden = [
        user.id for user in users
        if user.admin_id is None or not admin_hierarchy.admin_in_scope(db, dbactor, user.admin_id)
    ]
    if missing:
        raise BulkOperationError("selected_user_not_found", f"Unknown or hidden User IDs: {missing}", status_code=404)
    if forbidden:
        raise BulkOperationError("bulk_scope_forbidden", f"User IDs outside actor scope: {forbidden}", status_code=403)
    return dbactor, users


def _selection_changes(user: User, values: BulkSelectionRequest) -> tuple[dict, int, int, str | None]:
    changes: dict = {}
    traffic_delta = 0
    duration_delta = 0
    status_change = None
    for action in values.actions:
        operation = action.operation
        amount = int(action.amount or 0)
        if operation == BulkUserOperation.activate:
            changes["status"] = UserStatus.active
            status_change = UserStatus.active.value
        elif operation == BulkUserOperation.deactivate:
            changes["status"] = UserStatus.disabled
            status_change = UserStatus.disabled.value
        elif operation in (BulkUserOperation.add_data, BulkUserOperation.subtract_data):
            if user.data_limit is None:
                raise BulkOperationError("unlimited_data", f"{user.username} has unlimited traffic")
            traffic_delta += amount if operation == BulkUserOperation.add_data else -amount
        elif operation in (BulkUserOperation.add_days, BulkUserOperation.subtract_days):
            if user.expire is None:
                raise BulkOperationError("unlimited_expiry", f"{user.username} has unlimited duration")
            duration_delta += amount if operation == BulkUserOperation.add_days else -amount
        elif operation == BulkUserOperation.delete:
            changes["delete"] = True
        else:
            raise BulkOperationError("unsupported_operation", f"Unsupported operation: {operation.value}")
    if traffic_delta:
        changes["data_limit"] = max(1, int(user.data_limit or 0) + traffic_delta)
    if duration_delta:
        changes["expire"] = max(1, int(user.expire or 0) + duration_delta * 86400)
    return changes, traffic_delta, duration_delta, status_change


def preview_selection(db: Session, actor: Admin | object, values: BulkSelectionRequest) -> BulkSelectionPreview:
    dbactor, users = _selected_users(db, actor, values.user_ids)
    traffic_change = 0
    duration_change = 0
    status_change = None
    cost_toman = 0
    for user in users:
        changes, traffic_delta, duration_delta, status = _selection_changes(user, values)
        traffic_change += traffic_delta
        duration_change += duration_delta
        status_change = status or status_change
        settings = db.get(MarzhelpAdminSettings, user.admin_id) if user.admin_id else None
        if settings and billing_mode(settings) == BillingMode.ALLOCATED_TRAFFIC:
            if traffic_delta < 0:
                raise BulkOperationError("allocated_traffic_reduction_forbidden", "Admin cannot reduce allocated user traffic")
            if traffic_delta > 0:
                preset = (
                    owner_pricing.duration_days_preset(db, abs(duration_delta))
                    if duration_delta
                    else owner_pricing.duration_preset(db, user.expire)
                )
                policy = owner_pricing.get_policy(db)
                denominator = owner_pricing.GIB * 10_000
                cost_toman += (
                    traffic_delta * int(policy.price_per_gib_toman) * int(preset.multiplier_basis_points)
                    + denominator - 1
                ) // denominator
    return BulkSelectionPreview(
        user_count=len(users),
        traffic_change=traffic_change,
        duration_change_days=duration_change,
        status_change=status_change,
        cost_toman=cost_toman,
        usernames=[user.username for user in users],
    )


def execute_selection(db: Session, actor: Admin | object, values: BulkSelectionRequest) -> BulkSelectionResponse:
    dbactor, users = _selected_users(db, actor, values.user_ids, lock=True)
    results: list[BulkSelectionResult] = []
    for user in users:
        try:
            with db.begin_nested():
                changes, _, _, _ = _selection_changes(user, values)
                if changes.pop("delete", False):
                    marzhelp_policy.capture_delete(db, user)
                    db.delete(user)
                    db.flush()
                else:
                    if changes.get("status") == UserStatus.active:
                        marzhelp_policy.validate_activation(db, user)
                    settings = db.get(MarzhelpAdminSettings, user.admin_id) if user.admin_id else None
                    if settings and not admin_hierarchy.is_owner(db, dbactor) and admin_hierarchy.allows_form_creation(settings):
                        duration_action = next(
                            (action for action in values.actions if action.operation in {BulkUserOperation.add_days, BulkUserOperation.subtract_days}),
                            None,
                        )
                        if duration_action:
                            owner_pricing.duration_days_preset(db, int(duration_action.amount or 0))
                    crud.update_user(
                        db,
                        user,
                        UserModify(next_plan=_next_plan_snapshot(user), **changes),
                        commit=False,
                        actor=dbactor,
                    )
            results.append(BulkSelectionResult(user_id=user.id, username=user.username, status="SUCCESS"))
        except Exception as exc:
            results.append(
                BulkSelectionResult(
                    user_id=user.id,
                    username=user.username,
                    status="FAILED",
                    reason=str(exc)[:512],
                )
            )
    db.commit()
    success = sum(result.status == "SUCCESS" for result in results)
    return BulkSelectionResponse(
        operation_id=values.operation_id,
        success=success,
        failed=len(results) - success,
        results=results,
    )


def _fingerprint(payload: dict) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _target_fingerprint(job: AdminBulkJob, target_type: str, target_id: int) -> str:
    return _fingerprint(
        {
            "job": job.payload_fingerprint,
            "operation_id": job.idempotency_key,
            "target_id": target_id,
            "target_type": target_type,
        }
    )


def _db_actor(db: Session, actor: Admin | object) -> Admin:
    actor_id = getattr(actor, "id", None)
    if actor_id is not None:
        result = db.get(Admin, int(actor_id))
    else:
        result = crud.get_admin(db, getattr(actor, "username", ""))
    if result is None:
        raise BulkOperationError("actor_not_found", "Bulk actor does not exist", status_code=403)
    admin_hierarchy.require_active_account(db, result)
    return result


def _selected_admins(db: Session, actor: Admin, selected_ids: Iterable[int]) -> list[Admin]:
    ids = sorted({int(value) for value in selected_ids})
    admins = db.query(Admin).filter(Admin.id.in_(ids)).order_by(Admin.id).all()
    found = {item.id for item in admins}
    missing = [value for value in ids if value not in found]
    if missing:
        raise BulkOperationError("selected_admin_not_found", f"Unknown Admin IDs: {missing}", status_code=404)
    forbidden = [item.id for item in admins if not admin_hierarchy.admin_in_scope(db, actor, item.id)]
    if forbidden:
        raise BulkOperationError(
            "bulk_scope_forbidden",
            f"Admin IDs outside actor scope: {forbidden}",
            status_code=403,
        )
    return admins


def _user_target_query(
    db: Session,
    actor: Admin,
    target_scope: BulkTargetScope,
    selected_admin_ids: list[int],
):
    query = db.query(User.id, User.username, User.admin_id).filter(User.admin_id.isnot(None))
    if target_scope == BulkTargetScope.ALL_USERS:
        if not admin_hierarchy.is_owner(db, actor):
            raise BulkOperationError(
                "all_users_owner_required",
                "ALL_USERS requires Owner authorization",
                status_code=403,
            )
    else:
        _selected_admins(db, actor, selected_admin_ids)
        if target_scope == BulkTargetScope.SELECTED_ADMINS_DIRECT:
            query = query.filter(User.admin_id.in_(selected_admin_ids))
        elif target_scope == BulkTargetScope.SELECTED_ADMINS_SUBTREE:
            query = query.filter(
                exists().where(
                    and_(
                        AdminHierarchy.ancestor_id.in_(selected_admin_ids),
                        AdminHierarchy.descendant_id == User.admin_id,
                    )
                )
            )
        else:
            raise BulkOperationError("invalid_target_scope", "Unsupported bulk target scope")

    # Inbound restrictions remain part of authorization for non-Owner actors.
    query = crud.apply_inbound_access_filter(
        query,
        marzhelp_policy.allowed_inbound_tags(db, actor),
    )
    return query.order_by(User.id)


def preview_user_job(
    db: Session,
    actor: Admin | object,
    values: BulkUserPreviewRequest,
) -> BulkPreviewResponse:
    dbactor = _db_actor(db, actor)
    query = _user_target_query(db, dbactor, values.target_scope, values.selected_admin_ids)
    count = query.order_by(None).count()
    sample = [row.username for row in query.limit(20).all()]
    return BulkPreviewResponse(
        target_scope=values.target_scope.value,
        selected_admin_ids=values.selected_admin_ids,
        resolved_target_count=count,
        sample_targets=sample,
    )


def preview_admin_job(
    db: Session,
    actor: Admin | object,
    values: BulkAdminPreviewRequest,
) -> BulkPreviewResponse:
    dbactor = _db_actor(db, actor)
    admins = _selected_admins(db, dbactor, values.selected_admin_ids)
    invalid = [
        item.id
        for item in admins
        if item.parent_admin_id is None
        or (not admin_hierarchy.is_owner(db, dbactor) and item.parent_admin_id != dbactor.id)
    ]
    if invalid:
        raise BulkOperationError(
            "direct_child_required",
            f"Credit moves require direct-child targets: {invalid}",
            status_code=403,
        )
    return BulkPreviewResponse(
        target_scope="SELECTED_ADMINS_DIRECT",
        selected_admin_ids=values.selected_admin_ids,
        resolved_target_count=len(admins),
        sample_targets=[item.username for item in admins[:20]],
    )


def _checked_replay(
    job: AdminBulkJob | None,
    *,
    job_kind: str,
    payload_fingerprint: str,
) -> AdminBulkJob | None:
    if job is None:
        return None
    if job.job_kind != job_kind or job.payload_fingerprint != payload_fingerprint:
        raise BulkOperationError(
            "idempotency_conflict",
            "Operation ID belongs to another bulk payload",
            status_code=409,
        )
    return job


def _insert_target_rows(db: Session, rows: list[dict]) -> None:
    for start in range(0, len(rows), TARGET_INSERT_CHUNK):
        db.bulk_insert_mappings(AdminBulkJobTarget, rows[start : start + TARGET_INSERT_CHUNK])


def create_user_job(
    db: Session,
    actor: Admin | object,
    values: BulkUserJobCreateRequest,
) -> tuple[AdminBulkJob, bool]:
    dbactor = _db_actor(db, actor)
    protected_operations = {
        BulkUserOperation.add_data,
        BulkUserOperation.subtract_data,
        BulkUserOperation.add_days,
        BulkUserOperation.subtract_days,
        BulkUserOperation.add_data_and_days,
    }
    actor_settings = db.get(MarzhelpAdminSettings, dbactor.id)
    if (
        values.operation in protected_operations
        and not admin_hierarchy.is_owner(db, dbactor)
        and actor_settings is not None
        and actor_settings.user_creation_mode_id == admin_hierarchy.USER_CREATION_MODE_IDS[admin_hierarchy.PLAN_ONLY]
    ):
        raise BulkOperationError(
            "plan_only_direct_edit_forbidden",
            "Plan-only administrators must change traffic and expiry through a Plan",
            status_code=403,
        )
    payload = {
        "actor_admin_id": dbactor.id,
        "job_kind": "USER",
        "operation": values.operation.value,
        "target_scope": values.target_scope.value,
        "selected_admin_ids": values.selected_admin_ids,
        "data_amount": values.data_amount,
        "days_amount": values.days_amount,
    }
    fingerprint = _fingerprint(payload)
    existing = _checked_replay(
        db.query(AdminBulkJob)
        .filter(AdminBulkJob.idempotency_key == values.operation_id)
        .one_or_none(),
        job_kind="USER",
        payload_fingerprint=fingerprint,
    )
    if existing is not None:
        return existing, False

    query = _user_target_query(db, dbactor, values.target_scope, values.selected_admin_ids)
    total = query.order_by(None).count()
    anchor_admin_id = values.selected_admin_ids[0] if values.selected_admin_ids else dbactor.id
    job = AdminBulkJob(
        actor_admin_id=dbactor.id,
        target_admin_id=anchor_admin_id,
        job_kind="USER",
        target_scope=values.target_scope.value,
        selected_admin_ids=values.selected_admin_ids,
        payload_fingerprint=fingerprint,
        operation=values.operation.value,
        amount=values.data_amount,
        days_amount=values.days_amount,
        include_subtree=values.target_scope == BulkTargetScope.SELECTED_ADMINS_SUBTREE,
        status="PENDING" if total else "COMPLETE",
        total_count=total,
        processed_count=0,
        success_count=0,
        failed_count=0,
        skipped_count=0,
        idempotency_key=values.operation_id,
        completed_at=datetime.utcnow() if total == 0 else None,
    )
    db.add(job)
    try:
        db.flush()
        rows: list[dict] = []
        cursor = 0
        sequence = 0
        while True:
            batch = query.filter(User.id > cursor).limit(TARGET_INSERT_CHUNK).all()
            if not batch:
                break
            for row in batch:
                sequence += 1
                target_key = f"{values.operation_id}:U:{row.id}"
                rows.append(
                    {
                        "job_id": job.id,
                        "target_type": "USER",
                        "target_id": row.id,
                        "sequence": sequence,
                        "target_username": row.username,
                        "owner_admin_id": row.admin_id,
                        "idempotency_key": target_key,
                        "payload_fingerprint": _target_fingerprint(job, "USER", row.id),
                        "status": "PENDING",
                        "attempts": 0,
                        "retryable": True,
                    }
                )
            _insert_target_rows(db, rows)
            rows.clear()
            cursor = batch[-1].id
        if sequence != total:
            raise BulkOperationError(
                "target_snapshot_changed",
                "Target snapshot count changed during creation",
                status_code=409,
            )
        db.commit()
        db.refresh(job)
        return job, True
    except IntegrityError:
        db.rollback()
        replay = _checked_replay(
            db.query(AdminBulkJob)
            .filter(AdminBulkJob.idempotency_key == values.operation_id)
            .one_or_none(),
            job_kind="USER",
            payload_fingerprint=fingerprint,
        )
        if replay is not None:
            return replay, False
        raise


def create_admin_job(
    db: Session,
    actor: Admin | object,
    values: BulkAdminJobCreateRequest,
) -> tuple[AdminBulkJob, bool]:
    dbactor = _db_actor(db, actor)
    admins = _selected_admins(db, dbactor, values.selected_admin_ids)
    invalid = [
        item.id
        for item in admins
        if item.parent_admin_id is None
        or (not admin_hierarchy.is_owner(db, dbactor) and item.parent_admin_id != dbactor.id)
    ]
    if invalid:
        raise BulkOperationError(
            "direct_child_required",
            f"Credit moves require direct-child targets: {invalid}",
            status_code=403,
        )
    modes = {
        row.billing_mode or "LEGACY_COMPAT"
        for row in db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id.in_([item.id for item in admins]))
        .all()
    }
    resources = {
        "user" if mode == "USER_CREDIT" else "seat" if mode == "SEAT_CREDIT" else "traffic"
        for mode in modes
    }
    if len(resources) != 1:
        raise BulkOperationError(
            "mixed_credit_resources",
            "Bulk credit targets must use the same accounting unit",
            status_code=409,
        )
    note = (values.note or "system:dashboard_bulk_credit").strip()
    payload = {
        "actor_admin_id": dbactor.id,
        "job_kind": "ADMIN_CREDIT",
        "operation": values.operation.value,
        "selected_admin_ids": values.selected_admin_ids,
        "amount": values.amount,
        "note": note,
    }
    fingerprint = _fingerprint(payload)
    existing = _checked_replay(
        db.query(AdminBulkJob)
        .filter(AdminBulkJob.idempotency_key == values.operation_id)
        .one_or_none(),
        job_kind="ADMIN_CREDIT",
        payload_fingerprint=fingerprint,
    )
    if existing is not None:
        return existing, False

    job = AdminBulkJob(
        actor_admin_id=dbactor.id,
        target_admin_id=admins[0].id,
        job_kind="ADMIN_CREDIT",
        target_scope="SELECTED_ADMINS_DIRECT",
        selected_admin_ids=values.selected_admin_ids,
        payload_fingerprint=fingerprint,
        operation=values.operation.value,
        amount=values.amount,
        note=note,
        include_subtree=False,
        status="PENDING",
        total_count=len(admins),
        processed_count=0,
        success_count=0,
        failed_count=0,
        skipped_count=0,
        idempotency_key=values.operation_id,
    )
    db.add(job)
    try:
        db.flush()
        _insert_target_rows(
            db,
            [
                {
                    "job_id": job.id,
                    "target_type": "ADMIN",
                    "target_id": item.id,
                    "sequence": sequence,
                    "target_username": item.username,
                    "owner_admin_id": item.parent_admin_id,
                    "idempotency_key": f"{values.operation_id}:A:{item.id}",
                    "payload_fingerprint": _target_fingerprint(job, "ADMIN", item.id),
                    "status": "PENDING",
                    "attempts": 0,
                    "retryable": True,
                }
                for sequence, item in enumerate(admins, start=1)
            ],
        )
        db.commit()
        db.refresh(job)
        return job, True
    except IntegrityError:
        db.rollback()
        replay = _checked_replay(
            db.query(AdminBulkJob)
            .filter(AdminBulkJob.idempotency_key == values.operation_id)
            .one_or_none(),
            job_kind="ADMIN_CREDIT",
            payload_fingerprint=fingerprint,
        )
        if replay is not None:
            return replay, False
        raise


def _next_plan_snapshot(user: User):
    if user.next_plan is None:
        return None
    return {
        "data_limit": user.next_plan.data_limit,
        "expire": user.next_plan.expire,
        "add_remaining_traffic": user.next_plan.add_remaining_traffic,
        "fire_on_either": user.next_plan.fire_on_either,
    }


def _mutate_user(db: Session, job: AdminBulkJob, target: AdminBulkJobTarget) -> tuple[str, dict]:
    actor = db.get(Admin, job.actor_admin_id)
    if actor is None:
        raise BulkOperationError("actor_not_found", "Bulk actor no longer exists")
    user = db.query(User).filter(User.id == target.target_id).with_for_update().one_or_none()
    if user is None:
        return "SKIPPED", {"code": "user_deleted_after_snapshot"}
    if user.username != target.target_username:
        raise BulkOperationError("target_identity_changed", "Target identity no longer matches snapshot")

    before = {
        "status": getattr(user.status, "value", user.status),
        "data_limit": user.data_limit,
        "expire": user.expire,
        "admin_id": user.admin_id,
    }
    operation = BulkUserOperation(job.operation)
    changes: dict = {}
    if operation == BulkUserOperation.activate:
        if user.status == UserStatus.active:
            return "SKIPPED", {"code": "already_active", "before": before}
        marzhelp_policy.validate_activation(db, user)
        changes["status"] = UserStatus.active
    elif operation == BulkUserOperation.deactivate:
        if user.status == UserStatus.disabled:
            return "SKIPPED", {"code": "already_disabled", "before": before}
        changes["status"] = UserStatus.disabled
    elif operation in (BulkUserOperation.add_data, BulkUserOperation.subtract_data):
        if user.data_limit is None:
            return "SKIPPED", {"code": "unlimited_data", "before": before}
        changes["data_limit"] = (
            int(user.data_limit) + int(job.amount or 0)
            if operation == BulkUserOperation.add_data
            else max(1, int(user.data_limit) - int(job.amount or 0))
        )
    elif operation in (BulkUserOperation.add_days, BulkUserOperation.subtract_days):
        if user.expire is None:
            return "SKIPPED", {"code": "unlimited_expiry", "before": before}
        delta = int(job.days_amount or 0) * 86400
        changes["expire"] = (
            int(user.expire) + delta
            if operation == BulkUserOperation.add_days
            else max(1, int(user.expire) - delta)
        )
    elif operation == BulkUserOperation.add_data_and_days:
        if user.data_limit is None or user.expire is None:
            return "SKIPPED", {"code": "unlimited_combined_field", "before": before}
        changes["data_limit"] = int(user.data_limit) + int(job.amount or 0)
        changes["expire"] = int(user.expire) + int(job.days_amount or 0) * 86400
    elif operation == BulkUserOperation.delete:
        marzhelp_policy.capture_delete(db, user)
        db.delete(user)
        db.flush()
        return "SUCCESS", {"before": before, "deleted": True}
    else:
        raise BulkOperationError("unsupported_operation", f"Unsupported operation: {job.operation}")

    updated = crud.update_user(
        db,
        user,
        UserModify(next_plan=_next_plan_snapshot(user), **changes),
        commit=False,
        actor=actor,
    )
    after = {
        "status": getattr(updated.status, "value", updated.status),
        "data_limit": updated.data_limit,
        "expire": updated.expire,
        "admin_id": updated.admin_id,
    }
    return "SUCCESS", {"before": before, "after": after}


def _mutate_admin(db: Session, job: AdminBulkJob, target: AdminBulkJobTarget) -> tuple[str, dict]:
    actor = db.get(Admin, job.actor_admin_id)
    admin = db.query(Admin).filter(Admin.id == target.target_id).with_for_update().one_or_none()
    if actor is None:
        raise BulkOperationError("actor_not_found", "Bulk actor no longer exists")
    if admin is None:
        return "SKIPPED", {"code": "admin_deleted_after_snapshot"}
    if admin.username != target.target_username or admin.parent_admin_id != target.owner_admin_id:
        raise BulkOperationError(
            "target_admin_changed",
            "Admin identity or parent changed after snapshot",
        )
    source = db.get(Admin, admin.parent_admin_id)
    if source is None:
        raise BulkOperationError("credit_source_missing", "Target parent credit account is missing")
    operation_type = (
        "grant" if job.operation == BulkAdminOperation.GRANT_CREDIT.value else "reclaim"
    )
    transfer, created = admin_hierarchy.transfer_credit(
        db,
        actor=actor,
        source=source,
        target=admin,
        amount=int(job.amount or 0),
        operation_type=operation_type,
        idempotency_key=target.idempotency_key,
        note=job.note,
        commit=False,
        return_created=True,
    )
    return "SUCCESS", {
        "transfer_id": transfer.id,
        "created": created,
        "balance_before": transfer.balance_before,
        "balance_after": transfer.balance_after,
        "source_delegated_before": transfer.source_delegated_before,
        "source_delegated_after": transfer.source_delegated_after,
    }


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "code", exc.__class__.__name__.lower()))[:64]


def _retryable_domain_error(code: str) -> bool:
    return code in {
        "credit_concurrent_conflict",
        "credit_exhausted",
        "reclaim_exceeds_available",
        "traffic_exhausted",
        "operation_allowance_exhausted",
        "account_read_only",
    }


def _eligible(status: str, retry_failed: bool, retryable: bool) -> bool:
    return status == "PENDING" or (retry_failed and status == "FAILED" and retryable)


def _process_target_once(
    db: Session,
    *,
    job_id: int,
    target_type: str,
    target_id: int,
    retry_failed: bool,
) -> bool:
    job = db.query(AdminBulkJob).filter(AdminBulkJob.id == job_id).with_for_update().one()
    target = (
        db.query(AdminBulkJobTarget)
        .filter(
            AdminBulkJobTarget.job_id == job_id,
            AdminBulkJobTarget.target_type == target_type,
            AdminBulkJobTarget.target_id == target_id,
        )
        .with_for_update()
        .one()
    )
    if not _eligible(target.status, retry_failed, bool(target.retryable)):
        db.commit()
        return False
    if target.payload_fingerprint != _target_fingerprint(job, target_type, target_id):
        db.rollback()
        raise BulkOperationError("target_fingerprint_mismatch", "Bulk target snapshot is corrupt")

    target.attempts = int(target.attempts or 0) + 1
    target.error_code = None
    target.error_message = None
    target.result_details = None
    try:
        with db.begin_nested():
            if target_type == "USER":
                status, details = _mutate_user(db, job, target)
            elif target_type == "ADMIN":
                status, details = _mutate_admin(db, job, target)
            else:
                raise BulkOperationError("invalid_target_type", "Unsupported bulk target type")
        target.status = status
        target.retryable = False
        target.result_details = details
        target.completed_at = datetime.utcnow()
    except OperationalError:
        raise
    except Exception as exc:
        code = _error_code(exc)
        target.status = "FAILED"
        target.retryable = _retryable_domain_error(code)
        target.error_code = code
        target.error_message = str(exc)[:512]
        target.completed_at = datetime.utcnow()
    job.status = "PROCESSING"
    job.last_user_id = target_id if target_type == "USER" else job.last_user_id
    db.commit()
    return True


def _mark_operational_failure(
    db: Session,
    *,
    job_id: int,
    target_type: str,
    target_id: int,
    code: str,
    message: str,
    retry_failed: bool,
) -> None:
    db.rollback()
    job = db.query(AdminBulkJob).filter(AdminBulkJob.id == job_id).with_for_update().one()
    target = (
        db.query(AdminBulkJobTarget)
        .filter_by(job_id=job_id, target_type=target_type, target_id=target_id)
        .with_for_update()
        .one()
    )
    if _eligible(target.status, retry_failed, bool(target.retryable)):
        target.attempts = int(target.attempts or 0) + 1
        target.status = "FAILED"
        target.retryable = True
        target.error_code = code[:64]
        target.error_message = message[:512]
        target.completed_at = datetime.utcnow()
        job.status = "PROCESSING"
    db.commit()


def _refresh_job_summary(db: Session, job_id: int) -> AdminBulkJob:
    job = db.query(AdminBulkJob).filter(AdminBulkJob.id == job_id).with_for_update().one()
    counts = dict(
        db.query(AdminBulkJobTarget.status, func.count(AdminBulkJobTarget.target_id))
        .filter(AdminBulkJobTarget.job_id == job_id)
        .group_by(AdminBulkJobTarget.status)
        .all()
    )
    pending = int(counts.get("PENDING", 0))
    success = int(counts.get("SUCCESS", 0))
    failed = int(counts.get("FAILED", 0))
    skipped = int(counts.get("SKIPPED", 0))
    job.success_count = success
    job.failed_count = failed
    job.skipped_count = skipped
    job.processed_count = success + failed + skipped
    if pending:
        job.status = "PROCESSING"
        job.completed_at = None
    elif failed:
        job.status = "PARTIAL_FAILED" if success or skipped else "FAILED"
        job.completed_at = datetime.utcnow()
    else:
        job.status = "COMPLETE"
        job.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job


def execute_job(
    db: Session,
    actor: Admin | object,
    operation_id: str,
    *,
    chunk_size: int,
    retry_failed: bool,
) -> tuple[AdminBulkJob, list[int]]:
    dbactor = _db_actor(db, actor)
    job = db.query(AdminBulkJob).filter(AdminBulkJob.idempotency_key == operation_id).one_or_none()
    if job is None or job.job_kind not in {"USER", "ADMIN_CREDIT"}:
        raise BulkOperationError("bulk_job_not_found", "Bulk job does not exist", status_code=404)
    if job.actor_admin_id != dbactor.id and not admin_hierarchy.is_owner(db, dbactor):
        raise BulkOperationError("bulk_job_forbidden", "Bulk job is outside actor scope", status_code=403)
    target_type = "USER" if job.job_kind == "USER" else "ADMIN"
    conditions = [
        and_(
            AdminBulkJobTarget.status == "PENDING",
            AdminBulkJobTarget.retryable.is_(True),
        )
    ]
    if retry_failed:
        conditions.append(
            and_(
                AdminBulkJobTarget.status == "FAILED",
                AdminBulkJobTarget.retryable.is_(True),
            )
        )
    targets = [
        row[0]
        for row in db.query(AdminBulkJobTarget.target_id)
        .filter(
            AdminBulkJobTarget.job_id == job.id,
            AdminBulkJobTarget.target_type == target_type,
            or_(*conditions),
        )
        .order_by(AdminBulkJobTarget.sequence)
        .limit(max(1, min(int(chunk_size), 500)))
        .all()
    ]
    job_id = job.id
    db.commit()
    processed: list[int] = []
    for target_id in targets:
        for attempt in range(3):
            try:
                if _process_target_once(
                    db,
                    job_id=job_id,
                    target_type=target_type,
                    target_id=target_id,
                    retry_failed=retry_failed,
                ):
                    processed.append(target_id)
                break
            except OperationalError as exc:
                db.rollback()
                mysql_code = getattr(getattr(exc, "orig", None), "args", [None])[0]
                if mysql_code not in DEADLOCK_CODES or attempt == 2:
                    _mark_operational_failure(
                        db,
                        job_id=job_id,
                        target_type=target_type,
                        target_id=target_id,
                        code="mysql_lock_retry_exhausted" if mysql_code in DEADLOCK_CODES else "database_error",
                        message=str(exc),
                        retry_failed=retry_failed,
                    )
                    break
                time.sleep(0.02 * (attempt + 1))
            except BulkOperationError as exc:
                db.rollback()
                fatal = (
                    db.query(AdminBulkJob)
                    .filter(AdminBulkJob.id == job_id)
                    .with_for_update()
                    .one()
                )
                fatal.status = "FATAL_FAILED"
                fatal.error = f"{exc.code}: {exc}"
                fatal.completed_at = datetime.utcnow()
                db.commit()
                raise
    return _refresh_job_summary(db, job_id), processed


def get_job(db: Session, actor: Admin | object, operation_id: str) -> AdminBulkJob:
    dbactor = _db_actor(db, actor)
    job = db.query(AdminBulkJob).filter(AdminBulkJob.idempotency_key == operation_id).one_or_none()
    if job is None or job.job_kind not in {"USER", "ADMIN_CREDIT"}:
        raise BulkOperationError("bulk_job_not_found", "Bulk job does not exist", status_code=404)
    if job.actor_admin_id != dbactor.id and not admin_hierarchy.is_owner(db, dbactor):
        raise BulkOperationError("bulk_job_forbidden", "Bulk job is outside actor scope", status_code=403)
    return job


def job_response(
    db: Session,
    job: AdminBulkJob,
    *,
    target_after: int = 0,
    target_limit: int = REPORT_LIMIT,
) -> BulkJobResponse:
    target_limit = max(1, min(int(target_limit), REPORT_LIMIT))
    rows = (
        db.query(AdminBulkJobTarget)
        .filter(
            AdminBulkJobTarget.job_id == job.id,
            AdminBulkJobTarget.sequence > max(0, int(target_after)),
        )
        .order_by(AdminBulkJobTarget.sequence)
        .limit(target_limit + 1)
        .all()
    )
    has_report_more = len(rows) > target_limit
    visible = rows[:target_limit]
    pending = max(
        int(job.total_count or 0)
        - int(job.success_count or 0)
        - int(job.failed_count or 0)
        - int(job.skipped_count or 0),
        0,
    )
    return BulkJobResponse(
        operation_id=job.idempotency_key,
        job_kind=job.job_kind,
        operation=job.operation,
        target_scope=job.target_scope,
        selected_admin_ids=list(job.selected_admin_ids or []),
        status=job.status,
        total=int(job.total_count or 0),
        success=int(job.success_count or 0),
        failed=int(job.failed_count or 0),
        skipped=int(job.skipped_count or 0),
        pending=pending,
        has_more=pending > 0,
        report_has_more=has_report_more,
        next_target_cursor=visible[-1].sequence if has_report_more and visible else None,
        created_at=job.created_at,
        completed_at=job.completed_at,
        targets=[
            BulkTargetResultResponse(
                target_type=row.target_type,
                target_id=row.target_id,
                target_username=row.target_username,
                owner_admin_id=row.owner_admin_id,
                status=row.status,
                attempts=int(row.attempts or 0),
                retryable=bool(row.retryable),
                error_code=row.error_code,
                error_message=row.error_message,
                result_details=row.result_details,
                completed_at=row.completed_at,
            )
            for row in visible
        ],
    )
