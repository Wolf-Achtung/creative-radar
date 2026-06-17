"""Engine connect_args hardening (post-#281 infra fix).

A dead/half-open Postgres socket (public proxy on local runs) blocked the
pool_pre_ping reconnect indefinitely because connect_args carried no
client-side deadline. The Postgres path now sets a libpq connect_timeout + TCP
keepalives; the SQLite path is untouched.
"""
from __future__ import annotations

from app.database import _build_connect_args


def test_postgres_connect_args_have_timeout_and_keepalives():
    args = _build_connect_args(is_sqlite=False)
    assert args["connect_timeout"] == 10
    assert args["keepalives"] == 1
    assert args["keepalives_idle"] == 30
    assert args["keepalives_interval"] == 10
    assert args["keepalives_count"] == 3
    # must NOT leak the sqlite-only flag
    assert "check_same_thread" not in args


def test_sqlite_connect_args_unchanged():
    args = _build_connect_args(is_sqlite=True)
    assert args == {"check_same_thread": False}
    # no libpq params on the sqlite path
    assert "connect_timeout" not in args
    assert "keepalives" not in args
