#!/usr/bin/env python3
"""Lokales Validierungs-Harness fuer den Pair-Brief (Sprint 9b).

Generiert einen Pair-Brief lokal und schreibt das ``LLMReport`` als
formatiertes JSON nach ``--out`` — OHNE die DB-Persist-Stufe und ohne
irgendeinen DB-Write. Zweck: Vorher/Nachher-Prompt-Diff ueber
``git checkout main`` vs. Branch, ohne Deploy/Merge.

Seam (read-only auf die Generierungs-Logik):
- Aggregation laeuft ueber den bestehenden ``aggregate_pair(pair, now=anchor)``.
- Die LLM-Generierung ruft ``generate_weekly_report(...)`` — das ist genau
  die Stufe, die in ``generate_and_persist_report`` VOR dem DB-Write-Branch
  (``insight_engine.py`` :4181/:4242 / ``_persist_report`` :4352) das
  ``InsightReport`` mit ``.llm_output`` zurueckgibt. ``_persist_report`` wird
  hier nie erreicht — kein Brief-Row-Write.
- Der Anti-Repetition-``previous_context`` wird identisch zu
  ``generate_and_persist_report`` (:4272-4307) rekonstruiert, damit das Dump
  dem entspricht, was ``/weekly`` Usern zeigt.

DB-Write-Isolation (kein Scope-Bruch in der Generierungs-Logik):
``generate_weekly_report`` hat EINE incidentelle Schreib-Nebenwirkung — der
Anthropic-Costlog-Row via ``record_anthropic_call`` -> ``cost_log._persist``,
der eine eigene Session ueber die globale Engine oeffnet und committet
(``cost_log.py`` :41). Dieses Harness stubt ``record_anthropic_call`` zur
Laufzeit auf einen No-Op. Das ist KEINE Aenderung an ``insight_engine.py`` —
nur ein Werkzeug, das seinen einzigen Seiteneffekt neutralisiert, damit der
Lauf die DB ausschliesslich liest.

Aufruf:
    CR_DB_URL=postgresql://...  ANTHROPIC_API_KEY=sk-ant-...  \
        python scripts/brief_diff_harness.py --pair netflix --out /tmp/netflix_branch.json

Env:
- ``CR_DB_URL``       (Pflicht) — DB-Verbindung; wird read-only genutzt.
- ``ANTHROPIC_API_KEY`` (Pflicht) — vom ``app.config.Settings``-Objekt aus
                        der Umgebung (oder ``backend/.env``) gelesen.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Das Backend-Package (``app.*``) liegt unter ``<repo>/backend``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"brief_diff_harness: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _parse_anchor(raw: str) -> datetime:
    """``--anchor`` als ISO-Datum/Zeit (``YYYY-MM-DD`` oder volles
    ``YYYY-MM-DDTHH:MM:SS``). Naive Werte werden als UTC interpretiert,
    damit sie zu ``last_completed_iso_week_anchor()`` (UTC) passen."""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        _die(f"--anchor muss ein ISO-Datum/Zeit sein (z.B. 2026-06-01), bekam {raw!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="brief_diff_harness.py",
        description="Pair-Brief lokal generieren und LLMReport als JSON dumpen (kein DB-Write).",
    )
    parser.add_argument("--pair", required=True, help="Pair-Key, z.B. 'netflix' oder 'warnerbros'.")
    parser.add_argument("--out", required=True, help="Zieldatei fuer das LLMReport-JSON.")
    parser.add_argument(
        "--anchor",
        default=None,
        help="ISO-Datum/Zeit fuer das Datenfenster-Ende. Default: "
        "last_completed_iso_week_anchor() — dieselbe KW, die /weekly Usern zeigt.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Datenfenster in Tagen (Default 30, analog /weekly).",
    )
    args = parser.parse_args()

    db_url = (os.environ.get("CR_DB_URL") or "").strip()
    if not db_url:
        _die("CR_DB_URL ist nicht gesetzt — bitte die DB-Verbindung in CR_DB_URL exportieren.")

    # app.database resolved DATABASE_URL beim Import (database.py:50). Wir
    # zeigen den Resolver auf CR_DB_URL, BEVOR app-Module importiert werden,
    # damit eine einzige Verbindung (= CR_DB_URL) im Spiel ist.
    os.environ["DATABASE_URL"] = db_url

    from sqlmodel import Session  # noqa: E402 — nach sys.path / DATABASE_URL

    import app.services.insight_engine as ie  # noqa: E402
    from app.services.insight_engine import (  # noqa: E402
        PAIRS,
        _compute_top_post_diff,
        _format_previous_context_block,
        _load_previous_brief,
        aggregate_pair,
        generate_weekly_report,
        last_completed_iso_week_anchor,
    )
    from app.schemas.insights import PairAggregation  # noqa: E402
    from app.services.anthropic_client import is_anthropic_configured  # noqa: E402
    from app.database import engine  # noqa: E402

    # DB-Write-Isolation: den einzigen Schreib-Seiteneffekt von
    # generate_weekly_report (Costlog-Row via record_anthropic_call ->
    # cost_log._persist) zur Laufzeit neutralisieren. Patch greift, weil
    # _run_brief_llm (insight_engine.py:3821) den Modul-globalen Namen nutzt.
    ie.record_anthropic_call = lambda *a, **k: None  # type: ignore[assignment]

    if args.pair not in PAIRS:
        _die(f"unbekannter Pair-Key {args.pair!r}; bekannt: {sorted(PAIRS)}")
    if not is_anthropic_configured():
        _die(
            "ANTHROPIC_API_KEY ist nicht konfiguriert "
            "(gelesen von app.config.Settings aus Env oder backend/.env)."
        )

    anchor = _parse_anchor(args.anchor) if args.anchor else last_completed_iso_week_anchor()

    session = Session(engine)
    # Belt-and-suspenders: Read-only-Transaktion erzwingen (Postgres), damit
    # ein versehentlicher Write hart fehlschlaegt statt still durchzugehen.
    try:
        if engine.url.get_backend_name().startswith("postgres"):
            session.connection(
                execution_options={"postgresql_readonly": True, "postgresql_deferrable": True}
            )
    except Exception:  # noqa: BLE001 — best effort, SQLite/andere Treiber
        pass

    try:
        # Anti-Repetition-previous_context exakt wie generate_and_persist_report
        # (insight_engine.py:4272-4307) rekonstruieren, damit das Dump dem
        # /weekly-Output entspricht. Reine Reads.
        agg = aggregate_pair(session, args.pair, window_days=args.window_days, now=anchor)
        previous_context_block = None
        previous = _load_previous_brief(session, args.pair, agg.iso_year, agg.iso_week)
        if previous is not None:
            try:
                prev_headline = (previous.llm_output or {}).get("headline")
                if prev_headline and str(prev_headline).strip():
                    previous_agg = PairAggregation.model_validate(previous.aggregation)
                    diff = _compute_top_post_diff(agg, previous_agg)
                    previous_context_block = _format_previous_context_block(
                        prev_iso_year=previous.iso_year,
                        prev_iso_week=previous.iso_week,
                        prev_headline=str(prev_headline).strip(),
                        diff=diff,
                    )
            except (KeyError, ValueError, TypeError, AttributeError):
                previous_context_block = None

        report = generate_weekly_report(
            session,
            args.pair,
            window_days=args.window_days,
            dry_run=False,
            now=anchor,
            previous_context=previous_context_block,
        )
    finally:
        session.close()

    if report.llm_output is None:
        _die(
            "LLM lieferte kein parsebares Output (llm_output is None) — "
            "nichts geschrieben.",
            code=3,
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.llm_output.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"brief_diff_harness: {args.pair} LLMReport geschrieben "
        f"(KW {report.iso_week}/{report.iso_year}, anchor {anchor.date().isoformat()}, "
        f"previous_context={'ja' if previous_context_block else 'nein'}) -> {out_path}"
    )


if __name__ == "__main__":
    main()
