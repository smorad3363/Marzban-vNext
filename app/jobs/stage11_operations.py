from pathlib import Path
from datetime import timezone
from sqlalchemy.exc import IntegrityError

from app import logger, scheduler
from app.db import GetDB
from app.db.models import BackupArtifact
from app.utils.stage11_operations import (dispatch_outbox, enqueue_outbox,
                                          generate_backup, now,
                                          purge_delivered_backup_files,
                                          purge_outbox)
from config import (SQLALCHEMY_DATABASE_URL, STAGE11_BACKUP_ENABLED,
                    STAGE11_BACKUP_INTERVAL_MINUTES, STAGE11_BACKUP_KEY,
                    STAGE11_BACKUP_SPOOL, STAGE11_OUTBOX_MAX_ATTEMPTS,
                    STAGE11_TELEGRAM_CHAT_ID, STAGE11_TELEGRAM_MAX_BYTES)


def telegram_sender(row):
    from app.telegram import bot
    if not bot or not STAGE11_TELEGRAM_CHAT_ID:
        raise RuntimeError("telegram_not_configured")
    if row.event_type == "backup.ready":
        path = Path(row.payload["path"])
        if path.stat().st_size > STAGE11_TELEGRAM_MAX_BYTES:
            raise RuntimeError("telegram_backup_size_exceeded")
        with path.open("rb") as stream:
            bot.send_document(STAGE11_TELEGRAM_CHAT_ID, stream, caption=f"MySQL backup {row.payload['sha256']}")
    else:
        bot.send_message(STAGE11_TELEGRAM_CHAT_ID, str(row.payload))


def process_stage11_outbox():
    with GetDB() as db:
        dispatch_outbox(db, telegram_sender, max_attempts=STAGE11_OUTBOX_MAX_ATTEMPTS)


def create_stage11_backup():
    period = now().strftime("%Y%m%dT%H%M")
    with GetDB() as db:
        artifact = BackupArtifact(period_key=period, database_name="pending")
        db.add(artifact)
        try:
            db.commit()
        except Exception:
            db.rollback()
            return
        try:
            path, size, digest, database = generate_backup(SQLALCHEMY_DATABASE_URL, Path(STAGE11_BACKUP_SPOOL), STAGE11_BACKUP_KEY, period)
            artifact.database_name = database
            artifact.encrypted_path = str(path)
            artifact.size_bytes = size
            artifact.sha256 = digest
            artifact.generation_status = "SUCCESS"
            enqueue_outbox(db, idempotency_key=f"backup:{period}", event_type="backup.ready",
                           payload={"artifact_id": artifact.id, "path": str(path), "size": size,
                                    "sha256": digest, "database": database})
            db.commit()
        except Exception as exc:
            artifact.generation_status = "FAILED"
            artifact.delivery_status = "BLOCKED"
            artifact.error_code = type(exc).__name__[:64]
            db.commit()
            logger.error("Stage 11 backup generation failed: %s", type(exc).__name__)


def cleanup_stage11_history():
    with GetDB() as db:
        purge_outbox(db)
        purge_delivered_backup_files(db)


def create_configured_panel_backup():
    from app import __version__
    from app.utils import stage11_operations as operations
    from app.routers.backup import PANEL_FILE_TARGETS

    with GetDB() as db:
        settings = operations.backup_settings(db)
        if not settings.enabled:
            return
        seconds = operations.SCHEDULE_SECONDS[settings.schedule]
        period = f"scheduled-{settings.schedule}-{int(now().replace(tzinfo=timezone.utc).timestamp()) // seconds}"
        artifact = BackupArtifact(period_key=period, database_name="pending")
        db.add(artifact)
        try:
            db.commit()  # Unique period claim prevents duplicate multi-worker backups.
        except IntegrityError:
            db.rollback()
            return
        try:
            archive, manifest = operations.create_panel_backup(
                SQLALCHEMY_DATABASE_URL, Path(STAGE11_BACKUP_SPOOL), period,
                app_version=__version__, include_paths=PANEL_FILE_TARGETS,
            )
            artifact.database_name = manifest["database_name"]
            artifact.encrypted_path = str(archive)
            artifact.size_bytes = archive.stat().st_size
            artifact.sha256 = manifest["archive_sha256"]
            artifact.generation_status = "SUCCESS"
            db.commit()
            operations.deliver_panel_backup(db, artifact)
            operations.enforce_retention(Path(STAGE11_BACKUP_SPOOL), settings.retention_count)
        except Exception as exc:
            db.rollback()
            artifact.generation_status = "FAILED"
            artifact.error_code = type(exc).__name__[:64]
            db.commit()


scheduler.add_job(process_stage11_outbox, "interval", seconds=30, coalesce=True, max_instances=1)
scheduler.add_job(cleanup_stage11_history, "interval", hours=6, coalesce=True, max_instances=1)
scheduler.add_job(create_configured_panel_backup, "interval", minutes=1, coalesce=True, max_instances=1)
if STAGE11_BACKUP_ENABLED:
    scheduler.add_job(create_stage11_backup, "interval", minutes=STAGE11_BACKUP_INTERVAL_MINUTES,
                      coalesce=True, max_instances=1)
