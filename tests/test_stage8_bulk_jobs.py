from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.routing import APIRoute
from sqlalchemy.orm import sessionmaker

from app import app as fastapi_app
from app.db.base import Base
from app.db.models import (
    Admin,
    AdminAccountStatus,
    AdminBulkJobTarget,
    AdminHierarchy,
    AdminHierarchySettings,
    AdminRole,
    AdminSuspensionReason,
    AdminUserCreationMode,
    MarzhelpAccountingTransaction,
    MarzhelpAdminSettings,
    SystemOwner,
    User,
)
from app.models.bulk import (
    BulkAdminJobCreateRequest,
    BulkAdminOperation,
    BulkJobExecuteRequest,
    BulkTargetScope,
    BulkUserJobCreateRequest,
    BulkUserPreviewRequest,
    BulkSelectionRequest,
)
from app.models.user import BulkUserOperation, UserStatus
from app.utils import bulk_operations


@pytest.fixture()
def db(tmp_path):
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'stage8.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add_all(
        [
            AdminRole(id=1, code="OWNER"),
            AdminRole(id=2, code="SUPER_ADMIN"),
            AdminRole(id=3, code="ADMIN"),
            AdminUserCreationMode(id=1, code="FREE_FORM"),
            AdminUserCreationMode(id=2, code="PLAN_ONLY"),
            AdminAccountStatus(id=1, code="ACTIVE"),
            AdminAccountStatus(id=2, code="SUSPENDED"),
            AdminAccountStatus(id=3, code="DISABLED"),
            AdminSuspensionReason(id=1, code="MANUAL"),
            AdminSuspensionReason(id=2, code="CREDIT_EXHAUSTED"),
            AdminSuspensionReason(id=3, code="ACCOUNT_EXPIRED"),
            AdminHierarchySettings(id=1, enabled=True, max_depth=64),
        ]
    )
    owner = Admin(username="owner", hashed_password="x", is_sudo=True, role_id=1)
    direct = Admin(username="direct", hashed_password="x", role_id=2)
    leaf = Admin(username="leaf", hashed_password="x", role_id=3)
    sibling = Admin(username="sibling", hashed_password="x", role_id=3)
    session.add_all([owner, direct, leaf, sibling])
    session.flush()
    direct.parent_admin_id = owner.id
    leaf.parent_admin_id = direct.id
    sibling.parent_admin_id = owner.id
    session.add(SystemOwner(id=1, admin_id=owner.id))
    session.add_all(
        [
            AdminHierarchy(ancestor_id=owner.id, descendant_id=owner.id, depth=0),
            AdminHierarchy(ancestor_id=owner.id, descendant_id=direct.id, depth=1),
            AdminHierarchy(ancestor_id=owner.id, descendant_id=leaf.id, depth=2),
            AdminHierarchy(ancestor_id=owner.id, descendant_id=sibling.id, depth=1),
            AdminHierarchy(ancestor_id=direct.id, descendant_id=direct.id, depth=0),
            AdminHierarchy(ancestor_id=direct.id, descendant_id=leaf.id, depth=1),
            AdminHierarchy(ancestor_id=leaf.id, descendant_id=leaf.id, depth=0),
            AdminHierarchy(ancestor_id=sibling.id, descendant_id=sibling.id, depth=0),
        ]
    )
    for admin in (owner, direct, leaf, sibling):
        session.add(
            MarzhelpAdminSettings(
                admin_id=admin.id,
                account_status_id=1,
                billing_mode="USED_TRAFFIC",
                total_traffic=None if admin is owner else 10_000,
                delegated_traffic=0,
                used_traffic=0,
                user_limit=None,
                all_inbounds=True,
                all_user_limits=True,
            )
        )
    session.commit()
    session.info["tree"] = {
        "owner": owner,
        "direct": direct,
        "leaf": leaf,
        "sibling": sibling,
    }
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(db, admin: Admin, username: str, *, data_limit=100, expire=2_000_000_000):
    row = User(
        username=username,
        admin_id=admin.id,
        status=UserStatus.active,
        data_limit=data_limit,
        expire=expire,
        used_traffic=0,
        concurrent_user_limit=2,
    )
    db.add(row)
    db.commit()
    return row


def _create_user_job(db, operation_id: str, **overrides):
    tree = db.info["tree"]
    values = {
        "operation_id": operation_id,
        "operation": BulkUserOperation.add_data,
        "target_scope": BulkTargetScope.SELECTED_ADMINS_DIRECT,
        "selected_admin_ids": [tree["direct"].id],
        "data_amount": 10,
    }
    values.update(overrides)
    return bulk_operations.create_user_job(
        db,
        tree["owner"],
        BulkUserJobCreateRequest(**values),
    )[0]


def test_checked_selection_is_exact_and_combines_compatible_actions(db):
    tree = db.info["tree"]
    selected = _user(db, tree["direct"], "checked-selected", data_limit=100, expire=2_000_000_000)
    untouched = _user(db, tree["direct"], "checked-untouched", data_limit=100, expire=2_000_000_000)
    values = BulkSelectionRequest(
        operation_id="checked-combined-01",
        user_ids=[selected.id],
        actions=[
            {"operation": "add_data", "amount": 10},
            {"operation": "add_days", "amount": 7},
        ],
    )
    preview = bulk_operations.preview_selection(db, tree["owner"], values)
    assert (preview.user_count, preview.traffic_change, preview.duration_change_days) == (1, 10, 7)
    result = bulk_operations.execute_selection(db, tree["owner"], values)
    db.refresh(selected)
    db.refresh(untouched)
    assert (result.success, result.failed) == (1, 0), result.results
    assert (selected.data_limit, selected.expire) == (110, 2_000_604_800)
    assert (untouched.data_limit, untouched.expire) == (100, 2_000_000_000)


def test_checked_selection_rejects_incompatible_actions():
    with pytest.raises(ValueError):
        BulkSelectionRequest(
            operation_id="checked-invalid-01",
            user_ids=[1],
            actions=[{"operation": "activate"}, {"operation": "deactivate"}],
        )


def test_stage8_api_routes_are_registered():
    paths = {route.path for route in fastapi_app.routes if isinstance(route, APIRoute)}
    assert {
        "/api/users/bulk/preview",
        "/api/users/bulk/jobs",
        "/api/users/bulk/jobs/{operation_id}/execute",
        "/api/users/bulk/jobs/{operation_id}",
        "/api/admin-management/bulk-credit/preview",
        "/api/admin-management/bulk-credit/jobs",
        "/api/admin-management/bulk-credit/jobs/{operation_id}/execute",
        "/api/admin-management/bulk-credit/jobs/{operation_id}",
    } <= paths


def test_direct_subtree_and_all_scopes_are_explicit_and_snapshotted(db):
    tree = db.info["tree"]
    direct_user = _user(db, tree["direct"], "direct-user")
    leaf_user = _user(db, tree["leaf"], "leaf-user")
    sibling_user = _user(db, tree["sibling"], "sibling-user")

    direct = bulk_operations.preview_user_job(
        db,
        tree["owner"],
        BulkUserPreviewRequest(
            target_scope=BulkTargetScope.SELECTED_ADMINS_DIRECT,
            selected_admin_ids=[tree["direct"].id],
        ),
    )
    subtree = bulk_operations.preview_user_job(
        db,
        tree["owner"],
        BulkUserPreviewRequest(
            target_scope=BulkTargetScope.SELECTED_ADMINS_SUBTREE,
            selected_admin_ids=[tree["direct"].id],
        ),
    )
    all_users = bulk_operations.preview_user_job(
        db,
        tree["owner"],
        BulkUserPreviewRequest(target_scope=BulkTargetScope.ALL_USERS),
    )
    assert direct.resolved_target_count == 1
    assert subtree.resolved_target_count == 2
    assert all_users.resolved_target_count == 3

    job = _create_user_job(
        db,
        "stage8-snapshot-subtree",
        target_scope=BulkTargetScope.SELECTED_ADMINS_SUBTREE,
    )
    leaf_user.admin_id = tree["sibling"].id
    db.commit()
    target_ids = {
        row.target_id
        for row in db.query(AdminBulkJobTarget).filter_by(job_id=job.id).all()
    }
    assert target_ids == {direct_user.id, leaf_user.id}
    assert sibling_user.id not in target_ids


def test_all_users_requires_owner_and_selected_scope_rejects_foreign_admin(db):
    tree = db.info["tree"]
    with pytest.raises(bulk_operations.BulkOperationError) as all_error:
        bulk_operations.preview_user_job(
            db,
            tree["direct"],
            BulkUserPreviewRequest(target_scope=BulkTargetScope.ALL_USERS),
        )
    assert all_error.value.code == "all_users_owner_required"
    with pytest.raises(bulk_operations.BulkOperationError) as scope_error:
        bulk_operations.preview_user_job(
            db,
            tree["direct"],
            BulkUserPreviewRequest(
                target_scope=BulkTargetScope.SELECTED_ADMINS_DIRECT,
                selected_admin_ids=[tree["sibling"].id],
            ),
        )
    assert scope_error.value.code == "bulk_scope_forbidden"


def test_job_creation_and_successful_target_retry_are_idempotent(db):
    tree = db.info["tree"]
    user = _user(db, tree["direct"], "idempotent-user")
    job = _create_user_job(db, "stage8-idempotent-job")
    replay = _create_user_job(db, "stage8-idempotent-job")
    assert replay.id == job.id

    first, _ = bulk_operations.execute_job(
        db,
        tree["owner"],
        job.idempotency_key,
        chunk_size=100,
        retry_failed=False,
    )
    second, processed = bulk_operations.execute_job(
        db,
        tree["owner"],
        job.idempotency_key,
        chunk_size=100,
        retry_failed=True,
    )
    db.refresh(user)
    assert first.status == "COMPLETE"
    assert processed == []
    assert second.success_count == 1
    assert user.data_limit == 110
    assert db.query(MarzhelpAccountingTransaction).filter_by(user_id=user.id).count() == 1


def test_partial_failure_continues_and_retry_never_reapplies_success(db):
    tree = db.info["tree"]
    first = _user(db, tree["direct"], "partial-first", data_limit=100)
    second = _user(db, tree["direct"], "partial-second", data_limit=100)
    settings = db.get(MarzhelpAdminSettings, tree["direct"].id)
    settings.billing_mode = "ALLOCATED_TRAFFIC"
    settings.total_traffic = 115
    settings.used_traffic = 100
    db.commit()
    job = _create_user_job(db, "stage8-partial-failure")

    result, _ = bulk_operations.execute_job(
        db,
        tree["owner"],
        job.idempotency_key,
        chunk_size=100,
        retry_failed=False,
    )
    assert result.status == "PARTIAL_FAILED"
    assert (result.success_count, result.failed_count) == (1, 1)
    db.refresh(first)
    db.refresh(second)
    assert (first.data_limit, second.data_limit) == (110, 100)

    settings = db.get(MarzhelpAdminSettings, tree["direct"].id)
    settings.total_traffic = 125
    db.commit()
    retried, _ = bulk_operations.execute_job(
        db,
        tree["owner"],
        job.idempotency_key,
        chunk_size=100,
        retry_failed=True,
    )
    db.refresh(first)
    db.refresh(second)
    assert retried.status == "COMPLETE"
    assert (first.data_limit, second.data_limit) == (110, 110)


@pytest.mark.parametrize(
    ("mode", "expected_spend", "expected_capacity"),
    [
        ("USED_TRAFFIC", 0, 0),
        ("ALLOCATED_TRAFFIC", 10, 0),
        ("SEAT_CREDIT", 0, 7),
    ],
)
def test_volume_accounting_differs_by_mode_without_renewal_consumption(
    db, mode, expected_spend, expected_capacity
):
    tree = db.info["tree"]
    user = _user(db, tree["direct"], f"mode-{mode.lower()}")
    settings = db.get(MarzhelpAdminSettings, tree["direct"].id)
    settings.billing_mode = mode
    settings.total_traffic = 1_000
    settings.used_traffic = 0
    settings.device_capacity_limit = 100 if mode == "SEAT_CREDIT" else None
    settings.capacity_used = 7 if mode == "SEAT_CREDIT" else 0
    db.commit()
    job = _create_user_job(db, f"stage8-accounting-{mode.lower()}")
    result, _ = bulk_operations.execute_job(
        db, tree["owner"], job.idempotency_key, chunk_size=100, retry_failed=False
    )
    db.refresh(settings)
    ledger = db.query(MarzhelpAccountingTransaction).filter_by(user_id=user.id).one()
    assert result.status == "COMPLETE"
    assert int(settings.used_traffic or 0) == expected_spend
    assert int(settings.capacity_used or 0) == expected_capacity
    assert ledger.volume_delta == 10
    assert ledger.renewal_delta == 0


def test_bulk_admin_credit_grant_uses_per_target_ledgers_and_replay_is_safe(db):
    tree = db.info["tree"]
    values = BulkAdminJobCreateRequest(
        operation_id="stage8-admin-grant",
        operation=BulkAdminOperation.GRANT_CREDIT,
        selected_admin_ids=[tree["direct"].id, tree["sibling"].id],
        amount=100,
        note="Stage 8 bulk grant",
    )
    job, created = bulk_operations.create_admin_job(db, tree["owner"], values)
    assert created is True
    result, _ = bulk_operations.execute_job(
        db, tree["owner"], job.idempotency_key, chunk_size=1, retry_failed=False
    )
    assert result.processed_count == 1
    result, _ = bulk_operations.execute_job(
        db, tree["owner"], job.idempotency_key, chunk_size=10, retry_failed=False
    )
    replay, processed = bulk_operations.execute_job(
        db, tree["owner"], job.idempotency_key, chunk_size=10, retry_failed=True
    )
    assert result.status == replay.status == "COMPLETE"
    assert processed == []
    assert db.get(MarzhelpAdminSettings, tree["direct"].id).total_traffic == 10_100
    assert db.get(MarzhelpAdminSettings, tree["sibling"].id).total_traffic == 10_100
    assert db.get(MarzhelpAdminSettings, tree["owner"].id).delegated_traffic == 0


def test_snapshot_creation_uses_bounded_bulk_inserts_and_report_is_paginated(db):
    tree = db.info["tree"]
    db.add_all(
        [
            User(
                username=f"bounded-{index:03d}",
                admin_id=tree["direct"].id,
                status=UserStatus.active,
                data_limit=100,
                expire=2_000_000_000,
                concurrent_user_limit=1,
            )
            for index in range(75)
        ]
    )
    db.commit()
    job = _create_user_job(db, "stage8-bounded-snapshot")
    first_page = bulk_operations.job_response(db, job, target_limit=25)
    assert job.total_count == 75
    assert len(first_page.targets) == 25
    assert first_page.report_has_more is True
    second_page = bulk_operations.job_response(
        db, job, target_after=first_page.next_target_cursor or 0, target_limit=25
    )
    assert len(second_page.targets) == 25
    assert {row.target_id for row in first_page.targets}.isdisjoint(
        {row.target_id for row in second_page.targets}
    )


def test_stage8_migration_declares_mysql_safe_additive_schema():
    source = Path(
        "app/db/migrations/versions/2e8c4a6f9b17_add_stage8_bulk_jobs.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "7c9a2e4f1b65"' in source
    assert '"admin_bulk_job_targets"' in source
    assert 'sa.UniqueConstraint("idempotency_key"' in source
    assert 'op.drop_table("admin_bulk_job_targets")' in source
