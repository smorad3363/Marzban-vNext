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
