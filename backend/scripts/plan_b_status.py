"""Wochen-Statusbericht: Cron, Abdeckung, Backlog, Plan-B-Arbeitsliste.

Wolf-owned-Script, **read-only**. Ersetzt die vier Einzelabfragen aus
``docs/BRIEFING_MONTAG_10_08_2026.md`` (Schritte A, B, C, E) durch einen
Aufruf, dessen Ausgabe sich am Stueck in den Chat kopieren laesst —
statt vier Screenshots.

    railway run --environment production python -m scripts.plan_b_status

Optional die Annotations-Arbeitsliste zusaetzlich als CSV:

    railway run --environment production python -m scripts.plan_b_status \\
        --csv worklist.csv

Warum ein Skript und nicht vier Abfragen: Die Fragen wiederholen sich
jede Woche, und beim Abtippen sind heute (10.08.) zwei Fehler passiert,
die Zeit gekostet haben — ein fehlender ``::jsonb``-Cast und ein Blick
in ``weeklyreport`` statt ``insight_report``. Beides steht hier nun
einmal richtig.

Die vier Bloecke
================

1. **Cron** — lief der letzte Lauf durch? Laufzeit, Fehlermeldung,
   erreichte Stages. Der Statuswert ``error`` kommt im Code
   ausschliesslich von Timeouts.
2. **Abdeckung** — Anteil Posts mit ``format`` (Klassifikation) und mit
   ``duration_seconds`` (Grundlage fuer Plan B).
3. **Artefakte** — wurden Briefs, Roundups und Wochenbriefings frisch
   erzeugt? Die Reihenfolge ihrer Zeitstempel spiegelt die Kette; bleibt
   ein Block alt, ist der Lauf vorher stehengeblieben.
4. **Plan-B-Arbeitsliste** — Query P4 aus
   ``TRAILER_INTELLIGENCE_STUFE5_PLAN_B.md``, Abschnitt 10.6: wilde
   Langform/Cutdown-Paare, bereinigt um Nicht-Trailer, Titel-Platzhalter
   und regionale Doppelungen.

Sicherheit: kein ``UPDATE``/``INSERT``/``DELETE``, nur ``SELECT``.
Bricht ab, wenn ``DATABASE_URL`` fehlt.
"""
from __future__ import annotations

import argparse
import csv
import sys
from typing import Any, Optional, Sequence

import sqlalchemy as sa
from sqlmodel import Session

from app.database import engine


# Grenzwerte der Plan-B-Auswertung (Abschnitt 10 des Plan-B-Dokuments).
TRAILER_MIN_SECONDS = 90
TRAILER_MAX_SECONDS = 180   # darueber: Featurette, Katalog-Clip, Stream
CUTDOWN_MIN_SECONDS = 15    # darunter zu wenige Einstellungen fuer Rhythmus
CUTDOWN_MAX_SECONDS = 59
PLACEHOLDER_TITLES = ("Unknown", "Star Wars")

# Gemessen an neun Backfill-Laeufen am 10.08.2026 (2.780 Posts): 3,7s
# pro Post mit ``--skip-vision``. Der frueher hier stehende Wert 5,6 kam
# aus dem abgebrochenen Cron-Lauf und war zu pessimistisch — dort lief
# die Stage neben Vision und Scrape.
SECONDS_PER_POST = 3.7

MIN_PAIRS_FOR_POC = 20


def _fetch(session: Session, sql: str) -> list[dict[str, Any]]:
    rows = session.exec(sa.text(sql))  # type: ignore[call-overload]
    return [dict(r._mapping) for r in rows]


def _fmt(value: Any, dash: str = "—") -> str:
    if value is None:
        return dash
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _table(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> str:
    if not rows:
        return "  (keine Zeilen)"
    widths = {
        c: max(len(c), *(len(_fmt(r.get(c))) for r in rows)) for c in columns
    }
    head = "  " + "  ".join(c.ljust(widths[c]) for c in columns)
    sep = "  " + "  ".join("-" * widths[c] for c in columns)
    body = [
        "  " + "  ".join(_fmt(r.get(c)).ljust(widths[c]) for c in columns)
        for r in rows
    ]
    return "\n".join([head, sep, *body])


# ---------- 1. Cron ----------

SQL_CRON = """
SELECT started_at,
       completed_at,
       status,
       round(EXTRACT(EPOCH FROM (completed_at - started_at))) AS laufzeit_s,
       error_message,
       jsonb_typeof(summary_json::jsonb)                      AS summary_typ,
       CASE WHEN jsonb_typeof(summary_json::jsonb) = 'object'
            THEN (SELECT count(*) FROM jsonb_object_keys(summary_json::jsonb))
       END                                                    AS stages
FROM creative_radar.cron_run
ORDER BY started_at DESC
LIMIT 5
"""


def _block_cron(session: Session) -> list[str]:
    rows = _fetch(session, SQL_CRON)
    out = ["## 1. Cron-Läufe (neueste zuerst)", ""]
    out.append(_table(rows, [
        "started_at", "status", "laufzeit_s", "stages", "error_message",
    ]))
    if not rows:
        return out
    last = rows[0]
    out.append("")
    if last["status"] == "completed":
        out.append("  Bewertung: letzter Lauf durchgelaufen.")
    elif last["status"] == "running":
        out.append("  Bewertung: laeuft gerade noch.")
    else:
        out.append(
            f"  Bewertung: letzter Lauf NICHT durchgelaufen "
            f"({last['status']}: {last['error_message']}). "
            f"``error`` kommt im Code nur von Timeouts."
        )
    return out


# ---------- 2. Abdeckung ----------

# ``analysis`` ist eine json-Spalte; der ?-Operator existiert nur fuer
# jsonb. Ohne den Cast bricht die Abfrage mit einem Operator-Fehler ab —
# genau daran ist die Handabfrage am 10.08. gescheitert.
SQL_COVERAGE = """
SELECT count(*)                                                        AS posts,
       count(*) FILTER (WHERE p.analysis::jsonb ? 'format')            AS mit_format,
       round(100.0 * count(*) FILTER (WHERE p.analysis::jsonb ? 'format')
             / NULLIF(count(*), 0), 1)                                 AS format_prozent,
       count(*) FILTER (WHERE p.duration_seconds IS NOT NULL)          AS mit_dauer,
       round(100.0 * count(*) FILTER (WHERE p.duration_seconds IS NOT NULL)
             / NULLIF(count(*), 0), 1)                                 AS dauer_prozent
FROM creative_radar.post p
WHERE p.detected_at > now() - interval '90 days'
"""


def _block_coverage(session: Session) -> list[str]:
    row = _fetch(session, SQL_COVERAGE)[0]
    backlog = int(row["posts"]) - int(row["mit_format"])
    stunden = backlog * SECONDS_PER_POST / 3600.0
    return [
        "## 2. Abdeckung im 90-Tage-Fenster",
        "",
        f"  Posts gesamt          {row['posts']}",
        f"  mit format            {row['mit_format']}  ({_fmt(row['format_prozent'], '0.0')} %)",
        f"  mit duration_seconds  {row['mit_dauer']}  ({_fmt(row['dauer_prozent'], '0.0')} %)",
        "",
        f"  Ohne format insgesamt: {backlog} Posts "
        f"(Obergrenze {stunden:.1f} h bei {SECONDS_PER_POST} s/Post)",
        "",
        "  ACHTUNG: Der Backfill erreicht davon nur die Posts der aktiven",
        "  Pairs — Kanaele ausserhalb bleiben unberuehrt. Die belastbare",
        "  Zahl liefert der Dry-Run, er aendert nichts:",
        "",
        "    railway ssh",
        "    cd /app && python -m scripts.backfill_post_analyzer",
        "",
        "  Danach je Pair (idempotent, jederzeit abbrechbar):",
        "    python -m scripts.backfill_post_analyzer \\",
        "        --apply --yes --skip-vision --pair <pair-key>",
    ]


# ---------- 3. Artefakte ----------

SQL_ARTEFACTS = """
SELECT 'insight_report (Pair-Briefs)' AS artefakt,
       count(*) FILTER (WHERE generated_at > now() - interval '24 hours') AS neu_24h,
       max(generated_at) AS zuletzt
FROM creative_radar.insight_report
UNION ALL
SELECT 'segment_roundup',
       count(*) FILTER (WHERE generated_at > now() - interval '24 hours'),
       max(generated_at) FROM creative_radar.segment_roundup
UNION ALL
SELECT 'cutter_weekly_briefing',
       count(*) FILTER (WHERE generated_at > now() - interval '24 hours'),
       max(generated_at) FROM creative_radar.cutter_weekly_briefing
UNION ALL
SELECT 'designer_weekly_briefing',
       count(*) FILTER (WHERE generated_at > now() - interval '24 hours'),
       max(generated_at) FROM creative_radar.designer_weekly_briefing
ORDER BY 1
"""


def _block_artefacts(session: Session) -> list[str]:
    rows = _fetch(session, SQL_ARTEFACTS)
    out = [
        "## 3. Erzeugte Artefakte (Ende der Cron-Kette)",
        "",
        _table(rows, ["artefakt", "neu_24h", "zuletzt"]),
        "",
    ]
    stale = [r["artefakt"] for r in rows if not r["neu_24h"]]
    if stale:
        out.append(
            f"  Ohne frischen Eintrag: {', '.join(stale)} — entweder "
            f"Cache-Hit (Inhalte der Ziel-KW existieren schon) oder die "
            f"Kette blieb davor stehen. ``zuletzt`` unterscheidet das."
        )
    else:
        out.append("  Alle vier Blöcke frisch erzeugt.")
    return out


# ---------- 4. Plan-B-Arbeitsliste (Query P4) ----------

SQL_WORKLIST = f"""
WITH post_titel AS (
  SELECT DISTINCT p.id, p.channel_id, p.duration_seconds, p.post_url, a.title_id
  FROM creative_radar.post p
  JOIN creative_radar.asset a   ON a.post_id = p.id
  JOIN creative_radar.channel c ON c.id = p.channel_id
  JOIN creative_radar.title t   ON t.id = a.title_id
  WHERE a.title_id IS NOT NULL
    AND c.platform = 'youtube'
    AND p.detected_at > now() - interval '90 days'
    AND p.duration_seconds IS NOT NULL
    AND t.title_original NOT IN {PLACEHOLDER_TITLES!r}
),
kandidaten AS (
  SELECT channel_id, title_id
  FROM post_titel
  GROUP BY 1, 2
  HAVING count(*) FILTER (WHERE duration_seconds
           BETWEEN {TRAILER_MIN_SECONDS} AND {TRAILER_MAX_SECONDS}) > 0
     AND count(*) FILTER (WHERE duration_seconds
           BETWEEN {CUTDOWN_MIN_SECONDS} AND {CUTDOWN_MAX_SECONDS}) > 0
),
lang AS (
  SELECT DISTINCT ON (channel_id, title_id) channel_id, title_id, post_url, duration_seconds
  FROM post_titel
  WHERE duration_seconds BETWEEN {TRAILER_MIN_SECONDS} AND {TRAILER_MAX_SECONDS}
  ORDER BY channel_id, title_id, duration_seconds ASC
),
kurz AS (
  SELECT DISTINCT ON (channel_id, title_id) channel_id, title_id, post_url, duration_seconds
  FROM post_titel
  WHERE duration_seconds BETWEEN {CUTDOWN_MIN_SECONDS} AND {CUTDOWN_MAX_SECONDS}
  ORDER BY channel_id, title_id, duration_seconds DESC
),
je_titel AS (
  SELECT DISTINCT ON (k.title_id)
         k.channel_id, k.title_id, k.duration_seconds AS kurz_s, k.post_url AS kurz_url
  FROM kandidaten kd
  JOIN kurz k ON k.channel_id = kd.channel_id AND k.title_id = kd.title_id
  ORDER BY k.title_id, k.duration_seconds DESC
)
SELECT row_number() OVER (ORDER BY t.title_original)            AS nr,
       regexp_replace(lower(t.title_original), '[^a-z0-9]+', '-', 'g') AS pair_key,
       t.title_original          AS titel,
       c.name                    AS kanal,
       round(l.duration_seconds)::int AS lang_s,
       l.post_url                AS langform_url,
       round(j.kurz_s)::int       AS kurz_s,
       j.kurz_url                AS kurzform_url
FROM je_titel j
JOIN lang l ON l.channel_id = j.channel_id AND l.title_id = j.title_id
JOIN creative_radar.channel c ON c.id = j.channel_id
JOIN creative_radar.title t   ON t.id = j.title_id
ORDER BY t.title_original
"""


def _block_worklist(
    session: Session, csv_path: Optional[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    rows = _fetch(session, SQL_WORKLIST)
    out = [
        f"## 4. Plan-B-Arbeitsliste — {len(rows)} unabhängige Paare",
        "",
        _table(rows, ["nr", "pair_key", "titel", "kanal", "lang_s", "kurz_s"]),
        "",
    ]
    if len(rows) >= MIN_PAIRS_FOR_POC:
        out.append(
            f"  Reicht für den PoC ({MIN_PAIRS_FOR_POC} Paare nötig). "
            f"Annotieren kann starten."
        )
    else:
        out.append(
            f"  ZU WENIG: {len(rows)} von {MIN_PAIRS_FOR_POC} Paaren. Nicht "
            f"anfangen — erst Abdeckung erhöhen oder die Grenzfälle "
            f"zwischen {TRAILER_MAX_SECONDS} und 240 s per Augenschein prüfen."
        )
    # Kurzformen nahe der Untergrenze verlieren die Rhythmus-Merkmale.
    knapp = [r["pair_key"] for r in rows if (r["kurz_s"] or 0) <= 20]
    if knapp:
        out.append(
            f"  Kurzform unter 20 s (Rhythmus-Merkmale bleiben evtl. leer): "
            f"{', '.join(knapp)}"
        )
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "nr", "pair_key", "titel", "kanal",
                    "lang_s", "langform_url", "kurz_s", "kurzform_url",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
        out.append(f"  CSV geschrieben: {csv_path}")
    return out, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--csv", default=None,
        help="Arbeitsliste zusaetzlich als CSV schreiben (Pfad).",
    )
    parser.add_argument(
        "--only",
        choices=("cron", "coverage", "artefacts", "worklist"),
        default=None,
        help="Nur einen Block ausgeben.",
    )
    args = parser.parse_args()

    blocks: list[list[str]] = []
    with Session(engine) as session:
        if args.only in (None, "cron"):
            blocks.append(_block_cron(session))
        if args.only in (None, "coverage"):
            blocks.append(_block_coverage(session))
        if args.only in (None, "artefacts"):
            blocks.append(_block_artefacts(session))
        if args.only in (None, "worklist"):
            block, _ = _block_worklist(session, args.csv)
            blocks.append(block)

    print("# Creative Radar — Statusbericht")
    for block in blocks:
        print()
        print("\n".join(block))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:  # fehlende DATABASE_URL o. ae.
        print(f"Fehler: {exc}", file=sys.stderr)
        raise SystemExit(1)
