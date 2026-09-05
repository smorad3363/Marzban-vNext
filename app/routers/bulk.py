from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from app import xray
from app.db import Session, get_db
from app.db.models import AdminBulkJobTarget
from app.models.admin import Admin
from app.models.bulk import (
    BulkAdminJobCreateRequest,
    BulkAdminPreviewRequest,
    BulkJobExecuteRequest,
    BulkJobResponse,
    BulkPreviewResponse,
    BulkUserJobCreateRequest,
    BulkUserPreviewRequest,
    BulkSelectionPreview,
    BulkSelectionRequest,
    BulkSelectionResponse,
)
from app.utils import bulk_operations, responses
from app.utils.audit import AuditLogService


router = APIRouter(tags=["Bulk Operations"], prefix="/api", responses={401: responses._401})


@router.post("/users/bulk-selection/preview", response_model=BulkSelectionPreview)
def preview_checked_users(
    values: BulkSelectionRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        return bulk_operations.preview_selection(db, admin, values)
    except Exception as exc:
        _raise_domain(exc)


@router.post("/users/bulk-selection/execute", response_model=BulkSelectionResponse)
def execute_checked_users(
    values: BulkSelectionRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        result = bulk_operations.execute_selection(db, admin, values)
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)
    if result.success:
        bg.add_task(xray.operations.restart_all_cores)
    AuditLogService.log(
        db,
        admin,
        "bulk.selection.execute",
        "users",
        f"Admin {admin.username} processed {len(values.user_ids)} checked users",
        details={
            "operation_id": values.operation_id,
            "user_ids": values.user_ids,
            "actions": [action.model_dump(mode="json") for action in values.actions],
            "success": result.success,
            "failed": result.failed,
        },
        request=request,
    )
    return result


def _raise_domain(exc: Exception) -> None:
    if isinstance(exc, bulk_operations.BulkOperationError):
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    code = getattr(exc, "code", None)
    if code:
        status = 403 if "forbidden" in code or "required" in code else 409
        raise HTTPException(status_code=status, detail={"code": code, "message": str(exc)}) from exc
    raise exc


def _audit_create(request: Request, db: Session, admin: Admin, job, created: bool) -> None:
    if not created:
        return
    AuditLogService.log(
        db,
        admin,
        "bulk.job.create",
        "bulk_job",
        f"Admin {admin.username} created bulk job {job.idempotency_key}",
        target_id=job.id,
        target_name=job.idempotency_key,
        details={
            "operation_id": job.idempotency_key,
            "job_kind": job.job_kind,
            "operation": job.operation,
            "target_scope": job.target_scope,
            "selected_admin_ids": job.selected_admin_ids or [],
            "resolved_target_count": int(job.total_count or 0),
        },
        request=request,
    )


def _execute(
    operation_id: str,
    values: BulkJobExecuteRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session,
    admin: Admin,
    expected_kind: str,
) -> BulkJobResponse:
    try:
        existing = bulk_operations.get_job(db, admin, operation_id)
        if existing.job_kind != expected_kind:
            raise bulk_operations.BulkOperationError(
                "bulk_job_kind_mismatch",
                "Bulk job belongs to another endpoint",
                status_code=409,
            )
        job, processed_ids = bulk_operations.execute_job(
            db,
            admin,
            operation_id,
            chunk_size=values.chunk_size,
            retry_failed=values.retry_failed,
        )
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)

    successful = 0
    if processed_ids:
        target_type = "USER" if expected_kind == "USER" else "ADMIN"
        successful = (
            db.query(AdminBulkJobTarget)
            .filter(
                AdminBulkJobTarget.job_id == job.id,
                AdminBulkJobTarget.target_type == target_type,
                AdminBulkJobTarget.target_id.in_(processed_ids),
                AdminBulkJobTarget.status == "SUCCESS",
            )
            .count()
        )
    if expected_kind == "USER" and successful:
        # One bounded post-commit reload synchronizes every successful mutation in
        # this chunk without holding database locks during Xray I/O.
        bg.add_task(xray.operations.restart_all_cores)
    AuditLogService.log(
        db,
        admin,
        "bulk.job.execute",
        "bulk_job",
        f"Admin {admin.username} processed bulk job {operation_id}",
        target_id=job.id,
        target_name=operation_id,
        details={
            "operation_id": operation_id,
            "chunk_size": values.chunk_size,
            "retry_failed": values.retry_failed,
            "processed_in_request": len(processed_ids),
            "successful_in_request": successful,
            "total": int(job.total_count or 0),
            "success": int(job.success_count or 0),
            "failed": int(job.failed_count or 0),
            "skipped": int(job.skipped_count or 0),
            "status": job.status,
        },
        request=request,
    )
    return bulk_operations.job_response(db, job)


@router.post("/users/bulk/preview", response_model=BulkPreviewResponse)
def preview_users(
    values: BulkUserPreviewRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        return bulk_operations.preview_user_job(db, admin, values)
    except Exception as exc:
        _raise_domain(exc)


@router.post("/users/bulk/jobs", response_model=BulkJobResponse)
def create_user_job(
    values: BulkUserJobCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        job, created = bulk_operations.create_user_job(db, admin, values)
        _audit_create(request, db, admin, job, created)
        return bulk_operations.job_response(db, job)
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)


@router.post("/users/bulk/jobs/{operation_id}/execute", response_model=BulkJobResponse)
def execute_user_job(
    operation_id: str,
    values: BulkJobExecuteRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _execute(operation_id, values, request, bg, db, admin, "USER")


@router.get("/users/bulk/jobs/{operation_id}", response_model=BulkJobResponse)
def get_user_job(
    operation_id: str,
    target_after: int = Query(default=0, ge=0),
    target_limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        job = bulk_operations.get_job(db, admin, operation_id)
        if job.job_kind != "USER":
            raise bulk_operations.BulkOperationError("bulk_job_kind_mismatch", "Not a User bulk job", status_code=409)
        return bulk_operations.job_response(
            db,
            job,
            target_after=target_after,
            target_limit=target_limit,
        )
    except Exception as exc:
        _raise_domain(exc)


@router.post("/admin-management/bulk-credit/preview", response_model=BulkPreviewResponse)
def preview_admin_credit(
    values: BulkAdminPreviewRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        return bulk_operations.preview_admin_job(db, admin, values)
    except Exception as exc:
        _raise_domain(exc)


@router.post("/admin-management/bulk-credit/jobs", response_model=BulkJobResponse)
def create_admin_credit_job(
    values: BulkAdminJobCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        job, created = bulk_operations.create_admin_job(db, admin, values)
        _audit_create(request, db, admin, job, created)
        return bulk_operations.job_response(db, job)
    except Exception as exc:
        db.rollback()
        _raise_domain(exc)


@router.post("/admin-management/bulk-credit/jobs/{operation_id}/execute", response_model=BulkJobResponse)
def execute_admin_credit_job(
    operation_id: str,
    values: BulkJobExecuteRequest,
    request: Request,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    return _execute(operation_id, values, request, bg, db, admin, "ADMIN_CREDIT")


@router.get("/admin-management/bulk-credit/jobs/{operation_id}", response_model=BulkJobResponse)
def get_admin_credit_job(
    operation_id: str,
    target_after: int = Query(default=0, ge=0),
    target_limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    try:
        job = bulk_operations.get_job(db, admin, operation_id)
        if job.job_kind != "ADMIN_CREDIT":
            raise bulk_operations.BulkOperationError("bulk_job_kind_mismatch", "Not an Admin credit job", status_code=409)
        return bulk_operations.job_response(
            db,
            job,
            target_after=target_after,
            target_limit=target_limit,
        )
    except Exception as exc:
        _raise_domain(exc)
