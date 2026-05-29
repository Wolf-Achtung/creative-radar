"""PAIRS-Channel-Row-Check (Sprint 29.05.2026).

Wolf-owned Read-only-Script. Diagnose-Befund vom 29.05.2026 (Befund 2.3 +
Befund 1.1 DB-Lese-Faelle): fuer jeden Handle in ``PAIRS`` (alle enabled
Pairs, alle Plattformen) pruefen, ob eine entsprechende DB-Row in
``creative_radar.channel`` existiert — und mit welchen ``mvp``/``active``-
Flags.

Loest zwei offene Befunde aus der vorangegangenen Diagnose:

- **Befund 1.1**: Die drei DB-Lese-Faelle (``20thcentury`` TT,
  ``sonyanimation`` IG, ``paramountplus`` YT) — gibt es Handle-Mismatches
  zwischen PAIRS und DB?
- **Befund 2.3**: Existenz-Check fuer alle ~50 PAIRS-Handles systematisch.

Read-only — keine Schreibvorgaenge. Default-Aufruf:

    cd backend && python -m scripts.pairs_channel_row_check

Optional: einzelnes Pair einschraenken (z.B. fuer Detail-Sichtkontrolle):

    cd backend && python -m scripts.pairs_channel_row_check --pair disney

Output ist ein Markdown-Snippet, das Wolf in den Chat kopiert.

Befund-Klassen (Spalte ``Befund``):

- ``ok`` — DB-Row existiert, ``mvp=True`` UND ``active=True``.
- ``mvp_disabled`` — DB-Row existiert, ``mvp=False`` (egal welcher
  ``active``-Wert). Apify scraped nicht, weil ``select_channels_for_cron``
  hart auf ``mvp=True`` filtert.
- ``inactive_disabled`` — DB-Row existiert, ``mvp=True`` aber
  ``active=False``. Apify scraped ebenfalls nicht.
- ``PAIRS->keine DB-Row`` — Handle steht in PAIRS, aber keine
  case-insensitive Match in ``channel.handle`` fuer die Plattform.
  Wolf-Sicht: Handle-Tippfehler in PAIRS, Channel-Row fehlt, oder
  PAIRS-Entry ist Geist.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from typing import Any, Iterable

import sqlalchemy as sa
from sqlmodel import Session, select

from app.database import engine
from app.models.entities import Channel
from app.services.insight_engine import PAIRS, _platforms_dict_for


# ---- PAIRS-Iteration --------------------------------------------------


def _iter_pair_channels(
    pairs_filter: str | None = None,
) -> Iterable[tuple[str, str, str, str]]:
    """Yield ``(pair_key, platform, handle, market)``-Tupel fuer alle
    enabled Pairs, alle Plattformen.

    Quelle ist ``_platforms_dict_for(pdef)`` — derselbe Aufloeser, den
    der Insight-Aggregator nutzt. So ist der Existenz-Check gegen
    dieselbe Channel-Menge, die spaeter auch in Briefs/Aggregaten landet.
    """
    for pair_key, pdef in PAIRS.items():
        if not pdef.get("enabled", False):
            continue
        if pairs_filter and pair_key != pairs_filter:
            continue
        platforms = _platforms_dict_for(pdef)
        for platform, specs in platforms.items():
            for spec in specs:
                handle = spec.get("handle")
                market = spec.get("market") or "—"
                if handle:
                    yield (pair_key, platform, handle, market)


# ---- DB-Lookup --------------------------------------------------------


def _lookup_channels_by_handle(
    session: Session, lookups: list[tuple[str, str]]
) -> dict[tuple[str, str], list[Channel]]:
    """Bulk-Lookup: fuer eine Liste ``[(platform, handle_lower), ...]``
    ein Mapping ``(platform, handle_lower) -> [Channel, ...]`` zurueck.

    Eine einzige Query mit ``WHERE LOWER(handle) IN (...) AND platform IN
    (...)`` — wir overfetch leicht (Cross-Product moeglich), filtern in
    Python auf das exakte Paar. Bei ~50 PAIRS-Handles ist das deutlich
    sparsamer als 50 Einzel-Queries.
    """
    if not lookups:
        return {}
    handles_lower = list({h for _, h in lookups})
    platforms = list({p for p, _ in lookups})

    rows = session.exec(
        select(Channel).where(
            sa.func.lower(Channel.handle).in_(handles_lower),
            Channel.platform.in_(platforms),
        )
    ).all()

    out: dict[tuple[str, str], list[Channel]] = defaultdict(list)
    for ch in rows:
        key = (ch.platform, (ch.handle or "").lower())
        out[key].append(ch)
    return out


# ---- Klassifizierung --------------------------------------------------


def _classify(matches: list[Channel]) -> tuple[str, Channel | None]:
    """Eine Treffer-Liste in eine Befund-Klasse abbilden. Bei mehreren
    Treffern bevorzugen wir die ``mvp=True``-Row (die wuerde der Cron
    real scrapen); danach die ``active=True``-Row.

    Returns ``(befund, gewaehlte_row)``. Wenn die Liste leer ist,
    ist die Klasse ``PAIRS->keine DB-Row`` und die Row ``None``.
    """
    if not matches:
        return ("PAIRS->keine DB-Row", None)

    # Bei mehreren Matches: bevorzuge mvp=True, dann active=True, dann
    # aelteste (created_at). Stabile Sortierung.
    def _rank(ch: Channel) -> tuple[int, int]:
        return (0 if ch.mvp else 1, 0 if ch.active else 1)

    chosen = sorted(matches, key=_rank)[0]

    if chosen.mvp and chosen.active:
        return ("ok", chosen)
    if not chosen.mvp:
        return ("mvp_disabled", chosen)
    # mvp=True, active=False
    return ("inactive_disabled", chosen)


# ---- Markdown-Output --------------------------------------------------


def _print_markdown(
    rows_by_pair: dict[str, list[dict[str, Any]]],
    summary: dict[str, int],
) -> None:
    """Markdown-Snippet ausgeben. Pro Pair eine Tabelle, am Ende eine
    Summary."""
    print("# PAIRS-Channel-Row-Check")
    print()
    print(
        "Read-only Diagnose: pro PAIRS-Handle DB-Row-Existenz + "
        "`mvp`/`active`-Flags."
    )
    print()

    for pair_key in sorted(rows_by_pair):
        rows = rows_by_pair[pair_key]
        print(f"## {pair_key} ({len(rows)} channels)")
        print()
        print("| Plt/Handle | Market | DB-Row | mvp | active | Befund |")
        print("|---|---|---|---|---|---|")
        for row in rows:
            plt_handle = f"{row['platform'][:2]}/{row['pairs_handle']}"
            db_marker = "—"
            mvp_marker = "—"
            active_marker = "—"
            if row["chosen"] is not None:
                ch = row["chosen"]
                # Wenn der gespeicherte Handle case-different ist,
                # zeigen wir das mit an — z.B. ``WarnerBrosPictures``
                # vs. PAIRS-Eintrag ``WarnerBrosPictures``.
                stored = ch.handle or "—"
                pid_short = str(ch.id)[:8]
                if stored.lower() == row["pairs_handle"].lower() and stored != row["pairs_handle"]:
                    db_marker = f"OK ({pid_short}, casing: `{stored}`)"
                else:
                    db_marker = f"OK ({pid_short})"
                mvp_marker = "T" if ch.mvp else "F"
                active_marker = "T" if ch.active else "F"
            else:
                db_marker = "—"
            print(
                f"| {plt_handle} | {row['market']} | {db_marker} | "
                f"{mvp_marker} | {active_marker} | {row['befund']} |"
            )
        print()

    print("## Summary")
    print()
    total = summary["total"]
    print(f"- Total PAIRS-Channels: {total}")
    db_present = summary["ok"] + summary["mvp_disabled"] + summary["inactive_disabled"]
    if total > 0:
        pct = db_present * 100.0 / total
        print(f"- DB-Row vorhanden:     {db_present} ({pct:.1f} %)")
    else:
        print(f"- DB-Row vorhanden:     {db_present}")
    print(f"  - davon ok:             {summary['ok']}")
    print(f"  - davon mvp_disabled:   {summary['mvp_disabled']}")
    print(f"  - davon inactive_disabled: {summary['inactive_disabled']}")
    print(f"- PAIRS->keine DB-Row:    {summary['PAIRS->keine DB-Row']} (Wolf-Sicht)")


# ---- Orchestrator -----------------------------------------------------


def run_check(session: Session, pairs_filter: str | None = None) -> int:
    """Hauptroutine: PAIRS auflisten, DB-Lookup, klassifizieren,
    Markdown drucken. Returns 0 bei Erfolg.

    Read-only: nur ``select`` auf ``channel``. Keine Schreibvorgaenge.
    """
    tuples = list(_iter_pair_channels(pairs_filter))
    if not tuples:
        if pairs_filter:
            print(f"# PAIRS-Channel-Row-Check")
            print()
            print(f"Kein enabled Pair mit Key `{pairs_filter}` gefunden — Abbruch.")
            return 1
        print("# PAIRS-Channel-Row-Check")
        print()
        print("Keine enabled Pairs in PAIRS-Definition gefunden.")
        return 0

    lookups = [(plt, handle.lower()) for _, plt, handle, _ in tuples]
    matches_by_key = _lookup_channels_by_handle(session, lookups)

    rows_by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    summary: dict[str, int] = defaultdict(int)
    summary["total"] = 0
    summary["ok"] = 0
    summary["mvp_disabled"] = 0
    summary["inactive_disabled"] = 0
    summary["PAIRS->keine DB-Row"] = 0

    for pair_key, platform, handle, market in tuples:
        key = (platform, handle.lower())
        matches = matches_by_key.get(key, [])
        befund, chosen = _classify(matches)
        rows_by_pair[pair_key].append({
            "platform": platform,
            "pairs_handle": handle,
            "market": market,
            "befund": befund,
            "chosen": chosen,
        })
        summary["total"] += 1
        summary[befund] += 1

    _print_markdown(rows_by_pair, summary)
    return 0


# ---- CLI --------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--pair",
        default=None,
        help="Nur ein einzelnes Pair pruefen (z.B. --pair disney).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    with Session(engine) as session:
        return run_check(session, pairs_filter=args.pair)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
