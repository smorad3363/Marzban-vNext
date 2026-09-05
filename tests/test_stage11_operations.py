import base64
import hashlib
import json
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import BackupSettings, TelegramOutbox
from app.models.backup import BackupSettingsUpdate
from app.utils.stage11_operations import (decrypt_backup, dispatch_outbox,
                                          encrypt_backup, enqueue_outbox, now,
                                          purge_outbox, telegram_parts,
                                          update_backup_settings,
                                          validate_panel_backup)


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_outbox_is_idempotent_and_business_commit_is_independent():
    db = session()
    first = enqueue_outbox(db, idempotency_key="operation:1", event_type="audit", payload={"safe": True})
    second = enqueue_outbox(db, idempotency_key="operation:1", event_type="audit", payload={"safe": True})
    db.commit()
    assert first.id == second.id
    assert db.query(TelegramOutbox).count() == 1


def test_transient_failure_retries_then_delivery_does_not_duplicate_business_event():
    db = session()
    enqueue_outbox(db, idempotency_key="operation:2", event_type="audit", payload={})
    db.commit()
    attempts = []

    def failing(row):
        attempts.append(row.id)
        raise TimeoutError()

    assert dispatch_outbox(db, failing, max_attempts=3) == 1
    row = db.query(TelegramOutbox).one()
    assert row.status == "RETRYING" and row.attempts == 1
    row.next_attempt_at = now() - timedelta(seconds=1)
    db.commit()
    assert dispatch_outbox(db, lambda item: attempts.append(item.id), max_attempts=3) == 1
    assert db.query(TelegramOutbox).one().status == "DELIVERED"
    assert attempts == [row.id, row.id]
    assert dispatch_outbox(db, lambda item: attempts.append(item.id)) == 0


def test_retry_budget_dead_letters_and_records_safe_error_code():
    db = session()
    enqueue_outbox(db, idempotency_key="operation:3", event_type="audit", payload={})
    db.commit()
    row = db.query(TelegramOutbox).one()
    for _ in range(2):
        row.next_attempt_at = now() - timedelta(seconds=1)
        db.commit()
        dispatch_outbox(db, lambda _: (_ for _ in ()).throw(ConnectionError("secret")), max_attempts=2)
    row = db.query(TelegramOutbox).one()
    assert row.status == "DEAD_LETTER"
    assert row.last_error_code == "ConnectionError"


def test_cleanup_is_bounded_and_never_purges_pending_or_recent_rows():
    db = session()
    old = now() - timedelta(days=100)
    for index, status in enumerate(("DELIVERED", "DEAD_LETTER", "PENDING")):
        row = TelegramOutbox(idempotency_key=f"cleanup:{index}", event_type="audit", payload={}, status=status,
                             next_attempt_at=now(), created_at=old, completed_at=old if status != "PENDING" else None)
        db.add(row)
    db.commit()
    assert purge_outbox(db, batch_size=1) == 1
    assert db.query(TelegramOutbox).filter_by(status="PENDING").count() == 1
    assert purge_outbox(db, batch_size=10) == 1


def test_authenticated_encryption_round_trip_and_tamper_detection(tmp_path):
    key = base64.b64encode(b"k" * 32).decode()
    source = tmp_path / "dump.sql"
    encrypted = tmp_path / "dump.sql.aesgcm"
    restored = tmp_path / "restored.sql"
    source.write_bytes(b"CREATE TABLE proof (id INT); INSERT INTO proof VALUES (1);")
    size, digest = encrypt_backup(source, encrypted, key)
    assert size > source.stat().st_size and len(digest) == 64
    decrypt_backup(encrypted, restored, key)
    assert restored.read_bytes() == source.read_bytes()
    raw = bytearray(encrypted.read_bytes())
    raw[-1] ^= 1
    encrypted.write_bytes(raw)
    try:
        decrypt_backup(encrypted, restored, key)
    except Exception:
        pass
    else:
        raise AssertionError("tampered backup decrypted")


def test_panel_archive_manifest_checksum_and_transport_split(tmp_path):
    archive = tmp_path / "proof.panel-backup.zip"
    sql = b"CREATE TABLE proof (id INT);"
    manifest = {
        "format": "panel-backup-v1",
        "database_engine": "mysql",
        "files": {"database.sql": hashlib.sha256(sql).hexdigest()},
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("database.sql", sql)
        bundle.writestr("manifest.json", json.dumps(manifest))
    assert validate_panel_backup(archive)["database_engine"] == "mysql"
    parts = telegram_parts(archive, max_bytes=20)
    assert len(parts) > 1
    assert b"".join(part.read_bytes() for part in parts) == archive.read_bytes()

    manifest["files"]["database.sql"] = "0" * 64
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("database.sql", sql)
        bundle.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="backup_checksum_mismatch"):
        validate_panel_backup(archive)


def test_backup_settings_keep_redacted_secrets_on_update():
    db = session()
    db.add(BackupSettings(
        id=1,
        destination="TELEGRAM_EMAIL",
        telegram_bot_token="secret-token",
        telegram_chat_id="1234",
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_password="secret-password",
        email_from="from@example.test",
        email_to="to@example.test",
    ))
    db.commit()

    updated = update_backup_settings(db, BackupSettingsUpdate(
        enabled=True,
        destination="TELEGRAM_EMAIL",
        telegram_chat_id="1234",
        smtp_host="smtp.example.test",
        smtp_port=587,
        email_from="from@example.test",
        email_to="to@example.test",
    ))

    assert updated.telegram_bot_token == "secret-token"
    assert updated.smtp_password == "secret-password"


def test_panel_archive_rejects_unchecked_and_duplicate_members(tmp_path):
    sql = b"CREATE TABLE proof (id INT);"
    manifest = {"format": "panel-backup-v1", "database_engine": "mysql",
                "files": {"database.sql": hashlib.sha256(sql).hexdigest()}}
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("database.sql", sql)
        bundle.writestr("files/0-.env/.env", b"unchecked secret replacement")
    with pytest.raises(ValueError, match="backup_checksum_manifest_incomplete"):
        validate_panel_backup(archive)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("database.sql", sql)
    with pytest.raises(ValueError, match="backup_archive_duplicate_member"):
        validate_panel_backup(archive)


def test_restore_dotenv_targets_file_and_prevalidates_all_roots(tmp_path):
    from app.utils.stage11_operations import restore_panel_files
    target = tmp_path / ".env"
    target.write_text("old")
    sql = b"CREATE TABLE proof (id INT);"
    files = {"database.sql": sql, "files/0-.env/.env": b"new"}
    manifest = {"format": "panel-backup-v1", "database_engine": "mysql",
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                "restore_roots": {"files/0-.env": str(target)}}
    archive = tmp_path / "restore.zip"
    def write_archive():
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest))
            for name, data in files.items():
                bundle.writestr(name, data)
    write_archive()
    assert restore_panel_files(archive, [target]) == 1
    assert target.read_text() == "new"
    target.write_text("preserved")
    manifest["restore_roots"]["files/forbidden"] = str(tmp_path / "outside")
    write_archive()
    with pytest.raises(ValueError, match="backup_restore_target_forbidden"):
        restore_panel_files(archive, [target])
    assert target.read_text() == "preserved"


def test_online_restore_fails_before_any_database_or_file_mutation(monkeypatch):
    from fastapi import HTTPException
    from app.routers import backup
    monkeypatch.setattr(backup, "_owner", lambda *args: None)
    monkeypatch.setattr(backup, "complete_upload", lambda *args: pytest.fail("upload must not be processed"))
    with pytest.raises(HTTPException) as error:
        backup.restore_backup("unused", backup=None, db=None, actor=None)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "offline_restore_required"


def test_configured_backup_schedule_claim_and_delivery_failure_keep_local_copy(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from app.jobs import stage11_operations as jobs
    from app.utils import stage11_operations as operations
    from app.db.models import BackupArtifact
    db = session()
    db.add(BackupSettings(id=1, enabled=True, schedule="15m", destination="EMAIL",
                          smtp_host="smtp.example.test", smtp_port=587,
                          email_from="from@example.test", email_to="to@example.test"))
    db.commit()
    @contextmanager
    def get_db():
        yield db
    archive = tmp_path / "backup.panel-backup.zip"
    archive.write_bytes(b"local recovery copy")
    calls = []
    def generate(*args, **kwargs):
        calls.append(args)
        return archive, {"database_name": "test", "archive_sha256": "0" * 64}
    monkeypatch.setattr(jobs, "GetDB", get_db)
    monkeypatch.setattr(jobs, "STAGE11_BACKUP_SPOOL", str(tmp_path))
    monkeypatch.setattr(operations, "create_panel_backup", generate)
    monkeypatch.setattr(operations, "send_backup_email", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    fixed_now = jobs.now()
    monkeypatch.setattr(jobs, "now", lambda: fixed_now)
    jobs.create_configured_panel_backup()
    jobs.create_configured_panel_backup()
    assert len(calls) == 1
    artifact = db.query(BackupArtifact).one()
    assert artifact.generation_status == "SUCCESS"
    assert artifact.delivery_status == "FAILED"
    assert archive.read_bytes() == b"local recovery copy"


def test_legacy_cleanup_does_not_override_panel_archive_retention(tmp_path):
    from app.db.models import BackupArtifact
    from app.utils.stage11_operations import purge_delivered_backup_files
    db = session()
    old = now() - timedelta(days=10)
    panel = tmp_path / "retained.panel-backup.zip"
    legacy = tmp_path / "old.sql.aesgcm"
    panel.write_bytes(b"panel")
    legacy.write_bytes(b"legacy")
    for period, path in (("old-panel", panel), ("old-legacy", legacy)):
        db.add(BackupArtifact(period_key=period, database_name="test", encrypted_path=str(path),
                              generation_status="SUCCESS", delivery_status="DELIVERED",
                              delivered_at=old, created_at=old))
    db.add(BackupArtifact(period_key="newest", database_name="test", generation_status="SUCCESS"))
    db.commit()
    assert purge_delivered_backup_files(db) == 1
    assert panel.exists()
    assert not legacy.exists()
