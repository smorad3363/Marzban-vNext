from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import inspect
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db import crud
from app.db.models import (
    Admin,
    AdminAuditLog,
    MarzhelpAccountingTransaction,
    MarzhelpAdminSettings,
    MarzhelpAdminSubscriptionModePermission,
    MarzhelpDeletedUser,
    Proxy,
    User,
    MarzhelpAdminUserLimitPermission,
    DeviceLimitPenaltyStage,
    DeviceLimitUserState,
    DeviceLimitIncident,
    DeviceLimitSettings,
)
from app.device_limit.clients import observe_subscription_client, parse_user_agent
from app.device_limit.constants import PenaltyAction, SubscriptionMode
from app.device_limit.engine import DeviceLimitEngine, HIT_BUFFER_CAPACITY, mask_ip
from app.device_limit.slots import slot_email, sync_device_slots


def test_collector_exits_when_node_object_is_replaced(monkeypatch):
    from collections import deque
    from unittest.mock import Mock
    from app import xray
    engine = DeviceLimitEngine()
    source = Mock()
    @contextmanager
    def logs():
        yield deque(["must not consume stale source"])
    source.get_logs = logs
    monkeypatch.setattr(xray, "nodes", {7: object()})
    engine._stop = Mock()
    engine._stop.wait.side_effect = [False, True]
    engine.record_log = Mock()
    engine._collect(source, "node:7")
    engine.record_log.assert_not_called()
from app.models.user import UserStatus
from app.models.user import UserCreate
from app.models.device_limit import DeviceLimitSettingsUpdate
from app.models.admin import Admin as AdminSchema
from app.models.proxy import ProxyTypes
from app.routers.device_limit import (
    delete_warning,
    get_diagnostics,
    get_penalty_stages,
    get_settings,
    list_incidents,
    modify_slot,
    reset_strikes,
    unblock_user,
    update_penalty_stages,
    update_settings,
    user_summary,
)
from app.xray import operations
from app.utils import marzhelp_policy


XRAY_ACCESS_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "xray_access_v26.7.28.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'device-limit.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.info["session_factory"] = Session
    yield db
    db.close()
    engine.dispose()


def test_xray_access_parser_is_bounded_and_requires_hit_threshold():
    tracker = DeviceLimitEngine()
    tracker.configure(True, "hybrid")
    lines = "\n".join(
        (
            "2026/08/16 12:00:00 8.8.8.8:51000 accepted tcp:example.com:443 [vless >> direct] email: 42.demo.slot2",
            "2026/08/16 12:00:01 8.8.8.8:51001 accepted tcp:example.com:443 [vless >> direct] email: 42.demo.slot2",
            "2026/08/16 12:00:02 1.1.1.1:51002 accepted tcp:example.com:443 [vless >> direct] email: 42.demo.slot2",
            "2026/08/16 12:00:03 192.168.1.4:51003 accepted tcp:example.com:443 [vless >> direct] email: 42.demo.slot2",
        )
    )
    assert tracker.record_log(lines, "node:7") == 3
    addresses, sources, per_slot = tracker.live_snapshot(42, 300, 2)
    assert addresses == {"8.8.8.8"}
    assert sources == {"node:7"}
    assert per_slot == {2: {"8.8.8.8"}}
    assert mask_ip("8.8.8.8") == "8.8.***.***"


def test_auto_delete_uses_credit_guard_and_never_refunds_allocated_traffic(
    session,
    monkeypatch,
):
    allocated = 50 * 1024**3
    used = 30 * 1024**3
    admin = Admin(username="device-delete-admin", hashed_password="x")
    session.add(admin)
    session.flush()
    policy_settings = MarzhelpAdminSettings(
        admin_id=admin.id,
        total_traffic=allocated,
        used_traffic=allocated,
        calculate_volume="created_traffic",
        max_users=1,
        user_count_used=1,
        device_capacity_limit=2,
        capacity_used=2,
    )
    settings = DeviceLimitSettings(id=1, enabled=True, auto_delete_enabled=True)
    stage = DeviceLimitPenaltyStage(
        violation_count=1,
        action=PenaltyAction.delete.value,
        enabled=True,
    )
    user = User(
        username="device-auto-delete",
        admin=admin,
        data_limit=allocated,
        used_traffic=used,
        concurrent_user_limit=2,
        status=UserStatus.active,
    )
    state = DeviceLimitUserState(user=user)
    session.add_all((policy_settings, settings, stage, user, state))
    session.commit()
    user_id = user.id

    monkeypatch.setattr(operations, "remove_user", lambda _user: None)
    tracker = DeviceLimitEngine()
    tracker._apply_penalty(
        session,
        settings,
        user,
        state,
        stage,
        {"8.8.8.8", "1.1.1.1"},
        {"master"},
        {},
        datetime(2026, 8, 22, 12, 0, 0),
        100,
        {"ip_concurrency": True},
    )
    session.commit()

    session.refresh(policy_settings)
    ledger = session.get(MarzhelpDeletedUser, user_id)
    transaction = (
        session.query(MarzhelpAccountingTransaction)
        .filter_by(operation_key=f"delete:{user_id}")
        .one()
    )
    audit = session.query(AdminAuditLog).filter_by(action="device_limit.delete").one()
    assert session.get(User, user_id) is None
    assert ledger.admin_id == admin.id
    assert ledger.allocated_traffic == allocated
    assert ledger.used_traffic_total == used
    assert ledger.refunded_traffic == 0
    assert transaction.admin_id == admin.id
    assert transaction.traffic_delta == 0
    assert transaction.volume_delta == 0
    assert policy_settings.used_traffic == allocated
    assert policy_settings.user_count_used == 0
    assert policy_settings.capacity_used == 0
    assert audit.admin_username == "device-limit-engine"
    assert audit.target_id == str(user_id)
    assert audit.target_name == "device-auto-delete"


def test_xray_26_7_28_access_source_variants_and_slot_identity_parse():
    tracker = DeviceLimitEngine()
    tracker.configure(True, "hybrid")
    cases = XRAY_ACCESS_FIXTURE["cases"]

    recorded = tracker.record_log(
        "\n".join(
            cases[name]
            for name in (
                "direct_public_ipv4",
                "tcp_public_ipv4",
                "direct_public_ipv6",
                "tcp_public_ipv6",
            )
        ),
        "node:7",
    )

    assert XRAY_ACCESS_FIXTURE["xray_version"] == "26.7.28"
    assert recorded == 4
    addresses, sources, per_slot = tracker.live_snapshot(42, 300, 1)
    assert addresses == {
        "8.8.8.8",
        "1.1.1.1",
        "2606:4700:4700::1111",
        "2606:4700:4700::1001",
    }
    assert sources == {"node:7"}
    assert per_slot == {
        1: {"8.8.8.8", "2606:4700:4700::1111"},
        2: {"1.1.1.1", "2606:4700:4700::1001"},
    }


def test_xray_source_parser_never_falls_through_to_destination_address():
    tracker = DeviceLimitEngine()
    tracker.configure(True, "hybrid")

    assert tracker.record_log(
        XRAY_ACCESS_FIXTURE["cases"]["malformed_source"]
    ) == 0
    assert tracker.live_snapshot(42, 300, 1)[0] == set()


def test_xray_parser_diagnostics_are_bounded_and_reasoned(monkeypatch):
    tracker = DeviceLimitEngine()
    tracker.configure(True, "hybrid")
    tracker._limited_user_ids = {42}
    monkeypatch.setattr("app.device_limit.engine.time.time", lambda: 1_777_000_000.0)
    cases = XRAY_ACCESS_FIXTURE["cases"]

    recorded = tracker.record_log(
        "\n".join(
            cases[name]
            for name in (
                "not_accepted",
                "source_unparseable",
                "malformed_source",
                "missing_email",
                "unrelated_email",
                "private_ipv4",
                "user_not_limited",
                "valid_slot",
            )
        )
    )

    diagnostics = tracker.diagnostics()
    assert recorded == 1
    assert diagnostics["received_lines"] == 8
    assert diagnostics["accepted_lines"] == 7
    assert diagnostics["rejected_not_accepted"] == 1
    assert diagnostics["rejected_source_parse"] == 1
    assert diagnostics["rejected_invalid_ip"] == 1
    assert diagnostics["rejected_identity_parse"] == 2
    assert diagnostics["rejected_private_or_loopback"] == 1
    assert diagnostics["rejected_user_not_limited"] == 1
    assert diagnostics["recorded_events"] == 1
    assert diagnostics["dropped_buffer_events"] == 0
    expected_timestamp = datetime.fromtimestamp(1_777_000_000, UTC).replace(tzinfo=None)
    assert diagnostics["last_log_seen_at"] == expected_timestamp
    assert diagnostics["last_valid_match_at"] == expected_timestamp

    for _ in range(HIT_BUFFER_CAPACITY + 2):
        tracker.record_log(cases["valid_slot"])
    diagnostics = tracker.diagnostics()
    assert diagnostics["recorded_events"] == HIT_BUFFER_CAPACITY + 3
    assert diagnostics["dropped_buffer_events"] == 3
    assert diagnostics["hit_buffer_capacity"] == HIT_BUFFER_CAPACITY


def test_device_limit_diagnostics_endpoint_is_sudo_protected():
    dependency = inspect.signature(get_diagnostics).parameters["_"].default.dependency
    assert dependency.__func__ is AdminSchema.check_sudo_admin.__func__


@pytest.mark.parametrize(
    ("endpoint", "parameter"),
    [
        (get_settings, "_"),
        (get_diagnostics, "_"),
        (update_settings, "admin"),
        (get_penalty_stages, "_"),
        (update_penalty_stages, "admin"),
        (list_incidents, "admin"),
        (user_summary, "admin"),
        (modify_slot, "admin"),
        (reset_strikes, "admin"),
        (delete_warning, "admin"),
        (unblock_user, "admin"),
    ],
)
def test_every_device_limit_api_is_owner_protected(endpoint, parameter):
    dependency = inspect.signature(endpoint).parameters[parameter].default.dependency
    assert dependency.__func__ is AdminSchema.check_sudo_admin.__func__


def test_same_ip_is_safe_but_two_fresh_ips_trigger_after_grace(
    session,
    monkeypatch,
):
    user = User(
        username="grace-user",
        status=UserStatus.active,
        concurrent_user_limit=1,
    )
    settings = DeviceLimitSettings(
        id=1,
        enabled=True,
        ip_detection_enabled=True,
        check_interval_seconds=10,
        active_window_seconds=300,
        min_successful_connections=2,
        handoff_grace_seconds=30,
    )
    session.add_all((user, settings))
    session.commit()

    clock = {"seconds": 1_000.0}
    base = datetime(2026, 8, 21, 12, 0, 0)
    monkeypatch.setattr("app.device_limit.engine.time.time", lambda: clock["seconds"])
    monkeypatch.setattr(
        "app.device_limit.engine.time.monotonic", lambda: clock["seconds"]
    )
    monkeypatch.setattr(
        "app.device_limit.engine.utc_now",
        lambda: base + timedelta(seconds=clock["seconds"] - 1_000),
    )

    @contextmanager
    def current_db():
        yield session

    monkeypatch.setattr("app.device_limit.engine.GetDB", current_db)
    tracker = DeviceLimitEngine()
    tracker.configure(True, "hybrid")

    def hits(address):
        return "\n".join(
            f"from tcp:{address}:{51_000 + index} accepted tcp:x:443 "
            f"email: {user.id}.grace-user"
            for index in range(2)
        )

    tracker.record_log(hits("8.8.8.8"))
    tracker.evaluate()
    assert session.get(DeviceLimitUserState, user.id) is None
    assert session.query(DeviceLimitIncident).count() == 0

    clock["seconds"] += 11
    tracker.record_log(hits("8.8.8.8") + "\n" + hits("1.1.1.1"))
    tracker.evaluate()
    session.expire_all()
    state = session.get(DeviceLimitUserState, user.id)
    assert state.penalty_status == "pending_handoff"
    assert state.violation_count == 0
    assert session.query(DeviceLimitIncident).count() == 0

    clock["seconds"] += 31
    tracker.record_log(hits("8.8.8.8") + "\n" + hits("1.1.1.1"))
    tracker.evaluate()
    session.expire_all()
    state = session.get(DeviceLimitUserState, user.id)
    incident = session.query(DeviceLimitIncident).one()
    assert state.penalty_status == "warning"
    assert state.violation_count == 1
    assert incident.action == "warn"
    assert incident.observed_count == 2


def test_user_agent_parser_and_patch_update_identity():
    android = parse_user_agent("v2rayNG/1.10.32")
    android_patch = parse_user_agent("v2rayNG/1.10.33")
    apple = parse_user_agent("Streisand/48 CFNetwork/3860.700.1 Darwin/25.6.0")

    assert (android.client_name, android.client_version, android.platform) == (
        "v2rayNG",
        "1.10.32",
        "Android",
    )
    assert android.normalized_identity == android_patch.normalized_identity
    assert apple.client_name == "Streisand"
    assert apple.network_stack == "CFNetwork/3860.700.1"
    assert apple.os_token == "Darwin/25.6.0"
    assert apple.platform == "Apple"


def test_slot_client_observations_are_aggregated(session):
    user = User(
        username="observed-client",
        status=UserStatus.active,
        concurrent_user_limit=1,
        proxies=[Proxy(type="vless", settings={"id": str(uuid4()), "flow": ""})],
    )
    session.add(user)
    session.flush()
    sync_device_slots(session, user)
    user._device_slot_index = 1

    first = observe_subscription_client(session, user, "v2rayNG/1.10.32")
    second = observe_subscription_client(session, user, "v2rayNG/1.10.33")
    session.commit()

    assert first.id == second.id
    assert second.seen_count == 2
    assert second.client_version == "1.10.33"


def test_delegated_admin_cannot_delete_another_admin_warning(session):
    owner = Admin(username="warning-owner", hashed_password="x")
    attacker = Admin(username="warning-attacker", hashed_password="x")
    user = User(username="warning-user", status=UserStatus.active, admin=owner)
    session.add_all((owner, attacker, user))
    session.flush()
    incident = DeviceLimitIncident(
        user_id=user.id,
        admin_id=owner.id,
        username=user.username,
        stage=1,
        action="warn",
        configured_limit=1,
        observed_count=2,
        event_state="warning",
        reason="test",
    )
    session.add(incident)
    session.commit()

    request = Request({"type": "http", "method": "DELETE", "path": "/", "headers": []})
    with pytest.raises(HTTPException) as exc:
        delete_warning(
            incident.id,
            request,
            db=session,
            admin=AdminSchema.model_validate(attacker),
        )

    assert exc.value.status_code == 403
    assert session.get(DeviceLimitIncident, incident.id) is not None


def test_stale_old_ip_is_handoff_but_two_fresh_ips_are_concurrent(monkeypatch):
    tracker = DeviceLimitEngine()
    tracker.configure(True, "hybrid", True)
    clock = {"now": 1000.0}
    monkeypatch.setattr("app.device_limit.engine.time.time", lambda: clock["now"])

    ip_a = "\n".join(
        f"8.8.8.8:{51000 + index} accepted tcp:x:443 email: 42.demo"
        for index in range(3)
    )
    ip_b = "\n".join(
        f"1.1.1.1:{52000 + index} accepted tcp:x:443 email: 42.demo"
        for index in range(3)
    )
    tracker.record_log(ip_a)
    clock["now"] = 1002.0
    tracker.record_log(ip_b)
    historical, _, _, fresh, _ = tracker._snapshot_user_detailed(42, 300, 1, 3)
    assert historical == {"8.8.8.8", "1.1.1.1"}
    assert fresh == {"1.1.1.1"}

    state = DeviceLimitUserState(user_id=42)
    tracker._begin_pending(state, historical, {"master"}, 70, datetime.utcnow())
    tracker._clear_pending(state)
    assert state.violation_count in (None, 0)
    assert state.penalty_status == "clear"

    clock["now"] = 1003.0
    tracker.record_log(ip_a)
    tracker.record_log(ip_b)
    _, _, _, fresh, _ = tracker._snapshot_user_detailed(42, 300, 1, 3)
    assert fresh == {"8.8.8.8", "1.1.1.1"}


@pytest.mark.parametrize(
    ("slots", "ip", "client"),
    [
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (True, False, True),
        (True, True, True),
    ],
)
def test_independent_capability_combinations_validate(slots, ip, client):
    values = DeviceLimitSettingsUpdate(
        enabled=True,
        device_slots_enabled=slots,
        ip_detection_enabled=ip,
        client_fingerprint_enabled=client,
        check_interval_seconds=60,
        active_window_seconds=300,
        min_successful_connections=3,
        strike_reset_seconds=2592000,
        full_ip_retention_days=7,
        incident_retention_days=90,
        audit_retention_days=180,
    )
    assert (
        values.device_slots_enabled,
        values.ip_detection_enabled,
        values.client_fingerprint_enabled,
    ) == (slots, ip, client)


def test_disabled_client_and_slot_signals_do_not_affect_risk(session):
    user = User(username="risk-signals", status=UserStatus.active)
    session.add(user)
    session.flush()
    observe_subscription_client(session, user, "v2rayNG/1.10.32")
    observe_subscription_client(
        session,
        user,
        "Streisand/48 CFNetwork/3860.700.1 Darwin/25.6.0",
    )
    session.commit()

    tracker = DeviceLimitEngine()
    settings = DeviceLimitSettings(
        id=1,
        device_slots_enabled=False,
        client_fingerprint_enabled=False,
        active_window_seconds=300,
    )
    per_slot = {1: {"8.8.8.8"}, 2: {"8.8.8.8"}}
    risk, signals = tracker._risk_for_user(
        session,
        user.id,
        settings,
        {"8.8.8.8"},
        per_slot,
        datetime.utcnow(),
    )
    assert risk == 0
    assert signals["client_family_count"] == 0
    assert signals["platform_count"] == 0
    assert signals["active_slot_count"] == 0

    settings.client_fingerprint_enabled = True
    risk, signals = tracker._risk_for_user(
        session,
        user.id,
        settings,
        {"8.8.8.8"},
        per_slot,
        datetime.utcnow(),
    )
    assert risk == 25
    assert signals["client_family_count"] == 2
    assert signals["platform_count"] == 2
    assert signals["active_slot_count"] == 0


def test_delegated_admin_xray_sync_reloads_committed_user(session, monkeypatch):
    inbound = {"tag": "delegated", "protocol": "vless", "network": "tcp", "tls": "none"}
    monkeypatch.setattr(operations.xray.config, "inbounds_by_protocol", {ProxyTypes.VLESS: [inbound]})
    monkeypatch.setattr(operations.xray.config, "inbounds_by_tag", {"delegated": inbound})
    admin = Admin(username="delegated-owner", hashed_password="x", is_sudo=False)
    session.add(admin)
    session.flush()
    session.add(MarzhelpAdminSettings(admin_id=admin.id))
    session.commit()
    dbuser = crud.create_user(
        session,
        UserCreate(
            username="delegated-created",
            proxies={"vless": {}},
            inbounds={"vless": ["delegated"]},
            concurrent_user_limit=1,
        ),
        admin=admin,
    )
    user_id = dbuser.id
    Session = session.info["session_factory"]
    session.expunge_all()
    captured = {}

    @contextmanager
    def fresh_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    def capture(loaded):
        captured["admin_id"] = loaded.admin_id
        captured["proxy_types"] = sorted(item.type for item in loaded.proxies)
        captured["slots"] = [slot.slot_index for slot in loaded.device_slots if slot.enabled]
        captured["credentials"] = loaded.device_slots[0].credentials

    monkeypatch.setattr(operations, "GetDB", fresh_db)
    monkeypatch.setattr(operations, "add_user", capture)
    operations.add_user_by_id(user_id)

    assert captured["admin_id"] == admin.id
    assert captured["proxy_types"] == ["vless"]
    assert captured["slots"] == [1]
    assert "vless" in captured["credentials"]


def test_warning_cleanup_does_not_remove_confirmed_punishment(session, monkeypatch):
    now = datetime.utcnow()
    user = User(username="warning-cleanup", status=UserStatus.disabled)
    session.add(user)
    session.flush()
    session.add(DeviceLimitSettings(id=1, enabled=True, warning_auto_delete_seconds=3600))
    session.add(
        DeviceLimitUserState(
            user_id=user.id,
            penalty_status="temporarily_disabled",
            violation_count=1,
        )
    )
    warning = DeviceLimitIncident(
        user_id=user.id,
        username=user.username,
        stage=1,
        action="warn",
        configured_limit=1,
        observed_count=2,
        event_state="warning",
        reason="warning",
        expires_at=now - timedelta(seconds=1),
        created_at=now - timedelta(hours=2),
    )
    punishment = DeviceLimitIncident(
        user_id=user.id,
        username=user.username,
        stage=2,
        action="temporary_disable",
        configured_limit=1,
        observed_count=2,
        event_state="temporarily_disabled",
        reason="punishment",
        created_at=now - timedelta(hours=2),
    )
    session.add_all([warning, punishment])
    session.commit()
    warning_id = warning.id
    punishment_id = punishment.id
    user_id = user.id

    @contextmanager
    def current_db():
        yield session

    monkeypatch.setattr("app.device_limit.engine.GetDB", current_db)
    DeviceLimitEngine().retention_cleanup()
    session.expire_all()

    assert session.get(DeviceLimitIncident, warning_id) is None
    assert session.get(DeviceLimitIncident, punishment_id) is not None
    assert session.get(DeviceLimitUserState, user_id).penalty_status == "temporarily_disabled"


def test_finite_limit_creates_independent_standard_credentials(session):
    base_id = str(uuid4())
    user = User(
        username="slot-user",
        status=UserStatus.active,
        concurrent_user_limit=2,
        proxies=[Proxy(type="vless", settings={"id": base_id, "flow": ""})],
    )
    session.add(user)
    session.flush()

    slots = sync_device_slots(session, user)

    assert [slot.slot_index for slot in slots] == [1, 2]
    assert slots[0].credentials["vless"]["id"] == base_id
    assert slots[1].credentials["vless"]["id"] != base_id
    assert slot_email(user.id, user.username, 1) == f"{user.id}.slot-user"
    assert slot_email(user.id, user.username, 2) == f"{user.id}.slot-user.slot2"


def test_explicit_subscription_mode_permissions_are_enforced(session):
    admin = Admin(username="mode-admin", hashed_password="x", is_sudo=False)
    session.add(admin)
    session.flush()
    settings = MarzhelpAdminSettings(admin_id=admin.id)
    settings.subscription_mode_permissions = [
        MarzhelpAdminSubscriptionModePermission(
            admin_id=admin.id,
            mode=SubscriptionMode.unlimited_traffic_limited_devices.value,
        )
    ]
    session.add(settings)
    session.commit()

    allowed = type("Plan", (), {
        "data_limit": None,
        "concurrent_user_limit": 2,
        "expire": None,
        "on_hold_expire_duration": None,
        "next_plan": None,
        "inbounds": None,
    })()
    assert marzhelp_policy.validate_create(session, admin.id, allowed) is settings
    session.rollback()

    denied = type("Plan", (), {
        "data_limit": None,
        "concurrent_user_limit": None,
        "expire": None,
        "on_hold_expire_duration": None,
        "next_plan": None,
        "inbounds": None,
    })()
    with pytest.raises(marzhelp_policy.MarzhelpPolicyError) as exc:
        marzhelp_policy.validate_create(session, admin.id, denied)
    assert exc.value.code == "subscription_mode_forbidden"


def test_unlimited_devices_are_controlled_by_mode_not_finite_limit_allowlist(session):
    admin = Admin(username="unlimited-device-admin", hashed_password="x", is_sudo=False)
    session.add(admin)
    session.flush()
    settings = MarzhelpAdminSettings(admin_id=admin.id, all_user_limits=False)
    settings.user_limit_permissions = [
        MarzhelpAdminUserLimitPermission(admin_id=admin.id, concurrent_user_limit=2)
    ]
    settings.subscription_mode_permissions = [
        MarzhelpAdminSubscriptionModePermission(
            admin_id=admin.id,
            mode=SubscriptionMode.limited_traffic_unlimited_devices.value,
        )
    ]
    session.add(settings)
    session.commit()

    plan = type("Plan", (), {
        "data_limit": 10 * 1024**3,
        "concurrent_user_limit": None,
        "expire": None,
        "on_hold_expire_duration": None,
        "next_plan": None,
        "inbounds": None,
    })()
    assert marzhelp_policy.validate_create(session, admin.id, plan) is settings
