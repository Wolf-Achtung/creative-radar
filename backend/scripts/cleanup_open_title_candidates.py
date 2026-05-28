"""Backlog-Aufraeumer fuer offene Titelkandidaten (Sprint 28.05.2026).

Wolf-owned-Script. Diagnose-Befund vom 28.05.: ~2.463 offene
TitleCandidates aufgelaufen, geschaetzt 60-80 % Rauschen aus dem
alten ``_extract_title_guess``-Pfad (in #200 abgestellt). Dieses
Skript raeumt den Bestand kontrolliert ab — per Status-Wechsel
``open → ignored``, reversibel.

Drei Modi
=========

**Default (ohne Flags) — Vorschau:**

    cd backend && python -m scripts.cleanup_open_title_candidates

Fuehrt NUR SELECT-Queries aus. Zeigt:
- Gesamtzahl der ``status='open'``-Kandidaten in der DB.
- Wieviele davon wuerden vom Filter erfasst (source IN
  ('hashtag','text') AND confidence < 0.40 AND created_at < now-14d).
- Verteilung der erfassten Rows nach source/confidence-Bucket/Alter.

AENDERT NICHTS. Default-mode ist absichtlich der sicherste — ein
versehentlicher Aufruf ohne Flag schreibt nichts.

**--apply --yes (Schreiben):**

    cd backend && python -m scripts.cleanup_open_title_candidates --apply --yes

Setzt die erfassten Rows auf ``status='ignored'`` und gibt den
Lauf-Timestamp aus. Ohne ``--yes`` fragt das Skript nach einer
expliziten Konsole-Bestaetigung ("ja" eingeben).

WHERE-Filter (strikt, hardcoded):

    status = 'open'                       # nur offene
    AND source IN ('hashtag', 'text')     # NICHT OCR/OpenAI/Perplexity
    AND confidence < 0.40                 # NICHT sicherere Treffer
    AND created_at < (now - 14 days)      # NICHT junge Kandidaten

Schreibmechanik:
- ``status``: open → ignored
- ``updated_at``: wird auf den Lauf-Timestamp gesetzt — dient als
  Wiedererkennung fuer den ``--undo``-Lauf.

**--undo <iso-timestamp>:**

    cd backend && python -m scripts.cleanup_open_title_candidates \\
        --undo 2026-05-28T17:30:00+00:00

Rollback: alle Rows, die im genannten Lauf auf IGNORED gesetzt wurden
(Wiedererkennung ueber ``updated_at == timestamp`` PLUS dieselben
Original-WHERE-Bedingungen, sodass keine fremden IGNORED-Rows
zurueckspringen koennen). Status wird auf OPEN gesetzt,
``updated_at`` wird auf den Rollback-Zeitpunkt aktualisiert
(transparent: "wir haben das angepasst").

Sicherheits-Garantien
=====================

- Kein Hard-Delete. Status-Wechsel ist die einzige Operation.
- WHERE-Bedingungen sind hardcoded. Keine Command-Line-Override
  fuer die Filter — vorbeugend gegen versehentliche Erweiterung des
  Erfassungsbereichs.
- ``--apply`` ohne ``--yes`` blockt mit einer interaktiven
  Konsole-Frage. Wer das Skript ueber `python` startet, muss
  bewusst ``ja`` eingeben.
- ``--undo`` setzt nur Rows zurueck, die dem Lauf-Timestamp UND
  den Original-WHERE-Bedingungen entsprechen — wenn dazwischen
  ein anderer Admin manuell einen Candidate auf IGNORED gesetzt
  hat (mit irgendeinem anderen updated_at), bleibt der unangetastet.
- Alle DB-Writes laufen in einer einzigen Transaktion; ein Fehler
  rollt automatisch zurueck.
- Skript schreibt KEINE Daten in andere Tabellen. Nur
  ``title_candidate`` wird angefasst.

Wolf-Ausfuehrungs-Pfad
======================

1. Vorschau (Railway-Dashboard → "Run a command" auf dem Backend-Service):

       cd backend && python -m scripts.cleanup_open_title_candidates

   Zahl pruefen. Wenn die Verteilung ueberraschend aussieht
   (z.B. > 80 % wuerden weggeworfen, oder Quellen-Mix nicht wie
   erwartet) — STOPP, Wolf-Ping.

2. Apply (wenn die Vorschau passt):

       cd backend && python -m scripts.cleanup_open_title_candidates --apply --yes

   Output haelt den Lauf-Timestamp fest (z.B.
   ``2026-05-28T17:30:00.123456+00:00``). Diesen Timestamp speichern,
   falls ein Rollback gebraucht wird.

3. Gegenchecken (Admin /admin → "Treffer pruefen"):
   - Offene Kandidaten sollten deutlich weniger sein.
   - Brief-Generierung + Asset.title_id sind weiterhin unberuehrt
     (Candidates fliessen nicht in Briefs ein, im Diagnose-Befund
     verifiziert).

4. Notfall-Rollback:

       cd backend && python -m scripts.cleanup_open_title_candidates \\
           --undo 2026-05-28T17:30:00.123456+00:00
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlmodel import Session, select

from app.database import engine
from app.models.entities import CandidateSource, CandidateStatus, TitleCandidate


# ---- Filter-Konstanten (hardcoded, keine CLI-Override) ----------------
ELIGIBLE_SOURCES: tuple[str, ...] = (
    CandidateSource.HASHTAG.value,
    CandidateSource.TEXT.value,
)
MAX_CONFIDENCE_EXCLUSIVE = 0.40
MIN_AGE_DAYS = 14


def _cutoff_now() -> datetime:
    return datetime.now(timezone.utc)


def _age_cutoff(now: datetime) -> datetime:
    return now - timedelta(days=MIN_AGE_DAYS)


def _build_filter_where(now: datetime):
    """Sqlalchemy-Where-Clause-Bauteile. Eine Funktion, damit das
    Filter-Set garantiert identisch ist zwischen Vorschau, --apply und
    --undo (kein Drift moeglich)."""
    age_cut = _age_cutoff(now)
    return (
        TitleCandidate.status == CandidateStatus.OPEN,
        TitleCandidate.source.in_(ELIGIBLE_SOURCES),
        TitleCandidate.confidence < MAX_CONFIDENCE_EXCLUSIVE,
        TitleCandidate.created_at < age_cut,
    )


# ---- Vorschau ---------------------------------------------------------


def _confidence_bucket(value: float) -> str:
    if value < 0.10:
        return "0.00-0.10"
    if value < 0.20:
        return "0.10-0.20"
    if value < 0.30:
        return "0.20-0.30"
    return "0.30-0.40"


def _age_bucket(created_at: datetime, now: datetime) -> str:
    # tz-Toleranz: SQLite gibt datetime ohne tz zurueck, Postgres mit.
    # Beide auf naive UTC normalisieren bevor wir subtrahieren —
    # gleicher Mechanismus wie ``_compute_breakout_scores`` (#190).
    ref_created = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
    ref_now = now.replace(tzinfo=None) if now.tzinfo else now
    days = max(0, int((ref_now - ref_created).total_seconds() // 86400))
    if days >= 90:
        return "90d+"
    if days >= 60:
        return "60-89d"
    if days >= 30:
        return "30-59d"
    return "14-29d"


def _print_preview(session: Session) -> int:
    """Druckt die Verteilung der erfassten Rows. Returns die Anzahl
    der Rows, die ein ``--apply`` schreiben wuerde."""
    now = _cutoff_now()

    total_open = session.exec(
        select(sa.func.count())
        .select_from(TitleCandidate)
        .where(TitleCandidate.status == CandidateStatus.OPEN)
    ).one()
    total_open_int = total_open[0] if isinstance(total_open, tuple) else total_open

    where = _build_filter_where(now)
    eligible = session.exec(
        select(TitleCandidate).where(*where)
    ).all()
    n = len(eligible)

    print("=" * 70)
    print("BACKLOG-AUFRAEUMER — VORSCHAU")
    print("=" * 70)
    print(f"Stichzeit:               {now.isoformat()}")
    print(f"Alter-Cutoff:            created_at < {_age_cutoff(now).isoformat()}")
    print(f"Confidence-Cutoff:       confidence < {MAX_CONFIDENCE_EXCLUSIVE}")
    print(f"Source-Filter:           {', '.join(ELIGIBLE_SOURCES)}")
    print("-" * 70)
    print(f"Offene Kandidaten total: {total_open_int}")
    print(f"Erfasst vom Filter:      {n}")
    if total_open_int > 0:
        print(f"Anteil:                  {n / total_open_int * 100:.1f} %")
    print("-" * 70)

    if n == 0:
        print("Keine Rows erfasst — nichts zu tun.")
        return 0

    by_source: Counter[str] = Counter()
    by_confidence: Counter[str] = Counter()
    by_age: Counter[str] = Counter()
    by_source_age: dict[tuple[str, str], int] = defaultdict(int)

    for c in eligible:
        src = c.source.value if hasattr(c.source, "value") else str(c.source)
        conf_bucket = _confidence_bucket(c.confidence)
        age_bucket = _age_bucket(c.created_at, now)
        by_source[src] += 1
        by_confidence[conf_bucket] += 1
        by_age[age_bucket] += 1
        by_source_age[(src, age_bucket)] += 1

    print("Nach Quelle:")
    for key in ("hashtag", "text"):
        if key in by_source:
            print(f"  {key:10s} {by_source[key]:>6d}")
    print()
    print("Nach Confidence-Bucket:")
    for bucket in ("0.00-0.10", "0.10-0.20", "0.20-0.30", "0.30-0.40"):
        if bucket in by_confidence:
            print(f"  {bucket:12s} {by_confidence[bucket]:>6d}")
    print()
    print("Nach Alter:")
    for bucket in ("14-29d", "30-59d", "60-89d", "90d+"):
        if bucket in by_age:
            print(f"  {bucket:8s} {by_age[bucket]:>6d}")
    print()
    print("Source × Alter (Kreuztabelle):")
    print(f"  {'':10s} {'14-29d':>8s} {'30-59d':>8s} {'60-89d':>8s} {'90d+':>8s}")
    for src in ("hashtag", "text"):
        row = [
            by_source_age.get((src, b), 0)
            for b in ("14-29d", "30-59d", "60-89d", "90d+")
        ]
        if sum(row) > 0:
            print(f"  {src:10s} {row[0]:>8d} {row[1]:>8d} {row[2]:>8d} {row[3]:>8d}")
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
    now_for_filter = _cutoff_now()
    where = _build_filter_where(now_for_filter)

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
    print(f"    python -m scripts.cleanup_open_title_candidates \\")
    print(f"        --undo {run_timestamp.isoformat()}")
    print("=" * 70)
    return n_updated, run_timestamp


# ---- Undo -------------------------------------------------------------


def _parse_undo_timestamp(raw: str) -> datetime:
    try:
        # ISO-8601 mit "+00:00"-Suffix oder "Z"
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
    UND originalen Filter-Bedingungen zurueck auf ``OPEN``.

    Die doppelte WHERE-Bedingung (Timestamp PLUS Filter) schuetzt vor
    dem Edge-Case, dass ein anderer Admin manuell einen Candidate auf
    IGNORED gesetzt hat und durch Zufall denselben mikrosekundengenauen
    Timestamp produziert — extrem unwahrscheinlich, aber dadurch
    abgefangen, dass die manuelle Aussortierung normalerweise eine
    andere Source/Confidence/Alter-Kombination trifft.
    """
    # Original-Filter (gleiche Konstanten) — aber MIT
    # ``status=IGNORED`` statt OPEN, weil wir die durch --apply
    # umgesetzten Rows suchen. Der created_at-Filter bleibt: wenn ein
    # Candidate, der diese Bedingung erfuellte, jetzt nicht mehr
    # erfuellt (theoretisch unmoeglich, created_at ist immutable),
    # wuerde er nicht zurueckkippen.
    age_cut = _age_cutoff(_cutoff_now())
    where = (
        TitleCandidate.status == CandidateStatus.IGNORED,
        TitleCandidate.updated_at == run_timestamp,
        TitleCandidate.source.in_(ELIGIBLE_SOURCES),
        TitleCandidate.confidence < MAX_CONFIDENCE_EXCLUSIVE,
        TitleCandidate.created_at < age_cut,
    )

    matching = session.exec(
        select(sa.func.count()).select_from(TitleCandidate).where(*where)
    ).one()
    n_matching = matching[0] if isinstance(matching, tuple) else matching

    print("=" * 70)
    print("BACKLOG-AUFRAEUMER — UNDO")
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
            "Backlog-Aufraeumer fuer offene TitleCandidates "
            "(Sprint 28.05.2026). Default: Vorschau, kein Schreibvorgang."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--apply", action="store_true",
        help="Status-Wechsel ausfuehren (open → ignored). Ohne diese Flag laeuft nur die Vorschau.",
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
