"""Integration: ``_acquire_brief_lock`` against real Postgres advisory locks.

Today's SQLite suite ``mock``-tests this function (test_insight_engine.py:
test_advisory_lock_issued_on_postgres_dialect) — it monkeypatches the
dialect name and intercepts ``session.exec`` to collect SQL text. That
verifies the right strings are emitted, but it has **never executed**
either ``SET LOCAL lock_timeout`` or ``pg_advisory_xact_lock`` against a
real Postgres server. This file closes that gap:

1. Two concurrent sessions request the SAME lock key. Session A acquires
   first; session B must block until A's transaction commits. Verified
   via threading-coordinated wait + elapsed-time assertion.
2. Two concurrent sessions request DIFFERENT lock keys. Both acquire
   without blocking. Verified by measuring that both complete before a
   shared barrier.
3. ``SET LOCAL lock_timeout = '300s'`` is the production setting; for
   the test we override to a short timeout and confirm that a blocked
   ``pg_advisory_xact_lock`` cancels with a lock-timeout error rather
   than hanging forever.
4. The ``_acquire_brief_lock`` helper itself returns ``True`` on a real
   Postgres binding (the SQLite path returns False; the dialect branch
   in the helper has never been tested for the True case until now).
"""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.services.insight_engine import _acquire_brief_lock


# Single shared lock-key for "same-key contention" tests. Chosen well
# below 2**31 so it stays inside Postgres' bigint advisory-lock space
# without colliding with the hash-derived keys the production code uses
# (those are ``hash((pair, year, week)) % 2**31`` — sentinel below picks
# a number that no real (pair, year, week) tuple realistically hashes
# to, but the lock namespace is global anyway so we just isolate via
# per-test schema reset).
LOCK_KEY_SAME = 4242424242
LOCK_KEY_OTHER = 9999999999


def test_acquire_brief_lock_helper_returns_true_on_postgres(pg_session):
    """The SQLite suite verifies the False branch (dialect != postgresql);
    this is the symmetric True branch. After this call the lock is held
    by the session's open transaction and would block any concurrent
    acquirer — but we commit immediately in the test to release it."""
    acquired = _acquire_brief_lock(
        pg_session,
        pair_key="warnerbros",
        iso_year=2026,
        iso_week=21,
    )
    assert acquired is True
    pg_session.commit()


def test_same_key_blocks_until_first_session_commits(pg_engine):
    """Session A holds the advisory lock; session B requesting the SAME
    key must block until A commits. We measure elapsed time on B —
    it must be at least the artificial delay we hold in A (~500ms).

    Coordination via two ``threading.Event``s: ``a_holds_lock`` signals
    that A is inside its transaction with the lock; ``release_a`` lets
    the main thread tell A when to commit. B grabs the lock as soon as A
    releases."""
    a_holds_lock = threading.Event()
    release_a = threading.Event()
    results: dict = {}

    def worker_a():
        # A separate Session on its own connection so the transaction
        # isolation is real, not shared with the test's pg_session.
        with Session(pg_engine) as session, session.begin():
            session.exec(
                text("SELECT pg_advisory_xact_lock(:k)"),
                params={"k": LOCK_KEY_SAME},
            )
            a_holds_lock.set()
            # Hold the lock until the main thread says "release". The
            # ``with session.begin()`` block commits on exit, which is
            # what actually drops the advisory lock.
            release_a.wait(timeout=5)
            results["a_finished"] = True

    def worker_b():
        a_holds_lock.wait(timeout=5)
        start = time.monotonic()
        with Session(pg_engine) as session, session.begin():
            session.exec(
                text("SELECT pg_advisory_xact_lock(:k)"),
                params={"k": LOCK_KEY_SAME},
            )
            results["b_wait_seconds"] = time.monotonic() - start

    t_a = threading.Thread(target=worker_a, daemon=True)
    t_b = threading.Thread(target=worker_b, daemon=True)
    t_a.start()
    t_b.start()

    # Wait for A to be inside its lock, then give B 500ms to try and
    # block. Then release A; B should grab the lock right after.
    assert a_holds_lock.wait(timeout=5), "worker A never reported lock acquisition"
    time.sleep(0.5)
    # B must NOT have completed in this window — it's blocked.
    assert "b_wait_seconds" not in results, (
        f"worker B acquired the lock without waiting (got {results.get('b_wait_seconds')}s) "
        "— pg_advisory_xact_lock did not block as expected."
    )

    release_a.set()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    assert results.get("a_finished") is True
    # B should have waited at least the ~0.5s we held A — give some
    # slack for thread-scheduling jitter on the lower bound.
    assert results.get("b_wait_seconds") is not None
    assert results["b_wait_seconds"] >= 0.4, (
        f"worker B unblocked too early: {results['b_wait_seconds']}s — "
        "the advisory lock did not actually serialise contenders."
    )


def test_different_keys_do_not_block_each_other(pg_engine):
    """Different advisory-lock keys must allow concurrent acquisition.
    Both workers grab their respective locks "at the same time" — we
    enforce ordering only via the shared barrier and verify neither
    waited substantially for the other.

    Note: there's no per-test schema barrier needed here — advisory
    locks are session/transaction-scoped, not row-scoped."""
    barrier = threading.Barrier(2, timeout=5)
    results: dict = {}

    def worker(key: int, label: str):
        barrier.wait()
        start = time.monotonic()
        with Session(pg_engine) as session, session.begin():
            session.exec(
                text("SELECT pg_advisory_xact_lock(:k)"),
                params={"k": key},
            )
            results[f"{label}_wait_seconds"] = time.monotonic() - start

    t1 = threading.Thread(target=worker, args=(LOCK_KEY_SAME, "t1"), daemon=True)
    t2 = threading.Thread(target=worker, args=(LOCK_KEY_OTHER, "t2"), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert results.get("t1_wait_seconds") is not None
    assert results.get("t2_wait_seconds") is not None
    # Generous upper bound — under contention these would be seconds,
    # without contention they're milliseconds. 250ms is a comfortable
    # ceiling that still flags real blocking.
    for label in ("t1", "t2"):
        assert results[f"{label}_wait_seconds"] < 0.25, (
            f"{label} took {results[f'{label}_wait_seconds']}s for an "
            "uncontended advisory-lock acquisition — keys may have "
            "collided or pg_advisory_xact_lock semantics changed."
        )


def test_lock_timeout_setting_cancels_blocked_acquire(pg_engine):
    """Production sets ``SET LOCAL lock_timeout = '300s'`` to cap the
    wait. For the test we use 200ms so the timeout fires fast. The
    blocked acquirer must raise — Postgres' canonical message contains
    ``canceling statement due to lock timeout``."""
    a_holds_lock = threading.Event()
    release_a = threading.Event()
    b_error: dict = {}

    def worker_a():
        with Session(pg_engine) as session, session.begin():
            session.exec(
                text("SELECT pg_advisory_xact_lock(:k)"),
                params={"k": LOCK_KEY_SAME},
            )
            a_holds_lock.set()
            release_a.wait(timeout=5)

    def worker_b():
        a_holds_lock.wait(timeout=5)
        try:
            with Session(pg_engine) as session, session.begin():
                # Short timeout for the test — production uses 300s,
                # the integration suite just verifies the mechanism.
                session.exec(text("SET LOCAL lock_timeout = '200ms'"))
                session.exec(
                    text("SELECT pg_advisory_xact_lock(:k)"),
                    params={"k": LOCK_KEY_SAME},
                )
                b_error["unexpected_success"] = True
        except Exception as exc:
            b_error["message"] = str(exc).lower()

    t_a = threading.Thread(target=worker_a, daemon=True)
    t_b = threading.Thread(target=worker_b, daemon=True)
    t_a.start()
    t_b.start()

    # Let B's lock_timeout actually fire before we release A.
    t_b.join(timeout=5)
    release_a.set()
    t_a.join(timeout=5)

    assert "unexpected_success" not in b_error, (
        "worker B somehow acquired the lock despite contention + short timeout"
    )
    assert "message" in b_error, "worker B did not raise — lock_timeout did not fire"
    # Postgres' standard message for lock_timeout looks like
    # "canceling statement due to lock timeout". Accept either the
    # exact phrase or the underlying 55P03 error.
    msg = b_error["message"]
    assert "lock timeout" in msg or "55p03" in msg, (
        f"unexpected error from blocked acquire: {msg}"
    )


def test_lock_released_on_commit_allows_subsequent_acquire(pg_engine):
    """The whole point of ``pg_advisory_xact_lock`` over the session-level
    variant is the transaction-end release. After session A commits, a
    fresh session must be able to grab the same key without contention.
    """
    with Session(pg_engine) as session_a, session_a.begin():
        session_a.exec(
            text("SELECT pg_advisory_xact_lock(:k)"),
            params={"k": LOCK_KEY_SAME},
        )
        # Hold + release via the with-block exit.

    # Now session_a's transaction has committed. The lock is dropped.
    start = time.monotonic()
    with Session(pg_engine) as session_b, session_b.begin():
        session_b.exec(
            text("SELECT pg_advisory_xact_lock(:k)"),
            params={"k": LOCK_KEY_SAME},
        )
    elapsed = time.monotonic() - start
    assert elapsed < 0.25, (
        f"second acquirer waited {elapsed}s — the lock from session_a was "
        "not released on commit."
    )
