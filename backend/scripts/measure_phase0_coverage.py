"""Phase-0-Coverage-Messung fuer den Stufe-2-Sprint (28.05.2026).

Wolf-owned-Script. Liefert die Daten, auf deren Basis die Sprint-
Reihenfolge fuer das praeskriptive Format+Cadence-Modul festgelegt
wird (Briefing: "Diagnose-First, Claude Code waehlt Reihenfolge nach
realer Coverage").

Read-only — keine Schreibvorgaenge auf der DB. Default-Aufruf:

    cd backend && python -m scripts.measure_phase0_coverage

Output ist ein Markdown-Snippet, das Wolf in den Chat kopiert. Auf
dessen Basis schlaegt Claude Code die Reihenfolge vor:

- Analyzer-Coverage < 30 %  → P2 (Backfill) zuerst.
- 30-60 %                    → P1+P3 parallel, P2 im Hintergrund.
- > 60 %                     → P1+P3 zuerst, P2 optional.

Gemessen werden:

1. **Analyzer-Coverage**: Anteil Posts mit ``Post.analysis IS NOT NULL``,
   gesamt und pro enabled Pair. Definiert die Sample-Groesse fuer die
   spaeteren ``format × activation_rate``- und
   ``lifecycle_stage × activation_rate``-Cross-Tabs.

2. **Release-Date-Coverage**: Anteil ``Title`` mit ``release_date_de``
   bzw. ``release_date_us``. Definiert, wieviele Posts ueberhaupt einen
   ``days_to_release``-Anker bekommen koennen.

3. **Title-zu-Post-Kopplung**: Anteil Posts der enabled Pairs, deren
   Asset ein ``title_id`` traegt UND dessen Title ein Release-Date hat
   — das ist die effektive Cadence-Sample-Groesse pro Pair.

4. **Duration-Coverage**: Anteil Posts mit ``duration_seconds NOT NULL``
   — Format-Cross-Tab fuer Videos.

5. **Sample-Groesse pro Pair fuer die kommenden 7d/30d-Fenster**: wieviele
   Posts der enabled Pairs liegen im aktuellen 7d-Window? Wenn pro
   Pair < 10 Posts im 7d-Window, faellt das Pair ggf. unter die
   Ehrlich-Klausel (Sample-Size < 3 pro Cross-Tab-Wert) ohne dass
   der Aggregator je etwas liefert.

Sicherheits-Anforderungen:
- KEIN Schreiben.
- Nutzt bestehende ``app.database.engine`` + ``Session``.
- Bricht klar mit Fehler ab, wenn ``DATABASE_URL`` fehlt (kein Sandbox-
  Risiko).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlmodel import Session, select

from app.database import engine
from app.models.entities import Asset, Channel, Post, Title
from app.services.insight_engine import PAIRS, _platforms_dict_for


# ---- Helpers ----------------------------------------------------------


def _enabled_pair_handles() -> dict[str, list[str]]:
    """Pro Pair-Key die Liste der (lowercased) Channel-Handles ueber
    alle Plattformen. Spiegel des Aggregator-Patterns in #195 — gleiche
    Quelle, damit die Coverage-Messung die spaeteren Aggregator-
    Sample-Groessen korrekt vorhersagt."""
    out: dict[str, list[str]] = {}
    for pair_key, pdef in PAIRS.items():
        if not pdef.get("enabled", False):
            continue
        handles: list[str] = []
        for specs in _platforms_dict_for(pdef).values():
            for spec in specs:
                h = spec.get("handle")
                if h:
                    handles.append(h.lower())
        out[pair_key] = handles
    return out


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{part * 100.0 / total:.1f} %"


# ---- Measurements -----------------------------------------------------


def _analysis_present():
    """Filter-Clause "Post.analysis enthaelt echte Analyse-Daten".

    Engine-Quirk: Postgres-JSONB speichert ``None`` als echtes SQL NULL
    → ``IS NOT NULL`` ist trennscharf. SQLite mit SQLModel-JSON-Column
    serialisiert ``None`` als JSON-string ``"null"`` → ``IS NOT NULL``
    ist immer true.

    Robust gegen beide: ``IS NOT NULL`` PLUS cast-to-text-Check, damit
    die JSON-"null"-Strings rausfallen. In Postgres ist der zweite
    Check redundant (alle echten Rows haben JSON-Objekte), in SQLite
    notwendig.
    """
    text_value = sa.func.cast(Post.analysis, sa.Text)
    return sa.and_(
        Post.analysis.is_not(None),
        text_value != "null",
        text_value != "",
    )


def _measure_global_post_counts(session: Session) -> dict[str, int]:
    total = session.exec(select(sa.func.count()).select_from(Post)).one()
    with_analysis = session.exec(
        select(sa.func.count())
        .select_from(Post)
        .where(_analysis_present())
    ).one()
    with_duration = session.exec(
        select(sa.func.count())
        .select_from(Post)
        .where(Post.duration_seconds.is_not(None))
    ).one()
    with_published = session.exec(
        select(sa.func.count())
        .select_from(Post)
        .where(Post.published_at.is_not(None))
    ).one()
    return {
        "posts_total": int(_unwrap(total)),
        "posts_with_analysis": int(_unwrap(with_analysis)),
        "posts_with_duration": int(_unwrap(with_duration)),
        "posts_with_published": int(_unwrap(with_published)),
    }


def _measure_global_title_counts(session: Session) -> dict[str, int]:
    total = session.exec(select(sa.func.count()).select_from(Title)).one()
    de = session.exec(
        select(sa.func.count())
        .select_from(Title)
        .where(Title.release_date_de.is_not(None))
    ).one()
    us = session.exec(
        select(sa.func.count())
        .select_from(Title)
        .where(Title.release_date_us.is_not(None))
    ).one()
    either = session.exec(
        select(sa.func.count())
        .select_from(Title)
        .where(
            sa.or_(
                Title.release_date_de.is_not(None),
                Title.release_date_us.is_not(None),
            )
        )
    ).one()
    return {
        "titles_total": int(_unwrap(total)),
        "titles_with_release_de": int(_unwrap(de)),
        "titles_with_release_us": int(_unwrap(us)),
        "titles_with_release_either": int(_unwrap(either)),
    }


def _measure_per_pair(session: Session, now: datetime) -> list[dict[str, Any]]:
    """Pro enabled Pair:
    - Posts-Total ueber alle Channels
    - Posts mit analysis IS NOT NULL
    - Posts der letzten 7 Tage (sample-size-Vorhersage)
    - Posts mit title_id gesetzt
    - Posts mit title_id UND Title.release_date_either NOT NULL
      (effective Cadence-Sample-Size)
    """
    enabled = _enabled_pair_handles()
    if not enabled:
        return []

    # Alle handles in einem Schritt → channel_id-Map.
    all_handles = sorted({h for hs in enabled.values() for h in hs})
    channel_rows = session.exec(
        select(Channel.id, Channel.handle).where(
            sa.func.lower(Channel.handle).in_(all_handles)
        )
    ).all()
    channels_by_handle: dict[str, list] = defaultdict(list)
    for cid, ch in channel_rows:
        channels_by_handle[ch.lower()].append(cid)

    week_start = now - timedelta(days=7)
    out: list[dict[str, Any]] = []
    for pair_key, handles in enabled.items():
        cids: set = set()
        for h in handles:
            for cid in channels_by_handle.get(h, []):
                cids.add(cid)
        if not cids:
            out.append({
                "pair_key": pair_key,
                "posts_total": 0,
                "posts_with_analysis": 0,
                "posts_in_7d": 0,
                "posts_with_title": 0,
                "posts_with_release_date": 0,
                "no_channels_resolved": True,
            })
            continue

        # COUNT-Queries pro Pair (5 schmale Aggregat-Queries pro Pair —
        # Phase-0-Script darf das, ist read-only und einmalig).
        total = _unwrap(session.exec(
            select(sa.func.count()).select_from(Post).where(Post.channel_id.in_(cids))
        ).one())
        with_analysis = _unwrap(session.exec(
            select(sa.func.count()).select_from(Post)
            .where(Post.channel_id.in_(cids))
            .where(_analysis_present())
        ).one())
        in_7d = _unwrap(session.exec(
            select(sa.func.count()).select_from(Post)
            .where(Post.channel_id.in_(cids))
            .where(
                sa.or_(
                    sa.and_(Post.published_at.is_not(None), Post.published_at >= week_start),
                    sa.and_(Post.published_at.is_(None), Post.detected_at >= week_start),
                )
            )
        ).one())

        # Posts-mit-Title via Asset-Join
        with_title = _unwrap(session.exec(
            select(sa.func.count(sa.distinct(Post.id))).select_from(Post)
            .join(Asset, Asset.post_id == Post.id)
            .where(Post.channel_id.in_(cids))
            .where(Asset.title_id.is_not(None))
        ).one())

        # Posts-mit-Title-mit-Release-Date via doppel-Join
        with_release = _unwrap(session.exec(
            select(sa.func.count(sa.distinct(Post.id))).select_from(Post)
            .join(Asset, Asset.post_id == Post.id)
            .join(Title, Title.id == Asset.title_id)
            .where(Post.channel_id.in_(cids))
            .where(
                sa.or_(
                    Title.release_date_de.is_not(None),
                    Title.release_date_us.is_not(None),
                )
            )
        ).one())

        out.append({
            "pair_key": pair_key,
            "posts_total": int(total),
            "posts_with_analysis": int(with_analysis),
            "posts_in_7d": int(in_7d),
            "posts_with_title": int(with_title),
            "posts_with_release_date": int(with_release),
            "no_channels_resolved": False,
        })
    return out


def _unwrap(value):
    """``session.exec(select(count())).one()`` liefert je nach
    SQLModel-Version entweder ``int`` direkt oder ``(int,)`` — defensiv."""
    if isinstance(value, tuple):
        return value[0]
    return value


# ---- Output -----------------------------------------------------------


def _print_global(global_posts: dict, global_titles: dict) -> None:
    print("## Globale Coverage")
    print()
    print("| Signal | Anteil | Total |")
    print("|---|---|---|")
    n_posts = global_posts["posts_total"]
    print(
        f"| Posts mit `analysis` (Format/Tone/Purpose/Lifecycle) | "
        f"{_pct(global_posts['posts_with_analysis'], n_posts)} | "
        f"{global_posts['posts_with_analysis']:,} / {n_posts:,} |"
    )
    print(
        f"| Posts mit `duration_seconds` | "
        f"{_pct(global_posts['posts_with_duration'], n_posts)} | "
        f"{global_posts['posts_with_duration']:,} / {n_posts:,} |"
    )
    print(
        f"| Posts mit `published_at` | "
        f"{_pct(global_posts['posts_with_published'], n_posts)} | "
        f"{global_posts['posts_with_published']:,} / {n_posts:,} |"
    )
    n_titles = global_titles["titles_total"]
    print(
        f"| Titles mit `release_date_de` | "
        f"{_pct(global_titles['titles_with_release_de'], n_titles)} | "
        f"{global_titles['titles_with_release_de']:,} / {n_titles:,} |"
    )
    print(
        f"| Titles mit `release_date_us` | "
        f"{_pct(global_titles['titles_with_release_us'], n_titles)} | "
        f"{global_titles['titles_with_release_us']:,} / {n_titles:,} |"
    )
    print(
        f"| Titles mit `release_date_*` (DE oder US) | "
        f"{_pct(global_titles['titles_with_release_either'], n_titles)} | "
        f"{global_titles['titles_with_release_either']:,} / {n_titles:,} |"
    )
    print()


def _print_per_pair(per_pair: list[dict]) -> None:
    print("## Pro Pair (enabled)")
    print()
    print(
        "| Pair | Posts total | Analyzer-Coverage | Posts 7d | Mit Title | "
        "Mit Release-Date |"
    )
    print("|---|---|---|---|---|---|")
    for row in per_pair:
        if row.get("no_channels_resolved"):
            print(f"| `{row['pair_key']}` | — | — | — | — | — (keine Channels in DB) |")
            continue
        n = row["posts_total"]
        print(
            f"| `{row['pair_key']}` | "
            f"{n:,} | "
            f"{_pct(row['posts_with_analysis'], n)} ({row['posts_with_analysis']:,}) | "
            f"{row['posts_in_7d']:,} | "
            f"{_pct(row['posts_with_title'], n)} ({row['posts_with_title']:,}) | "
            f"{_pct(row['posts_with_release_date'], n)} ({row['posts_with_release_date']:,}) |"
        )
    print()


def _print_classification(global_posts: dict, per_pair: list[dict]) -> None:
    n = global_posts["posts_total"]
    if n == 0:
        print("**Analyzer-Coverage gesamt: keine Posts in DB.**")
        return
    pct = global_posts["posts_with_analysis"] * 100.0 / n
    print("## Sprint-Reihenfolge — Vorschlag laut Briefing-Schwelle")
    print()
    print(f"Analyzer-Coverage gesamt: **{pct:.1f} %**")
    if pct < 30:
        print()
        print("→ **< 30 %**: P2 (Backfill) zuerst. Aggregator-Sample-Groessen waeren sonst")
        print("  durchgaengig unter der 3-Sample-Ehrlich-Klausel.")
    elif pct < 60:
        print()
        print("→ **30-60 %**: P1+P3 parallel; P2 als Backfill-Cron im Hintergrund.")
    else:
        print()
        print("→ **> 60 %**: P1+P3 zuerst; P2 ist optional.")

    print()
    # Pairs unter Schwelle: 7d-Sample-Groesse + Coverage
    flagged = [
        r for r in per_pair
        if not r.get("no_channels_resolved")
        and (r["posts_in_7d"] < 10 or (r["posts_total"] > 0 and r["posts_with_analysis"] * 100.0 / r["posts_total"] < 30))
    ]
    if flagged:
        print("**Pairs unter Ehrlich-Klausel-Risiko** (7d-Window < 10 ODER Coverage < 30 %):")
        for r in flagged:
            cov = r["posts_with_analysis"] * 100.0 / max(r["posts_total"], 1)
            print(f"- `{r['pair_key']}`: 7d={r['posts_in_7d']}, coverage={cov:.1f} %")
    else:
        print("Kein Pair unter Ehrlich-Klausel-Risiko.")


def main() -> int:
    with Session(engine) as session:
        now = datetime.now(timezone.utc)
        gp = _measure_global_post_counts(session)
        gt = _measure_global_title_counts(session)
        per_pair = _measure_per_pair(session, now)

    print("# Stufe-2-Sprint — Phase-0-Coverage")
    print()
    print(f"Stichzeit: `{datetime.now(timezone.utc).isoformat()}`")
    print()
    _print_global(gp, gt)
    _print_per_pair(per_pair)
    _print_classification(gp, per_pair)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
