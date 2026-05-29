"""Sicherheits-Garantien des ``pairs_channel_row_check``-Scripts
(Sprint 29.05.2026).

Read-only-Script — Tests bestaetigen:

1. Befund-Klassifizierung trifft jede der 4 Klassen (``ok``,
   ``mvp_disabled``, ``inactive_disabled``, ``PAIRS->keine DB-Row``).
2. PAIRS-Filter (``--pair`` Flag) schraenkt korrekt ein.
3. Empty-PAIRS-Edge-Case (kein enabled Pair) bricht sauber ab.
4. Case-insensitivity: PAIRS-Handle ``Netflix`` matched DB-Row
   ``netflix`` und umgekehrt.
5. Read-only-Garantie: Channel-Snapshot before == after.
"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Channel
from scripts.pairs_channel_row_check import (
    _classify,
    _iter_pair_channels,
    _lookup_channels_by_handle,
    run_check,
)


def _shared_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_engine()
    SQLModel.metadata.create_all(engine)
    return engine


# Minimal PAIRS-Mock fuer alle Tests in dieser Datei. Ein einziger Pair
# mit zwei Plattformen, drei Channels — das deckt die vier Befund-
# Klassen ab, wenn wir die DB entsprechend seeden.
_MOCK_PAIRS = {
    "warnerbros": {
        "enabled": True,
        "display_name": "Warner Bros",
        "platforms": {
            "tiktok": [
                {"handle": "warnerbros", "market": "US"},
                {"handle": "dc", "market": "US"},
            ],
            "instagram": [
                {"handle": "warnerbrosde", "market": "DE"},
            ],
        },
    },
    "netflix": {
        "enabled": True,
        "display_name": "Netflix",
        "platforms": {
            "tiktok": [
                {"handle": "Netflix", "market": "US"},
            ],
        },
    },
    "disney": {
        "enabled": False,
        "display_name": "Disney",
        "platforms": {
            "tiktok": [
                {"handle": "disneystudios", "market": "US"},
            ],
        },
    },
}


def _seed_channel(session: Session, *, handle: str, platform: str,
                  mvp: bool = True, active: bool = True) -> Channel:
    ch = Channel(
        name=handle,
        platform=platform,
        url=f"https://example.com/{uuid4()}",
        handle=handle,
        mvp=mvp,
        active=active,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _snapshot_channels(session: Session) -> list[tuple]:
    """(handle, platform, mvp, active)-Snapshot fuer Read-only-Diff."""
    rows = session.exec(select(Channel)).all()
    return sorted([
        (c.handle, c.platform, c.mvp, c.active) for c in rows
    ])


# ---- Klassifizierung -------------------------------------------------


def test_classify_empty_returns_keine_db_row():
    befund, chosen = _classify([])
    assert befund == "PAIRS->keine DB-Row"
    assert chosen is None


def test_classify_mvp_true_active_true_returns_ok():
    ch = Channel(
        name="x", platform="tiktok", url="https://x", handle="x",
        mvp=True, active=True,
    )
    befund, chosen = _classify([ch])
    assert befund == "ok"
    assert chosen is ch


def test_classify_mvp_false_returns_mvp_disabled():
    ch = Channel(
        name="x", platform="tiktok", url="https://x", handle="x",
        mvp=False, active=True,
    )
    befund, chosen = _classify([ch])
    assert befund == "mvp_disabled"
    assert chosen is ch


def test_classify_mvp_true_active_false_returns_inactive_disabled():
    ch = Channel(
        name="x", platform="tiktok", url="https://x", handle="x",
        mvp=True, active=False,
    )
    befund, chosen = _classify([ch])
    assert befund == "inactive_disabled"
    assert chosen is ch


def test_classify_multiple_matches_prefers_mvp_then_active():
    """Bei zwei DB-Rows mit demselben Handle bevorzugt ``_classify``
    die mvp=True/active=True-Row. So bekommt das Markdown den
    "ok"-Befund, auch wenn parallel eine inactive Bestands-Row liegt
    (Beispiel: Sony-IG-UK in PAIRS-Code Z. 168 dokumentiert)."""
    inactive = Channel(
        name="dead", platform="instagram", url="https://x",
        handle="sonypictures.uk", mvp=False, active=False,
    )
    live = Channel(
        name="live", platform="instagram", url="https://y",
        handle="sonypictures.uk", mvp=True, active=True,
    )
    befund, chosen = _classify([inactive, live])
    assert befund == "ok"
    assert chosen is live


# ---- PAIRS-Iteration -------------------------------------------------


def test_iter_pair_channels_skips_disabled_pairs():
    with patch("scripts.pairs_channel_row_check.PAIRS", _MOCK_PAIRS), \
         patch("scripts.pairs_channel_row_check._platforms_dict_for",
               side_effect=lambda pdef: pdef.get("platforms", {})):
        tuples = list(_iter_pair_channels())
    pair_keys = {t[0] for t in tuples}
    assert "disney" not in pair_keys, "Disabled Pair darf nicht erscheinen."
    assert pair_keys == {"warnerbros", "netflix"}


def test_iter_pair_channels_pair_filter_restricts():
    with patch("scripts.pairs_channel_row_check.PAIRS", _MOCK_PAIRS), \
         patch("scripts.pairs_channel_row_check._platforms_dict_for",
               side_effect=lambda pdef: pdef.get("platforms", {})):
        tuples = list(_iter_pair_channels(pairs_filter="netflix"))
    assert {t[0] for t in tuples} == {"netflix"}
    assert len(tuples) == 1


def test_iter_pair_channels_yields_per_platform_handle():
    with patch("scripts.pairs_channel_row_check.PAIRS", _MOCK_PAIRS), \
         patch("scripts.pairs_channel_row_check._platforms_dict_for",
               side_effect=lambda pdef: pdef.get("platforms", {})):
        tuples = list(_iter_pair_channels(pairs_filter="warnerbros"))
    # tiktok: 2 channels, instagram: 1 channel = 3 Tupel.
    assert len(tuples) == 3
    by_plt = {(t[1], t[2]) for t in tuples}
    assert ("tiktok", "warnerbros") in by_plt
    assert ("tiktok", "dc") in by_plt
    assert ("instagram", "warnerbrosde") in by_plt


# ---- Lookup-Bulk-Query ----------------------------------------------


def test_lookup_returns_empty_for_no_lookups(db):
    with Session(db) as session:
        result = _lookup_channels_by_handle(session, [])
    assert result == {}


def test_lookup_groups_matches_by_platform_handle(db):
    with Session(db) as session:
        _seed_channel(session, handle="warnerbros", platform="tiktok")
        _seed_channel(session, handle="warnerbros", platform="instagram")
        result = _lookup_channels_by_handle(
            session,
            [("tiktok", "warnerbros"), ("instagram", "warnerbros")],
        )
    assert len(result[("tiktok", "warnerbros")]) == 1
    assert len(result[("instagram", "warnerbros")]) == 1


def test_lookup_case_insensitive(db):
    """PAIRS-Handle ``Netflix`` (uppercase N) matched DB-Row
    ``netflix`` (lowercase)."""
    with Session(db) as session:
        _seed_channel(session, handle="netflix", platform="tiktok")
        # PAIRS-Quelle gibt Handle in Original-Casing zu — wir lowercasen
        # vor dem Lookup. Symmetric: DB-Row mit Mischschreibweise wuerde
        # auch matchen.
        result = _lookup_channels_by_handle(
            session, [("tiktok", "netflix")],
        )
    assert len(result[("tiktok", "netflix")]) == 1


def test_lookup_case_insensitive_db_side(db):
    """Symmetric: DB-Row ``WarnerBrosPictures`` (Mischschreibweise)
    matched PAIRS-Handle ``warnerbrospictures``."""
    with Session(db) as session:
        _seed_channel(
            session, handle="WarnerBrosPictures", platform="youtube",
        )
        result = _lookup_channels_by_handle(
            session, [("youtube", "warnerbrospictures")],
        )
    matches = result[("youtube", "warnerbrospictures")]
    assert len(matches) == 1
    assert matches[0].handle == "WarnerBrosPictures"


# ---- run_check (End-to-End) ------------------------------------------


def test_run_check_empty_pairs(capsys, db):
    """Wenn PAIRS leer ist (keine enabled), bricht run_check sauber ab
    mit Markdown-Header und Hinweis, return 0."""
    with patch("scripts.pairs_channel_row_check.PAIRS", {}), \
         patch("scripts.pairs_channel_row_check._platforms_dict_for",
               side_effect=lambda pdef: pdef.get("platforms", {})):
        with Session(db) as session:
            rc = run_check(session)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Keine enabled Pairs" in out


def test_run_check_unknown_pair_filter(capsys, db):
    """Filter auf nicht-existenten Pair-Key => return 1 mit Hinweis."""
    with patch("scripts.pairs_channel_row_check.PAIRS", _MOCK_PAIRS), \
         patch("scripts.pairs_channel_row_check._platforms_dict_for",
               side_effect=lambda pdef: pdef.get("platforms", {})):
        with Session(db) as session:
            rc = run_check(session, pairs_filter="nonexistent")
    assert rc == 1
    out = capsys.readouterr().out
    assert "Kein enabled Pair mit Key `nonexistent`" in out


def test_run_check_renders_all_four_befund_classes(capsys, db):
    """End-to-End: vier Channels mit jeweils einer Befund-Klasse.
    Markdown enthaelt alle vier Strings, Summary stimmt."""
    with Session(db) as session:
        # warnerbros tiktok/warnerbros -> ok
        _seed_channel(session, handle="warnerbros", platform="tiktok",
                      mvp=True, active=True)
        # warnerbros tiktok/dc -> inactive_disabled
        _seed_channel(session, handle="dc", platform="tiktok",
                      mvp=True, active=False)
        # warnerbros instagram/warnerbrosde -> mvp_disabled
        _seed_channel(session, handle="warnerbrosde", platform="instagram",
                      mvp=False, active=True)
        # netflix tiktok/Netflix -> PAIRS->keine DB-Row (kein Seed)

        with patch("scripts.pairs_channel_row_check.PAIRS", _MOCK_PAIRS), \
             patch("scripts.pairs_channel_row_check._platforms_dict_for",
                   side_effect=lambda pdef: pdef.get("platforms", {})):
            rc = run_check(session)

    assert rc == 0
    out = capsys.readouterr().out
    # Pair-Header
    assert "## warnerbros" in out
    assert "## netflix" in out
    # Vier Befund-Klassen
    assert " ok " in out
    assert "mvp_disabled" in out
    assert "inactive_disabled" in out
    assert "PAIRS->keine DB-Row" in out
    # Summary
    assert "Total PAIRS-Channels: 4" in out
    assert "davon ok:             1" in out
    assert "davon mvp_disabled:   1" in out
    assert "davon inactive_disabled: 1" in out
    assert "PAIRS->keine DB-Row:    1" in out


# ---- Read-only-Garantie ----------------------------------------------


def test_run_check_is_read_only(db):
    """Snapshot before/after identical."""
    with Session(db) as session:
        _seed_channel(session, handle="warnerbros", platform="tiktok")
        _seed_channel(session, handle="dc", platform="tiktok",
                      mvp=False)
        before = _snapshot_channels(session)

        with patch("scripts.pairs_channel_row_check.PAIRS", _MOCK_PAIRS), \
             patch("scripts.pairs_channel_row_check._platforms_dict_for",
                   side_effect=lambda pdef: pdef.get("platforms", {})):
            run_check(session)

        after = _snapshot_channels(session)
    assert before == after
