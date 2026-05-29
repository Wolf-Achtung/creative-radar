"""Cleanup-Apply fuer OPENAI-Beschreibungs-Kandidaten (Sprint 29.05.2026).

Wolf-owned-Script. Folge-PR nach #208 (Vorschau-Befund) — die ganze
``status=OPEN AND source=OPENAI``-Menge wurde als Beschreibungs-
Fragmente verifiziert (H1-Praefix-Match 67,4 %, Restmenge-Stichprobe
gleiches Bild, alle ~1.771 Rows bei confidence 0.4). Pauschal-Cleanup
ist sicher.

Analog #201 (``cleanup_open_title_candidates.py``), aber mit OPENAI-
spezifischem WHERE statt des hashtag/text + Confidence + Alter-Filters.

Schema-Anmerkungen
==================

- **``DISMISSED`` existiert im Schema nicht.** ``CandidateStatus`` ist
  ``OPEN``/``RESOLVED``/``IGNORED``. Wir nutzen ``IGNORED`` analog zu
  #201 — semantisch identisch zum Briefing-Wunsch (Wolf-Briefing
  erlaubt explizit "oder vergleichbares Feld, falls es im Schema
  heisst").
- **``TitleCandidate.notes`` existiert nicht.** Der Run-Timestamp in
  ``updated_at`` ist der Wiedererkennungs-Anker fuer ``--undo``. Ein
  ``notes``-Feld wuerde eine Migration brauchen — Scope-Creep, hier
  nicht erlaubt.

Drei Modi
=========

**Default (ohne Flags) — Vorschau:**

    cd backend && python -m scripts.cleanup_openai_descriptions

SELECT-only. Zeigt:
- Gesamtzahl der ``status=OPEN AND source=OPENAI``-Rows.
- Confidence-Bucket-Verteilung (erwartet: alles bei 0.4).
- Alter-Bucket-Verteilung (sanity).

AENDERT NICHTS. Default-Modus ist absichtlich der sicherste — ein
versehentlicher Aufruf ohne Flag schreibt nichts.

**--apply --yes (Schreiben):**

    cd backend && python -m scripts.cleanup_openai_descriptions --apply --yes

Setzt die erfassten Rows auf ``status=IGNORED`` und gibt den
Lauf-Timestamp aus. Ohne ``--yes`` fragt das Skript nach einer
expliziten Konsole-Bestaetigung ("ja" eingeben).

WHERE-Filter (strikt, hardcoded):

    status = 'open'
    AND source = 'openai'

Bewusst kein Confidence- oder Alter-Filter — die Befund-Verifikation
hat gezeigt, dass die ganze Menge Beschreibungs-Fragmente sind.

Schreibmechanik:
- ``status``: open -> ignored
- ``updated_at``: wird auf den Lauf-Timestamp gesetzt — dient als
  Wiedererkennung fuer den ``--undo``-Lauf.

**--undo <iso-timestamp>:**

    cd backend && python -m scripts.cleanup_openai_descriptions \\
        --undo 2026-05-29T18:15:00+00:00

Rollback: alle Rows, die im genannten Lauf auf IGNORED gesetzt wurden
(Wiedererkennung ueber ``updated_at == timestamp`` PLUS dieselben
Original-WHERE-Bedingungen ``source=OPENAI``, sodass keine fremden
IGNORED-Rows zurueckspringen koennen). Status wird auf OPEN gesetzt,
``updated_at`` wird auf den Rollback-Zeitpunkt aktualisiert.

Sicherheits-Garantien
=====================

- Kein Hard-Delete. Status-Wechsel ist die einzige Operation.
- WHERE-Bedingungen sind hardcoded. Keine Command-Line-Override.
- ``--apply`` ohne ``--yes`` blockt mit interaktiver Konsole-Frage.
- ``--undo`` setzt nur Rows zurueck, die dem Lauf-Timestamp UND
  ``source=OPENAI`` entsprechen — fremde IGNORED-Rows (z.B. aus dem
  #201-Lauf vom 28.05. mit source HASHTAG/TEXT) bleiben unangetastet.
- Alle DB-Writes laufen in einer einzigen Transaktion; Fehler rollt
  automatisch zurueck.
- Skript schreibt KEINE Daten in andere Tabellen.

Wolf-Ausfuehrungs-Pfad
======================

1. Vorschau (Railway-Dashboard -> "Run a command" auf dem Backend-Service):

       railway run sh -c 'DATABASE_URL="$CR_DB_URL" \\
           python -m scripts.cleanup_openai_descriptions'

   Erwartet: ~1.771 Rows erfasst, alle Confidence-Bucket 0.30-0.50,
   alle status OPEN. Falls deutlich abweichend (z.B. > 2.500 oder
   < 1.000) — STOP, Wolf-Ping.

2. Apply (wenn die Vorschau passt):

       railway run sh -c 'DATABASE_URL="$CR_DB_URL" \\
           python -m scripts.cleanup_openai_descriptions --apply --yes'

   Output haelt den Lauf-Timestamp fest (z.B.
   ``2026-05-29T18:15:00.123456+00:00``). AUFBEWAHREN fuer Rollback.

3. Gegenchecken:

       railway run psql "$CR_DB_URL" -c "SELECT COUNT(*) FROM \\
           creative_radar.titlecandidate \\
           WHERE status = 'open' AND source = 'openai';"

   Erwartet: 0 (oder kleine Zahl, falls seit Run-Start neue Rows
   dazugekommen sind).

4. Notfall-Rollback (nur falls noetig):

       railway run sh -c 'DATABASE_URL="$CR_DB_URL" \\
           python -m scripts.cleanup_openai_descriptions \\
           --undo 2026-05-29T18:15:00.123456+00:00'
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlmodel import Session, select

from app.database import engine
from app.models.entities import CandidateSource, CandidateStatus, TitleCandidate


# ---- Filter-Konstanten (hardcoded, keine CLI-Override) ----------------

ELIGIBLE_SOURCE = CandidateSource.OPENAI


def _build_apply_where():
    """WHERE fuer den Apply-Pfad: alle OPEN+OPENAI-Rows."""
    return (
        TitleCandidate.status == CandidateStatus.OPEN,
        TitleCandidate.source == ELIGIBLE_SOURCE,
    )


def _build_undo_where(run_timestamp: datetime):
    """WHERE fuer den Undo-Pfad: nur IGNORED-Rows desselben Run-
    Timestamps, die im selben source-OPENAI-Eimer landen — fremde
    IGNORED-Rows (z.B. aus #201) bleiben unangetastet."""
    return (
        TitleCandidate.status == CandidateStatus.IGNORED,
        TitleCandidate.updated_at == run_timestamp,
        TitleCandidate.source == ELIGIBLE_SOURCE,
    )


# ---- Vorschau ---------------------------------------------------------


def _confidence_bucket(value: float) -> str:
    """Konservative Bucket-Liste: erwartet ist 1 Bucket dominant
    (~0.30-0.50, weil alle Beschreibungs-Guesses dort landen). Wenn
    die Verteilung anders aussieht, ist die Annahme falsch und Wolf
    muss erneut sichten."""
    if value < 0.10:
        return "0.00-0.10"
    if value < 0.30:
        return "0.10-0.30"
    if value < 0.50:
        return "0.30-0.50"
    if value < 0.70:
        return "0.50-0.70"
    return "0.70-1.00"


def _age_bucket(created_at: datetime | None, now: datetime) -> str:
    if not created_at:
        return "unknown"
    ref_created = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
    ref_now = now.replace(tzinfo=None) if now.tzinfo else now
    days = max(0, int((ref_now - ref_created).total_seconds() // 86400))
    if days >= 90:
        return "90d+"
    if days >= 60:
        return "60-89d"
    if days >= 30:
        return "30-59d"
    if days >= 14:
        return "14-29d"
    return "0-13d"


def _print_preview(session: Session) -> int:
    """Druckt die Verteilung der erfassten Rows. Returns die Anzahl
    der Rows, die ein ``--apply`` schreiben wuerde."""
    now = datetime.now(timezone.utc)
    where = _build_apply_where()

    eligible = session.exec(select(TitleCandidate).where(*where)).all()
    n = len(eligible)

    print("=" * 70)
    print("OPENAI-BESCHREIBUNGS-CLEANUP — VORSCHAU")
    print("=" * 70)
    print(f"Stichzeit:               {now.isoformat()}")
    print(f"Zielmenge:               status='open' AND source='openai'")
    print("-" * 70)
    print(f"Erfasst vom Filter:      {n}")
    print("-" * 70)

    if n == 0:
        print("Keine Rows erfasst — nichts zu tun.")
        print("=" * 70)
        return 0

    by_confidence: Counter[str] = Counter()
    by_age: Counter[str] = Counter()
    for c in eligible:
        by_confidence[_confidence_bucket(c.confidence)] += 1
        by_age[_age_bucket(c.created_at, now)] += 1

    print()
    print("Nach Confidence-Bucket:")
    for bucket in ("0.00-0.10", "0.10-0.30", "0.30-0.50",
                   "0.50-0.70", "0.70-1.00"):
        if bucket in by_confidence:
            print(f"  {bucket:12s} {by_confidence[bucket]:>6d}")
    print()
    print("Nach Alter:")
    for bucket in ("0-13d", "14-29d", "30-59d", "60-89d", "90d+", "unknown"):
        if bucket in by_age:
            print(f"  {bucket:8s} {by_age[bucket]:>6d}")
    print("-" * 70)
    print(f"==> Mit --apply wuerden {n} Rows auf IGNORED gesetzt.")
    print("=" * 70)
    return n


# ---- Apply ------------------------------------------------------------


def _confirm_interactively() -> bool:
    """Konsole-Bestaetigung wenn kein --yes-Flag. Verlangt exakt
    ``ja`` als Eingabe; alles andere bricht ab."""
    print()
    print("Tippe 'ja' und Enter, um den Schreibvorgang auszufuehren.")
    print("Jede andere Eingabe (oder Strg-C) bricht ab.")
    try:
        answer = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAbgebrochen.")
        return False
    if answer != "ja":
        print(f"Eingabe war '{answer}', nicht 'ja' — Abbruch.")
        return False
    return True


def _apply(session: Session, *, skip_confirmation: bool) -> tuple[int, datetime] | None:
    """Schreibt den Status-Wechsel. Returns (n_updated, run_timestamp)
    bei Erfolg, ``None`` bei Abbruch.

    Die Vorschau wird IMMER vor dem Schreibvorgang ausgegeben — auch
    mit ``--yes``. Wer das Skript ausfuehrt, sieht die Zahl davor.
    """
    n = _print_preview(session)
    if n == 0:
        return None

    if not skip_confirmation:
        if not _confirm_interactively():
            return None
    else:
        print()
        print("--yes uebergeben — Bestaetigung uebersprungen.")

    # Run-Timestamp generieren — dient als Wiedererkennung fuer --undo.
    # Mikrosekundengenau, damit Kollisionen mit anderen Updates extrem
    # unwahrscheinlich sind.
    run_timestamp = datetime.now(timezone.utc)
    where = _build_apply_where()

    stmt = (
        sa.update(TitleCandidate)
        .where(*where)
        .values(
            status=CandidateStatus.IGNORED,
            updated_at=run_timestamp,
        )
        .execution_options(synchronize_session=False)
    )
    result = session.exec(stmt)
    session.commit()
    n_updated = result.rowcount if result.rowcount is not None else 0

    print()
    print("=" * 70)
    print("APPLY ABGESCHLOSSEN")
    print("=" * 70)
    print(f"Rows auf IGNORED gesetzt: {n_updated}")
    print(f"Run-Timestamp:            {run_timestamp.isoformat()}")
    print()
    print("Diesen Timestamp aufbewahren — fuer einen Notfall-Rollback:")
    print()
    print(f"    python -m scripts.cleanup_openai_descriptions \\")
    print(f"        --undo {run_timestamp.isoformat()}")
    print("=" * 70)
    return n_updated, run_timestamp


# ---- Undo -------------------------------------------------------------


def _parse_undo_timestamp(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(
            f"--undo Timestamp '{raw}' nicht parsbar (erwartet ISO 8601 mit TZ): {exc}"
        ) from exc
    if dt.tzinfo is None:
        raise SystemExit(
            f"--undo Timestamp '{raw}' hat keine Timezone — bitte als ISO-8601 mit '+00:00' uebergeben."
        )
    return dt


def _undo(session: Session, run_timestamp: datetime) -> int:
    """Setzt Rows mit ``status=IGNORED`` UND ``updated_at == run_timestamp``
    UND ``source=OPENAI`` zurueck auf ``OPEN``.

    Die doppelte WHERE-Bedingung (Timestamp PLUS source-Filter) schuetzt
    vor dem Edge-Case, dass eine fremde IGNORED-Row zufaellig denselben
    mikrosekundengenauen Timestamp traegt — z.B. aus dem #201-Lauf vom
    28.05.: dort sind die Sources HASHTAG/TEXT, also gefahrlos
    abgegrenzt.
    """
    where = _build_undo_where(run_timestamp)

    matching = session.exec(
        select(sa.func.count()).select_from(TitleCandidate).where(*where)
    ).one()
    n_matching = matching[0] if isinstance(matching, tuple) else matching

    print("=" * 70)
    print("OPENAI-CLEANUP — UNDO")
    print("=" * 70)
    print(f"Run-Timestamp gesucht: {run_timestamp.isoformat()}")
    print(f"Passende Rows:         {n_matching}")
    if n_matching == 0:
        print("Keine Rows zum Zuruecksetzen — Timestamp pruefen.")
        return 0

    rollback_ts = datetime.now(timezone.utc)
    stmt = (
        sa.update(TitleCandidate)
        .where(*where)
        .values(
            status=CandidateStatus.OPEN,
            updated_at=rollback_ts,
        )
        .execution_options(synchronize_session=False)
    )
    result = session.exec(stmt)
    session.commit()
    n_updated = result.rowcount if result.rowcount is not None else 0

    print(f"Rows zurueck auf OPEN: {n_updated}")
    print(f"Rollback-Timestamp:    {rollback_ts.isoformat()}")
    print("=" * 70)
    return n_updated


# ---- CLI --------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cleanup-Apply fuer OPENAI-Beschreibungs-Kandidaten "
            "(Sprint 29.05.2026). Default: Vorschau, kein Schreibvorgang."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--apply", action="store_true",
        help="Status-Wechsel ausfuehren (open -> ignored). Ohne diese Flag laeuft nur die Vorschau.",
    )
    group.add_argument(
        "--undo", metavar="ISO_TIMESTAMP", default=None,
        help="Rollback: Rows eines frueheren --apply-Laufs zurueck auf OPEN.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Bestaetigung ueberspringen (nur sinnvoll mit --apply).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    if args.undo:
        ts = _parse_undo_timestamp(args.undo)
        with Session(engine) as session:
            _undo(session, ts)
        return 0

    if args.apply:
        with Session(engine) as session:
            result = _apply(session, skip_confirmation=args.yes)
        if result is None:
            return 1
        return 0

    # Default: Vorschau
    with Session(engine) as session:
        _print_preview(session)
    print()
    print("Hinweis: Default-Modus aendert nichts.")
    print("Zum Schreiben:  --apply --yes")
    print("Zum Rollback:   --undo <iso-timestamp>")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
