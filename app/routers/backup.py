import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import __version__
from app.db import Session, get_db
from app.db.models import BackupArtifact
from app.models.admin import Admin
from app.models.backup import (
    BackupArtifactResponse,
    BackupSettingsResponse,
    BackupSettingsUpdate,
    BackupValidationResponse,
)
from app.utils import admin_hierarchy, responses, stage11_operations
from config import SQLALCHEMY_DATABASE_URL, STAGE11_BACKUP_SPOOL


router = APIRouter(tags=["Backup & Restore"], prefix="/api/owner/backups", responses={401: responses._401})
MAX_RESTORE_BYTES = 2 * 1024 * 1024 * 1024
PANEL_FILE_TARGETS = [Path("/opt/marzban/.env"), Path("/var/lib/marzban/certs"), Path("/var/lib/marzban/xray_config.json")]


def _owner(db: Session, actor: Admin) -> None:
    if not admin_hierarchy.is_owner(db, actor):
        raise HTTPException(status_code=403, detail={"code": "owner_required", "message": "Owner access required"})


def _artifact(row: BackupArtifact) -> BackupArtifactResponse:
    return BackupArtifactResponse(
        id=row.id,
        period_key=row.period_key,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        generation_status=row.generation_status,
        delivery_status=row.delivery_status,
        error_code=row.error_code,
    )


@router.get("/settings", response_model=BackupSettingsResponse)
def get_settings(db: Session = Depends(get_db), actor: Admin = Depends(Admin.get_current)):
    _owner(db, actor)
    return stage11_operations.settings_payload(stage11_operations.backup_settings(db))


@router.put("/settings", response_model=BackupSettingsResponse)
def put_settings(values: BackupSettingsUpdate, db: Session = Depends(get_db), actor: Admin = Depends(Admin.get_current)):
    _owner(db, actor)
    return stage11_operations.settings_payload(stage11_operations.update_backup_settings(db, values))


@router.get("", response_model=list[BackupArtifactResponse])
def list_backups(db: Session = Depends(get_db), actor: Admin = Depends(Admin.get_current)):
    _owner(db, actor)
    rows = db.query(BackupArtifact).order_by(BackupArtifact.created_at.desc()).limit(100).all()
    return [_artifact(row) for row in rows]


def _create(db: Session, period: str) -> BackupArtifact:
    spool = Path(STAGE11_BACKUP_SPOOL)
    archive, manifest = stage11_operations.create_panel_backup(
        SQLALCHEMY_DATABASE_URL,
        spool,
        period,
        app_version=__version__,
        include_paths=PANEL_FILE_TARGETS,
    )
    row = BackupArtifact(
        period_key=period,
        database_name=manifest["database_name"],
        encrypted_path=str(archive),
        size_bytes=archive.stat().st_size,
        sha256=manifest["archive_sha256"],
        generation_status="SUCCESS",
        delivery_status="LOCAL",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    settings = stage11_operations.backup_settings(db)
    stage11_operations.enforce_retention(spool, settings.retention_count)
    return row


@router.post("", response_model=BackupArtifactResponse)
def create_backup(db: Session = Depends(get_db), actor: Admin = Depends(Admin.get_current)):
    _owner(db, actor)
    return _artifact(_create(db, datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")))


def _save_upload(upload: UploadFile) -> Path:
    suffix = ".panel-backup.zip"
    with NamedTemporaryFile(prefix="panel-restore-", suffix=suffix, delete=False) as target:
        total = 0
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_RESTORE_BYTES:
                raise HTTPException(status_code=413, detail={"code": "backup_too_large", "message": "Backup exceeds 2 GiB"})
            target.write(chunk)
        return Path(target.name)


@router.post("/validate", response_model=BackupValidationResponse)
def validate_backup(backup: UploadFile = File(...), db: Session = Depends(get_db), actor: Admin = Depends(Admin.get_current)):
    _owner(db, actor)
    path = _save_upload(backup)
    try:
        manifest = stage11_operations.validate_panel_backup(path)
        return BackupValidationResponse(valid=True, manifest=manifest, validation_token=stage11_operations.archive_sha256(path))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc), "message": "Invalid backup archive"}) from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("/restore")
def restore_backup(
    validation_token: str,
    backup: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: Admin = Depends(Admin.get_current),
):
    _owner(db, actor)
    path = _save_upload(backup)
    maintenance = Path(STAGE11_BACKUP_SPOOL) / ".maintenance"
    try:
        stage11_operations.validate_panel_backup(path)
        digest = stage11_operations.archive_sha256(path)
        if digest != validation_token:
            raise HTTPException(status_code=409, detail={"code": "validation_token_mismatch", "message": "Backup differs from validated upload"})
        _create(db, datetime.utcnow().strftime("pre-restore-%Y%m%dT%H%M%S%f"))
        maintenance.parent.mkdir(parents=True, exist_ok=True)
        maintenance.write_text("restore-in-progress\n", encoding="utf-8")
        stage11_operations.restore_mysql_dump(SQLALCHEMY_DATABASE_URL, path)
        restored_files = stage11_operations.restore_panel_files(path, PANEL_FILE_TARGETS)
        migration = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, check=False)
        if migration.returncode != 0:
            raise RuntimeError("post_restore_migration_failed")
        return {"status": "RESTORED", "restored_files": restored_files, "health_check": "restart_required"}
    finally:
        maintenance.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
