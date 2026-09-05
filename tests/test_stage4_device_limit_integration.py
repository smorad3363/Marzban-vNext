from collections import deque
from contextlib import contextmanager
import threading
import time
from unittest.mock import patch
from app import xray

from app.device_limit.engine import DeviceLimitEngine


class LogSource:
    def __init__(self, *payloads: str):
        self.payloads = deque(payloads)

    @contextmanager
    def get_logs(self):
        yield self.payloads


def collect_payloads(tracker: DeviceLimitEngine, source: LogSource, name: str) -> None:
    registered = {int(name.split(":", 1)[1]): source} if name.startswith("node:") else {}
    with patch.dict(xray.nodes, registered):
        worker = threading.Thread(target=tracker._collect, args=(source, name), daemon=True)
        worker.start()
        deadline = time.monotonic() + 2
        while source.payloads and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        tracker.stop()
        worker.join(timeout=1)
    assert not source.payloads


def accepted_lines(*addresses: str) -> str:
    return "\n".join(
        f"from tcp:{address}:{51000 + index} accepted tcp:example.com:443 "
        "[vless >> direct] email: 42.stage4-user"
        for index, address in enumerate(addresses)
    )


def configured_tracker() -> DeviceLimitEngine:
    tracker = DeviceLimitEngine()
    tracker.configure(True, "ip", True)
    tracker._limited_user_ids = {42}
    return tracker


def test_master_log_collector_keeps_one_public_ip_safe():
    tracker = configured_tracker()
    source = LogSource(accepted_lines("8.8.8.8", "8.8.8.8", "8.8.8.8"))

    collect_payloads(tracker, source, "master")

    addresses, sources, per_slot = tracker.live_snapshot(42, 300, 3)
    assert addresses == {"8.8.8.8"}
    assert sources == {"master"}
    assert per_slot == {1: {"8.8.8.8"}}


def test_rest_node_batched_log_payload_distinguishes_two_public_ips():
    tracker = configured_tracker()
    source = LogSource(
        accepted_lines(
            "8.8.8.8",
            "8.8.8.8",
            "8.8.8.8",
            "1.1.1.1",
            "1.1.1.1",
            "1.1.1.1",
        )
    )

    collect_payloads(tracker, source, "node:7")

    addresses, sources, per_slot = tracker.live_snapshot(42, 300, 3)
    assert addresses == {"8.8.8.8", "1.1.1.1"}
    assert sources == {"node:7"}
    assert per_slot == {1: {"8.8.8.8", "1.1.1.1"}}
    diagnostics = tracker.diagnostics()
    assert diagnostics["received_lines"] == 6
    assert diagnostics["recorded_events"] == 6


def test_nat_source_is_fail_closed_and_x_forwarded_for_is_not_trusted():
    tracker = configured_tracker()
    source = LogSource(
        "from tcp:192.168.13.1:51000 accepted tcp:8.8.8.8:443 "
        "[vless >> direct] email: 42.stage4-user X-Forwarded-For: 1.1.1.1"
    )

    collect_payloads(tracker, source, "node:7")

    assert tracker.live_snapshot(42, 300, 1)[0] == set()
    diagnostics = tracker.diagnostics()
    assert diagnostics["rejected_private_or_loopback"] == 1
    assert diagnostics["recorded_events"] == 0
