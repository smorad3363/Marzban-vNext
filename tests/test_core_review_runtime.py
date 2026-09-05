from unittest.mock import Mock

from app.utils import system


def test_ipv6_response_from_ipv4_discovery_does_not_crash_startup(monkeypatch):
    monkeypatch.setattr(system.requests, "get", Mock(return_value=Mock(text="2001:db8::1")))
    sock = Mock()
    sock.getsockname.return_value = ("192.168.1.2", 1234)
    monkeypatch.setattr(system.socket, "socket", Mock(return_value=sock))
    assert system.get_public_ip() == "127.0.0.1"
    sock.close.assert_called_once()
