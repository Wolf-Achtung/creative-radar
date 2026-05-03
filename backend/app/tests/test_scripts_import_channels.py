"""Tests for scripts/import_channels.py.

Sprint 5.3.X Mini-Run 2. We exercise the script's run() function against
an in-memory SQLite session — same pattern as test_backfill.py — and
the main() entry point with a patched engine for the transaction-rollback
case. No real DB is touched.

Coverage targets the eight cases Wolf-explicit-listed plus four extras
that come straight from the pre-commitments (header-validation, missing-
file-with-example-fallback, invalid-platform, name/notes-preservation).
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Channel, Market
from scripts import import_channels


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _write_csv(path: Path, rows: str, *, header: str | None = None) -> Path:
    default_header = (
        "company_name,category,market,platform,handle,url,confidence,notes"
    )
    text = dedent(rows).lstrip("\n")
    path.write_text(
        f"{header if header is not None else default_header}\n{text}",
        encoding="utf-8",
    )
    return path


def _existing_channel(
    session: Session,
    *,
    name: str,
    platform: str,
    handle: str,
    category: str | None = None,
    import_source: str | None = None,
    notes: str | None = None,
) -> Channel:
    ch = Channel(
        id=uuid4(),
        name=name,
        platform=platform,
        url=f"https://example.com/c/{handle}",
        handle=handle,
        market=Market.DE,
        category=category,
        import_source=import_source,
        notes=notes,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


# --------------------------------------------------------------------------
# Header / file-existence guards (exit before any DB write)
# --------------------------------------------------------------------------


def test_invalid_header_aborts_no_db_writes(
    session: Session, tmp_path: Path
) -> None:
    csv_path = _write_csv(
        tmp_path / "bad_header.csv",
        rows="Foo,major_studio,DE,instagram,foo,https://x/foo,verified,\n",
        header="wrong,columns,here",
    )
    with pytest.raises(SystemExit) as exc:
        import_channels.run(csv_path, session=session)
    assert exc.value.code == 1
    assert session.exec(select(Channel)).all() == []


def test_missing_real_csv_with_example_present_exits_with_clear_message(
    session: Session, tmp_path: Path, capsys
) -> None:
    real = tmp_path / "channels_perplexity_2026_05_03.csv"
    example = tmp_path / "channels_perplexity_2026_05_03.csv.example"
    example.write_text(
        "company_name,category,market,platform,handle,url,confidence,notes\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        import_channels.run(real, session=session)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert ".example-CSV ist eine Demo-Vorlage" in err
    assert str(real) in err


# --------------------------------------------------------------------------
# Three core UPSERT paths: CREATE / UPDATE / SKIP
# --------------------------------------------------------------------------


def test_inserts_new_channel(session: Session, tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows="Netflix DE,streamer,DE,instagram,netflixde,https://i/netflixde,verified,seed\n",
    )
    stats = import_channels.run(csv_path, session=session)
    session.commit()
    assert (stats.created, stats.updated, stats.skipped, stats.errored) == (1, 0, 0, 0)
    rows = session.exec(select(Channel)).all()
    assert len(rows) == 1
    ch = rows[0]
    assert ch.name == "Netflix DE"
    assert ch.platform == "instagram"
    assert ch.handle == "netflixde"
    assert ch.market == Market.DE
    assert ch.active is True
    assert ch.category == "streamer"
    assert ch.import_source == import_channels.IMPORT_SOURCE_VALUE
    assert ch.notes == "seed"


def test_updates_when_category_null(session: Session, tmp_path: Path) -> None:
    _existing_channel(
        session,
        name="Netflix DE (existing)",
        platform="instagram",
        handle="netflixde",
        category=None,
        import_source=None,
    )
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows="Netflix Deutschland,streamer,DE,instagram,netflixde,https://i/netflixde,verified,\n",
    )
    stats = import_channels.run(csv_path, session=session)
    session.commit()
    assert (stats.created, stats.updated, stats.skipped, stats.errored) == (0, 1, 0, 0)
    ch = session.exec(
        select(Channel).where(Channel.handle == "netflixde")
    ).one()
    assert ch.category == "streamer"
    assert ch.import_source == import_channels.IMPORT_SOURCE_VALUE


def test_skips_when_both_already_set(
    session: Session, tmp_path: Path
) -> None:
    """Beide Audit-Felder gesetzt → echter SKIP-Pfad, keine Änderung."""
    _existing_channel(
        session,
        name="Netflix DE",
        platform="instagram",
        handle="netflixde",
        category="streamer",
        import_source="perplexity_2026_04_15",
    )
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows="Netflix Deutschland,major_studio,DE,instagram,netflixde,https://i/netflixde,verified,\n",
    )
    stats = import_channels.run(csv_path, session=session)
    session.commit()
    assert (stats.created, stats.updated, stats.skipped, stats.errored) == (0, 0, 1, 0)
    ch = session.exec(
        select(Channel).where(Channel.handle == "netflixde")
    ).one()
    # No override of either pre-set field.
    assert ch.category == "streamer"
    assert ch.import_source == "perplexity_2026_04_15"


def test_partial_update_when_only_category_set(
    session: Session, tmp_path: Path, capsys
) -> None:
    """Per-Feld-Semantik: category bereits gesetzt, import_source NULL.
    UPDATE setzt nur import_source und lässt category in Ruhe. Pretty-Print
    soll situativ nur das tatsächlich geänderte Feld zeigen — nicht beide
    hardcoded."""
    _existing_channel(
        session,
        name="Netflix DE",
        platform="instagram",
        handle="netflixde",
        category="legacy_category_value",
        import_source=None,
    )
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows="Netflix Deutschland,streamer,DE,instagram,netflixde,https://i/netflixde,verified,\n",
    )
    stats = import_channels.run(csv_path, session=session)
    session.commit()
    assert (stats.created, stats.updated, stats.skipped, stats.errored) == (0, 1, 0, 0)
    ch = session.exec(
        select(Channel).where(Channel.handle == "netflixde")
    ).one()
    # category preserved, import_source written.
    assert ch.category == "legacy_category_value"
    assert ch.import_source == import_channels.IMPORT_SOURCE_VALUE

    out = capsys.readouterr().out
    assert "[UPDATE]" in out
    assert "import_source=perplexity_2026_05_03" in out
    # Honest output: ``category=`` must NOT appear in this UPDATE line —
    # only the field actually written is reported.
    assert "category=" not in out


def test_update_does_not_overwrite_name_or_notes(
    session: Session, tmp_path: Path
) -> None:
    """Wolf's clarification: UPDATE-Pfad fasst NUR category + import_source
    an. name und notes (möglicherweise handgepflegt) bleiben stehen, auch
    wenn die CSV andere Werte trägt."""
    _existing_channel(
        session,
        name="Netflix DE (Wolf-Variante)",
        platform="instagram",
        handle="netflixde",
        category=None,
        import_source=None,
        notes="Wolf-Notiz nicht überschreiben",
    )
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows="Netflix Deutschland,streamer,DE,instagram,netflixde,https://i/netflixde,verified,csv-note\n",
    )
    import_channels.run(csv_path, session=session)
    session.commit()
    ch = session.exec(
        select(Channel).where(Channel.handle == "netflixde")
    ).one()
    assert ch.name == "Netflix DE (Wolf-Variante)"
    assert ch.notes == "Wolf-Notiz nicht überschreiben"
    assert ch.category == "streamer"
    assert ch.import_source == import_channels.IMPORT_SOURCE_VALUE


# --------------------------------------------------------------------------
# Per-row validation (continues, doesn't abort the batch)
# --------------------------------------------------------------------------


def test_skips_invalid_market_row_and_continues(
    session: Session, tmp_path: Path
) -> None:
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows=(
            "Bad Market Co,streamer,EU,instagram,badmarket,https://i/bm,verified,\n"
            "Good Co,streamer,DE,instagram,goodco,https://i/goodco,verified,\n"
        ),
    )
    stats = import_channels.run(csv_path, session=session)
    session.commit()
    assert (stats.created, stats.updated, stats.skipped, stats.errored) == (1, 0, 0, 1)
    handles = {ch.handle for ch in session.exec(select(Channel)).all()}
    assert handles == {"goodco"}


def test_skips_invalid_platform_row_and_continues(
    session: Session, tmp_path: Path
) -> None:
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows=(
            "Snap Corp,streamer,DE,snapchat,snapcorp,https://s/snapcorp,verified,\n"
            "Good Co,streamer,DE,instagram,goodco,https://i/goodco,verified,\n"
        ),
    )
    stats = import_channels.run(csv_path, session=session)
    session.commit()
    assert (stats.created, stats.updated, stats.skipped, stats.errored) == (1, 0, 0, 1)
    handles = {ch.handle for ch in session.exec(select(Channel)).all()}
    assert handles == {"goodco"}


def test_does_not_filter_by_confidence_value(
    session: Session, tmp_path: Path
) -> None:
    """Wolf filtert die CSV vor dem Lauf. Das Skript importiert ALLE Zeilen
    unabhängig vom confidence-Wert — falsche Werte sind Wolf-Fehler, kein
    Skript-Verhalten."""
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows=(
            "Verified Co,streamer,DE,instagram,verified_co,https://i/v,verified,\n"
            "Guessed Co,streamer,US,instagram,guessed_co,https://i/g,guessed_naming_pattern,\n"
            "Inferred Co,streamer,INT,instagram,inferred_co,https://i/i,inferred_from_source,\n"
        ),
    )
    stats = import_channels.run(csv_path, session=session)
    session.commit()
    assert stats.created == 3
    assert stats.errored == 0
    handles = {ch.handle for ch in session.exec(select(Channel)).all()}
    assert handles == {"verified_co", "guessed_co", "inferred_co"}


# --------------------------------------------------------------------------
# Idempotency, dry-run, transaction-rollback
# --------------------------------------------------------------------------


def test_dry_run_does_not_persist(
    session: Session, tmp_path: Path, monkeypatch
) -> None:
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows="Netflix DE,streamer,DE,instagram,netflixde,https://i/netflixde,verified,\n",
    )
    monkeypatch.setattr(import_channels, "engine", session.bind)
    rc = import_channels.main([str(csv_path), "--dry-run"])
    assert rc == 0
    # Use a fresh session because main() closed its own.
    fresh = Session(session.bind)
    try:
        assert fresh.exec(select(Channel)).all() == []
    finally:
        fresh.close()


def test_idempotent_second_run_is_pure_skip(
    session: Session, tmp_path: Path
) -> None:
    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows=(
            "A Co,streamer,DE,instagram,aco,https://i/aco,verified,\n"
            "B Co,major_studio,US,instagram,bco,https://i/bco,verified,\n"
        ),
    )
    first = import_channels.run(csv_path, session=session)
    session.commit()
    assert (first.created, first.updated, first.skipped, first.errored) == (2, 0, 0, 0)

    second = import_channels.run(csv_path, session=session)
    session.commit()
    assert (second.created, second.updated, second.skipped, second.errored) == (0, 0, 2, 0)
    # Row count unchanged — no duplicates.
    assert len(session.exec(select(Channel)).all()) == 2


def test_per_row_errors_dont_change_exit_code(
    tmp_path: Path, monkeypatch
) -> None:
    """Per-row-Validation-Fehler sind erwartetes Verhalten (Wolf filtert
    den Rest manuell), kein Sprint-failure. Exit-Code bleibt 0, das
    errored-Feld in der Summary trägt die Statistik. Nur ein Mid-Batch-
    Crash mit Rollback gibt rc=1 (siehe nachfolgenden Test)."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(import_channels, "engine", engine)

    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows=(
            "Bad Co,streamer,EU,instagram,badco,https://i/badco,verified,\n"
            "Good Co,streamer,DE,instagram,goodco,https://i/goodco,verified,\n"
        ),
    )

    rc = import_channels.main([str(csv_path)])
    assert rc == 0

    fresh = Session(engine)
    try:
        rows = fresh.exec(select(Channel)).all()
    finally:
        fresh.close()
    assert {ch.handle for ch in rows} == {"goodco"}


def test_transaction_rollback_on_unexpected_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Mid-batch RuntimeError → main() rolls back, returns rc=1, ZERO rows
    persisted (not even those processed before the failure)."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(import_channels, "engine", engine)

    csv_path = _write_csv(
        tmp_path / "in.csv",
        rows=(
            "A Co,streamer,DE,instagram,aco,https://i/aco,verified,\n"
            "B Co,streamer,US,instagram,bco,https://i/bco,verified,\n"
            "C Co,streamer,INT,instagram,cco,https://i/cco,verified,\n"
        ),
    )

    real_process = import_channels._process_row
    call_counter = {"n": 0}

    def exploding_process(session, row):
        call_counter["n"] += 1
        if call_counter["n"] == 2:
            raise RuntimeError("boom — simulated mid-batch failure")
        return real_process(session, row)

    monkeypatch.setattr(import_channels, "_process_row", exploding_process)

    rc = import_channels.main([str(csv_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ABORT" in err
    assert "boom" in err

    fresh = Session(engine)
    try:
        rows = fresh.exec(select(Channel)).all()
    finally:
        fresh.close()
    assert rows == [], (
        "Mid-batch failure must not leave any committed rows behind — "
        f"found {[(r.platform, r.handle) for r in rows]}"
    )
