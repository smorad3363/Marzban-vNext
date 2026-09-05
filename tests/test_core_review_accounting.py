from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from pymysql.err import OperationalError as DriverOperationalError
from sqlalchemy.exc import OperationalError

from app.jobs import record_usages


@pytest.mark.parametrize("code", [1205, 1213])
def test_record_usage_retries_sqlalchemy_wrapped_lock_errors(monkeypatch, code):
    db = Mock()
    db.query.return_value.filter.return_value.all.return_value = [(7, 9)]
    @contextmanager
    def get_db():
        yield db
    monkeypatch.setattr(record_usages, "GetDB", get_db)
    monkeypatch.setattr(record_usages.xray, "nodes", {})
    monkeypatch.setattr(record_usages, "get_users_stats", lambda _: [{"uid": "7", "value": 100}])
    monkeypatch.setattr(record_usages, "DISABLE_RECORDING_NODE_USAGE", True)
    failure = OperationalError("UPDATE", {}, DriverOperationalError(code, "lock failure"))
    settle = Mock(side_effect=[failure, set()])
    monkeypatch.setattr(record_usages.money_billing, "settle_used_traffic", settle)
    record_usages.record_user_usages()
    assert settle.call_count == 2
    assert db.rollback.call_count == 1
    assert db.commit.call_count == 1


def test_safe_execute_retries_driver_deadlock_wrapped_by_sqlalchemy():
    db = Mock()
    db.bind.name = "mysql"
    db.connection.return_value.execute.side_effect = [
        OperationalError("UPDATE", {}, DriverOperationalError(1213, "deadlock")), None,
    ]
    record_usages.safe_execute(db, Mock())
    assert db.rollback.call_count == 1
    assert db.commit.call_count == 1
