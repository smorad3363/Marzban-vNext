from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from sqlalchemy import update

from app import logger, xray
from app.db import GetDB
from app.db.models import (
    AdminAuditLog,
    DeviceClientObservation,
    DeviceLimitIncident,
    DeviceLimitPenaltyStage,
    DeviceLimitSettings,
    DeviceLimitUserState,
    DeviceSlot,
    User,
)
from app.device_limit.constants import DeviceEventState, PenaltyAction, PenaltyStatus
from app.models.user import UserStatus
from app.utils import marzhelp_policy
from app.utils.audit import AuditLogService


SOURCE_RE = re.compile(
    r"^\s*(?:(?:\S+\s+){2})?(?:from\s+)?(?:(?:tcp|udp):)?"
    r"(?P<address>\[[^\]]+\]|[^\s:]+):\d+\s+accepted\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(
    r"email:\s*(\d+)\.([A-Za-z0-9_@+%\-.]+?)(?:\.slot(\d+))?(?:\s|$)"
)
MAX_IPS_PER_SLOT = 64
HIT_BUFFER_CAPACITY = 128
DIAGNOSTIC_COUNTERS = (
    "received_lines",
    "accepted_lines",
    "rejected_runtime_disabled",
    "rejected_not_accepted",
    "rejected_source_parse",
    "rejected_identity_parse",
    "rejected_invalid_ip",
    "rejected_private_or_loopback",
    "rejected_user_not_limited",
    "recorded_events",
    "dropped_buffer_events",
)


def utc_now() -> datetime:
    return datetime.utcnow()


def mask_ip(value: str) -> str:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return "***"
    if parsed.version == 4:
        parts = value.split(".")
        return f"{parts[0]}.{parts[1]}.***.***"
    groups = parsed.exploded.split(":")
    return ":".join(groups[:3] + ["****"] * 5)


class DeviceLimitEngine:
    """Bounded, in-memory Xray activity tracker with durable incidents only."""

    def __init__(self):
        self._lock = threading.RLock()
        self._activity: dict[int, dict[int, dict[str, deque[float]]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self._sources: dict[int, set[str]] = defaultdict(set)
        self._stop = threading.Event()
        self._collector_threads: dict[str, threading.Thread] = {}
        self._manager_thread: threading.Thread | None = None
        self._last_evaluation = 0.0
        self._event_logger: logging.Logger | None = None
        self._runtime_enabled = False
        self._ip_detection_enabled = True
        self._limited_user_ids: set[int] | None = None
        self._last_user_cache_refresh = 0.0
        self._diagnostic_counts = Counter(
            {counter: 0 for counter in DIAGNOSTIC_COUNTERS}
        )
        self._last_log_seen_at: float | None = None
        self._last_valid_match_at: float | None = None

    def start(self) -> None:
        if self._manager_thread and self._manager_thread.is_alive():
            return
        self._stop.clear()
        self._configure_event_logger()
        try:
            with GetDB() as db:
                settings = db.get(DeviceLimitSettings, 1)
                if settings is not None:
                    self.configure(
                        settings.enabled,
                        settings.enforcement_mode,
                        settings.ip_detection_enabled,
                    )
                    if settings.enabled:
                        self._refresh_limited_users(db, force=True)
        except Exception as exc:
            logger.warning("Unable to load device-limit settings at startup: %s", exc)
        self._manager_thread = threading.Thread(
            target=self._manage_collectors,
            name="device-limit-log-manager",
            daemon=True,
        )
        self._manager_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _configure_event_logger(self) -> None:
        event_logger = logging.getLogger("marzban.device_limit.events")
        if event_logger.handlers:
            self._event_logger = event_logger
            return
        try:
            from config import DEVICE_LIMIT_LOG_DIR

            path = Path(DEVICE_LIMIT_LOG_DIR)
            path.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                path / "events.jsonl",
                maxBytes=25 * 1024 * 1024,
                backupCount=10,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            event_logger.addHandler(handler)
            event_logger.setLevel(logging.INFO)
            event_logger.propagate = False
            self._event_logger = event_logger
        except OSError as exc:
            logger.warning("Unable to initialize device-limit event file: %s", exc)

    def _manage_collectors(self) -> None:
        while not self._stop.wait(1):
            if getattr(xray.core, "started", False):
                self._ensure_collector("main", xray.core, "master")
            for node_id, node in list(xray.nodes.items()):
                try:
                    ready = node.connected and node.started
                except Exception:
                    ready = False
                if ready:
                    self._ensure_collector(f"node:{node_id}", node, f"node:{node_id}")

    def _ensure_collector(self, key: str, source, source_name: str) -> None:
        current = self._collector_threads.get(key)
        if current and current.is_alive():
            return
        thread = threading.Thread(
            target=self._collect,
            args=(source, source_name),
            name=f"device-limit-{key}",
            daemon=True,
        )
        self._collector_threads[key] = thread
        thread.start()

    def _collect(self, source, source_name: str) -> None:
        generation = (getattr(source, "process", None), getattr(source, "_session_id", None))
        try:
            with source.get_logs() as logs:
                while not self._stop.wait(0.2):
                    if source_name.startswith("node:") and xray.nodes.get(int(source_name.split(":", 1)[1])) is not source:
                        break
                    if generation != (getattr(source, "process", None), getattr(source, "_session_id", None)):
                        break
                    try:
                        line = logs.popleft()
                    except IndexError:
                        continue
                    self.record_log(line, source_name)
        except Exception as exc:
            logger.debug("Device-limit collector %s stopped: %s", source_name, exc)

    def record_log(self, raw: str, source_name: str = "master") -> int:
        lines = str(raw).splitlines()
        if not lines:
            return 0
        now = time.time()
        counts = Counter(received_lines=len(lines))
        with self._lock:
            runtime_enabled = self._runtime_enabled and self._ip_detection_enabled
            limited_user_ids = (
                None
                if self._limited_user_ids is None
                else set(self._limited_user_ids)
            )
        if not runtime_enabled:
            counts["rejected_runtime_disabled"] = len(lines)
            with self._lock:
                self._diagnostic_counts.update(counts)
                self._last_log_seen_at = now
            return 0

        parsed_events: list[tuple[int, int, str]] = []
        for line in lines:
            if "accepted" not in line.lower() or "BLOCK]" in line:
                counts["rejected_not_accepted"] += 1
                continue
            counts["accepted_lines"] += 1
            source_match = SOURCE_RE.search(line)
            if source_match is None:
                counts["rejected_source_parse"] += 1
                continue
            address = source_match.group("address").strip("[]")
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                counts["rejected_invalid_ip"] += 1
                continue
            if parsed.is_private or parsed.is_loopback or parsed.is_unspecified:
                counts["rejected_private_or_loopback"] += 1
                continue
            email_match = EMAIL_RE.search(line)
            if email_match is None:
                counts["rejected_identity_parse"] += 1
                continue
            user_id = int(email_match.group(1))
            if (
                limited_user_ids is not None
                and user_id not in limited_user_ids
            ):
                counts["rejected_user_not_limited"] += 1
                continue
            slot_index = int(email_match.group(3) or 1)
            parsed_events.append((user_id, slot_index, str(parsed)))

        with self._lock:
            for user_id, slot_index, address in parsed_events:
                slot = self._activity[user_id][slot_index]
                if address not in slot and len(slot) >= MAX_IPS_PER_SLOT:
                    oldest = min(slot, key=lambda key: slot[key][-1])
                    del slot[oldest]
                hits = slot.setdefault(
                    address,
                    deque(maxlen=HIT_BUFFER_CAPACITY),
                )
                if len(hits) == HIT_BUFFER_CAPACITY:
                    counts["dropped_buffer_events"] += 1
                hits.append(now)
                self._sources[user_id].add(source_name)
            counts["recorded_events"] = len(parsed_events)
            self._diagnostic_counts.update(counts)
            self._last_log_seen_at = now
            if parsed_events:
                self._last_valid_match_at = now
        return len(parsed_events)

    def diagnostics(self) -> dict:
        """Return bounded, process-local parser/collector health without raw IPs."""

        with self._lock:
            result = {
                counter: int(self._diagnostic_counts[counter])
                for counter in DIAGNOSTIC_COUNTERS
            }
            result.update(
                {
                    "runtime_enabled": self._runtime_enabled,
                    "ip_detection_enabled": self._ip_detection_enabled,
                    "active_collectors": sorted(
                        key
                        for key, thread in self._collector_threads.items()
                        if thread.is_alive()
                    ),
                    "hit_buffer_capacity": HIT_BUFFER_CAPACITY,
                    "last_log_seen_at": (
                        datetime.fromtimestamp(self._last_log_seen_at, UTC).replace(
                            tzinfo=None
                        )
                        if self._last_log_seen_at is not None
                        else None
                    ),
                    "last_valid_match_at": (
                        datetime.fromtimestamp(self._last_valid_match_at, UTC).replace(
                            tzinfo=None
                        )
                        if self._last_valid_match_at is not None
                        else None
                    ),
                }
            )
            return result

    def _snapshot_user(
        self,
        user_id: int,
        window_seconds: int,
        hit_threshold: int,
    ) -> tuple[set[str], set[str], dict[int, set[str]]]:
        cutoff = time.time() - window_seconds
        per_slot: dict[int, set[str]] = {}
        with self._lock:
            slots = self._activity.get(user_id, {})
            for slot_index, addresses in list(slots.items()):
                qualified: set[str] = set()
                for address, hits in list(addresses.items()):
                    while hits and hits[0] < cutoff:
                        hits.popleft()
                    if not hits:
                        del addresses[address]
                    elif len(hits) >= hit_threshold:
                        qualified.add(address)
                if not addresses:
                    slots.pop(slot_index, None)
                if qualified:
                    per_slot[slot_index] = qualified
            if not slots:
                self._activity.pop(user_id, None)
                self._sources.pop(user_id, None)
            sources = set(self._sources.get(user_id, set()))
        all_addresses = set().union(*per_slot.values()) if per_slot else set()
        return all_addresses, sources, per_slot

    def live_snapshot(
        self,
        user_id: int,
        window_seconds: int,
        hit_threshold: int,
    ) -> tuple[set[str], set[str], dict[int, set[str]]]:
        return self._snapshot_user(user_id, window_seconds, hit_threshold)

    def _snapshot_user_detailed(
        self,
        user_id: int,
        window_seconds: int,
        fresh_seconds: int,
        hit_threshold: int,
    ) -> tuple[
        set[str],
        set[str],
        dict[int, set[str]],
        set[str],
        dict[int, set[str]],
    ]:
        now = time.time()
        cutoff = now - window_seconds
        fresh_cutoff = now - fresh_seconds
        historical_per_slot: dict[int, set[str]] = {}
        fresh_per_slot: dict[int, set[str]] = {}
        with self._lock:
            slots = self._activity.get(user_id, {})
            for slot_index, addresses in list(slots.items()):
                historical: set[str] = set()
                fresh: set[str] = set()
                for address, hits in list(addresses.items()):
                    while hits and hits[0] < cutoff:
                        hits.popleft()
                    if not hits:
                        del addresses[address]
                        continue
                    if len(hits) >= hit_threshold:
                        historical.add(address)
                        if hits[-1] >= fresh_cutoff:
                            fresh.add(address)
                if not addresses:
                    slots.pop(slot_index, None)
                if historical:
                    historical_per_slot[slot_index] = historical
                if fresh:
                    fresh_per_slot[slot_index] = fresh
            if not slots:
                self._activity.pop(user_id, None)
                self._sources.pop(user_id, None)
            sources = set(self._sources.get(user_id, set()))
        historical_all = (
            set().union(*historical_per_slot.values()) if historical_per_slot else set()
        )
        fresh_all = set().union(*fresh_per_slot.values()) if fresh_per_slot else set()
        return historical_all, sources, historical_per_slot, fresh_all, fresh_per_slot

    @staticmethod
    def _fresh_window(settings: DeviceLimitSettings) -> int:
        if settings.handoff_grace_seconds <= 0:
            return max(5, min(int(settings.check_interval_seconds), 60))
        return max(
            5,
            min(
                int(settings.check_interval_seconds),
                max(int(settings.handoff_grace_seconds) // 3, 5),
            ),
        )

    def _risk_for_user(
        self,
        db,
        user_id: int,
        settings: DeviceLimitSettings,
        fresh_addresses: set[str],
        fresh_per_slot: dict[int, set[str]],
        now: datetime,
    ) -> tuple[int, dict]:
        signals = {
            "ip_concurrency": len(fresh_addresses) > 1,
            "fresh_ip_count": len(fresh_addresses),
            "active_slot_count": (
                len(fresh_per_slot) if settings.device_slots_enabled else 0
            ),
            "client_family_count": 0,
            "platform_count": 0,
        }
        risk = 70 if signals["ip_concurrency"] else 0
        if settings.client_fingerprint_enabled:
            observations = (
                db.query(DeviceClientObservation)
                .filter(
                    DeviceClientObservation.user_id == user_id,
                    DeviceClientObservation.last_seen_at
                    >= now - timedelta(seconds=settings.active_window_seconds),
                )
                .all()
            )
            families = {item.client_name.lower() for item in observations if item.client_name}
            platforms = {item.platform.lower() for item in observations if item.platform}
            signals["client_family_count"] = len(families)
            signals["platform_count"] = len(platforms)
            if len(families) > 1:
                risk += 15
            if len(platforms) > 1:
                risk += 10
        if settings.device_slots_enabled and len(fresh_per_slot) > 1:
            risk += 5
        return min(risk, 100), signals

    def evaluate(self) -> None:
        with GetDB() as db:
            settings = db.get(DeviceLimitSettings, 1)
            self.configure(
                bool(settings and settings.enabled),
                settings.enforcement_mode if settings else "hybrid",
                settings.ip_detection_enabled if settings else True,
            )
            if settings is None or not settings.enabled:
                return
            self._refresh_limited_users(db)
            if not settings.ip_detection_enabled:
                self._release_due_penalties(db, settings, force=True)
                return
            now_monotonic = time.monotonic()
            if now_monotonic - self._last_evaluation < settings.check_interval_seconds:
                return
            self._last_evaluation = now_monotonic
            with self._lock:
                active_ids = list(self._activity)
            if not active_ids:
                self._release_due_penalties(db, settings)
                return

            stages = (
                db.query(DeviceLimitPenaltyStage)
                .filter(DeviceLimitPenaltyStage.enabled.is_(True))
                .order_by(DeviceLimitPenaltyStage.violation_count.asc())
                .all()
            )
            now = utc_now()
            for chunk_start in range(0, len(active_ids), 500):
                users = (
                    db.query(User)
                    .filter(
                        User.id.in_(active_ids[chunk_start:chunk_start + 500]),
                        User.concurrent_user_limit.is_not(None),
                        User.status.in_((UserStatus.active, UserStatus.on_hold)),
                    )
                    .all()
                )
                for user in users:
                    (
                        addresses,
                        sources,
                        per_slot,
                        fresh_addresses,
                        fresh_per_slot,
                    ) = self._snapshot_user_detailed(
                        user.id,
                        settings.active_window_seconds,
                        self._fresh_window(settings),
                        settings.min_successful_connections,
                    )
                    limit = int(user.concurrent_user_limit or 0)
                    if limit < 1 or len(addresses) <= limit:
                        state = db.get(DeviceLimitUserState, user.id)
                        if state and state.pending_handoff_started_at:
                            self._clear_pending(state)
                        continue
                    state = db.get(DeviceLimitUserState, user.id)
                    if state is None:
                        state = DeviceLimitUserState(user_id=user.id)
                        db.add(state)
                    risk_score, signals = self._risk_for_user(
                        db,
                        user.id,
                        settings,
                        fresh_addresses,
                        fresh_per_slot,
                        now,
                    )
                    if len(fresh_addresses) <= limit:
                        if state.pending_handoff_started_at:
                            logger.info(
                                "device_handoff_completed user_id=%s historical_ips=%s fresh_ips=%s",
                                user.id,
                                len(addresses),
                                len(fresh_addresses),
                            )
                            self._clear_pending(state)
                        continue
                    if state.pending_handoff_started_at is None:
                        if settings.handoff_grace_seconds > 0:
                            self._begin_pending(
                                state,
                                fresh_addresses,
                                sources,
                                risk_score,
                                now,
                            )
                            logger.info(
                                "device_handoff_pending user_id=%s grace_seconds=%s risk=%s",
                                user.id,
                                settings.handoff_grace_seconds,
                                risk_score,
                            )
                            continue
                    elif (
                        state.pending_handoff_started_at
                        + timedelta(seconds=settings.handoff_grace_seconds)
                        > now
                    ):
                        state.pending_ip_addresses = sorted(fresh_addresses)
                        state.pending_source_nodes = sorted(sources)
                        state.pending_risk_score = risk_score
                        state.pending_last_fresh_at = now
                        continue
                    if state and state.last_violation_at:
                        cooldown = timedelta(seconds=settings.active_window_seconds)
                        if state.last_violation_at + cooldown > now:
                            self._clear_pending(state)
                            continue
                        if state.last_violation_at + timedelta(
                            seconds=settings.strike_reset_seconds
                        ) <= now:
                            state.violation_count = 0
                    self._clear_pending(state)
                    state.violation_count = int(state.violation_count or 0) + 1
                    stage = self._stage_for(stages, state.violation_count)
                    self._apply_penalty(
                        db,
                        settings,
                        user,
                        state,
                        stage,
                        fresh_addresses,
                        sources,
                        fresh_per_slot,
                        now,
                        risk_score,
                        signals,
                    )
            db.commit()
            self._release_due_penalties(db, settings)

    @staticmethod
    def _begin_pending(
        state: DeviceLimitUserState,
        addresses: set[str],
        sources: set[str],
        risk_score: int,
        now: datetime,
    ) -> None:
        state.penalty_status = PenaltyStatus.pending_handoff.value
        state.pending_handoff_started_at = now
        state.pending_ip_addresses = sorted(addresses)
        state.pending_source_nodes = sorted(sources)
        state.pending_risk_score = risk_score
        state.pending_last_fresh_at = now
        state.active_ip_count = len(addresses)
        state.last_seen_at = now

    @staticmethod
    def _clear_pending(state: DeviceLimitUserState) -> None:
        state.pending_handoff_started_at = None
        state.pending_ip_addresses = None
        state.pending_source_nodes = None
        state.pending_risk_score = None
        state.pending_last_fresh_at = None
        if state.penalty_status == PenaltyStatus.pending_handoff.value:
            state.penalty_status = PenaltyStatus.clear.value

    def _refresh_limited_users(self, db, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_user_cache_refresh < 60:
            return
        limited_ids = {
            row[0]
            for row in db.query(User.id).filter(
                User.concurrent_user_limit.is_not(None),
                User.status.in_((UserStatus.active, UserStatus.on_hold)),
            )
        }
        with self._lock:
            self._limited_user_ids = limited_ids
            for user_id in set(self._activity) - limited_ids:
                self._activity.pop(user_id, None)
                self._sources.pop(user_id, None)
        self._last_user_cache_refresh = now

    @staticmethod
    def _stage_for(stages: Iterable[DeviceLimitPenaltyStage], count: int):
        selected = None
        for stage in stages:
            if stage.violation_count <= count:
                selected = stage
            else:
                break
        return selected

    def _apply_penalty(
        self,
        db,
        settings: DeviceLimitSettings,
        user: User,
        state: DeviceLimitUserState,
        stage: DeviceLimitPenaltyStage | None,
        addresses: set[str],
        sources: set[str],
        per_slot: dict[int, set[str]],
        now: datetime,
        risk_score: int,
        signals: dict,
    ) -> None:
        action = PenaltyAction(stage.action) if stage else PenaltyAction.warn
        # User-Agent is diagnostic/non-cryptographic. A destructive action is
        # impossible unless fresh Xray IP concurrency independently confirms it.
        if not signals.get("ip_concurrency") and action != PenaltyAction.warn:
            action = PenaltyAction.warn
        if action == PenaltyAction.delete and not settings.auto_delete_enabled:
            action = PenaltyAction.permanent_disable
        reason = (
            f"Confirmed {len(addresses)} fresh public IPs for configured device limit "
            f"{user.concurrent_user_limit}; risk={risk_score}"
        )
        state.current_stage = stage.violation_count if stage else state.violation_count
        state.last_violation_at = now
        state.last_seen_at = now
        state.active_ip_count = len(addresses)
        state.last_reason = reason

        if action == PenaltyAction.warn:
            state.penalty_status = PenaltyStatus.warning.value
            state.blocked_until = None
        elif action == PenaltyAction.temporary_disable:
            if state.penalty_status != PenaltyStatus.temporarily_disabled.value:
                state.status_before_penalty = getattr(user.status, "value", user.status)
            state.penalty_status = PenaltyStatus.temporarily_disabled.value
            state.blocked_until = now + timedelta(seconds=int(stage.duration_seconds))
            user.status = UserStatus.disabled
            user.last_status_change = now
            xray.operations.remove_user(user)
        elif action == PenaltyAction.permanent_disable:
            if state.penalty_status != PenaltyStatus.permanently_disabled.value:
                state.status_before_penalty = getattr(user.status, "value", user.status)
            state.penalty_status = PenaltyStatus.permanently_disabled.value
            state.blocked_until = None
            user.status = UserStatus.disabled
            user.last_status_change = now
            xray.operations.remove_user(user)
        else:
            state.penalty_status = PenaltyStatus.deleted.value
            state.blocked_until = None
            xray.operations.remove_user(user)

        incident = DeviceLimitIncident(
            user_id=user.id,
            admin_id=user.admin_id,
            username=user.username,
            stage=state.current_stage,
            action=action.value,
            configured_limit=int(user.concurrent_user_limit),
            observed_count=len(addresses),
            ip_addresses=sorted(addresses),
            source_nodes=sorted(sources),
            event_state={
                PenaltyAction.warn: DeviceEventState.warning.value,
                PenaltyAction.temporary_disable: DeviceEventState.temporarily_disabled.value,
                PenaltyAction.permanent_disable: DeviceEventState.permanently_disabled.value,
                PenaltyAction.delete: DeviceEventState.permanently_disabled.value,
            }[action],
            risk_score=risk_score,
            signal_summary=signals,
            reason=reason,
            expires_at=(
                now + timedelta(seconds=settings.warning_auto_delete_seconds)
                if action == PenaltyAction.warn and settings.warning_auto_delete_seconds > 0
                else None
            ),
            created_at=now,
        )
        db.add(incident)
        for slot in user.device_slots:
            slot_addresses = per_slot.get(slot.slot_index)
            if slot_addresses:
                slot.last_seen_at = now
                slot.last_ip = sorted(slot_addresses)[-1]
        AuditLogService.log(
            db,
            "device-limit-engine",
            f"device_limit.{action.value}",
            "user",
            reason,
            target_id=user.id,
            target_name=user.username,
            details={
                "stage": state.current_stage,
                "configured_limit": user.concurrent_user_limit,
                "observed_count": len(addresses),
                "risk_score": risk_score,
                "signals": signals,
            },
            commit=False,
        )
        self._write_event(incident)
        if action == PenaltyAction.delete:
            marzhelp_policy.capture_delete(db, user)
            incident.user_id = None
            db.delete(user)

    def _release_due_penalties(
        self,
        db,
        settings: DeviceLimitSettings,
        force: bool = False,
    ) -> None:
        now = utc_now()
        query = db.query(DeviceLimitUserState).filter(
            DeviceLimitUserState.penalty_status
            == PenaltyStatus.temporarily_disabled.value,
            DeviceLimitUserState.blocked_until.is_not(None),
        )
        if not force:
            query = query.filter(DeviceLimitUserState.blocked_until <= now)
        states = query.all()
        changed = False
        for state in states:
            user = state.user
            if user is None:
                continue
            manually_changed = (
                user.last_status_change
                and state.updated_at
                and user.last_status_change > state.updated_at + timedelta(seconds=1)
            )
            if not manually_changed and user.status == UserStatus.disabled:
                previous = state.status_before_penalty or UserStatus.active.value
                if previous in (UserStatus.active.value, UserStatus.on_hold.value):
                    user.status = UserStatus(previous)
                    user.last_status_change = now
                    xray.operations.add_user(user)
            state.penalty_status = PenaltyStatus.clear.value
            state.blocked_until = None
            db.query(DeviceLimitIncident).filter(
                DeviceLimitIncident.user_id == user.id,
                DeviceLimitIncident.resolved_at.is_(None),
            ).update(
                {
                    DeviceLimitIncident.resolved_at: now,
                    DeviceLimitIncident.event_state: DeviceEventState.resolved.value,
                },
                synchronize_session=False,
            )
            changed = True
        if changed:
            db.commit()

    def release_all_temporary_penalties(self) -> None:
        with GetDB() as db:
            settings = db.get(DeviceLimitSettings, 1)
            if settings is not None:
                self._release_due_penalties(db, settings, force=True)

    def retention_cleanup(self) -> None:
        with GetDB() as db:
            settings = db.get(DeviceLimitSettings, 1)
            if settings is None:
                return
            now = utc_now()
            if settings.warning_auto_delete_seconds > 0:
                expired_warning_user_ids = [
                    row[0]
                    for row in db.query(DeviceLimitIncident.user_id)
                    .filter(
                        DeviceLimitIncident.event_state == DeviceEventState.warning.value,
                        DeviceLimitIncident.resolved_at.is_(None),
                        DeviceLimitIncident.expires_at.is_not(None),
                        DeviceLimitIncident.expires_at <= now,
                    )
                    .distinct()
                ]
                db.query(DeviceLimitIncident).filter(
                    DeviceLimitIncident.event_state == DeviceEventState.warning.value,
                    DeviceLimitIncident.resolved_at.is_(None),
                    DeviceLimitIncident.expires_at.is_not(None),
                    DeviceLimitIncident.expires_at <= now,
                ).delete(synchronize_session=False)
                if expired_warning_user_ids:
                    db.query(DeviceLimitUserState).filter(
                        DeviceLimitUserState.user_id.in_(expired_warning_user_ids),
                        DeviceLimitUserState.penalty_status == PenaltyStatus.warning.value,
                    ).update(
                        {
                            DeviceLimitUserState.penalty_status: PenaltyStatus.clear.value,
                            DeviceLimitUserState.last_reason: None,
                        },
                        synchronize_session=False,
                    )
                    logger.info(
                        "device_warning_expired users=%s",
                        len(expired_warning_user_ids),
                    )
            db.execute(
                update(DeviceLimitIncident)
                .where(
                    DeviceLimitIncident.created_at
                    < now - timedelta(days=settings.full_ip_retention_days)
                )
                .values(ip_addresses=None)
            )
            db.query(DeviceLimitIncident).filter(
                DeviceLimitIncident.created_at
                < now - timedelta(days=settings.incident_retention_days)
            ).delete(synchronize_session=False)
            db.query(AdminAuditLog).filter(
                AdminAuditLog.created_at
                < now - timedelta(days=settings.audit_retention_days)
            ).delete(synchronize_session=False)
            db.commit()

    def clear_user_activity(self, user_id: int) -> None:
        with self._lock:
            self._activity.pop(user_id, None)
            self._sources.pop(user_id, None)

    def configure(
        self,
        enabled: bool,
        enforcement_mode: str = "hybrid",
        ip_detection_enabled: bool | None = None,
    ) -> None:
        self._runtime_enabled = enabled
        self._ip_detection_enabled = (
            enforcement_mode != "slots"
            if ip_detection_enabled is None
            else bool(ip_detection_enabled)
        )
        if not enabled or not self._ip_detection_enabled:
            with self._lock:
                self._activity.clear()
                self._sources.clear()
        if not enabled:
            self._limited_user_ids = set()

    def _write_event(self, incident: DeviceLimitIncident) -> None:
        if self._event_logger is None:
            return
        self._event_logger.info(
            json.dumps(
                {
                    "created_at": incident.created_at.isoformat(),
                    "event": f"device_limit.{incident.action}",
                    "user_id": incident.user_id,
                    "admin_id": incident.admin_id,
                    "username": incident.username,
                    "stage": incident.stage,
                    "configured_limit": incident.configured_limit,
                    "observed_count": incident.observed_count,
                    "event_state": incident.event_state,
                    "risk_score": incident.risk_score,
                    # Durable full addresses live only in the retention-managed DB.
                    "ip_addresses": [mask_ip(value) for value in (incident.ip_addresses or [])],
                    "source_nodes": incident.source_nodes,
                    "reason": incident.reason,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )


engine = DeviceLimitEngine()
