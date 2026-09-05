from unittest.mock import Mock
import pytest

from app.xray.node import ReSTXRayNode


def test_remote_started_probe_synchronizes_local_api_state(monkeypatch):
    node = ReSTXRayNode.__new__(ReSTXRayNode)
    node.address = "node.test"
    node.api_port = 62051
    node._node_cert = "certificate"
    node._session_id = "session"
    node._started = False
    node._api = None
    node.make_request = Mock(return_value={"started": True})
    api = Mock()
    monkeypatch.setattr("app.xray.node.XRayAPI", Mock(return_value=api))

    assert node.started is True
    assert node.api is api
    assert node._started is True


def test_remote_stopped_probe_clears_stale_api():
    node = ReSTXRayNode.__new__(ReSTXRayNode)
    node._started = True
    node._api = Mock()
    node.make_request = Mock(return_value={"started": False})

    assert node.started is False
    assert node._started is False
    assert node._api is None


def test_failed_disconnect_still_invalidates_cached_credentials_and_api():
    node = ReSTXRayNode.__new__(ReSTXRayNode)
    node._session_id = "old-session"
    node._started = True
    node._api = Mock()
    node.make_request = Mock(side_effect=ConnectionError("offline"))
    with pytest.raises(ConnectionError):
        node.disconnect()
    assert node._session_id is None
    assert node._started is False
    assert node._api is None
