import base64
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa
from OpenSSL import crypto
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db.models import TelegramOutbox
from app.utils.stage11_operations import decrypt_backup, dispatch_outbox, encrypt_backup, enqueue_outbox

MYSQL_URL = os.getenv("TEST_MYSQL_DATABASE_URL")


def migrate(revision, downgrade=False):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", MYSQL_URL)
    (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.mark.skipif(not MYSQL_URL, reason="TEST_MYSQL_DATABASE_URL is required")
def test_mysql_stage11_migration_claim_idempotency_indexes_and_isolated_restore(monkeypatch, tmp_path):
    url = make_url(MYSQL_URL)
    assert url.database.startswith("stage11_") and url.database.endswith("_test")
    server = sa.create_engine(url.set(database="mysql"))
    restore_name = f"{url.database}_restore"
    with server.begin() as connection:
        for name in (url.database, restore_name):
            connection.execute(sa.text(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4"))
    engine = sa.create_engine(MYSQL_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
        for table in sa.inspect(connection).get_table_names():
            connection.execute(sa.text(f"DROP TABLE `{table}`"))
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))
    original = crypto.X509.gmtime_adj_notAfter
    monkeypatch.setattr(crypto.X509, "gmtime_adj_notAfter", lambda cert, seconds: original(cert, min(seconds, 2_000_000_000)))
    migrate("1a9e7c3d5b20")
    migrate("head")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT VERSION()" )).scalar().startswith(os.getenv("TEST_MYSQL_VERSION_PREFIX", "8.0."))
        for table in ("telegram_outbox", "backup_artifacts"):
            assert connection.execute(sa.text(f"SHOW TABLE STATUS LIKE '{table}'")).mappings().one()["Engine"] == "InnoDB"
        names = {item["name"] for item in sa.inspect(connection).get_indexes("telegram_outbox")}
        assert {"ix_telegram_outbox_dispatch", "ix_telegram_outbox_retention"} <= names
    Session = sessionmaker(bind=engine)
    with Session() as db:
        for index in range(40):
            enqueue_outbox(db, idempotency_key=f"mysql:{index}", event_type="audit", payload={"index": index})
        db.commit()
    with engine.connect() as connection:
        plan = connection.execute(sa.text("EXPLAIN FORMAT=TRADITIONAL SELECT id FROM telegram_outbox WHERE status='PENDING' AND next_attempt_at<=NOW() ORDER BY next_attempt_at,id LIMIT 25")).mappings().one()
        assert plan["key"] == "ix_telegram_outbox_dispatch"
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE telegram_outbox SET next_attempt_at=UTC_TIMESTAMP() - INTERVAL 1 SECOND"))
    delivered = []
    def worker():
        with Session() as db:
            return dispatch_outbox(db, lambda row: delivered.append(row.id), limit=20)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: worker(), range(2))) == 20
    assert worker() == 20
    assert len(delivered) == len(set(delivered)) == 40
    with Session() as db:
        assert db.query(TelegramOutbox).filter_by(status="DELIVERED").count() == 40
    key = base64.b64encode(b"r" * 32).decode()
    dump = tmp_path / "restore.sql"
    encrypted = tmp_path / "restore.aesgcm"
    decrypted = tmp_path / "restore.decrypted.sql"
    dump.write_text("CREATE TABLE restore_proof (id INT PRIMARY KEY);\nINSERT INTO restore_proof VALUES (7);\n", encoding="utf-8")
    encrypt_backup(dump, encrypted, key)
    decrypt_backup(encrypted, decrypted, key)
    restore_engine = sa.create_engine(url.set(database=restore_name))
    with restore_engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE IF EXISTS restore_proof"))
        for statement in decrypted.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                connection.execute(sa.text(statement))
        assert connection.execute(sa.text("SELECT id FROM restore_proof")).scalar_one() == 7
    migrate("1a9e7c3d5b20", downgrade=True)
    assert "telegram_outbox" not in sa.inspect(engine).get_table_names()
    migrate("head")
    restore_engine.dispose(); engine.dispose(); server.dispose()
