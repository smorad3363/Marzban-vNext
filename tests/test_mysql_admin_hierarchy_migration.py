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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Admin,
    AdminCreditTransfer,
    AdminReferralAttribution,
    AdminReferralEvent,
    AdminSuspensionAdmin,
    AdminSuspensionEvent,
    MarzhelpAccountingTransaction,
    MarzhelpAdminSettings,
    SystemOwner,
    User,
)
from app.models.user import UserStatus
from app.utils import admin_hierarchy, marzhelp_policy, money_billing


MYSQL_URL = os.getenv("TEST_MYSQL_DATABASE_URL")
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "e2a6c1f4b903_add_admin_hierarchy_foundation.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("mysql_admin_hierarchy_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reset_test_schema(engine: sa.Engine) -> None:
    database = make_url(MYSQL_URL).database
    assert database and database.endswith("marzban_test")
    with engine.begin() as connection:
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
        for table in sa.inspect(connection).get_table_names():
            connection.execute(sa.text(f"DROP TABLE `{table.replace('`', '``')}`"))
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))


def _upgrade(url: str, revision: str) -> None:
    alembic = Config("alembic.ini")
    alembic.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic, revision)


def _assert_extended_schema(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    assert {
        "admin_roles",
        "admin_hierarchy_settings",
        "system_owner",
        "admin_hierarchy",
        "admin_credit_transfers",
        "admin_api_tokens",
        "admin_suspension_events",
        "admin_suspension_users",
        "admin_bulk_jobs",
        "admin_user_plans",
        "admin_user_plan_versions",
        "admin_user_plan_inbounds",
        "admin_user_plan_hosts",
        "admin_user_plan_access",
        "user_plan_assignments",
        "allocated_traffic_refund_requests",
        "allocated_traffic_refund_events",
        "admin_referral_attributions",
        "admin_referral_events",
        "admin_suspension_admins",
    } <= tables
    assert "billing_mode" in {
        column["name"] for column in inspector.get_columns("marzhelp_admin_settings")
    }
    assert {
        "role_id",
        "parent_admin_id",
        "external_api_enabled",
        "external_api_updated_by",
        "external_api_updated_at",
        "user_namespace_prefix",
    } <= {column["name"] for column in inspector.get_columns("admins")}
    assert any(
        index.get("column_names") == ["user_namespace_prefix"]
        and index.get("unique")
        for index in inspector.get_indexes("admins")
    )
    assert any(
        index.get("column_names") == ["descendant_id", "ancestor_id", "depth"]
        for index in inspector.get_indexes("admin_hierarchy")
    )
    assert {
        "adjusted_admin_id",
        "resource",
        "delta",
        "balance_before",
        "balance_after",
        "source_delegated_before",
        "source_delegated_after",
    } <= {
        column["name"]
        for column in inspector.get_columns("admin_credit_transfers")
    }
    assert any(
        index.get("column_names") == ["adjusted_admin_id", "created_at", "id"]
        for index in inspector.get_indexes("admin_credit_transfers")
    )
    assert any(
        index.get("column_names") == ["reviewer_admin_id", "status", "requested_at", "id"]
        for index in inspector.get_indexes("allocated_traffic_refund_requests")
    )
    create_sql = connection.execute(sa.text("SHOW CREATE TABLE admin_credit_transfers")).one()[1]
    assert "ENGINE=InnoDB" in create_sql
    for table in (
        "admin_roles",
        "admin_hierarchy_settings",
        "system_owner",
        "admin_user_creation_modes",
        "admin_account_statuses",
        "admin_suspension_reasons",
    ):
        create_sql = connection.execute(sa.text(f"SHOW CREATE TABLE `{table}`")).one()[1]
        assert "AUTO_INCREMENT" not in create_sql
    token_sql = connection.execute(sa.text("SHOW CREATE TABLE admin_api_tokens")).one()[1]
    assert "`token_hash` binary(32) NOT NULL" in token_sql
    assert "UNIQUE KEY `uq_admin_api_tokens_hash` (`token_hash`)" in token_sql
    event_columns = {column["name"] for column in inspector.get_columns("admin_suspension_events")}
    assert {
        "operation_type",
        "idempotency_key",
        "payload_fingerprint",
        "resolved_by_admin_id",
        "resolved_idempotency_key",
    } <= event_columns
    for table in ("admin_referral_attributions", "admin_referral_events", "admin_suspension_admins"):
        assert "ENGINE=InnoDB" in connection.execute(sa.text(f"SHOW CREATE TABLE `{table}`")).one()[1]


def _assert_mysql_credit_concurrency(engine: sa.Engine) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed = factory()
    owner = Admin(username="stage2-mysql-owner", hashed_password="x", is_sudo=True, role_id=1)
    child = Admin(
        username="stage2-mysql-child",
        hashed_password="x",
        is_sudo=False,
        role_id=3,
    )
    seed.add_all([owner, child])
    seed.flush()
    child.parent_admin_id = owner.id
    seed.add(SystemOwner(id=1, admin_id=owner.id))
    seed.add_all(
        [
            MarzhelpAdminSettings(admin_id=owner.id, total_traffic=None),
            MarzhelpAdminSettings(admin_id=child.id, total_traffic=0),
        ]
    )
    seed.execute(
        sa.text("UPDATE admin_hierarchy_settings SET enabled = 1 WHERE id = 1")
    )
    seed.commit()
    owner_id, child_id = owner.id, child.id
    admin_hierarchy.transfer_credit(
        seed,
        actor=owner,
        source=owner,
        target=child,
        amount=60,
        operation_type="grant",
        idempotency_key="stage2-mysql-concurrency-seed",
        note="concurrency seed",
    )
    seed.close()

    def reclaim(key: str):
        session = factory()
        try:
            return admin_hierarchy.transfer_credit(
                session,
                actor=session.get(Admin, owner_id),
                source=session.get(Admin, owner_id),
                target=session.get(Admin, child_id),
                amount=40,
                operation_type="reclaim",
                idempotency_key=key,
                note="concurrent reclaim",
            ).id
        except admin_hierarchy.HierarchyError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                reclaim,
                ("stage2-mysql-concurrent-a", "stage2-mysql-concurrent-b"),
            )
        )

    verify = factory()
    try:
        assert verify.query(AdminCreditTransfer).filter_by(operation_type="reclaim").count() == 1
        assert verify.get(MarzhelpAdminSettings, child_id).total_traffic == 20
        assert verify.get(MarzhelpAdminSettings, owner_id).delegated_traffic == 20
        assert "reclaim_exceeds_available" in outcomes
    finally:
        verify.close()


def _assert_mysql_preloaded_wallet_concurrency(engine: sa.Engine) -> None:
    from threading import Barrier
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed:
        admin = Admin(username="review-wallet-race", hashed_password="x", is_sudo=False)
        seed.add(admin)
        seed.flush()
        seed.add(MarzhelpAdminSettings(admin_id=admin.id, money_balance_toman=100))
        seed.commit()
        admin_id = admin.id
    ready = Barrier(2)
    def debit():
        with factory() as session:
            stale = session.get(MarzhelpAdminSettings, admin_id)
            assert stale.money_balance_toman == 100
            ready.wait(timeout=10)
            wallet = money_billing._settings_for_update(session, {admin_id})[admin_id]
            before = wallet.money_balance_toman
            wallet.money_balance_toman -= 10
            session.commit()
            return before
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: debit(), range(2))) == [90, 100]
    with factory() as verify:
        assert verify.get(MarzhelpAdminSettings, admin_id).money_balance_toman == 80


def _assert_mysql_seat_renewal_idempotency(engine: sa.Engine) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed = factory()
    admin = Admin(
        username="stage5-mysql-seat-admin",
        hashed_password="x",
        is_sudo=False,
        role_id=2,
    )
    seed.add(admin)
    seed.flush()
    settings = MarzhelpAdminSettings(
        admin_id=admin.id,
        billing_mode="SEAT_CREDIT",
        device_capacity_limit=10,
        capacity_used=2,
        renewal_enabled=True,
    )
    user = User(
        username="stage5-mysql-seat-user",
        admin_id=admin.id,
        status=UserStatus.active,
        concurrent_user_limit=2,
    )
    seed.add_all([settings, user])
    seed.commit()
    admin_id, user_id = admin.id, user.id
    seed.close()

    def renew():
        session = factory()
        try:
            locked_settings = (
                session.query(MarzhelpAdminSettings)
                .filter(MarzhelpAdminSettings.admin_id == admin_id)
                .with_for_update()
                .one()
            )
            marzhelp_policy.consume_seat_renewal(
                session,
                locked_settings,
                user=session.get(User, user_id),
                seat_cost=2,
                idempotency_key="stage5-mysql-same-renewal",
                plan_id=501,
                version_id=502,
            )
            session.commit()
            return "consumed"
        except IntegrityError:
            session.rollback()
            return "duplicate"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: renew(), range(2)))

    verify = factory()
    try:
        assert sorted(outcomes) == ["consumed", "duplicate"]
        assert verify.get(MarzhelpAdminSettings, admin_id).capacity_used == 4
        transactions = (
            verify.query(MarzhelpAccountingTransaction)
            .filter_by(admin_id=admin_id, operation_type="plan_renew_seat")
            .all()
        )
        assert len(transactions) == 1
        assert transactions[0].renewal_delta == 1
        assert transactions[0].details["seat_cost"] == 2
    finally:
        verify.close()


def _assert_mysql_stage7_freeze_and_referral_concurrency(engine: sa.Engine) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed = factory()
    owner = Admin(username="stage7-owner", hashed_password="x", is_sudo=True)
    target = Admin(username="stage7-target", hashed_password="x", is_sudo=True)
    descendant = Admin(username="stage7-descendant", hashed_password="x", is_sudo=False)
    seed.add_all([owner, target, descendant])
    seed.flush()
    seed.add_all(
        MarzhelpAdminSettings(admin_id=item.id, total_traffic=100)
        for item in (owner, target, descendant)
    )
    seed.commit()
    admin_hierarchy.set_owner(seed, owner.username)
    admin_hierarchy.attach_new_child(
        seed,
        actor=owner,
        parent=target,
        child=descendant,
        child_role=admin_hierarchy.ADMIN,
    )
    user = User(username="stage7-freeze-user", admin_id=descendant.id, status=UserStatus.active)
    seed.add(user)
    seed.commit()
    owner_id, target_id, descendant_id, user_id = owner.id, target.id, descendant.id, user.id
    seed.close()

    def freeze():
        session = factory()
        try:
            event, created = admin_hierarchy.freeze_admin(
                session,
                actor=session.get(Admin, owner_id),
                target=session.get(Admin, target_id),
                reason_id=1,
                idempotency_key="stage7-mysql-freeze-same-key",
                note="concurrency",
            )
            return event.id, created
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        freeze_results = list(executor.map(lambda _: freeze(), range(2)))
    assert len({result[0] for result in freeze_results}) == 1
    assert sorted(result[1] for result in freeze_results) == [False, True]

    def referral():
        session = factory()
        try:
            event, created = admin_hierarchy.set_referral_attribution(
                session,
                actor=session.get(Admin, owner_id),
                referred=session.get(Admin, descendant_id),
                referrer=session.get(Admin, target_id),
                rate_bps=500,
                idempotency_key="stage7-mysql-referral-same-key",
                note="attribution only",
            )
            return event.id, created
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        referral_results = list(executor.map(lambda _: referral(), range(2)))
    assert len({result[0] for result in referral_results}) == 1
    assert sorted(result[1] for result in referral_results) == [False, True]

    verify = factory()
    try:
        freeze_event = verify.query(AdminSuspensionEvent).filter_by(operation_type="owner_freeze").one()
        assert verify.query(AdminSuspensionAdmin).filter_by(event_id=freeze_event.id).count() == 2
        assert verify.get(User, user_id).status == UserStatus.disabled
        assert verify.query(AdminReferralEvent).count() == 1
        assert verify.get(AdminReferralAttribution, descendant_id).referrer_admin_id == target_id
        assert verify.query(AdminCreditTransfer).filter_by(operation_type="referral").count() == 0
    finally:
        verify.close()

    unfreeze_session = factory()
    try:
        _, restored_admins, restored_users, created = admin_hierarchy.unfreeze_admin(
            unfreeze_session,
            actor=unfreeze_session.get(Admin, owner_id),
            target=unfreeze_session.get(Admin, target_id),
            idempotency_key="stage7-mysql-unfreeze-same-key",
        )
        assert created and restored_admins == 2 and restored_users == 1
        _, replay_admins, replay_users, replay_created = admin_hierarchy.unfreeze_admin(
            unfreeze_session,
            actor=unfreeze_session.get(Admin, owner_id),
            target=unfreeze_session.get(Admin, target_id),
            idempotency_key="stage7-mysql-unfreeze-same-key",
        )
        assert replay_created is False
        assert replay_admins == restored_admins and replay_users == restored_users
    finally:
        unfreeze_session.close()


@pytest.mark.skipif(not MYSQL_URL, reason="TEST_MYSQL_DATABASE_URL is not configured")
def test_mysql_hierarchy_fresh_legacy_partial_and_rerun(monkeypatch):
    assert make_url(MYSQL_URL).get_backend_name() == "mysql"
    original_not_after = crypto.X509.gmtime_adj_notAfter
    monkeypatch.setattr(
        crypto.X509,
        "gmtime_adj_notAfter",
        lambda certificate, seconds: original_not_after(
            certificate,
            min(seconds, 2_000_000_000),
        ),
    )
    engine = sa.create_engine(MYSQL_URL, pool_pre_ping=True)

    _reset_test_schema(engine)
    _upgrade(MYSQL_URL, "head")
    with engine.begin() as connection:
        # Simulate interruption between nontransactional Access Group DDL steps.
        connection.execute(sa.text("DROP TABLE access_group_hosts"))
        access_path = MIGRATION_PATH.with_name("a7c4e9d2f610_separate_access_groups_from_plans.py")
        spec = importlib.util.spec_from_file_location("review_access_migration", access_path)
        access_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(access_migration)
        access_migration.op = Operations(MigrationContext.configure(connection))
        access_migration.upgrade()
        assert "access_group_hosts" in sa.inspect(connection).get_table_names()
        _assert_extended_schema(connection)
        module = _migration_module()
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        _assert_extended_schema(connection)
    _assert_mysql_credit_concurrency(engine)
    _assert_mysql_preloaded_wallet_concurrency(engine)
    _assert_mysql_seat_renewal_idempotency(engine)
    _assert_mysql_stage7_freeze_and_referral_concurrency(engine)

    # Supported expand path: current Stage 6 schema/data to Stage 7 head.
    _reset_test_schema(engine)
    _upgrade(MYSQL_URL, "5b8d1f3a7c64")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO admins "
                "(id, username, user_namespace_prefix, hashed_password, is_sudo, users_usage) "
                "VALUES (301, 'current-stage6-admin', 's7current', 'x', 0, 0)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO marzhelp_admin_settings "
                "(admin_id, billing_mode, total_traffic, trial_quota, trials_used) "
                "VALUES (301, 'LEGACY_COMPAT', 900, 4, 1)"
            )
        )
    _upgrade(MYSQL_URL, "head")
    with engine.begin() as connection:
        _assert_extended_schema(connection)
        assert connection.execute(
            sa.text(
                "SELECT username, user_namespace_prefix FROM admins WHERE id = 301"
            )
        ).one() == ("current-stage6-admin", "s7current")
        assert connection.execute(
            sa.text(
                "SELECT billing_mode, total_traffic, trial_quota, trials_used "
                "FROM marzhelp_admin_settings WHERE admin_id = 301"
            )
        ).one() == ("LEGACY_COMPAT", 900, 4, 1)

    _reset_test_schema(engine)
    _upgrade(MYSQL_URL, "a41c8e7d5b92")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO admins "
                "(id, username, hashed_password, is_sudo, users_usage) "
                "VALUES (101, 'legacy-owner', 'x', 1, 0), "
                "(202, 'legacy-admin', 'x', 0, 0)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO marzhelp_admin_settings "
                "(admin_id, renewal_limit, renewals_used) VALUES (202, 7, 2)"
            )
        )
        connection.execute(sa.text("ALTER TABLE admins ADD COLUMN role_id SMALLINT NULL"))
        connection.execute(
            sa.text(
                "ALTER TABLE admins ADD COLUMN user_namespace_prefix VARCHAR(16) NULL"
            )
        )

    _upgrade(MYSQL_URL, "head")
    with engine.begin() as connection:
        _assert_extended_schema(connection)
        legacy_admin_rows = connection.execute(
            sa.text(
                "SELECT id, username, is_sudo, role_id, parent_admin_id, "
                "user_namespace_prefix FROM admins ORDER BY id"
            )
        ).all()
        assert [row[:5] for row in legacy_admin_rows] == [
            (101, "legacy-owner", True, None, None),
            (202, "legacy-admin", False, None, None),
        ]
        namespace_rows = [row[5] for row in legacy_admin_rows]
        assert all(namespace_rows)
        assert len(namespace_rows) == len(set(namespace_rows))
        assert connection.execute(
            sa.text(
                "SELECT renewal_limit, renewals_used, renewal_remaining, billing_mode "
                "FROM marzhelp_admin_settings WHERE admin_id = 202"
            )
        ).one() == (7, 2, 5, "LEGACY_COMPAT")
        module = _migration_module()
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM admin_roles")) == 3
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM admin_hierarchy_settings")) == 1

    engine.dispose()
