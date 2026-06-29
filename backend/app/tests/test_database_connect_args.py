"""Engine connect_args hardening (post-#281 infra fix).

A dead/half-open Postgres socket (public proxy on local runs) blocked the
pool_pre_ping reconnect indefinitely because connect_args carried no
client-side deadline. The Postgres path now sets a libpq connect_timeout + TCP
keepalives; the SQLite path is untouched.
"""
from __future__ import annotations

from app.database import _build_connect_args, _pg_statement_timeout_ms


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


def test_postgres_connect_args_set_default_statement_timeout(monkeypatch):
    """Default 60000ms statement_timeout landet als libpq ``options`` im
    Postgres-Pfad (bestaetigte Title-Sync-Commit-Hang-Bremse)."""
    monkeypatch.delenv("PG_STATEMENT_TIMEOUT_MS", raising=False)
    args = _build_connect_args(is_sqlite=False)
    assert args["options"] == "-c statement_timeout=60000"


def test_statement_timeout_env_override(monkeypatch):
    monkeypatch.setenv("PG_STATEMENT_TIMEOUT_MS", "30000")
    assert _pg_statement_timeout_ms() == 30000
    args = _build_connect_args(is_sqlite=False)
    assert args["options"] == "-c statement_timeout=30000"


def test_statement_timeout_zero_disables_option(monkeypatch):
    """``0`` = Notausstieg: kein ``options``-Eintrag, der Rest bleibt."""
    monkeypatch.setenv("PG_STATEMENT_TIMEOUT_MS", "0")
    args = _build_connect_args(is_sqlite=False)
    assert "options" not in args
    assert args["connect_timeout"] == 10  # uebrige Haertung unberuehrt


def test_statement_timeout_never_reaches_sqlite(monkeypatch):
    """Selbst mit gesetztem ENV bekommt der sqlite-Testpfad kein ``options``
    — die Test-Engine bleibt unberuehrt."""
    monkeypatch.setenv("PG_STATEMENT_TIMEOUT_MS", "60000")
    args = _build_connect_args(is_sqlite=True)
    assert args == {"check_same_thread": False}


def test_statement_timeout_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("PG_STATEMENT_TIMEOUT_MS", "not-a-number")
    assert _pg_statement_timeout_ms() == 60000
