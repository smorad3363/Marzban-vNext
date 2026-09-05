from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import smtplib
import subprocess
import tempfile
import zipfile
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import BackupArtifact, BackupSettings, TelegramOutbox


BACKUP_FORMAT = "panel-backup-v1"
SCHEDULE_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "3h": 10800, "6h": 21600, "12h": 43200, "24h": 86400}


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_outbox(db: Session, *, idempotency_key: str, event_type: str, payload: dict) -> TelegramOutbox:
    existing = db.query(TelegramOutbox).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    row = TelegramOutbox(idempotency_key=idempotency_key, event_type=event_type, payload=payload)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return db.query(TelegramOutbox).filter_by(idempotency_key=idempotency_key).one()
    return row


def dispatch_outbox(db: Session, sender: Callable[[TelegramOutbox], None], *, limit: int = 25, max_attempts: int = 6) -> int:
    rows = (db.query(TelegramOutbox)
            .filter(TelegramOutbox.status.in_(("PENDING", "RETRYING")), TelegramOutbox.next_attempt_at <= now())
            .order_by(TelegramOutbox.next_attempt_at, TelegramOutbox.id)
            .limit(limit).with_for_update(skip_locked=True).all())
    processed = 0
    for row in rows:
        try:
            sender(row)
            row.status = "DELIVERED"
            row.completed_at = now()
            row.last_error_code = None
            if row.event_type == "backup.ready" and row.payload.get("artifact_id"):
                artifact = db.get(BackupArtifact, row.payload["artifact_id"])
                if artifact:
                    artifact.delivery_status = "DELIVERED"
                    artifact.delivered_at = now()
                    artifact.error_code = None
        except Exception as exc:
            row.attempts += 1
            row.last_error_code = type(exc).__name__[:64]
            row.status = "DEAD_LETTER" if row.attempts >= max_attempts else "RETRYING"
            row.completed_at = now() if row.status == "DEAD_LETTER" else None
            row.next_attempt_at = now() + timedelta(seconds=min(3600, 30 * (2 ** min(row.attempts, 7))))
            if row.event_type == "backup.ready" and row.payload.get("artifact_id"):
                artifact = db.get(BackupArtifact, row.payload["artifact_id"])
                if artifact:
                    artifact.delivery_status = "FAILED" if row.status == "DEAD_LETTER" else "RETRYING"
                    artifact.error_code = row.last_error_code
        processed += 1
    db.commit()
    return processed


def purge_outbox(db: Session, *, batch_size: int = 500, current: datetime | None = None) -> int:
    current = current or now()
    ids = [r[0] for r in (db.query(TelegramOutbox.id)
        .filter(((TelegramOutbox.status == "DELIVERED") & (TelegramOutbox.completed_at < current - timedelta(days=30))) |
                ((TelegramOutbox.status.in_(("FAILED", "DEAD_LETTER"))) & (TelegramOutbox.completed_at < current - timedelta(days=90))))
        .order_by(TelegramOutbox.id).limit(batch_size).all())]
    if ids:
        db.query(TelegramOutbox).filter(TelegramOutbox.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    return len(ids)


def purge_delivered_backup_files(db: Session, *, current: datetime | None = None, batch_size: int = 100) -> int:
    current = current or now()
    newest_valid_id = (db.query(BackupArtifact.id)
        .filter(BackupArtifact.generation_status == "SUCCESS")
        .order_by(BackupArtifact.created_at.desc(), BackupArtifact.id.desc()).limit(1).scalar())
    rows = (db.query(BackupArtifact)
        .filter(BackupArtifact.delivery_status == "DELIVERED",
                BackupArtifact.delivered_at < current - timedelta(hours=48),
                BackupArtifact.id != newest_valid_id)
        .order_by(BackupArtifact.id).limit(batch_size).all())
    removed = 0
    for row in rows:
        if row.encrypted_path:
            Path(row.encrypted_path).unlink(missing_ok=True)
            row.encrypted_path = None
            removed += 1
    db.commit()
    return removed


def encrypt_backup(source: Path, destination: Path, key_b64: str) -> tuple[int, str]:
    key = base64.b64decode(key_b64, validate=True)
    if len(key) != 32:
        raise ValueError("STAGE11_BACKUP_KEY must decode to exactly 32 bytes")
    plaintext = source.read_bytes()
    if not plaintext or b"CREATE TABLE" not in plaintext.upper():
        raise ValueError("backup_artifact_invalid")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, b"marzban-mysql-backup-v1")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"MZB1" + nonce + ciphertext)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return destination.stat().st_size, digest


def decrypt_backup(source: Path, destination: Path, key_b64: str) -> None:
    raw = source.read_bytes()
    if raw[:4] != b"MZB1":
        raise ValueError("backup_envelope_invalid")
    key = base64.b64decode(key_b64, validate=True)
    destination.write_bytes(AESGCM(key).decrypt(raw[4:16], raw[16:], b"marzban-mysql-backup-v1"))


def mysql_dump_command(database_url: str) -> tuple[list[str], dict[str, str], str]:
    parsed = urlparse(database_url.replace("mysql+pymysql://", "mysql://", 1))
    database = parsed.path.lstrip("/")
    command = ["mysqldump", "--single-transaction", "--routines", "--triggers", "--hex-blob",
               "--host", parsed.hostname or "127.0.0.1", "--port", str(parsed.port or 3306),
               "--user", unquote(parsed.username or "root"), database]
    env = os.environ.copy()
    if parsed.password:
        env["MYSQL_PWD"] = unquote(parsed.password)
    return command, env, database


def generate_backup(database_url: str, spool: Path, key_b64: str, period_key: str) -> tuple[Path, int, str, str]:
    command, env, database = mysql_dump_command(database_url)
    plain = spool / f"{period_key}.sql.tmp"
    encrypted = spool / f"{period_key}.sql.aesgcm"
    spool.mkdir(parents=True, exist_ok=True)
    with plain.open("wb") as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, env=env, check=False)
    try:
        if result.returncode != 0:
            raise RuntimeError("mysqldump_failed")
        size, digest = encrypt_backup(plain, encrypted, key_b64)
        return encrypted, size, digest, database
    finally:
        plain.unlink(missing_ok=True)


def backup_settings(db: Session) -> BackupSettings:
    row = db.get(BackupSettings, 1)
    if row is None:
        row = BackupSettings(id=1)
        db.add(row)
        db.flush()
    return row


def settings_payload(row: BackupSettings) -> dict:
    return {
        "enabled": bool(row.enabled),
        "destination": row.destination,
        "schedule": row.schedule,
        "retention_count": int(row.retention_count),
        "telegram_bot_token": None,
        "telegram_chat_id": row.telegram_chat_id,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_username": row.smtp_username,
        "smtp_password": None,
        "smtp_use_tls": bool(row.smtp_use_tls),
        "email_from": row.email_from,
        "email_to": row.email_to,
        "telegram_configured": bool(row.telegram_bot_token and row.telegram_chat_id),
        "smtp_configured": bool(row.smtp_host and row.smtp_port and row.email_from and row.email_to),
    }


def update_backup_settings(db: Session, values) -> BackupSettings:
    row = backup_settings(db)
    updates = values.model_dump()
    merged = {
        field: (getattr(row, field) if field in {"telegram_bot_token", "smtp_password"} and value is None else value)
        for field, value in updates.items()
    }
    if "TELEGRAM" in merged["destination"] and not (merged["telegram_bot_token"] and merged["telegram_chat_id"]):
        raise ValueError("Telegram destination requires bot token and chat ID")
    if "EMAIL" in merged["destination"] and not (merged["smtp_host"] and merged["smtp_port"] and merged["email_from"] and merged["email_to"]):
        raise ValueError("Email destination requires SMTP host/port and From/To")
    for field, value in updates.items():
        if field in {"telegram_bot_token", "smtp_password"} and value is None:
            continue
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_sha256(path: Path) -> str:
    return _sha256(path)


def create_panel_backup(
    database_url: str,
    spool: Path,
    period_key: str,
    *,
    app_version: str,
    include_paths: list[Path] | None = None,
) -> tuple[Path, dict]:
    """Create one portable archive with a logical dump and checksummed manifest."""
    command, env, database = mysql_dump_command(database_url)
    spool.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="panel-backup-", dir=spool))
    archive = spool / f"{period_key}.panel-backup.zip"
    try:
        dump = work / "database.sql"
        with dump.open("wb") as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, env=env, check=False)
        if result.returncode != 0 or not dump.exists() or dump.stat().st_size == 0:
            raise RuntimeError("mysqldump_failed")
        files = {"database.sql": _sha256(dump)}
        copied: list[tuple[Path, str]] = [(dump, "database.sql")]
        restore_roots = {}
        for root_index, requested in enumerate(include_paths or []):
            requested = requested.resolve()
            archive_root = f"files/{root_index}-{requested.name}"
            restore_roots[archive_root] = str(requested)
            candidates = [requested] if requested.is_file() else (
                sorted(path for path in requested.rglob("*") if path.is_file()) if requested.is_dir() else []
            )
            for source in candidates:
                relative = source.name if requested.is_file() else source.relative_to(requested).as_posix()
                name = f"{archive_root}/{relative}"
                target = work / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files[name] = _sha256(target)
                copied.append((target, name))
        manifest = {
            "format": BACKUP_FORMAT,
            "app_version": app_version,
            "database_engine": "mysql",
            "database_name": database,
            "database_target_version": "26.7.0",
            "created_at": now().isoformat() + "Z",
            "files": files,
            "restore_roots": restore_roots,
        }
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            bundle.write(manifest_path, "manifest.json")
            for source, name in copied:
                bundle.write(source, name)
        manifest["archive_sha256"] = _sha256(archive)
        manifest["size_bytes"] = archive.stat().st_size
        return archive, manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def validate_panel_backup(archive: Path) -> dict:
    if not archive.is_file() or archive.stat().st_size == 0:
        raise ValueError("backup_archive_empty")
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            if "manifest.json" not in names or "database.sql" not in names:
                raise ValueError("backup_manifest_missing")
            if any(name.startswith(("/", "\\")) or ".." in Path(name).parts for name in names):
                raise ValueError("backup_archive_unsafe_path")
            manifest = json.loads(bundle.read("manifest.json"))
            if manifest.get("format") != BACKUP_FORMAT or manifest.get("database_engine") != "mysql":
                raise ValueError("backup_format_unsupported")
            database_has_schema = False
            for name, expected in manifest.get("files", {}).items():
                if name not in names:
                    raise ValueError("backup_checksum_mismatch")
                digest = hashlib.sha256()
                with bundle.open(name) as member:
                    while chunk := member.read(1024 * 1024):
                        digest.update(chunk)
                        if name == "database.sql" and b"CREATE TABLE" in chunk.upper():
                            database_has_schema = True
                if digest.hexdigest() != expected:
                    raise ValueError("backup_checksum_mismatch")
            if not database_has_schema:
                raise ValueError("backup_database_invalid")
            return manifest
    except zipfile.BadZipFile as exc:
        raise ValueError("backup_archive_invalid") from exc


def restore_mysql_dump(database_url: str, archive: Path) -> None:
    """Validate completely before invoking mysql; callers create the pre-restore backup first."""
    validate_panel_backup(archive)
    parsed = urlparse(database_url.replace("mysql+pymysql://", "mysql://", 1))
    command = [
        "mysql", "--host", parsed.hostname or "127.0.0.1", "--port", str(parsed.port or 3306),
        "--user", unquote(parsed.username or "root"), unquote(parsed.path.lstrip("/")),
    ]
    env = os.environ.copy()
    if parsed.password:
        env["MYSQL_PWD"] = unquote(parsed.password)
    with tempfile.NamedTemporaryFile(prefix="panel-restore-db-", suffix=".sql") as extracted:
        with zipfile.ZipFile(archive) as bundle, bundle.open("database.sql") as source:
            shutil.copyfileobj(source, extracted)
        extracted.flush()
        extracted.seek(0)
        result = subprocess.run(command, stdin=extracted, stderr=subprocess.PIPE, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError("mysql_restore_failed")


def restore_panel_files(archive: Path, allowed_targets: list[Path]) -> int:
    manifest = validate_panel_backup(archive)
    allowed = {str(path.resolve()): path.resolve() for path in allowed_targets}
    restored = 0
    with zipfile.ZipFile(archive) as bundle:
        for archive_root, raw_target in manifest.get("restore_roots", {}).items():
            target = Path(raw_target).resolve()
            if str(target) not in allowed:
                raise ValueError("backup_restore_target_forbidden")
            members = [name for name in bundle.namelist() if name.startswith(archive_root + "/")]
            for name in members:
                relative = Path(name[len(archive_root) + 1:])
                destination = target if target.suffix and len(members) == 1 else target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".restore-tmp")
                with bundle.open(name) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                temporary.replace(destination)
                restored += 1
    return restored


def enforce_retention(spool: Path, retention_count: int) -> int:
    archives = sorted(spool.glob("*.panel-backup.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed = 0
    for path in archives[max(int(retention_count), 1):]:
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def send_backup_email(settings: BackupSettings, archive: Path, *, max_attachment_bytes: int = 25 * 1024 * 1024) -> None:
    """Email is deliberately one complete file; the local archive survives all failures."""
    if archive.stat().st_size > max_attachment_bytes:
        raise RuntimeError("smtp_attachment_limit_exceeded")
    if not (settings.smtp_host and settings.smtp_port and settings.email_from and settings.email_to):
        raise RuntimeError("smtp_not_configured")
    message = EmailMessage()
    message["Subject"] = f"Panel backup {archive.name}"
    message["From"] = settings.email_from
    message["To"] = settings.email_to
    message.set_content("Complete panel backup attached.")
    message.add_attachment(
        archive.read_bytes(),
        maintype="application",
        subtype="zip",
        filename=archive.name,
    )
    smtp_class = smtplib.SMTP_SSL if settings.smtp_use_tls and int(settings.smtp_port) == 465 else smtplib.SMTP
    with smtp_class(settings.smtp_host, int(settings.smtp_port), timeout=30) as client:
        if settings.smtp_use_tls and int(settings.smtp_port) != 465:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password or "")
        client.send_message(message)


def telegram_parts(archive: Path, max_bytes: int) -> list[Path]:
    if archive.stat().st_size <= max_bytes:
        return [archive]
    parts = []
    with archive.open("rb") as source:
        index = 1
        while chunk := source.read(max_bytes):
            part = archive.with_name(f"{archive.name}.part{index:03d}")
            part.write_bytes(chunk)
            parts.append(part)
            index += 1
    return parts
