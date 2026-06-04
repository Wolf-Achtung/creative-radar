"""Idempotent one-time backfill for the pending-vision backlog.

Runs ``analyze_asset_visual`` over assets stuck on
``visual_analysis_status='pending'`` — the historical backlog the
feed-forward cron path never reached. Companion to the per-run backlog
drain in ``api/cron.py`` (that keeps the steady state clean; this clears
the accumulated stock in one go).

Not run by pytest against the real DB. Wolf executes manually:

    cd backend && python -m scripts.backfill_vision --dry-run     # projection only
    cd backend && python -m scripts.backfill_vision --apply       # real Vision calls

Safety contract (OpenAI spend is otherwise ungated — there is no global
OpenAI hard-cap pre-flight, see diagnose 2026-06-04):

- **Dry-run is the default.** Without ``--apply`` the script makes ZERO
  OpenAI/screenshot calls and writes NOTHING — it only counts the pending
  assets, how many carry a fetchable image source, and the projected cost.
- **Hard budget self-stop** (``--budget-usd``, default 60.0): before each
  image-bearing asset the projected spend is incremented by the per-call
  cost; the run stops the moment the next call would cross the budget. Real
  OpenAI spend is therefore capped at the budget regardless of backlog size.
- **Idempotent**: only ``status='pending'`` rows are selected, oldest first;
  ``analyze_asset_visual`` flips the status away from pending, so re-runs skip
  everything already processed.

Exit code: 0 on a clean run (per-asset errors are counted, not raised);
2 if the budget self-stop fired before the backlog was exhausted (so a
wrapper can detect "needs another tranche / raise budget").
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlmodel import Session, select

from app.database import engine
from app.models.entities import Asset, Channel, Post
from app.services.visual_analysis import analyze_asset_visual

logger = logging.getLogger("backfill_vision")

PLATFORMS = ("youtube", "instagram", "tiktok")

# Mirrors api/cron.py:_VISION_COST_USD_PER_CALL — approximate gpt-4o-mini
# Vision cost per call. Kept as a local constant so this script does not
# import the FastAPI cron module (heavy import graph). Override via --cost.
VISION_COST_USD_PER_CALL = 0.015

DEFAULT_BUDGET_USD = 60.0
DEFAULT_BATCH_SIZE = 50

_SUCCESS_STATUSES = frozenset({"analyzed", "done"})
_FETCH_FAIL_STATUSES = frozenset({"fetch_failed", "no_source", "image_unreachable", "image_invalid"})


def _has_image_source(asset: Asset) -> bool:
    """Upper-bound predicate for "will this asset trigger a paid Vision call".

    Mirrors screenshot_capture._candidate_sources / visual_analysis image_url:
    a call happens only if at least one fetchable image source exists. Assets
    with none short-circuit to ``no_source`` for free. This is an upper bound
    (a present URL may 404 at capture time and fall through to no_source), so
    using it for the budget guard is conservative — real spend never exceeds
    the projection.
    """
    return bool(
        getattr(asset, "screenshot_url", None)
        or getattr(asset, "thumbnail_url", None)
        or getattr(asset, "visual_source_url", None)
        or getattr(asset, "asset_url", None)
    )


def _select_pending(
    session: Session, limit: int | None, platform: str | None = None
) -> list[Asset]:
    stmt = (
        select(Asset)
        .where(Asset.visual_analysis_status == "pending")
    )
    if platform is not None:
        # Restrict to one platform (asset -> post -> channel). Used to target
        # the reachable YouTube backlog and skip IG/TT assets whose signed CDN
        # URLs have expired (would just burn fetches on a guaranteed 403).
        stmt = (
            stmt.join(Post, Asset.post_id == Post.id)
            .join(Channel, Post.channel_id == Channel.id)
            .where(Channel.platform == platform)
        )
    stmt = stmt.order_by(Asset.created_at.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.exec(stmt).all())


def run(
    session: Session,
    *,
    apply: bool = False,
    budget_usd: float = DEFAULT_BUDGET_USD,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cost_per_call: float = VISION_COST_USD_PER_CALL,
    platform: str | None = None,
) -> dict:
    assets = _select_pending(session, limit, platform)
    total = len(assets)
    with_image = sum(1 for a in assets if _has_image_source(a))
    projected_cost = round(with_image * cost_per_call, 4)

    print("=" * 70)
    print("VISION BACKFILL" + ("  [APPLY]" if apply else "  [DRY-RUN]"))
    print("=" * 70)
    print(f"Platform filter:           {platform or 'all'}")
    print(f"Pending assets selected:   {total}")
    print(f"  with fetchable image:    {with_image}  (incur a Vision call)")
    print(f"  without image (free):    {total - with_image}  (short-circuit no_source)")
    print(f"Cost per call:             ${cost_per_call}")
    print(f"Projected cost (worst):    ${projected_cost}")
    print(f"Budget self-stop:          ${budget_usd}")
    print("-" * 70)

    summary = {
        "mode": "apply" if apply else "dry_run",
        "platform": platform,
        "total_pending": total,
        "with_image": with_image,
        "without_image": total - with_image,
        "projected_cost_usd": projected_cost,
        "budget_usd": budget_usd,
        "attempted": 0,
        "succeeded": 0,
        "text_fallback": 0,
        "fetch_failed": 0,
        "vision_error": 0,
        "spent_usd": 0.0,
        "budget_stopped": False,
        "remaining_after_stop": 0,
    }

    if not apply:
        if projected_cost > budget_usd:
            print(f"WARNING: projected cost ${projected_cost} exceeds budget ${budget_usd}.")
            print("         A real --apply run would self-stop before finishing.")
        print("\nDry-run only — no Vision calls, no writes. Re-run with --apply to execute.")
        return summary

    spent = 0.0
    processed = 0
    for idx, asset in enumerate(assets):
        will_cost = cost_per_call if _has_image_source(asset) else 0.0
        # Hard self-stop: never start a call that would cross the budget.
        if will_cost > 0 and spent + will_cost > budget_usd:
            summary["budget_stopped"] = True
            summary["remaining_after_stop"] = total - processed
            print(
                f"\nBUDGET STOP at ${spent:.4f} (next call would cross ${budget_usd}). "
                f"{summary['remaining_after_stop']} pending asset(s) left."
            )
            break

        summary["attempted"] += 1
        try:
            updated = analyze_asset_visual(session, asset)
        except Exception:  # noqa: BLE001 — per-asset isolation, never abort the run
            logger.exception("backfill vision call failed for asset %s", asset.id)
            summary["vision_error"] += 1
            spent += will_cost
            processed += 1
            continue

        status = updated.visual_analysis_status
        if status in _SUCCESS_STATUSES:
            summary["succeeded"] += 1
        elif status == "text_fallback":
            summary["text_fallback"] += 1
        elif status in _FETCH_FAIL_STATUSES:
            summary["fetch_failed"] += 1
        else:
            summary["vision_error"] += 1
        spent += will_cost
        processed += 1

        if processed % batch_size == 0:
            print(
                f"  ... {processed}/{total} processed | "
                f"ok={summary['succeeded']} text={summary['text_fallback']} "
                f"fail={summary['fetch_failed']} err={summary['vision_error']} | "
                f"spent=${spent:.4f}"
            )

    summary["spent_usd"] = round(spent, 4)
    print("-" * 70)
    print(
        f"DONE: attempted={summary['attempted']} succeeded={summary['succeeded']} "
        f"text_fallback={summary['text_fallback']} fetch_failed={summary['fetch_failed']} "
        f"vision_error={summary['vision_error']} | spent=${summary['spent_usd']}"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill the pending-vision backlog.")
    parser.add_argument("--apply", action="store_true",
                        help="Execute real Vision calls. Without this flag the script is a dry-run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Force dry-run (the default). Wins over --apply if both are given.")
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
                        help=f"Hard spend self-stop in USD (default {DEFAULT_BUDGET_USD}).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max pending assets to consider (oldest first). Default: all.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Progress-log cadence (default {DEFAULT_BATCH_SIZE}).")
    parser.add_argument("--cost", type=float, default=VISION_COST_USD_PER_CALL,
                        help=f"Per-call cost estimate in USD (default {VISION_COST_USD_PER_CALL}).")
    parser.add_argument("--platform", choices=PLATFORMS, default=None,
                        help="Only process assets on this platform (default: all). "
                             "Use 'youtube' to target the reachable backlog.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    # Dry-run wins over --apply if both are passed (fail safe).
    apply = args.apply and not args.dry_run
    with Session(engine) as session:
        summary = run(
            session,
            apply=apply,
            budget_usd=args.budget_usd,
            limit=args.limit,
            batch_size=args.batch_size,
            cost_per_call=args.cost,
            platform=args.platform,
        )
    # Exit 2 signals the budget self-stop fired mid-backlog (wrapper can react).
    return 2 if summary.get("budget_stopped") else 0


if __name__ == "__main__":
    sys.exit(main())
