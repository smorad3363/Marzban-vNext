from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.db.base import Base
from app.db.models import (
    Admin,
    AdminAccountStatus,
    AdminBulkJob,
    AdminHierarchySettings,
    AdminAuditLog,
    AdminPlanCategoryAccess,
    AdminMoneyTransaction,
    AdminRole,
    AdminCreditTransfer,
    AdminReferralAttribution,
    AdminReferralEvent,
    AdminSuspensionAdmin,
    AdminSuspensionReason,
    AdminUserCreationMode,
    AdminUserPlan,
    AdminUserPlanPrice,
    AdminUserPlanVersion,
    MarzhelpAdminSettings,
    ProxyHost,
    ProxyInbound,
    User,
    UserPlanAssignment,
)
from app.models.admin_hierarchy import (
    OwnerFreezeRequest,
    OwnerUnfreezeRequest,
    PlanCategoryCreate,
    PlanCreate,
    PlanVersionInput,
)
from pydantic import ValidationError
from app.models.admin import Admin as APIAdmin
from app.models.user import UserStatus
from app.routers.admin_hierarchy import (
    activate_disabled_admin as activate_disabled_admin_route,
    freeze_admin as freeze_admin_route,
    get_admin_tree,
    unfreeze_admin as unfreeze_admin_route,
)
from app.utils import admin_billing, admin_hierarchy, admin_plans, marzhelp_policy, money_billing


@pytest.fixture()
def db():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
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
            AdminHierarchySettings(id=1, enabled=False, max_depth=64),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _legacy_tree(db):
    owner = Admin(username="owner", hashed_password="x", is_sudo=True)
    sibling = Admin(username="sibling", hashed_password="x", is_sudo=True)
    leaf = Admin(username="leaf", hashed_password="x", is_sudo=False)
    db.add_all([owner, sibling, leaf])
    db.flush()
    db.add_all(
        [
            MarzhelpAdminSettings(admin_id=owner.id, total_traffic=10_000, calculate_volume="created_traffic"),
            MarzhelpAdminSettings(admin_id=sibling.id, total_traffic=0, calculate_volume="created_traffic"),
            MarzhelpAdminSettings(admin_id=leaf.id, total_traffic=0, calculate_volume="created_traffic"),
        ]
    )
    unowned = User(username="unowned", status=UserStatus.active)
    db.add(unowned)
    db.commit()
    report = admin_hierarchy.set_owner(db, "owner")
    db.refresh(owner)
    db.refresh(sibling)
    db.refresh(leaf)
    return owner, sibling, leaf, unowned, report


def _explicit_network(db, tag="VLESS TCP"):
    inbound = db.query(ProxyInbound).filter(ProxyInbound.tag == tag).one_or_none()
    if inbound is None:
        inbound = ProxyInbound(tag=tag)
        db.add(inbound)
        db.flush()
    host = ProxyHost(remark="stage4 {USERNAME}", address="127.0.0.1", inbound=inbound)
    db.add(host)
    db.flush()
    return {tag: [host.id]}


def test_set_owner_backfills_without_deleting_ids_or_users(db):
    owner, sibling, leaf, unowned, report = _legacy_tree(db)

    assert report["owner"] == "owner"
    assert report["admin_count"] == 3
    assert owner.role_id == admin_hierarchy.ROLE_IDS[admin_hierarchy.OWNER]
    assert owner.parent_admin_id is None
    assert sibling.role_id == admin_hierarchy.ROLE_IDS[admin_hierarchy.ADMIN]
    assert leaf.role_id == admin_hierarchy.ROLE_IDS[admin_hierarchy.ADMIN]
    assert sibling.parent_admin_id == owner.id
    assert leaf.parent_admin_id == owner.id
    assert db.get(User, unowned.id).admin_id == owner.id
    assert admin_hierarchy.hierarchy_enabled(db)
    assert report["closure_rows"] == 5


def test_used_traffic_parent_selects_child_mode_and_delegates_bounded_creation(db):
    owner, parent, _, _, _ = _legacy_tree(db)
    parent_settings = db.get(MarzhelpAdminSettings, parent.id)
    parent_settings.billing_mode = "USED_TRAFFIC"
    parent_settings.can_create_admins = True
    parent_settings.can_delegate_admin_creation = True
    parent_settings.can_create_allocated_children = True
    parent_settings.admin_creation_limit = 5
    child = Admin(username="delegated-child", hashed_password="x")
    db.add(child)
    db.flush()
    child_settings = MarzhelpAdminSettings(admin_id=child.id, billing_mode="USED_TRAFFIC")
    db.add(child_settings)

    assert admin_hierarchy.allowed_child_billing_modes(db, parent, parent_settings) == [
        admin_billing.BillingMode.USED_TRAFFIC,
        admin_billing.BillingMode.ALLOCATED_TRAFFIC,
    ]
    admin_hierarchy.configure_new_child_admin_creation(
        db,
        actor=parent,
        parent=parent,
        child=child,
        child_settings=child_settings,
        child_role=admin_hierarchy.ADMIN,
        child_billing_mode=admin_billing.BillingMode.USED_TRAFFIC,
        can_create_admins=True,
        can_delegate_admin_creation=False,
        can_create_allocated_children=True,
        admin_creation_limit=2,
    )
    admin_hierarchy.attach_new_child(
        db,
        actor=parent,
        parent=parent,
        child=child,
        child_role=admin_hierarchy.ADMIN,
        commit=False,
    )
    db.flush()

    assert parent_settings.admin_creations_used == 1
    assert parent_settings.delegated_admin_creation_limit == 2
    assert admin_hierarchy.admin_creation_remaining(db, parent, parent_settings) == 2
    assert child_settings.admin_creation_limit == 2
    assert child_settings.can_create_admins is True
    assert child_settings.user_creation_mode_id == admin_hierarchy.USER_CREATION_MODE_IDS[admin_hierarchy.PLAN_ONLY]

    parent_settings.user_creation_mode_id = admin_hierarchy.USER_CREATION_MODE_IDS[admin_hierarchy.PLAN_ONLY]
    with pytest.raises(admin_hierarchy.HierarchyError) as forbidden:
        admin_hierarchy.configure_child_user_creation_access(
            db,
            actor=parent,
            parent=parent,
            child_settings=child_settings,
            mode=admin_hierarchy.FREE_FORM,
            can_manage_plans=False,
        )
    assert forbidden.value.code == "child_creation_mode_too_powerful"

    parent_settings.user_creation_mode_id = admin_hierarchy.USER_CREATION_MODE_IDS[admin_hierarchy.FREE_FORM]
    parent_settings.can_manage_plans = True
    with pytest.raises(admin_hierarchy.HierarchyError) as owner_only:
        admin_hierarchy.configure_child_user_creation_access(
            db,
            actor=parent,
            parent=parent,
            child_settings=child_settings,
            mode=admin_hierarchy.FREE_FORM,
            can_manage_plans=True,
        )
    assert owner_only.value.code == "plan_management_owner_only"
    admin_hierarchy.configure_child_user_creation_access(
        db,
        actor=parent,
        parent=parent,
        child_settings=child_settings,
        mode=admin_hierarchy.FREE_FORM,
        can_manage_plans=False,
    )
    assert child_settings.user_creation_mode_id == admin_hierarchy.USER_CREATION_MODE_IDS[admin_hierarchy.FREE_FORM]
    assert child_settings.can_manage_plans is False


def test_manual_freeze_request_requires_human_reason():
    with pytest.raises(ValidationError):
        OwnerFreezeRequest(
            reason_id=1,
            idempotency_key="freeze-without-reason",
        )


def test_owner_credit_is_unlimited_for_plan_validation(db, monkeypatch):
    owner, _, _, _, _ = _legacy_tree(db)
    wallet = db.get(MarzhelpAdminSettings, owner.id)
    monkeypatch.setattr(
        admin_plans.xray.config,
        "inbounds_by_tag",
        {"VLESS TCP": {"tag": "VLESS TCP", "protocol": "vless"}},
    )

    assert wallet.total_traffic is None
    assert admin_hierarchy.available_credit(db, wallet) is None
    hosts = _explicit_network(db)
    plan = admin_plans.create_plan(
        db,
        owner,
        PlanCreate(
            name="owner-unlimited",
            version=PlanVersionInput(
                data_limit=10**15,
                duration_days=30,
                inbounds=["VLESS TCP"],
                hosts=hosts,
            ),
        ),
    )

    assert plan.owner_admin_id == owner.id


def test_plan_category_assignment_controls_admin_access(db, monkeypatch):
    owner, sibling, leaf, _, _ = _legacy_tree(db)
    monkeypatch.setattr(
        admin_plans.xray.config,
        "inbounds_by_tag",
        {"VLESS TCP": {"tag": "VLESS TCP", "protocol": "vless"}},
    )
    category = admin_plans.create_category(
        db,
        owner,
        PlanCategoryCreate(name="reseller plans"),
    )
    admin_plans.replace_admin_categories(
        db,
        actor=owner,
        target=sibling,
        category_ids=[category.id],
    )
    db.commit()
    hosts = _explicit_network(db)
    plan = admin_plans.create_plan(
        db,
        owner,
        PlanCreate(
            name="category-plan",
            category_id=category.id,
            version=PlanVersionInput(
                data_limit=100,
                duration_days=30,
                inbounds=["VLESS TCP"],
                hosts=hosts,
            ),
        ),
    )

    assert admin_plans.admin_category_ids(db, sibling.id) == [category.id]
    assert db.query(AdminPlanCategoryAccess).count() == 1
    assert admin_plans.can_use_plan(db, sibling, plan.id)
    assert not admin_plans.can_use_plan(db, leaf, plan.id)

    admin_plans.replace_admin_categories(
        db,
        actor=owner,
        target=sibling,
        category_ids=[],
    )
    db.commit()
    assert not admin_plans.can_use_plan(db, sibling, plan.id)


def test_scope_blocks_siblings_and_allows_ancestor(db):
    owner, sibling, leaf, _, _ = _legacy_tree(db)
    child = Admin(username="child", hashed_password="x", is_sudo=False)
    db.add(child)
    db.flush()
    db.add(MarzhelpAdminSettings(admin_id=child.id, calculate_volume="created_traffic"))
    admin_hierarchy.attach_new_child(
        db,
        actor=owner,
        parent=sibling,
        child=child,
        child_role=admin_hierarchy.ADMIN,
    )

    assert admin_hierarchy.admin_in_scope(db, sibling, child.id)
    assert not admin_hierarchy.admin_in_scope(db, leaf, child.id)
    assert not admin_hierarchy.admin_in_scope(db, child, sibling.id)
    assert admin_hierarchy.admin_in_scope(db, owner, leaf.id)


def test_admin_tree_uses_constant_query_count(db):
    owner, _, _, _, _ = _legacy_tree(db)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        tree = get_admin_tree(
            db=db,
            admin=APIAdmin(id=owner.id, username=owner.username, is_sudo=True),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert len(tree) == 1
    assert tree[0].username == owner.username
    assert len(tree[0].children) == 2
    # The count is fixed: one tree query plus bounded batch quota aggregates.
    assert len(statements) <= 12


def test_credit_transfer_is_idempotent_and_reclaim_is_bounded(db):
    owner, child, _, _, _ = _legacy_tree(db)
    owner_wallet = db.get(MarzhelpAdminSettings, owner.id)
    child_wallet = db.get(MarzhelpAdminSettings, child.id)
    owner_wallet.total_traffic = 1_000
    child_wallet.total_traffic = 0
    db.commit()

    first = admin_hierarchy.transfer_credit(
        db,
        actor=owner,
        source=owner,
        target=child,
        amount=300,
        operation_type="grant",
        idempotency_key="grant-test-0001",
    )
    duplicate = admin_hierarchy.transfer_credit(
        db,
        actor=owner,
        source=owner,
        target=child,
        amount=300,
        operation_type="grant",
        idempotency_key="grant-test-0001",
    )
    db.refresh(owner_wallet)
    db.refresh(child_wallet)
    assert duplicate.id == first.id
    # Owner is unrestricted, while finite legacy delegation stays reconcilable.
    assert owner_wallet.delegated_traffic == 300
    assert child_wallet.total_traffic == 300

    with pytest.raises(admin_hierarchy.HierarchyError) as conflict:
        admin_hierarchy.transfer_credit(
            db,
            actor=owner,
            source=owner,
            target=child,
            amount=299,
            operation_type="grant",
            idempotency_key="grant-test-0001",
        )
    assert conflict.value.code == "idempotency_conflict"


    with pytest.raises(admin_hierarchy.HierarchyError) as raised:
        admin_hierarchy.transfer_credit(
            db,
            actor=owner,
            source=owner,
            target=child,
            amount=301,
            operation_type="reclaim",
            idempotency_key="reclaim-test-0001",
        )
    assert raised.value.code == "reclaim_exceeds_available"

    reclaimed = admin_hierarchy.transfer_credit(
        db,
        actor=owner,
        source=owner,
        target=child,
        amount=100,
        operation_type="reclaim",
        idempotency_key="reclaim-test-0002",
    )
    assert reclaimed.from_admin_id == child.id
    assert reclaimed.to_admin_id == owner.id


def test_zero_credit_is_finite_after_hierarchy_activation(db):
    _, child, _, _, _ = _legacy_tree(db)
    wallet = db.get(MarzhelpAdminSettings, child.id)
    wallet.total_traffic = 0
    wallet.calculate_volume = "created_traffic"
    db.commit()

    quota = marzhelp_policy.quota_summary(db, child.id)
    assert quota["credit_limit"] == 0
    assert quota["credit_remaining"] == 0
    assert quota["credit_usage_percent"] == 100

    with pytest.raises(marzhelp_policy.MarzhelpPolicyError) as raised:
        marzhelp_policy._validate_traffic_credit(db, wallet, allocated_charge=1)
    assert raised.value.code == "traffic_exhausted"
    assert admin_hierarchy.automatic_suspension_reason(db, wallet) == 2

    wallet.total_traffic = None
    db.commit()
    assert admin_hierarchy.automatic_suspension_reason(db, wallet) is None


def test_bulk_disable_resumes_from_persisted_cursor(db):
    owner, child, _, _, _ = _legacy_tree(db)
    users = [User(username=f"bulk-{index}", admin_id=child.id, status=UserStatus.active) for index in range(4)]
    db.add_all(users)
    db.commit()
    users[0].status = UserStatus.disabled
    job = AdminBulkJob(
        actor_admin_id=owner.id,
        target_admin_id=child.id,
        operation="disable",
        include_subtree=False,
        status="processing",
        total_count=4,
        processed_count=1,
        last_user_id=users[0].id,
        idempotency_key="bulk-resume-0001",
    )
    db.add(job)
    db.commit()

    resumed = admin_hierarchy.run_disable_job(
        db,
        actor=owner,
        target=child,
        include_subtree=False,
        idempotency_key="bulk-resume-0001",
        batch_size=1,
    )
    assert resumed.status == "complete"
    assert resumed.processed_count == 4
    assert all(db.get(User, user.id).status == UserStatus.disabled for user in users)


def test_external_api_token_revoke_invalidates_active_token(db):
    owner, child, _, _, _ = _legacy_tree(db)
    child.external_api_enabled = True
    db.commit()
    row, plaintext = admin_hierarchy.issue_api_token(
        db,
        owner=owner,
        target=child,
        name="automation",
        scopes={"users:read"},
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )

    authenticated, scopes = admin_hierarchy.authenticate_api_token(db, plaintext)
    assert authenticated.id == child.id
    assert scopes == {"users:read"}
    assert admin_hierarchy.revoke_api_access(db, owner, child) == 1
    assert admin_hierarchy.authenticate_api_token(db, plaintext) is None
    assert db.get(type(row), row.id).revoked_at is not None


def test_suspend_resume_restores_only_users_changed_by_event(db):
    owner, child, _, _, _ = _legacy_tree(db)
    active = User(username="active-child", admin_id=child.id, status=UserStatus.active)
    disabled = User(username="disabled-child", admin_id=child.id, status=UserStatus.disabled)
    db.add_all([active, disabled])
    db.commit()

    event = admin_hierarchy.suspend_admin(
        db,
        actor=owner,
        target=child,
        reason_id=1,
        include_subtree=True,
        batch_size=1,
    )
    db.refresh(active)
    db.refresh(disabled)
    assert active.status == UserStatus.disabled
    assert disabled.status == UserStatus.disabled
    assert event.status == "complete"

    restored = admin_hierarchy.resume_admin(db, actor=owner, target=child)
    db.refresh(active)
    db.refresh(disabled)
    assert restored == 1
    assert active.status == UserStatus.active
    assert disabled.status == UserStatus.disabled


def test_resume_releases_eventless_manual_suspension(db):
    owner, child, _, _, _ = _legacy_tree(db)
    settings = db.get(MarzhelpAdminSettings, child.id)
    settings.account_status_id = admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.SUSPENDED]
    settings.suspended_reason_id = 1
    settings.suspended_at = datetime.utcnow()
    settings.suspended_by_admin_id = owner.id
    settings.suspension_event_id = None
    db.commit()

    assert admin_hierarchy.resume_admin(db, actor=owner, target=child) == 0
    db.refresh(settings)
    assert settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.ACTIVE]
    assert settings.suspended_reason_id is None
    assert settings.suspended_at is None
    assert settings.suspended_by_admin_id is None


def test_suspended_admin_can_read_users_but_cannot_mutate(db, monkeypatch):
    _, child, _, _, _ = _legacy_tree(db)
    settings = db.get(MarzhelpAdminSettings, child.id)
    settings.account_status_id = admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.SUSPENDED]
    settings.suspended_reason_id = 1
    db.commit()
    authenticated = APIAdmin(id=child.id, username=child.username, is_sudo=False)
    monkeypatch.setattr(
        APIAdmin,
        "get_admin",
        classmethod(lambda cls, token, session: authenticated),
    )

    readable = APIAdmin.get_current(
        Request({"type": "http", "method": "GET", "path": "/api/users", "headers": []}),
        db,
        "token",
    )
    assert readable.username == child.username

    with pytest.raises(HTTPException) as exc:
        APIAdmin.get_current(
            Request({"type": "http", "method": "POST", "path": "/api/user", "headers": []}),
            db,
            "token",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "account_read_only"


def test_authorized_parent_activates_disabled_admin_without_touching_users(db):
    owner, child, _, _, _ = _legacy_tree(db)
    settings = db.get(MarzhelpAdminSettings, child.id)
    settings.total_traffic = None
    settings.account_status_id = admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.DISABLED]
    disabled_user = User(username="disabled-admin-user", admin_id=child.id, status=UserStatus.disabled)
    db.add(disabled_user)
    db.commit()

    admin_hierarchy.activate_disabled_admin(db, actor=owner, target=child)

    db.refresh(settings)
    db.refresh(disabled_user)
    assert settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.ACTIVE]
    assert disabled_user.status == UserStatus.disabled


def test_active_admin_cannot_be_activated_again(db):
    owner, child, _, _, _ = _legacy_tree(db)

    with pytest.raises(admin_hierarchy.HierarchyError) as error:
        admin_hierarchy.activate_disabled_admin(db, actor=owner, target=child)

    assert error.value.code == "account_not_disabled"


def test_activate_disabled_route_updates_status_and_writes_audit(db):
    owner, child, _, _, _ = _legacy_tree(db)
    settings = db.get(MarzhelpAdminSettings, child.id)
    settings.total_traffic = None
    settings.account_status_id = admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.DISABLED]
    db.commit()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/admin-management/{child.username}/activate",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )

    response = activate_disabled_admin_route(
        child.username,
        request,
        db,
        APIAdmin(id=owner.id, username=owner.username, is_sudo=True),
    )

    db.refresh(settings)
    audit = db.query(AdminAuditLog).filter(AdminAuditLog.action == "admin.activate").one()
    assert response == {"account_status": "ACTIVE"}
    assert settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.ACTIVE]
    assert audit.target_name == child.username


def test_stage7_referral_is_owner_only_idempotent_and_attribution_only(db):
    owner, referrer, referred, _, _ = _legacy_tree(db)
    ledger_before = db.query(AdminCreditTransfer).count()

    with pytest.raises(admin_hierarchy.HierarchyError) as forbidden:
        admin_hierarchy.set_referral_attribution(
            db,
            actor=referrer,
            referred=referred,
            referrer=owner,
            rate_bps=250,
            idempotency_key="referral-forbidden-0001",
        )
    assert forbidden.value.code == "owner_required"

    event, created = admin_hierarchy.set_referral_attribution(
        db,
        actor=owner,
        referred=referred,
        referrer=referrer,
        rate_bps=250,
        idempotency_key="referral-stage7-0001",
        note="attribution only",
    )
    replay, replay_created = admin_hierarchy.set_referral_attribution(
        db,
        actor=owner,
        referred=referred,
        referrer=referrer,
        rate_bps=250,
        idempotency_key="referral-stage7-0001",
        note="attribution only",
    )
    attribution = db.get(AdminReferralAttribution, referred.id)
    assert created is True and replay_created is False and replay.id == event.id
    assert attribution.referrer_admin_id == referrer.id
    assert attribution.rate_bps == 250
    assert db.query(AdminReferralEvent).count() == 1
    assert db.query(AdminCreditTransfer).count() == ledger_before


def test_stage7_owner_freeze_cascades_and_restores_only_freeze_owned_state(db):
    owner, target, sibling, _, _ = _legacy_tree(db)
    grandchild = Admin(username="grandchild", hashed_password="x", is_sudo=False)
    db.add(grandchild)
    db.flush()
    db.add(MarzhelpAdminSettings(admin_id=grandchild.id, calculate_volume="created_traffic"))
    admin_hierarchy.attach_new_child(
        db,
        actor=owner,
        parent=target,
        child=grandchild,
        child_role=admin_hierarchy.ADMIN,
    )
    target_settings = db.get(MarzhelpAdminSettings, target.id)
    grandchild_settings = db.get(MarzhelpAdminSettings, grandchild.id)
    grandchild_settings.account_status_id = admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.DISABLED]
    active = User(username="freeze-active", admin_id=target.id, status=UserStatus.active)
    on_hold = User(username="freeze-on-hold", admin_id=grandchild.id, status=UserStatus.on_hold)
    pre_disabled = User(username="freeze-disabled", admin_id=grandchild.id, status=UserStatus.disabled)
    outside = User(username="freeze-outside", admin_id=sibling.id, status=UserStatus.active)
    db.add_all([active, on_hold, pre_disabled, outside])
    db.commit()

    with pytest.raises(admin_hierarchy.HierarchyError) as forbidden:
        admin_hierarchy.freeze_admin(
            db,
            actor=target,
            target=grandchild,
            reason_id=1,
            idempotency_key="freeze-forbidden-0001",
        )
    assert forbidden.value.code == "freeze_forbidden"

    event, created = admin_hierarchy.freeze_admin(
        db,
        actor=owner,
        target=target,
        reason_id=1,
        idempotency_key="freeze-stage7-0001",
        note="support review",
        batch_size=1,
    )
    replay, replay_created = admin_hierarchy.freeze_admin(
        db,
        actor=owner,
        target=target,
        reason_id=1,
        idempotency_key="freeze-stage7-0001",
        note="support review",
        batch_size=1,
    )
    assert created is True and replay_created is False and replay.id == event.id
    assert db.query(AdminSuspensionAdmin).filter(AdminSuspensionAdmin.event_id == event.id).count() == 2
    for item in (active, on_hold, pre_disabled):
        db.refresh(item)
        assert item.status == UserStatus.disabled
    db.refresh(outside)
    assert outside.status == UserStatus.active
    db.refresh(target_settings)
    db.refresh(grandchild_settings)
    assert target_settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.SUSPENDED]
    assert grandchild_settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.SUSPENDED]
    with pytest.raises(admin_hierarchy.HierarchyError) as blocked:
        admin_hierarchy.require_active_account(db, target)
    assert blocked.value.code == "account_read_only"

    # Simulate an independent state change after freeze. Unfreeze must not overwrite it.
    on_hold.status = UserStatus.on_hold
    db.commit()
    resolved, restored_admins, restored_users, unfreeze_created = admin_hierarchy.unfreeze_admin(
        db,
        actor=owner,
        target=target,
        idempotency_key="unfreeze-stage7-0001",
    )
    resolved_replay, replay_admins, replay_users, second_created = admin_hierarchy.unfreeze_admin(
        db,
        actor=owner,
        target=target,
        idempotency_key="unfreeze-stage7-0001",
    )
    assert unfreeze_created is True and second_created is False
    assert resolved_replay.id == resolved.id
    assert replay_admins == restored_admins and replay_users == restored_users
    assert restored_admins == 2 and restored_users == 1
    db.refresh(active)
    db.refresh(on_hold)
    db.refresh(pre_disabled)
    db.refresh(target_settings)
    db.refresh(grandchild_settings)
    assert active.status == UserStatus.active
    assert on_hold.status == UserStatus.on_hold
    assert pre_disabled.status == UserStatus.disabled
    assert target_settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.ACTIVE]
    assert grandchild_settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.DISABLED]


def test_stage7_freeze_and_unfreeze_routes_write_audit_rows(db):
    owner, target, _, _, _ = _legacy_tree(db)
    actor = APIAdmin(id=owner.id, username=owner.username, is_sudo=True)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/admin-management/{target.username}/freeze",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    frozen = freeze_admin_route(
        target.username,
        OwnerFreezeRequest(
            reason_id=1,
            idempotency_key="freeze-route-audit-0001",
            note="support audit",
        ),
        request,
        BackgroundTasks(),
        db,
        actor,
    )
    unfrozen = unfreeze_admin_route(
        target.username,
        OwnerUnfreezeRequest(idempotency_key="unfreeze-route-audit-0001"),
        request,
        BackgroundTasks(),
        db,
        actor,
    )
    assert frozen["replayed"] is False and unfrozen["replayed"] is False
    events = {
        row.action
        for row in db.query(AdminAuditLog)
        .filter(AdminAuditLog.target_id == str(target.id))
        .all()
    }
    assert {"admin.owner_freeze", "admin.owner_unfreeze"} <= events


def test_plan_updates_append_immutable_version(db, monkeypatch):
    owner, child, _, _, _ = _legacy_tree(db)
    monkeypatch.setattr(
        admin_plans.xray.config,
        "inbounds_by_tag",
        {"VLESS TCP": {"tag": "VLESS TCP", "protocol": "vless"}},
    )
    hosts = _explicit_network(db)
    values = PlanCreate(
        name="standard",
        version=PlanVersionInput(
            data_limit=100,
            duration_days=30,
            concurrent_user_limit=1,
            inbounds=["VLESS TCP"],
            hosts=hosts,
        ),
        allowed_admin_ids=[child.id],
    )
    plan = admin_plans.create_plan(db, owner, values)
    first_version = plan.current_version_id
    update = values.model_dump(exclude={"name"})
    update["version"]["data_limit"] = 200
    updated = admin_plans.update_plan(db, owner, plan, admin_plans.PlanUpdate(**update))

    assert updated.current_version_id != first_version
    response = admin_plans.plan_response(db, updated)
    assert response.version_number == 2
    assert response.version.data_limit == 200
    assert admin_plans.can_use_plan(db, child, plan.id)

    replay_user = User(username="idempotency-owner", admin_id=owner.id, status=UserStatus.active)
    db.add(replay_user)
    db.flush()
    db.add(
        UserPlanAssignment(
            user_id=replay_user.id,
            plan_id=plan.id,
            version_id=updated.current_version_id,
            actor_admin_id=owner.id,
            operation_type="create",
            idempotency_key="plan-replay-0001",
        )
    )
    db.commit()
    with pytest.raises(admin_hierarchy.HierarchyError) as conflict:
        admin_plans._assignment_replay(
            db,
            actor=child,
            plan_id=plan.id,
            username=replay_user.username,
            operation_type="create",
            idempotency_key="plan-replay-0001",
        )
    assert conflict.value.code == "idempotency_conflict"


def _money_tree(db):
    owner, parent, child, _, _ = _legacy_tree(db)
    parent_settings = db.get(MarzhelpAdminSettings, parent.id)
    parent_settings.can_create_admins = True
    admin_hierarchy.reparent_subtree(db, owner, child, parent)
    child_settings = db.get(MarzhelpAdminSettings, child.id)
    for settings in (parent_settings, child_settings):
        settings.money_billing_enabled = True
        settings.money_balance_toman = 1_000_000
    db.flush()
    return owner, parent, child, parent_settings, child_settings


def test_plan_money_chain_uses_reseller_prices_and_margin(db):
    owner, parent, child, parent_settings, child_settings = _money_tree(db)
    parent_settings.billing_mode = admin_billing.BillingMode.ALLOCATED_TRAFFIC.value
    child_settings.billing_mode = admin_billing.BillingMode.ALLOCATED_TRAFFIC.value
    plan = AdminUserPlan(owner_admin_id=owner.id, name="20 GiB")
    db.add(plan)
    db.flush()
    version = AdminUserPlanVersion(
        plan_id=plan.id, version_number=1, price_toman=50_000,
        data_limit=20 * 1024 ** 3, duration_days=30, reset_strategy="no_reset",
        renewal_volume_strategy="replace", renewal_time_strategy="extend_max",
        created_by_admin_id=owner.id,
    )
    db.add(version)
    db.flush()
    plan.current_version_id = version.id
    db.add_all([
        AdminUserPlanPrice(
            admin_id=parent.id,
            plan_id=plan.id,
            price_toman=50_000,
            assigned_by_admin_id=owner.id,
        ),
        AdminUserPlanPrice(
            admin_id=child.id,
            plan_id=plan.id,
            price_toman=70_000,
            assigned_by_admin_id=parent.id,
        ),
    ])
    db.flush()
    assert money_billing.effective_plan_price(db, parent, plan, version) == 50_000
    assert money_billing.effective_plan_price(db, child, plan, version) == 70_000
    assert admin_plans.plan_response(db, plan, actor=child).effective_price_toman == 70_000
    money_billing.charge_plan_purchase(
        db, buyer=child, actor=child, plan=plan, version=version,
        operation_type="create", idempotency_key="priced-plan-chain-1",
    )
    assert child_settings.money_balance_toman == 930_000
    assert parent_settings.money_balance_toman == 1_020_000
    assert db.query(AdminMoneyTransaction).filter(
        AdminMoneyTransaction.operation_key == "plan-money:priced-plan-chain-1"
    ).count() == 2


def test_used_traffic_money_chain_bills_fractional_gib_and_margin(db):
    _, parent, child, parent_settings, child_settings = _money_tree(db)
    parent_settings.billing_mode = admin_billing.BillingMode.USED_TRAFFIC.value
    parent_settings.used_traffic_price_per_gib_toman = 50_000
    child_settings.billing_mode = admin_billing.BillingMode.USED_TRAFFIC.value
    child_settings.used_traffic_price_per_gib_toman = 70_000
    money_billing.settle_used_traffic(db, {child.id: (1024 ** 3) // 2})
    assert child_settings.money_balance_toman == 965_000
    assert parent_settings.money_balance_toman == 1_010_000
    assert child_settings.usage_billing_remainder == 0
    assert parent_settings.usage_billing_remainder == 0


def test_used_traffic_zero_crossing_suspends_account_and_active_users_atomically(db):
    _, parent, child, parent_settings, child_settings = _money_tree(db)
    parent_settings.billing_mode = admin_billing.BillingMode.USED_TRAFFIC.value
    parent_settings.used_traffic_price_per_gib_toman = 50_000
    child_settings.billing_mode = admin_billing.BillingMode.USED_TRAFFIC.value
    child_settings.used_traffic_price_per_gib_toman = 70_000
    child_settings.money_balance_toman = 10_000
    user = User(username="prepaid-crossing", admin_id=child.id, status=UserStatus.active)
    db.add(user)
    db.flush()

    suspended = money_billing.settle_used_traffic(db, {child.id: 1024 ** 3})
    db.refresh(child_settings)

    assert suspended == {child.id}
    assert child_settings.money_balance_toman == -60_000
    assert child_settings.account_status_id == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.SUSPENDED]
    assert child_settings.suspended_reason_id == 2
    assert user.status == UserStatus.disabled
    assert db.query(AdminMoneyTransaction).filter(
        AdminMoneyTransaction.admin_id == child.id,
        AdminMoneyTransaction.operation_type == "usage_settlement",
    ).count() == 1


def test_owner_money_grant_is_idempotent(db):
    owner, _, child, _, child_settings = _money_tree(db)
    child.parent_admin_id = owner.id
    child_settings.money_balance_toman = 0
    result, created = money_billing.transfer_money(
        db, actor=owner, parent=owner, child=child, amount_toman=1_000_000,
        operation_type="grant", idempotency_key="initial-money-1",
    )
    replay, replay_created = money_billing.transfer_money(
        db, actor=owner, parent=owner, child=child, amount_toman=1_000_000,
        operation_type="grant", idempotency_key="initial-money-1",
    )
    assert created is True
    assert replay_created is False
    assert result["target_balance_toman"] == 1_000_000
    assert replay["target_balance_toman"] == 1_000_000
