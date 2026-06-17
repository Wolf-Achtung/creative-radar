#!/usr/bin/env python3
"""Diagnose Stufe-2 / Weg-1 — Title-Katalog-Freshness & Sync-Scope-Audit.

NUR DIAGNOSE. Read-only. Drei Aggregat-Queries, schreibt nichts, fasst keine
Engine an.

Hintergrund: Die sub2-Wurzel (96 % unknown) ist primaer Katalog-
VOLLSTAENDIGKEIT. Der TMDb-Sync (``title_sync.sync_titles_from_tmdb``) laeuft
je Cron, aber mit zwei harten Scope-Limitern: Release-Datum-Fenster
``[-8 Wochen, +24 Wochen]`` (title_sync.py:109-118) und 3-Seiten-Popularitaets-
Cap (~60 Titel/Region/Lauf, tmdb_client.py:64,101). Dieses Script belegt das am
echten Datenstand:

  (1) active_titles: count + aeltester/juengster created_at + Serien-/TMDb-Anteil
      → frisch befuellt oder eingefroren?
  (2) release_date-Spannweite (US/DE) der aktiven Titel
      → klebt sie um [heute-8w, heute+24w] (= Fenster-Artefakt) oder breit?
  (3) letzte 10 title_sync_run-Rows (created_at, markets, date_from/to,
      fetched/upserted, status)
      → laeuft der Sync regelmaessig? Immer dasselbe enge Fenster?

Aufruf (lokal gegen Prod, analog Sicht-Check):
    source ~/.creative-radar/db.env && \\
        DATABASE_URL="$CR_DB_URL" python -m scripts.diag_catalog_freshness
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"diag_catalog_freshness: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _has_db_config() -> bool:
    if any(os.environ.get(v) for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")):
        return True
    return all(os.environ.get(v) for v in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE"))


def _fmt(value) -> str:
    if value is None:
        return "<NULL>"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _age_days(value) -> str:
    if not isinstance(value, datetime):
        return ""
    now = datetime.now(timezone.utc)
    ref = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return f"  (vor {(now - ref).days} Tagen)"


def main() -> None:
    if not _has_db_config():
        _die("Keine DB-Config in der Umgebung. Setze DATABASE_URL. Aufruf: "
             "DATABASE_URL=\"$CR_DB_URL\" python -m scripts.diag_catalog_freshness")

    import sqlalchemy as sa
    from sqlmodel import Session, select

    from app.database import engine
    from app.models.entities import Title, TitleSyncRun

    now = datetime.now(timezone.utc)
    print(f"# Title-Katalog-Freshness-Audit — now={now.isoformat()}")
    print("# Erwartung bei 'Scope zu eng': frische Sync-Laeufe, aber immer dasselbe")
    print("# [-8w,+24w]-Fenster; release_date-Spannweite klebt um [heute-8w, heute+24w].")
    print()

    with Session(engine) as session:
        # --- (1) active_titles: count + created_at-Spannweite + Anteile ---
        row = session.exec(
            select(
                sa.func.count(),
                sa.func.min(Title.created_at),
                sa.func.max(Title.created_at),
                sa.func.min(Title.updated_at),
                sa.func.max(Title.updated_at),
                sa.func.sum(sa.case((Title.content_type == "Series", 1), else_=0)),
                sa.func.sum(sa.case((Title.source == "TMDb", 1), else_=0)),
                sa.func.sum(sa.case((Title.tmdb_id.is_not(None), 1), else_=0)),
            ).where(Title.active == True)  # noqa: E712
        ).one()
        (count, c_min, c_max, u_min, u_max, series_n, tmdb_src_n, tmdb_id_n) = row
        print("## (1) Aktive Titel — Count & Freshness")
        print(f"  active_titles        : {count}")
        print(f"  created_at oldest    : {_fmt(c_min)}{_age_days(c_min)}")
        print(f"  created_at newest    : {_fmt(c_max)}{_age_days(c_max)}")
        print(f"  updated_at oldest    : {_fmt(u_min)}")
        print(f"  updated_at newest    : {_fmt(u_max)}{_age_days(u_max)}")
        print(f"  content_type=Series  : {series_n or 0}")
        print(f"  source='TMDb'        : {tmdb_src_n or 0}")
        print(f"  tmdb_id IS NOT NULL  : {tmdb_id_n or 0}")
        print()

        # --- (2) release_date-Spannweite (US/DE) ---
        rd = session.exec(
            select(
                sa.func.min(Title.release_date_us),
                sa.func.max(Title.release_date_us),
                sa.func.count(Title.release_date_us),
                sa.func.min(Title.release_date_de),
                sa.func.max(Title.release_date_de),
                sa.func.count(Title.release_date_de),
            ).where(Title.active == True)  # noqa: E712
        ).one()
        (us_min, us_max, us_cnt, de_min, de_max, de_cnt) = rd
        print("## (2) Release-Datum-Spannweite (aktive Titel)")
        print(f"  release_date_us      : {_fmt(us_min)} .. {_fmt(us_max)}   (gesetzt: {us_cnt})")
        print(f"  release_date_de      : {_fmt(de_min)} .. {_fmt(de_max)}   (gesetzt: {de_cnt})")
        print(f"  Referenz-Fenster -8w/+24w um heute: "
              f"{(now.date() - timedelta(weeks=8))} .. {(now.date() + timedelta(weeks=24))}")
        print()

        # --- (3) letzte 10 title_sync_run ---
        runs = session.exec(
            select(TitleSyncRun).order_by(TitleSyncRun.created_at.desc()).limit(10)
        ).all()
        print("## (3) Letzte title_sync_run-Laeufe (max 10)")
        if not runs:
            print("  <keine Sync-Laeufe in title_sync_run> → Sync hat nie eine Audit-Row geschrieben.")
        else:
            print(f"  {'created_at':<28} {'markets':<12} {'date_from':<12} {'date_to':<12} "
                  f"{'fetched':>7} {'upserted':>8} {'status':<8}")
            for r in runs:
                markets = ",".join(r.markets) if isinstance(r.markets, list) else _fmt(r.markets)
                print(f"  {_fmt(r.created_at):<28} {markets:<12} "
                      f"{_fmt(r.date_from):<12} {_fmt(r.date_to):<12} "
                      f"{r.fetched_count:>7} {r.upserted_count:>8} {r.status:<8}")
            # Kadenz-Hinweis: Abstand zwischen den juengsten zwei Laeufen.
            if len(runs) >= 2 and isinstance(runs[0].created_at, datetime) and isinstance(runs[1].created_at, datetime):
                gap = runs[0].created_at - runs[1].created_at
                print(f"  → Abstand juengste zwei Laeufe: {gap}")
        print()
        print("# Lesehilfe:")
        print("#  - created_at newest 'frisch' + Sync-Laeufe alle ~3 Tage → Sync laeuft (kein Seed).")
        print("#  - date_from/date_to in (3) konstant + release_date-Spannweite (2) eng um das")
        print("#    Referenz-Fenster → Scope-zu-eng bestaetigt (Back-Katalog/alte Serien fehlen).")


if __name__ == "__main__":
    main()
