from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app import xray
from app.db import crud
from app.db.base import Base
from app.db.models import (
    Admin,
    AdminAccountStatus,
    AdminHierarchySettings,
    AdminRole,
    AdminSuspensionReason,
    AdminUserCreationMode,
    MarzhelpAccountingTransaction,
    MarzhelpAdminInboundPermission,
    MarzhelpAdminSettings,
    ProxyHost,
    ProxyInbound,
    SystemOwner,
    User,
)
from app.models.admin import Admin as APIAdmin, AdminCreate
from app.models import user as user_models
from app.models.admin_hierarchy import PlanCreate, PlanVersionInput
from app.models.proxy import ProxyTypes
from app.models.user import UserCreate, UserModify, UserResponse, UserStatus
from app.routers.user import add_user
from app.subscription import share as subscription_share
from app.utils import admin_hierarchy, admin_plans, marzhelp_policy


@pytest.fixture()
def db(tmp_path):
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'stage5.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
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
        yield session, Session
    finally:
        session.close()
        engine.dispose()


def _network(db, monkeypatch):
    tag = "VLESS TCP"
    inbound_config = {
        "tag": tag,
        "protocol": "vless",
        "network": "tcp",
        "tls": "none",
        "port": 443,
        "sni": [],
        "host": [],
        "path": "",
        "header_type": "none",
    }
    monkeypatch.setattr(xray.config, "inbounds_by_tag", {tag: inbound_config})
    monkeypatch.setattr(xray.config, "inbounds_by_protocol", {"vless": [inbound_config]})
    inbound = ProxyInbound(tag=tag)
    db.add(inbound)
    db.flush()
    host = ProxyHost(remark="stage5 {USERNAME}", address="one.example", inbound=inbound)
    db.add(host)
    db.commit()
    # Subscription rendering uses the process-global host cache, whose refresh
    # opens the configured application database. Keep this SQLite unit fixture
    # isolated while the network-scope assertions continue to use the DB rows.
    monkeypatch.setattr(xray, "hosts", {tag: []})
    monkeypatch.setattr(subscription_share.xray, "hosts", {tag: []})
    monkeypatch.setattr(user_models, "create_subscription_token", lambda username: f"token-{username}")
    return tag, host


def _admin(db, username, *, billing_mode="LEGACY_COMPAT", capacity=100):
    admin = Admin(username=username, hashed_password="x", is_sudo=False)
    db.add(admin)
    db.flush()
    settings = MarzhelpAdminSettings(
        admin_id=admin.id,
        billing_mode=billing_mode,
        total_traffic=None,
        device_capacity_limit=capacity,
        can_manage_plans=True,
        renewal_enabled=True,
    )
    db.add(settings)
    db.commit()
    return admin, settings


def _payload(username, tag):
    return UserCreate(
        username=username,
        status="active",
        data_limit=1024,
        expire=2_000_000_000,
        concurrent_user_limit=1,
        proxies={"vless": {}},
        inbounds={"vless": [tag]},
    )


def test_prefix_is_stable_unique_and_existing_users_are_not_renamed(db, monkeypatch):
    session, _ = db
    tag, _ = _network(session, monkeypatch)
    first, _ = _admin(session, "first")
    second, _ = _admin(session, "second")
    third, _ = _admin(session, "third")
    first.is_sudo = True
    first.role_id = admin_hierarchy.ROLE_IDS[admin_hierarchy.OWNER]
    second.role_id = admin_hierarchy.ROLE_IDS[admin_hierarchy.SUPER_ADMIN]
    second.parent_admin_id = first.id
    third.role_id = admin_hierarchy.ROLE_IDS[admin_hierarchy.ADMIN]
    third.parent_admin_id = second.id
    session.add(SystemOwner(id=1, admin_id=first.id))
    legacy = User(username="legacy-customer", status=UserStatus.active, admin=first)
    session.add(legacy)
    session.commit()

    first_user = crud.create_user(session, _payload("shared", tag), admin=first)
    first_prefix = first.user_namespace_prefix
    second_user = crud.create_user(session, _payload("shared", tag), admin=second)
    third_user = crud.create_user(session, _payload("shared", tag), admin=third)
    another = crud.create_user(session, _payload("another", tag), admin=first)

    assert first_prefix
    assert first.user_namespace_prefix == first_prefix
    assert second.user_namespace_prefix != first_prefix
    assert third.user_namespace_prefix not in {first_prefix, second.user_namespace_prefix}
    assert first_user.username == f"{first_prefix}_shared"
    assert second_user.username == f"{second.user_namespace_prefix}_shared"
    assert third_user.username == f"{third.user_namespace_prefix}_shared"
    assert another.username == f"{first_prefix}_another"
    assert session.get(User, legacy.id).username == "legacy-customer"
    assert first.username == "first"
    assert second.username == "second"
    assert third.username == "third"


def test_concurrent_admin_creation_produces_unique_persisted_prefixes(db):
    session, Session = db
    session.close()

    def create(number):
        worker = Session()
        try:
            admin = crud.create_admin(
                worker,
                AdminCreate(username=f"parallel-{number}", password="secret"),
            )
            return admin.user_namespace_prefix
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        prefixes = list(pool.map(create, range(4)))
    assert all(prefixes)
    assert len(prefixes) == len(set(prefixes))


@pytest.mark.parametrize("billing_mode", ["USED_TRAFFIC", "ALLOCATED_TRAFFIC"])
def test_restricted_create_accepts_simple_fields_and_rejects_injection(
    db, monkeypatch, billing_mode
):
    session, _ = db
    tag, _ = _network(session, monkeypatch)
    _, settings = _admin(session, "restricted", billing_mode=billing_mode)
    settings.all_inbounds = False
    settings.all_user_limits = False
    settings.inbound_permissions = [
        MarzhelpAdminInboundPermission(admin_id=settings.admin_id, inbound_tag=tag)
    ]
    from app.db.models import MarzhelpAdminUserLimitPermission

    settings.user_limit_permissions = [
        MarzhelpAdminUserLimitPermission(admin_id=settings.admin_id, concurrent_user_limit=2),
        MarzhelpAdminUserLimitPermission(admin_id=settings.admin_id, concurrent_user_limit=5),
    ]
    session.commit()

    simple = UserCreate(username="simple", data_limit=2048, expire=2_000_000_000, note="note")
    controlled = marzhelp_policy.restricted_create_payload(settings, simple)
    assert controlled.concurrent_user_limit == 2
    assert controlled.inbounds == {ProxyTypes.VLESS: [tag]}
    assert set(controlled.proxies) == {ProxyTypes.VLESS}
    assert controlled.status.value == "active"

    injected = _payload("injected", tag)
    with pytest.raises(marzhelp_policy.MarzhelpPolicyError) as exc:
        marzhelp_policy.restricted_create_payload(settings, injected)
    assert exc.value.code == "protected_create_fields"


def test_seat_raw_endpoint_is_denied_even_if_creation_mode_is_free_form(db, monkeypatch):
    session, _ = db
    tag, _ = _network(session, monkeypatch)
    admin, _ = _admin(session, "seat-raw", billing_mode="SEAT_CREDIT")
    session.get(AdminHierarchySettings, 1).enabled = True
    session.commit()

    with pytest.raises(HTTPException) as exc:
        add_user(
            Request({"type": "http", "method": "POST", "path": "/api/user", "headers": []}),
            _payload("forbidden", tag),
            BackgroundTasks(),
            session,
            APIAdmin(username=admin.username, is_sudo=False),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "plan_only"


def test_plan_only_raw_endpoint_is_denied_after_policy_save(db, monkeypatch):
    session, _ = db
    tag, _ = _network(session, monkeypatch)
    admin, settings = _admin(session, "plan-only-api", billing_mode="USER_CREDIT")
    settings.user_creation_mode_id = admin_hierarchy.USER_CREATION_MODE_IDS[
        admin_hierarchy.PLAN_ONLY
    ]
    session.get(AdminHierarchySettings, 1).enabled = True
    session.commit()

    with pytest.raises(HTTPException) as exc:
        add_user(
            Request({"type": "http", "method": "POST", "path": "/api/user", "headers": []}),
            _payload("forbidden-plan-only", tag),
            BackgroundTasks(),
            session,
            APIAdmin(username=admin.username, is_sudo=False),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "plan_only"
    assert session.query(User).filter(User.admin_id == admin.id).count() == 0


def test_plan_only_direct_quota_edits_are_blocked_but_notes_remain_editable(db):
    session, _ = db
    admin, settings = _admin(session, "plan-only-editor")
    settings.user_creation_mode_id = admin_hierarchy.USER_CREATION_MODE_IDS[
        admin_hierarchy.PLAN_ONLY
    ]
    user = User(
        username="plan-only-editor_customer",
        admin_id=admin.id,
        status=UserStatus.active,
        data_limit=1024,
        expire=2_000_000_000,
        concurrent_user_limit=1,
    )
    session.add(user)
    session.commit()
    actor = APIAdmin(id=admin.id, username=admin.username, is_sudo=False)

    with pytest.raises(marzhelp_policy.MarzhelpPolicyError) as exc:
        crud.update_user(
            session,
            user,
            UserModify(
                data_limit=2048,
                expire=2_100_000_000,
                concurrent_user_limit=2,
            ),
            actor=actor,
        )

    assert exc.value.code == "plan_only_direct_edit_forbidden"
    updated = crud.update_user(session, user, UserModify(note="read-only quota fields"), actor=actor)
    assert updated.note == "read-only quota fields"


def test_plan_only_plan_renewal_and_owner_override_can_change_quota(db):
    session, _ = db
    admin, settings = _admin(session, "plan-only-renew")
    settings.user_creation_mode_id = admin_hierarchy.USER_CREATION_MODE_IDS[
        admin_hierarchy.PLAN_ONLY
    ]
    user = User(
        username="plan-only-renew_customer",
        admin_id=admin.id,
        status=UserStatus.active,
        data_limit=1024,
        expire=2_000_000_000,
        concurrent_user_limit=1,
    )
    session.add(user)
    session.commit()
    actor = APIAdmin(id=admin.id, username=admin.username, is_sudo=False)
    owner = APIAdmin(username="owner", is_sudo=True)

    renewed = crud.update_user(
        session,
        user,
        UserModify(data_limit=2048),
        operation=marzhelp_policy.UserUpdateOperation.renew,
        actor=actor,
    )
    assert renewed.data_limit == 2048
    overridden = crud.update_user(
        session,
        user,
        UserModify(expire=2_100_000_000, concurrent_user_limit=2),
        actor=owner,
    )
    assert overridden.expire == 2_100_000_000
    assert overridden.concurrent_user_limit == 2


def test_partial_custom_payload_never_commits_a_proxyless_user_in_compatibility_mode(db):
    session, _ = db
    admin, settings = _admin(session, "plan-only-compat", billing_mode="USER_CREDIT")
    settings.user_creation_mode_id = admin_hierarchy.USER_CREATION_MODE_IDS[
        admin_hierarchy.PLAN_ONLY
    ]
    session.commit()

    with pytest.raises(HTTPException) as exc:
        add_user(
            Request({"type": "http", "method": "POST", "path": "/api/user", "headers": []}),
            UserCreate(username="thirteen-gib", data_limit=13 * 1024**3),
            BackgroundTasks(),
            session,
            APIAdmin(username=admin.username, is_sudo=False),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == {"proxies": "Each user needs at least one proxy"}
    assert session.query(User).filter(User.username == "thirteen-gib").count() == 0


def test_proxyless_existing_user_remains_readable_for_operator_recovery(db, monkeypatch):
    session, _ = db
    monkeypatch.setattr(user_models, "create_subscription_token", lambda username: f"token-{username}")
    admin, _ = _admin(session, "recovery-admin", billing_mode="USER_CREDIT")
    broken = User(
        username="recovery-admin_broken",
        admin_id=admin.id,
        status=UserStatus.active,
        data_limit=13 * 1024**3,
        proxies=[],
    )
    session.add(broken)
    session.commit()

    response = UserResponse.model_validate(broken)

    assert response.username == broken.username
    assert response.proxies == {}
    assert response.links == []


def test_restricted_raw_endpoint_rejects_protected_network_and_device_fields(db, monkeypatch):
    session, _ = db
    tag, _ = _network(session, monkeypatch)
    admin, _ = _admin(session, "used-api", billing_mode="USED_TRAFFIC")
    session.get(AdminHierarchySettings, 1).enabled = True
    session.commit()

    with pytest.raises(marzhelp_policy.MarzhelpPolicyError) as exc:
        add_user(
            Request({"type": "http", "method": "POST", "path": "/api/user", "headers": []}),
            _payload("injected-api", tag),
            BackgroundTasks(),
            session,
            APIAdmin(username=admin.username, is_sudo=False),
        )
    assert exc.value.code == "protected_create_fields"


@pytest.mark.parametrize("billing_mode", ["USED_TRAFFIC", "ALLOCATED_TRAFFIC"])
def test_restricted_raw_endpoint_creates_from_simple_fields(db, monkeypatch, billing_mode):
    session, _ = db
    tag, _ = _network(session, monkeypatch)
    admin, _ = _admin(session, f"simple-{billing_mode.lower()}", billing_mode=billing_mode)
    session.get(AdminHierarchySettings, 1).enabled = True
    session.commit()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/user",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    response = add_user(
        request,
        UserCreate(
            username="simple-customer",
            data_limit=2048,
            expire=2_000_000_000,
            note="description",
        ),
        BackgroundTasks(),
        session,
        APIAdmin(username=admin.username, is_sudo=False),
    )
    dbuser = session.query(User).filter(User.username == response.username).one()
    assert dbuser.username == f"{admin.user_namespace_prefix}_simple-customer"
    assert dbuser.inbounds == {ProxyTypes.VLESS: [tag]}
    assert dbuser.concurrent_user_limit is None


def test_seat_plan_renewal_charges_exact_cost_once_on_retry(db, monkeypatch):
    session, _ = db
    tag, host = _network(session, monkeypatch)
    admin, settings = _admin(session, "seat-plan", billing_mode="SEAT_CREDIT", capacity=10)
    admin.role_id = admin_hierarchy.ROLE_IDS[admin_hierarchy.SUPER_ADMIN]
    settings.user_creation_mode_id = 2  # PLAN_ONLY; Form-only accounts cannot use Plans.
    owner, _ = _admin(session, "plan-owner")
    owner.is_sudo = True
    owner.role_id = admin_hierarchy.ROLE_IDS[admin_hierarchy.OWNER]
    session.add(SystemOwner(id=1, admin_id=owner.id))
    session.commit()
    plan = admin_plans.create_plan(
        session,
        owner,
        PlanCreate(
            name="two-seat",
            allowed_admin_ids=[admin.id],
            version=PlanVersionInput(
                data_limit=1024,
                duration_days=30,
                concurrent_user_limit=2,
                inbounds=[tag],
                hosts={tag: [host.id]},
            ),
        ),
    )
    user, _, created = admin_plans.create_user_from_plan(
        session,
        actor=admin,
        plan_id=plan.id,
        username="customer",
        status="active",
        note=None,
        idempotency_key="stage5-seat-create",
    )
    assert created is True
    assert settings.capacity_used == 2
    assert user.username == f"{admin.user_namespace_prefix}_customer"

    _, _, renewed = admin_plans.renew_user_from_plan(
        session,
        actor=admin,
        user=user,
        plan_id=plan.id,
        idempotency_key="stage5-seat-renew",
    )
    assert renewed is True
    session.refresh(settings)
    assert settings.capacity_used == 4

    _, _, replay_created = admin_plans.renew_user_from_plan(
        session,
        actor=admin,
        user=user,
        plan_id=plan.id,
        idempotency_key="stage5-seat-renew",
    )
    assert replay_created is False
    session.refresh(settings)
    assert settings.capacity_used == 4
    transactions = session.query(MarzhelpAccountingTransaction).filter_by(
        operation_type="plan_renew_seat"
    ).all()
    assert len(transactions) == 1
    assert transactions[0].details["seat_cost"] == 2

    user.expire = 1
    session.commit()
    session.refresh(settings)
    assert settings.capacity_used == 4
