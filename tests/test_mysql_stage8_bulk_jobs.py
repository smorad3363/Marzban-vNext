import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa
from OpenSSL import crypto
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Admin,
    AdminBulkJob,
    AdminBulkJobTarget,
    AdminHierarchy,
    MarzhelpAccountingTransaction,
    MarzhelpAdminSettings,
    SystemOwner,
    User,
)
from app.models.bulk import BulkTargetScope, BulkUserJobCreateRequest
from app.models.user import BulkUserOperation, UserStatus
from app.utils import bulk_operations


MYSQL_URL = os.getenv("TEST_MYSQL_DATABASE_URL")
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "2e8c4a6f9b17_add_stage8_bulk_jobs.py"
)
PREVIOUS_REVISION = "7c9a2e4f1b65"


def _upgrade(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def _downgrade(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, revision)


def _ensure_database(url: str) -> None:
    parsed = make_url(url)
    database = parsed.database
    assert database and database.startswith("stage8_") and database.endswith("_test")
    server_url = parsed.set(database="mysql")
    engine = sa.create_engine(server_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            quoted = database.replace("`", "``")
            connection.execute(
                sa.text(
                    f"CREATE DATABASE IF NOT EXISTS `{quoted}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        engine.dispose()


def _reset_schema(engine: sa.Engine) -> None:
    database = make_url(MYSQL_URL).database
    assert database and database.startswith("stage8_") and database.endswith("_test")
    with engine.begin() as connection:
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
        for table in sa.inspect(connection).get_table_names():
            quoted = table.replace("`", "``")
            connection.execute(sa.text(f"DROP TABLE `{quoted}`"))
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))


def _migration_module():
    spec = importlib.util.spec_from_file_location("stage8_mysql_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_stage8_schema(connection: sa.Connection) -> None:
    assert connection.scalar(sa.text("SELECT VERSION()")).startswith(os.getenv("TEST_MYSQL_VERSION_PREFIX", "8.0."))
    create_sql = connection.execute(
        sa.text("SHOW CREATE TABLE admin_bulk_job_targets")
    ).one()[1]
    assert "ENGINE=InnoDB" in create_sql
    columns = {column["name"] for column in sa.inspect(connection).get_columns("admin_bulk_jobs")}
    assert {
        "job_kind",
        "target_scope",
        "selected_admin_ids",
        "payload_fingerprint",
        "success_count",
        "failed_count",
        "skipped_count",
        "completed_at",
    } <= columns
    indexes = {
        index["name"]: index["column_names"]
        for index in sa.inspect(connection).get_indexes("admin_bulk_job_targets")
    }
    assert indexes["ix_admin_bulk_job_targets_pending"] == [
        "job_id",
        "target_type",
        "status",
        "retryable",
        "sequence",
    ]
    assert indexes["ix_admin_bulk_job_targets_report"] == ["job_id", "sequence"]
    assert indexes["ix_admin_bulk_job_targets_target"] == [
        "target_type",
        "target_id",
        "job_id",
    ]


def _seed_and_run_concurrently(engine: sa.Engine) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed = factory()
    owner = Admin(username="stage8-owner", hashed_password="x", is_sudo=True, role_id=1)
    child = Admin(username="stage8-child", hashed_password="x", is_sudo=False, role_id=2)
    seed.add_all([owner, child])
    seed.flush()
    child.parent_admin_id = owner.id
    seed.add(SystemOwner(id=1, admin_id=owner.id))
    seed.add_all(
        [
            AdminHierarchy(ancestor_id=owner.id, descendant_id=owner.id, depth=0),
            AdminHierarchy(ancestor_id=owner.id, descendant_id=child.id, depth=1),
            AdminHierarchy(ancestor_id=child.id, descendant_id=child.id, depth=0),
            MarzhelpAdminSettings(
                admin_id=owner.id,
                billing_mode="USED_TRAFFIC",
                total_traffic=None,
                all_inbounds=True,
                all_user_limits=True,
            ),
            MarzhelpAdminSettings(
                admin_id=child.id,
                billing_mode="USED_TRAFFIC",
                total_traffic=100_000,
                delegated_traffic=0,
                used_traffic=0,
                all_inbounds=True,
                all_user_limits=True,
            ),
        ]
    )
    users = [
        User(
            username=f"stage8-user-{number:02d}",
            admin_id=child.id,
            status=UserStatus.active,
            data_limit=100,
            used_traffic=0,
            expire=2_000_000_000,
            concurrent_user_limit=2,
        )
        for number in range(20)
    ]
    seed.add_all(users)
    seed.commit()
    owner_id = owner.id
    user_ids = [user.id for user in users]

    request = BulkUserJobCreateRequest(
        operation_id="stage8-mysql-idempotent-job",
        operation=BulkUserOperation.add_data,
        target_scope=BulkTargetScope.SELECTED_ADMINS_DIRECT,
        selected_admin_ids=[child.id],
        data_amount=10,
    )
    job, created = bulk_operations.create_user_job(seed, owner, request)
    assert created and job.total_count == 20
    replay, replay_created = bulk_operations.create_user_job(seed, owner, request)
    assert replay.id == job.id and replay_created is False
    job_id = job.id
    seed.close()

    def execute() -> tuple[str, int]:
        session = factory()
        try:
            result, processed = bulk_operations.execute_job(
                session,
                session.get(Admin, owner_id),
                request.operation_id,
                chunk_size=20,
                retry_failed=False,
            )
            return result.status, len(processed)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: execute(), range(2)))

    verify = factory()
    try:
        final = verify.get(AdminBulkJob, job_id)
        assert final.status == "COMPLETE"
        assert (final.total_count, final.success_count, final.failed_count, final.skipped_count) == (
            20,
            20,
            0,
            0,
        )
        assert sum(processed for _, processed in outcomes) == 20
        assert (
            verify.query(AdminBulkJobTarget)
            .filter_by(job_id=job_id, status="SUCCESS")
            .count()
            == 20
        )
        assert all(verify.get(User, user_id).data_limit == 110 for user_id in user_ids)
        ledgers = (
            verify.query(MarzhelpAccountingTransaction)
            .filter(MarzhelpAccountingTransaction.user_id.in_(user_ids))
            .all()
        )
        assert len(ledgers) == 20
        assert all(row.volume_delta == 10 and row.renewal_delta == 0 for row in ledgers)

        pending_plan = verify.execute(
            sa.text(
                "EXPLAIN FORMAT=TRADITIONAL SELECT target_id FROM admin_bulk_job_targets "
                "WHERE job_id=:job_id AND target_type='USER' "
                "AND status='PENDING' AND retryable=1 ORDER BY sequence LIMIT 100"
            ),
            {"job_id": job_id},
        ).mappings().one()
        assert pending_plan["key"] == "ix_admin_bulk_job_targets_pending"
        assert pending_plan["type"] in {"ref", "range"}

        report_plan = verify.execute(
            sa.text(
                "EXPLAIN FORMAT=TRADITIONAL SELECT * FROM admin_bulk_job_targets "
                "WHERE job_id=:job_id AND sequence>:cursor ORDER BY sequence LIMIT 100"
            ),
            {"job_id": job_id, "cursor": 0},
        ).mappings().one()
        assert report_plan["key"] == "ix_admin_bulk_job_targets_report"
        assert report_plan["type"] in {"ref", "range"}
    finally:
        verify.close()


@pytest.mark.skipif(not MYSQL_URL, reason="TEST_MYSQL_DATABASE_URL is not configured")
def test_mysql_stage8_migration_concurrency_idempotency_and_query_plans(monkeypatch):
    parsed = make_url(MYSQL_URL)
    assert parsed.get_backend_name() == "mysql"
    _ensure_database(MYSQL_URL)
    original_not_after = crypto.X509.gmtime_adj_notAfter
    monkeypatch.setattr(
        crypto.X509,
        "gmtime_adj_notAfter",
        lambda certificate, seconds: original_not_after(certificate, min(seconds, 2_000_000_000)),
    )
    engine = sa.create_engine(MYSQL_URL, pool_pre_ping=True)
    try:
        _reset_schema(engine)
        _upgrade(MYSQL_URL, PREVIOUS_REVISION)
        assert "admin_bulk_job_targets" not in sa.inspect(engine).get_table_names()
        _upgrade(MYSQL_URL, "head")
        with engine.begin() as connection:
            _assert_stage8_schema(connection)
            module = _migration_module()
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
            _assert_stage8_schema(connection)

        _downgrade(MYSQL_URL, PREVIOUS_REVISION)
        assert "admin_bulk_job_targets" not in sa.inspect(engine).get_table_names()
        _upgrade(MYSQL_URL, "head")
        with engine.begin() as connection:
            _assert_stage8_schema(connection)

        _seed_and_run_concurrently(engine)
    finally:
        engine.dispose()
