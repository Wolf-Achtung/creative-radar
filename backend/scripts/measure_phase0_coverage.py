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


def _analysis_present(bind=None):
    """Filter-Clause "Post.analysis enthaelt echte Analyse-Daten" mit
    Dialect-Switch.

    **Bug-Befund (Wolf, 28.05.2026):** Die Vorgaenger-Version pruefte
    ``IS NOT NULL`` + cast-zu-Text-Stringvergleich. In Postgres-JSONB
    sind JSON-``null``-Werte SQL-NOT-NULL, der cast liefert den Text
    ``"null"``, das wurde durch ``!= 'null'`` rausgefiltert — aber leere
    Dicts ``{}`` (Persist-Skip-Pfade aus dem Analyzer) und nicht-Object-
    Werte rutschten als ``analyzed`` durch. SQL-Verifikation:
    `not_null_count=2494` vs `strict_count=1220`.

    Saubere Loesung mit Dialect-Switch:

    - **Postgres** (Production): ``jsonb_typeof(analysis::jsonb) = 'object'``.
      Native-JSONB-Check, trennscharf — JSON-null hat typeof ``"null"``,
      leeres Dict hat typeof ``"object"``, das aber faellt durch den
      zusaetzlichen ``!= '{}'``-Check raus.
    - **SQLite** (nur Tests): Substring-Match-Fallback. ``cast(analysis
      AS text) NOT IN ('null', '', '{}')``. Nicht perfekt — z.B. ``{"a":
      null}`` faellt durch — aber fuer kontrollierte Unit-Test-Fixtures
      reicht das. CI-Suite bleibt einheitlich, kein Postgres-only-Pfad.

    Test-Set, das in beiden Dialekten gruen sein muss:
    - ``NULL`` → not present
    - ``JSON null`` → not present
    - ``{}`` → not present
    - ``{"format": "trailer", ...}`` → **present**
    """
    text_value = sa.func.cast(Post.analysis, sa.Text)
    if bind is not None and bind.dialect.name == "postgresql":
        # jsonb_typeof + cast to jsonb. PostgreSQL akzeptiert
        # ``analysis::jsonb`` weil ``Post.analysis`` als JSON-Spalte
        # via Column(JSON) deklariert ist und Postgres die Column
        # transparent zu JSONB cast.
        analysis_as_jsonb = sa.cast(Post.analysis, sa.dialects.postgresql.JSONB)
        return sa.and_(
            Post.analysis.is_not(None),
            sa.func.jsonb_typeof(analysis_as_jsonb) == "object",
            text_value != "{}",
        )
    return sa.and_(
        Post.analysis.is_not(None),
        text_value != "null",
        text_value != "",
        text_value != "{}",
    )


def _measure_global_post_counts(session: Session) -> dict[str, int]:
    bind = session.get_bind()
    total = session.exec(select(sa.func.count()).select_from(Post)).one()
    with_analysis = session.exec(
        select(sa.func.count())
        .select_from(Post)
        .where(_analysis_present(bind))
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
    bind = session.get_bind()
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
            .where(_analysis_present(bind))
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


# ---- Teil 2: Channel-Aufstellung pro Pair -----------------------------


def _measure_per_pair_channels(session: Session) -> list[dict[str, Any]]:
    """Pro enabled Pair eine Liste der zugehoerigen Channels mit Posts-
    Total und analyzed-Count. Format passt zur Briefing-Vorgabe:

        platform/handle (market): N posts, M analyzed (X%)

    Sortiert pro Pair nach Coverage absteigend — die schwachen Channels
    fallen am Ende der Liste auf.
    """
    enabled = _enabled_pair_handles()
    if not enabled:
        return []
    all_handles = sorted({h for hs in enabled.values() for h in hs})
    channel_rows = session.exec(
        select(Channel).where(sa.func.lower(Channel.handle).in_(all_handles))
    ).all()
    channels_by_handle: dict[str, list[Channel]] = defaultdict(list)
    for ch in channel_rows:
        channels_by_handle[ch.handle.lower()].append(ch)

    bind = session.get_bind()
    out: list[dict[str, Any]] = []
    for pair_key, handles in enabled.items():
        channels: list[Channel] = []
        seen_ids: set = set()
        for h in handles:
            for ch in channels_by_handle.get(h, []):
                if ch.id not in seen_ids:
                    channels.append(ch)
                    seen_ids.add(ch.id)
        ch_rows: list[dict[str, Any]] = []
        for ch in channels:
            n_total = _unwrap(session.exec(
                select(sa.func.count()).select_from(Post)
                .where(Post.channel_id == ch.id)
            ).one())
            n_analyzed = _unwrap(session.exec(
                select(sa.func.count()).select_from(Post)
                .where(Post.channel_id == ch.id)
                .where(_analysis_present(bind))
            ).one())
            ch_rows.append({
                "platform": ch.platform,
                "handle": ch.handle,
                "market": ch.market.value if hasattr(ch.market, "value") else str(ch.market),
                "posts_total": int(n_total),
                "posts_analyzed": int(n_analyzed),
            })
        # Sort: Coverage absteigend; bei 0 Posts ans Ende.
        ch_rows.sort(
            key=lambda r: (
                -(r["posts_analyzed"] / r["posts_total"]) if r["posts_total"] > 0 else 1.0,
                -r["posts_total"],
            )
        )
        pair_total = sum(r["posts_total"] for r in ch_rows)
        out.append({
            "pair_key": pair_key,
            "channels": ch_rows,
            "pair_posts_total": pair_total,
        })
    return out


def _print_channel_breakdown(per_pair_channels: list[dict[str, Any]]) -> None:
    print("## Pair-Channel-Aufstellung")
    print()
    for row in per_pair_channels:
        chs = row["channels"]
        if not chs:
            print(f"{row['pair_key']} (0 channels, 0 posts total): kein Channel resolved")
            print()
            continue
        print(
            f"{row['pair_key']} ({len(chs)} channels, "
            f"{row['pair_posts_total']:,} posts total):"
        )
        for ch in chs:
            n_total = ch["posts_total"]
            n_anal = ch["posts_analyzed"]
            if n_total > 0:
                pct = n_anal * 100.0 / n_total
                print(
                    f"  - {ch['platform']}/{ch['handle']} ({ch['market']}): "
                    f"{n_total:,} posts, {n_anal:,} analyzed ({pct:.0f}%)"
                )
            else:
                print(
                    f"  - {ch['platform']}/{ch['handle']} ({ch['market']}): "
                    f"0 posts"
                )
        print()


# ---- Teil 3: Confidence-Verteilung pro Pair ---------------------------


CONFIDENCE_BUCKETS = [
    ("< 0.5", lambda c: c < 0.5),
    ("0.5-0.69", lambda c: 0.5 <= c < 0.7),
    ("0.7-0.79", lambda c: 0.7 <= c < 0.8),
    ("0.8-0.89", lambda c: 0.8 <= c < 0.9),
    ("≥ 0.9", lambda c: c >= 0.9),
]


def _measure_confidence_per_pair(session: Session) -> list[dict[str, Any]]:
    """Confidence-Verteilung pro Pair, nur ueber strict_analyzed Posts.
    Buckets aus dem Briefing.

    Lokales Python-Zaehlen statt SQL-CASE-WHEN, weil ``Post.analysis``
    JSON-Spalte ist und der Confidence-Subschluessel pro DB-Engine
    unterschiedlich angesprochen wird — einfacher pythonisch
    iterieren als zwei SQL-Pfade zu schreiben."""
    enabled = _enabled_pair_handles()
    if not enabled:
        return []
    all_handles = sorted({h for hs in enabled.values() for h in hs})
    channel_rows = session.exec(
        select(Channel.id, Channel.handle).where(
            sa.func.lower(Channel.handle).in_(all_handles)
        )
    ).all()
    channels_by_handle: dict[str, list] = defaultdict(list)
    for cid, ch in channel_rows:
        channels_by_handle[ch.lower()].append(cid)

    bind = session.get_bind()
    out: list[dict[str, Any]] = []
    for pair_key, handles in enabled.items():
        cids: set = set()
        for h in handles:
            for cid in channels_by_handle.get(h, []):
                cids.add(cid)
        bucket_counts: dict[str, int] = {name: 0 for name, _ in CONFIDENCE_BUCKETS}
        total = 0
        if cids:
            rows = session.exec(
                select(Post.analysis)
                .where(Post.channel_id.in_(cids))
                .where(_analysis_present(bind))
            ).all()
            for analysis in rows:
                # analysis ist ein dict (oder ein Tupel mit dem dict).
                if isinstance(analysis, tuple):
                    analysis = analysis[0]
                if not isinstance(analysis, dict):
                    continue
                conf = analysis.get("confidence")
                if conf is None:
                    continue
                try:
                    conf_f = float(conf)
                except (TypeError, ValueError):
                    continue
                for name, pred in CONFIDENCE_BUCKETS:
                    if pred(conf_f):
                        bucket_counts[name] += 1
                        total += 1
                        break
        out.append({
            "pair_key": pair_key,
            "buckets": bucket_counts,
            "total": total,
        })
    return out


def _print_confidence_distribution(per_pair_conf: list[dict[str, Any]]) -> None:
    print("## Confidence-Verteilung (nur strict_analyzed Posts)")
    print()
    print("| Pair | < 0.5 | 0.5-0.69 | 0.7-0.79 | 0.8-0.89 | ≥ 0.9 | Total |")
    print("|---|---|---|---|---|---|---|")
    for row in per_pair_conf:
        b = row["buckets"]
        cells = [str(b[name]) for name, _ in CONFIDENCE_BUCKETS]
        print(
            f"| {row['pair_key']} | {cells[0]} | {cells[1]} | "
            f"{cells[2]} | {cells[3]} | {cells[4]} | {row['total']} |"
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
        per_pair_channels = _measure_per_pair_channels(session)
        per_pair_conf = _measure_confidence_per_pair(session)

    print("# Stufe-2-Sprint — Phase-0-Coverage")
    print()
    print(f"Stichzeit: `{datetime.now(timezone.utc).isoformat()}`")
    print()
    _print_global(gp, gt)
    _print_per_pair(per_pair)
    _print_channel_breakdown(per_pair_channels)
    _print_confidence_distribution(per_pair_conf)
    _print_classification(gp, per_pair)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
