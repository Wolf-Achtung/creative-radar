"""Post-Analyzer-Backfill auf alle enabled-Pair-Posts (Stufe-2-Sprint, P2).

Wolf-owned-Script. Briefing-Pflicht: Coverage-Re-Messung nach erfolgreichem
Backfill muss > 60 % global UND > 50 % pro enabled Pair zeigen, sonst
fallen die spaeteren P3-Cross-Tabs durchgaengig unter die Ehrlich-Klausel
(Sample-Size < 3).

Phase-0-Ausgangslage (28.05.2026): 0,4 % Analyzer-Coverage global,
~1.220 Posts der enabled Pairs ohne ``Post.analysis``. Das Cron-Limit
(``cron_vision_max_assets_per_run`` fuer Vision, ``limit=50/500`` im
``/api/admin/analyze/{channel_id}``-Endpoint) macht den Backfill in der
HTTP-Schiene umstaendlich — ein CLI-Lauf im Railway-Shell ist sauberer.

Architektur-Gerade:
- Nutzt den bestehenden ``analyze_post(session, post)``-Service. Der ist
  schon idempotent (kein Pre-Skip — die Caller-Schicht macht den
  ``last_analyzed_at IS NULL``-Filter). Commit-pro-Post-Pattern lebt
  bereits darin.
- Kein neues Endpoint, kein Background-Task, kein HTTP-Timeout-Risiko.
- Konsistent mit Wolf-owned-Pattern aus #201 (cleanup_open_title_candidates)
  und #202 (measure_phase0_coverage): Default = Dry-Run, ``--apply --yes``
  schreibt, ``--pair`` filtert.

Drei Modi
=========

**Default (Dry-Run):**

    cd backend && python -m scripts.backfill_post_analyzer

Listet pro enabled Pair, wieviele Posts ohne Analyse zu backfillen
waeren. AENDERT NICHTS. Sicher, beliebig oft wiederholbar.

**--apply --yes:**

    cd backend && python -m scripts.backfill_post_analyzer --apply --yes

Geht alle Pair-Posts durch, ruft ``analyze_post`` pro Post (Haiku +
Sonnet-Vision + Sonnet-Klassifikation), commited pro Post.
Pro-Pair-Fortschritts-Logging. Idempotent: skippt Posts mit
``last_analyzed_at IS NOT NULL`` (= bereits analysiert).

**--pair <pair_key>:**

Eingrenzen auf ein einzelnes Pair (z.B. fuer Re-Try nach Errors).
Funktioniert sowohl mit Dry-Run als auch mit --apply.

Sicherheits-Garantien
=====================

- Default-Modus aendert nichts.
- Idempotent: ``last_analyzed_at IS NOT NULL``-Filter laesst bereits
  analysierte Posts in Ruhe. Re-Runs sind sicher.
- Resume bei Crash: ``analyze_post`` committed pro Post (etabliert in
  Sprint 5.3.1) — ein Crash-mid-Batch laesst die bereits-fertigen
  Posts intakt; Re-Run macht weiter, wo der vorherige Lauf abbrach.
- Auth-Fehler (AnthropicAuthError) bricht hart ab, weil das ein
  Konfig-Problem ist (kein Per-Post-Pfad).
- Andere Per-Post-Fehler werden geloggt und uebersprungen — bricht
  den Gesamt-Backfill nicht ab.

Wolf-Ausfuehrungs-Pfad
======================

1. Dry-Run pruefen:

       cd backend && python -m scripts.backfill_post_analyzer

   Output zeigt pro Pair, wieviele Posts zu backfillen waeren.

2. Apply (wenn die Zahlen passen):

       cd backend && python -m scripts.backfill_post_analyzer --apply --yes

   Laeuft typischerweise 30-120 Minuten je nach Pair-Set (1.220 Posts
   * je 3 Anthropic-Calls). Pro-Pair-Fortschritts-Logs landen im
   Standard-Logger.

3. Coverage-Re-Messung (Pflicht-Gate, separate Wolf-Pfad):

       cd backend && python -m scripts.measure_phase0_coverage

   Wenn Coverage gesamt > 60 % UND > 50 % pro enabled Pair → P1+P3
   freigegeben. Sonst: Wolf-Ping mit Befund (Re-Try, Teilmenge,
   Pair-Filter).

4. Re-Try einzelner Pairs (optional):

       cd backend && python -m scripts.backfill_post_analyzer --apply --yes --pair warnerbros
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict
from typing import Optional

from sqlmodel import Session, select

from app.database import engine
from app.models.entities import Channel, Post
from app.services.insight_engine import PAIRS, _platforms_dict_for

# Lazy-Import des Analyzers: vermeidet Import-Crash, wenn Anthropic-SDK
# unaufaufgeloest ist (selber Pfad wie das Admin-Endpoint).
def _import_analyzer():
    from app.services.anthropic_client import AnthropicAuthError, is_anthropic_configured
    from app.services.post_analyzer import analyze_post
    return analyze_post, AnthropicAuthError, is_anthropic_configured


logger = logging.getLogger(__name__)


# ---- Pair / Channel-Resolution ---------------------------------------


def _enabled_pair_handles() -> dict[str, list[str]]:
    """Gleicher Mechanismus wie ``measure_phase0_coverage`` — pro
    enabled Pair die Channel-Handles (lowercased) ueber alle
    Plattformen."""
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


def _channels_for_pair_keys(
    session: Session, pair_keys: list[str],
) -> dict[str, list]:
    """Liefert pro pair_key die Liste der channel_ids."""
    handles_per_pair = _enabled_pair_handles()
    all_handles = sorted({
        h for pk in pair_keys for h in handles_per_pair.get(pk, [])
    })
    if not all_handles:
        return {pk: [] for pk in pair_keys}
    import sqlalchemy as sa
    channels = session.exec(
        select(Channel.id, Channel.handle).where(
            sa.func.lower(Channel.handle).in_(all_handles)
        )
    ).all()
    handle_to_cids: dict[str, list] = defaultdict(list)
    for cid, h in channels:
        handle_to_cids[h.lower()].append(cid)
    out: dict[str, list] = {}
    for pk in pair_keys:
        cids = []
        for h in handles_per_pair.get(pk, []):
            cids.extend(handle_to_cids.get(h, []))
        # Dedupe — Multi-Plattform-Pairs koennten denselben Channel
        # mehrfach listen.
        out[pk] = list(dict.fromkeys(cids))
    return out


# ---- Dry-Run / Apply -------------------------------------------------


def _count_unanalyzed_per_pair(
    session: Session, channels_per_pair: dict[str, list],
) -> dict[str, tuple[int, int]]:
    """Pro Pair: (posts_unanalyzed, posts_total).

    ``unanalyzed`` = ``Post.last_analyzed_at IS NULL`` — gleicher
    Filter wie der ``/api/admin/analyze``-Default. So entspricht der
    Backfill-Scope der bestehenden Idempotenz-Logik des Analyzers.
    """
    import sqlalchemy as sa
    out: dict[str, tuple[int, int]] = {}
    for pair_key, cids in channels_per_pair.items():
        if not cids:
            out[pair_key] = (0, 0)
            continue
        total = session.exec(
            select(sa.func.count()).select_from(Post)
            .where(Post.channel_id.in_(cids))
        ).one()
        unanal = session.exec(
            select(sa.func.count()).select_from(Post)
            .where(Post.channel_id.in_(cids))
            .where(Post.last_analyzed_at.is_(None))
        ).one()
        total = total[0] if isinstance(total, tuple) else total
        unanal = unanal[0] if isinstance(unanal, tuple) else unanal
        out[pair_key] = (int(unanal), int(total))
    return out


def _print_dry_run(per_pair: dict[str, tuple[int, int]]) -> int:
    print("=" * 70)
    print("POST-ANALYZER BACKFILL — DRY-RUN")
    print("=" * 70)
    print()
    print(f"{'Pair':<22} {'Unanalyzed':>12} {'Total':>10}")
    print("-" * 70)
    total_unanal = 0
    total_total = 0
    for pair_key in sorted(per_pair.keys()):
        unanal, total = per_pair[pair_key]
        print(f"{pair_key:<22} {unanal:>12,} {total:>10,}")
        total_unanal += unanal
        total_total += total
    print("-" * 70)
    print(f"{'TOTAL':<22} {total_unanal:>12,} {total_total:>10,}")
    print()
    if total_unanal == 0:
        print("Nichts zu tun — alle Pair-Posts haben bereits last_analyzed_at gesetzt.")
    else:
        print(f"==> Mit --apply --yes wuerden {total_unanal:,} Posts analysiert.")
        print()
        print("Schaetzung Anthropic-Calls:")
        print(f"  Vision (Sonnet):   ~{total_unanal:,}")
        print(f"  Format+Tone (Haiku): ~{total_unanal:,}")
        print(f"  Purpose+Lifecycle (Sonnet): ~{total_unanal:,}")
    print("=" * 70)
    return total_unanal


def _confirm_interactively() -> bool:
    print()
    print("Tippe 'ja' und Enter, um den Backfill zu starten.")
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


def _apply_backfill(
    session: Session, channels_per_pair: dict[str, list],
    *, skip_confirmation: bool, skip_vision: bool = False,
) -> dict[str, dict[str, int]]:
    """Geht alle ungaenderten Posts pro Pair durch.

    Pro Pair eine Statistik: ``{analyzed, skipped, errors}``.

    ``analyze_post`` ist die Workhorse-Funktion aus Sprint 5.3.1 — sie
    commited NICHT selbst. Wir committen pro Post hier, damit ein
    Crash-mid-Batch die bereits-fertigen Posts intakt laesst.

    ``skip_vision=True`` laesst den Sonnet-Vision-Call aus. Der ist ~72 %
    der Kosten pro Post (~$0,0073 von ~$0,0101), liefert aber nur
    ``vision_description`` — die vier PostAnalysis-Felder kommen aus der
    Caption. Wer den Backfill nur fuer die Cross-Tab-Coverage
    (format / lifecycle_stage) braucht, spart damit rund zwei Drittel.
    """
    analyze_post, AnthropicAuthError, is_anthropic_configured = _import_analyzer()

    if not is_anthropic_configured():
        print(
            "Anthropic-API-Key ist nicht gesetzt — Backfill nicht moeglich. "
            "Pruefe ANTHROPIC_API_KEY in der Railway-ENV."
        )
        return {}

    # Vorschau zeigen
    per_pair_counts = _count_unanalyzed_per_pair(session, channels_per_pair)
    n_total = _print_dry_run(per_pair_counts)
    if n_total == 0:
        return {}

    if not skip_confirmation:
        if not _confirm_interactively():
            return {}
    else:
        print()
        print("--yes uebergeben — Bestaetigung uebersprungen.")
    print()

    stats: dict[str, dict[str, int]] = {}
    for pair_key in sorted(channels_per_pair.keys()):
        cids = channels_per_pair[pair_key]
        if not cids:
            continue
        # Posts pro Pair einsammeln (Snapshot — wenn waehrend des Laufs
        # neue Posts dazukommen, fangen die im naechsten Re-Run).
        posts = list(session.exec(
            select(Post)
            .where(Post.channel_id.in_(cids))
            .where(Post.last_analyzed_at.is_(None))
            .order_by(Post.detected_at.desc())
        ).all())
        if not posts:
            continue

        pair_stats = {"analyzed": 0, "errors": 0, "skipped": 0}
        print(f"[{pair_key}] starting backfill, {len(posts)} posts")
        t0 = time.monotonic()
        for i, post in enumerate(posts, start=1):
            try:
                result = analyze_post(session, post, skip_vision=skip_vision)
            except AnthropicAuthError as exc:
                # Auth ist nicht recoverable — wir brechen ab. Bereits
                # erfolgreich analysierte Posts wurden via session.commit()
                # bereits persistiert; die uncommitted Aenderungen an
                # diesem Post verschwinden, sobald die Session schliesst.
                # KEIN rollback() hier — der wuerde den ORM-State der
                # already-committed Objekte invalidieren (Identity-Map-
                # Effekt).
                print(
                    f"[{pair_key}] AUTH-Fehler: {exc} — Backfill abgebrochen. "
                    f"Bisher: {pair_stats}"
                )
                stats[pair_key] = pair_stats
                return stats
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "backfill-post-failed",
                    extra={"pair_key": pair_key, "post_id": str(post.id)},
                )
                # Per-Post-Crash: uncommitted Aenderungen an diesem Post
                # via expunge raus, damit die Loop sauber weiterlaufen
                # kann. Kein rollback (siehe Auth-Pfad-Begruendung).
                session.expunge(post)
                pair_stats["errors"] += 1
                continue

            if result.status == "analyzed":
                pair_stats["analyzed"] += 1
            elif result.status == "skipped":
                pair_stats["skipped"] += 1
            else:
                pair_stats["errors"] += 1

            # Commit pro Post — Resume-Safety.
            session.commit()

            # Sparse-Logging: jeden 10. Post zeigen
            if i % 10 == 0:
                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed > 0 else 0
                print(
                    f"[{pair_key}] {i}/{len(posts)} ({rate:.1f} posts/s) "
                    f"analyzed={pair_stats['analyzed']} "
                    f"errors={pair_stats['errors']} "
                    f"skipped={pair_stats['skipped']}"
                )

        elapsed = time.monotonic() - t0
        print(
            f"[{pair_key}] DONE in {elapsed:.1f}s — "
            f"analyzed={pair_stats['analyzed']} "
            f"errors={pair_stats['errors']} "
            f"skipped={pair_stats['skipped']}"
        )
        stats[pair_key] = pair_stats

    # Gesamt-Statistik
    print()
    print("=" * 70)
    print("BACKFILL ABGESCHLOSSEN")
    print("=" * 70)
    grand_analyzed = sum(s["analyzed"] for s in stats.values())
    grand_errors = sum(s["errors"] for s in stats.values())
    grand_skipped = sum(s["skipped"] for s in stats.values())
    print(f"analyzed total: {grand_analyzed:,}")
    print(f"errors total:   {grand_errors:,}")
    print(f"skipped total:  {grand_skipped:,}")
    print()
    print("Naechster Schritt — Coverage-Re-Messung:")
    print("  cd backend && python -m scripts.measure_phase0_coverage")
    print("=" * 70)
    return stats


# ---- CLI -------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Post-Analyzer-Backfill auf alle enabled-Pair-Posts. "
            "Default: Dry-Run."
        )
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Backfill tatsaechlich ausfuehren. Ohne diese Flag laeuft nur Dry-Run.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Bestaetigung ueberspringen (nur sinnvoll mit --apply).",
    )
    parser.add_argument(
        "--pair", default=None,
        help="Eingrenzen auf einen einzelnen Pair-Key (z.B. warnerbros).",
    )
    parser.add_argument(
        "--skip-vision", action="store_true",
        help=(
            "Sonnet-Vision-Call auslassen (nur format/tone/purpose/"
            "lifecycle_stage aus der Caption). Spart ~72%% der Kosten pro "
            "Post; ``vision_description`` bleibt dann leer."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = _build_argparser().parse_args(argv)

    # Pair-Set festlegen
    all_enabled = list(_enabled_pair_handles().keys())
    if args.pair:
        if args.pair not in all_enabled:
            print(
                f"Pair '{args.pair}' nicht in enabled-PAIRS. "
                f"Verfuegbar: {', '.join(all_enabled)}"
            )
            return 1
        pair_keys = [args.pair]
    else:
        pair_keys = all_enabled

    with Session(engine) as session:
        channels_per_pair = _channels_for_pair_keys(session, pair_keys)

        if not args.apply:
            per_pair_counts = _count_unanalyzed_per_pair(session, channels_per_pair)
            n = _print_dry_run(per_pair_counts)
            print()
            print("Hinweis: Default-Modus aendert nichts.")
            print("Zum Schreiben:  --apply --yes")
            if args.pair:
                print(f"Gefiltert auf Pair: {args.pair}")
            return 0

        stats = _apply_backfill(
            session, channels_per_pair, skip_confirmation=args.yes,
            skip_vision=args.skip_vision,
        )
        return 0 if stats else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
