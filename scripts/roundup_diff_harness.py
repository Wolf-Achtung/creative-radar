#!/usr/bin/env python3
"""Lokales Validierungs-Harness fuer den Segment-Roundup (Sprint
roundup-inherit-brief-voice, PR #259).

Generiert einen Segment-Roundup lokal und schreibt das
``SegmentRoundupLLMReport`` als formatiertes JSON nach ``--out`` — OHNE
die DB-Persist-Stufe und ohne irgendeinen DB-Write. Zweck:
Vorher/Nachher-Prompt-Diff ueber ``git checkout main`` vs. Branch, ohne
Deploy/Merge. Vorlage: ``scripts/brief_diff_harness.py`` (Pair-Pendant).

Seam (read-only auf die Generierungs-Logik):
- Die LLM-Generierung ruft ``generate_segment_roundup(...)`` — die
  Aggregation laeuft INNERHALB des Generators (``aggregate_segment``,
  ``segment_roundup.py``), es ist keine Vorbereitung noetig. Der
  Persist-Wrapper ``generate_and_persist_roundup`` / ``_persist_roundup``
  wird hier nie erreicht — kein Roundup-Row-Write.

DB-Write-Isolation (kein Scope-Bruch in der Generierungs-Logik):
``generate_segment_roundup`` hat EINE incidentelle Schreib-Nebenwirkung —
die Costlog-Rows via ``record_anthropic_call`` -> ``cost_log._persist``
(eigene Session ueber die globale Engine, committet). Dieses Harness
stubt ``record_anthropic_call`` zur Laufzeit auf einen No-Op (der
Generator nutzt den modul-globalen Namen, ``segment_roundup.py``).
Zusaetzlich wird auf Postgres eine Read-only-Transaktion erzwungen,
damit ein versehentlicher Write hart fehlschlaegt.

Prompt-Anker-Check (Pflicht, lionsgate-Lektion gegen "falscher Stand"):
Vor dem Opus-Call bestaetigt das Harness sichtbar, dass der geladene
``ROUNDUP_SYSTEM_PROMPT`` den Sammel-Carve-out enthaelt ("Generell: Alle
oben erwähnten Output-Felder") und die alte "mit Haltung"-Anweisung
abwesend ist. Schlaegt der Check fehl, bricht das Harness VOR dem
(kostenpflichtigen) LLM-Call ab — dann ist ein alter Code-Stand
ausgecheckt. Fuer einen BEWUSSTEN "Vorher"-Lauf auf main (Diff-Zweck)
laesst ``--allow-prompt-mismatch`` den Lauf trotz Fehl-Anker weiter —
die Check-Zeile zeigt den Zustand dann weiterhin sichtbar an.

Aufruf:
    source ~/.creative-radar/db.env   # liefert CR_DB_URL
    ANTHROPIC_API_KEY=sk-ant-...  \
        python scripts/roundup_diff_harness.py --segment de_verleih \
            --out /tmp/de_verleih_branch.json

Env:
- ``CR_DB_URL``         (Pflicht) — DB-Verbindung; wird read-only genutzt.
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

# Anker des Voice-Fixes (#259): Sammel-Carve-out muss drin sein, die alte
# "mit Haltung"-Headline-Anweisung muss raus sein, und das "Theatrical"-
# Verbot (Nachtrag nach Phase-2-Audit) muss in der geerbten
# ANTI-PATTERN-Liste stehen.
_ANKER_SAMMEL_CARVEOUT = "Generell: Alle oben erwähnten Output-Felder"
_ANKER_THEATRICAL_VERBOT = "Theatrical / Theatrical-Material / Theatrical-Release"
_ANKER_ALT_VERBOTEN = "mit Haltung"


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"roundup_diff_harness: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _parse_anchor(raw: str) -> datetime:
    """``--anchor`` als ISO-Datum/Zeit (``YYYY-MM-DD`` oder volles
    ``YYYY-MM-DDTHH:MM:SS``). Naive Werte werden als UTC interpretiert —
    ``aggregate_segment`` rechnet das Fenster und die ISO-KW in UTC."""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        _die(f"--anchor muss ein ISO-Datum/Zeit sein (z.B. 2026-06-08), bekam {raw!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="roundup_diff_harness.py",
        description=(
            "Segment-Roundup lokal generieren und SegmentRoundupLLMReport "
            "als JSON dumpen (kein DB-Write)."
        ),
    )
    parser.add_argument(
        "--segment",
        required=True,
        help=(
            "Segment-Key: us_major, us_independent, uk_major, "
            "uk_independent, de_verleih, de_independent."
        ),
    )
    parser.add_argument("--out", required=True, help="Zieldatei fuer das LLMReport-JSON.")
    parser.add_argument(
        "--window-days",
        type=int,
        default=None,
        help="Zeitfenster in Tagen. Default: Server-Default (ROUNDUP_DEFAULT_WINDOW_DAYS = 14).",
    )
    parser.add_argument(
        "--anchor",
        default=None,
        help=(
            "ISO-Datum/Zeit fuer das Datenfenster-Ende, z.B. 2026-06-08. "
            "Default: utcnow — exakt das Verhalten von "
            "POST /api/admin/roundups/generate. Fuer Vergleichslaeufe "
            "(de_verleih vs us_major) denselben Anchor setzen, damit beide "
            "auf identischem KW-Fenster rechnen."
        ),
    )
    parser.add_argument(
        "--allow-prompt-mismatch",
        action="store_true",
        help=(
            "Lauf trotz fehlgeschlagenem Prompt-Anker-Check fortsetzen — "
            "NUR fuer bewusste 'Vorher'-Laeufe auf main (Diff-Zweck). "
            "Ohne dieses Flag bricht das Harness bei altem Prompt-Stand "
            "vor dem LLM-Call ab."
        ),
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

    import app.services.segment_roundup as sr  # noqa: E402
    from app.models.entities import ChannelSegment  # noqa: E402
    from app.services.anthropic_client import is_anthropic_configured  # noqa: E402
    from app.database import engine  # noqa: E402

    # DB-Write-Isolation: den einzigen Schreib-Seiteneffekt von
    # generate_segment_roundup (Costlog-Rows via record_anthropic_call ->
    # cost_log._persist) zur Laufzeit neutralisieren. Patch greift, weil der
    # Generator den modul-globalen Namen nutzt (Pair-Harness-Muster).
    sr.record_anthropic_call = lambda *a, **k: None  # type: ignore[assignment]

    try:
        segment = ChannelSegment(args.segment)
    except ValueError:
        _die(
            f"unbekanntes Segment {args.segment!r}; "
            f"erlaubt: {[s.value for s in ChannelSegment]}"
        )

    # Prompt-Anker-Check VOR dem Opus-Call: bestaetigt, dass der geladene
    # ROUNDUP_SYSTEM_PROMPT der #259-Stand ist (Sammel-Carve-out vorhanden,
    # "mit Haltung" raus). Bei Fail kein kostenpflichtiger Call.
    prompt = sr.ROUNDUP_SYSTEM_PROMPT
    sammel_ok = _ANKER_SAMMEL_CARVEOUT in prompt
    theatrical_ok = _ANKER_THEATRICAL_VERBOT in prompt
    haltung_abwesend = _ANKER_ALT_VERBOTEN not in prompt
    print(
        "[Prompt-Check] Sammel-Carve-out: "
        f"{'OK' if sammel_ok else 'FEHLT'} · "
        f"Theatrical-Verbot: {'OK' if theatrical_ok else 'FEHLT'} · "
        f"'mit Haltung': {'abwesend' if haltung_abwesend else 'NOCH ENTHALTEN'} "
        f"({len(prompt)} Zeichen)"
    )
    if not (sammel_ok and theatrical_ok and haltung_abwesend):
        if args.allow_prompt_mismatch:
            print(
                "[Prompt-Check] WARNUNG: alter Prompt-Stand — Lauf wird wegen "
                "--allow-prompt-mismatch fortgesetzt (bewusster 'Vorher'-Lauf)."
            )
        else:
            _die(
                "ROUNDUP_SYSTEM_PROMPT ist NICHT der #259-Stand "
                "(Sammel-Carve-out/Theatrical-Verbot fehlt oder 'mit Haltung' "
                "noch enthalten) — falscher Branch/Stand ausgecheckt? Abbruch "
                "vor dem LLM-Call. Fuer einen bewussten 'Vorher'-Lauf: "
                "--allow-prompt-mismatch.",
                code=4,
            )

    if not is_anthropic_configured():
        _die(
            "ANTHROPIC_API_KEY ist nicht konfiguriert "
            "(gelesen von app.config.Settings aus Env oder backend/.env)."
        )

    window_days = args.window_days if args.window_days is not None else sr.ROUNDUP_DEFAULT_WINDOW_DAYS
    anchor = _parse_anchor(args.anchor) if args.anchor else None

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
        report = sr.generate_segment_roundup(
            session,
            segment,
            window_days=window_days,
            now=anchor,
        )
    finally:
        session.close()

    if report.llm_output is None:
        _die(
            "LLM lieferte kein parsebares/validierbares Output "
            "(llm_output is None) — nichts geschrieben. raw_llm_text (erste "
            f"500 Zeichen): {(report.raw_llm_text or '')[:500]!r}",
            code=3,
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.llm_output.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    agg = report.aggregation
    print(
        f"roundup_diff_harness: {segment.value} LLMReport geschrieben "
        f"(KW {report.iso_week}/{report.iso_year}, window {report.window_days}d, "
        f"anchor {'utcnow' if anchor is None else anchor.date().isoformat()}, "
        f"channels mit Posts {agg.channels_with_posts}/{agg.channels_evaluated}, "
        f"posts {agg.total_posts}, titles {len(report.llm_output.titles)}) -> {out_path}"
    )


if __name__ == "__main__":
    main()
