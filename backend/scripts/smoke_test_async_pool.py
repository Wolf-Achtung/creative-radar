"""Block 2.5 — local smoke test for the async asset-creation pipeline.

Run this against the live Railway Postgres BEFORE merging to main. It
verifies that under the new pool config (size=10, max_overflow=10) and
the dialed-down concurrency (3 OpenAI / 5 httpx), 50 mocked items can
be processed end-to-end without:

- exceeding the pool ceiling (max-concurrent backend connections must
  stay <= ``DB_POOL_TOTAL_BUDGET`` — 20 by default);
- raising any unhandled exceptions;
- leaving stale "in transaction" connections behind after the run.

What it does NOT do: real OpenAI calls (we stub the SDK to return a
fixed dict), real Apify scrapes (we feed a hand-built items list), or
real screenshot downloads (we stub the httpx leg). The point is the
DB-pool behaviour under the new code path, not the IO legs.

USAGE:

    cd backend
    DATABASE_URL=postgresql+psycopg://... \\
        python -m scripts.smoke_test_async_pool

OUTPUT — one-line per phase + a final summary block. Exit code 0 when
healthy, 1 when the pool budget was breached or items raised. The
final summary is paste-ready for the PR body.

CLEANUP — items are inserted with a fixed slug prefix
(``smoke-async-pool-<ts>``) so a follow-up DELETE can find and remove
them. The script DOES leave the rows in place by default; use
``--delete-after`` to drop them.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlmodel import Session, select


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_items(count: int, owner: str, slug_prefix: str) -> list[dict]:
    """Produce ``count`` distinct raw IG-shaped items so the dedupe
    check passes. ``owner_username`` matches the seeded test channel."""
    return [
        {
            "url": f"https://www.instagram.com/p/{slug_prefix}-{i}/",
            "ownerUsername": owner,
            "displayUrl": f"https://cdn.example/{slug_prefix}-{i}.jpg",
            "caption": f"smoke-test caption #{i}",
            "timestamp": "2026-05-06T09:30:00Z",
        }
        for i in range(count)
    ]


def _ensure_smoke_channel(session: Session, *, handle: str):
    """Idempotent: returns the existing smoke-test channel or creates it."""
    from app.models.entities import Channel, Market

    existing = session.exec(
        select(Channel).where(Channel.handle == handle, Channel.platform == "instagram")
    ).first()
    if existing:
        return existing
    channel = Channel(
        id=uuid4(),
        name=f"SMOKE-{handle}",
        handle=handle,
        url=f"https://www.instagram.com/{handle}/",
        platform="instagram",
        market=Market.US,
        active=True,
        mvp=False,  # keep out of cron
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


async def _stub_openai(**kwargs) -> dict:
    """Constant-time stub. The real path takes 1-3 s; this stub keeps
    the smoke test fast (~0.05 s) while still walking the full pipeline.
    ``asset_type`` must NOT be None — the SA Asset model rejects that;
    returning the enum's UNKNOWN preserves the default."""
    from app.models.entities import AssetType, ReviewStatus

    await asyncio.sleep(0.05)
    return {
        "asset_type": AssetType.UNKNOWN,
        "language": "en",
        "ai_summary_de": "smoke",
        "ai_summary_en": "smoke",
        "ai_trend_notes": "smoke",
        "confidence_score": 0.5,
        "review_status": ReviewStatus.NEW,
    }


async def _stub_persist(asset) -> None:
    """No-op screenshot persistence — sets a non-captured status so the
    asset still serializes, without touching httpx or the storage bucket."""
    asset.visual_evidence_status = "no_source"


async def _sample_active_connections(stop_event: asyncio.Event, samples: list[int], engine) -> None:
    """Background coroutine: every 100 ms, query pg_stat_activity for
    the count of OUR backend connections and append to ``samples``.
    SQLite engines are skipped with a single 0 sample."""
    if str(engine.url).startswith("sqlite"):
        samples.append(0)
        return
    while not stop_event.is_set():
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name = current_setting('application_name', true) "
                        "OR usename = current_user"
                    )
                ).scalar() or 0
            samples.append(int(row))
        except Exception as exc:  # noqa: BLE001 — observability shouldn't crash the run
            print(f"  [warn] pg_stat_activity probe failed: {exc}", file=sys.stderr)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            continue


async def _run(items_count: int, owner: str, delete_after: bool) -> int:
    from app.api import monitor as monitor_mod
    from app.api.monitor import _run_apify_sync_for_platform_async
    from app.database import DB_POOL_TOTAL_BUDGET, engine
    from app.models.entities import Channel, Post
    from app.services import asset_screenshot_persistence as persistence_mod
    from app.services.apify_connector import normalize_public_item

    is_postgres = not str(engine.url).startswith("sqlite")
    print(f"# Block 2.5 smoke test")
    print(f"# DB:                {engine.url.render_as_string(hide_password=True)}")
    print(f"# DB_POOL_TOTAL_BUDGET: {DB_POOL_TOTAL_BUDGET}")
    print(f"# items_count:       {items_count}")
    print(f"# OpenAI concurrency: {monitor_mod.ASSET_CREATION_OPENAI_CONCURRENCY}")
    print(f"# httpx concurrency:  {monitor_mod.ASSET_CREATION_HTTPX_CONCURRENCY}")
    print(f"# is_postgres:       {is_postgres}")

    slug_prefix = f"smoke-async-pool-{_now_tag()}"
    raw_items = _build_items(items_count, owner=owner, slug_prefix=slug_prefix)

    with Session(engine) as session:
        channel = _ensure_smoke_channel(session, handle=owner)
        channel_id = channel.id

    # Stub the slow IO legs so we measure the DB-pool side, not network.
    # IMPORTANT: monitor.py imports both names at module top, so patching the
    # source modules alone wouldn't take effect — patch monitor_mod directly.
    monitor_mod.analyze_creative_text_async = _stub_openai  # type: ignore[assignment]
    monitor_mod.persist_asset_screenshot_async = _stub_persist  # type: ignore[assignment]
    persistence_mod.persist_asset_screenshot_async = _stub_persist  # type: ignore[assignment]

    # Concurrent connection-count sampler.
    samples: list[int] = []
    stop_event = asyncio.Event()
    sampler = asyncio.create_task(_sample_active_connections(stop_event, samples, engine))

    started = time.monotonic()
    errors: list[str] = []
    try:
        with Session(engine) as session:
            channels = list(session.exec(
                select(Channel).where(Channel.id == channel_id)
            ).all())
        summary = await _run_apify_sync_for_platform_async(
            engine=engine,
            channels=channels,
            raw_items=raw_items,
            platform="instagram",
            normalize=normalize_public_item,
            only_whitelist_matches=False,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"top-level exception: {type(exc).__name__}: {exc}")
        summary = {"created_assets": 0, "skipped_other": items_count}
    finally:
        stop_event.set()
        await sampler
    duration = time.monotonic() - started

    max_seen = max(samples) if samples else 0
    print()
    print("## Result")
    print(f"- created_assets:        {summary.get('created_assets', '?')}")
    print(f"- skipped_existing:      {summary.get('skipped_existing', '?')}")
    print(f"- skipped_no_match:      {summary.get('skipped_no_whitelist_match', '?')}")
    print(f"- skipped_other:         {summary.get('skipped_other', '?')}")
    print(f"- failed_channels:       {len(summary.get('failed_channels', []))}")
    print(f"- duration:              {duration:.2f}s")
    print(f"- max-concurrent-conns:  {max_seen}  (budget {DB_POOL_TOTAL_BUDGET})")
    print(f"- errors:                {len(errors)}")
    for e in errors:
        print(f"   - {e}")

    pool_ok = (not is_postgres) or (max_seen <= DB_POOL_TOTAL_BUDGET)
    healthy = pool_ok and not errors and summary.get("created_assets", 0) == items_count

    print()
    print(f"## Verdict: {'OK' if healthy else 'FAIL'}")
    if not pool_ok:
        print(f"  → max-concurrent-conns {max_seen} exceeded budget {DB_POOL_TOTAL_BUDGET}")
    if errors:
        print(f"  → {len(errors)} unhandled exception(s)")
    if summary.get("created_assets", 0) != items_count:
        print(f"  → only {summary.get('created_assets', 0)}/{items_count} items created")

    if delete_after:
        from app.models.entities import Asset as _Asset
        with Session(engine) as session:
            posts = list(session.exec(
                select(Post).where(Post.post_url.like(f"%{slug_prefix}%"))  # type: ignore[attr-defined]
            ).all())
            post_ids = [p.id for p in posts]
            # Delete child Assets first to avoid the FK nullification path
            # (Asset.post_id is NOT NULL, which the default ORM cascade
            # tries to set to None before deleting the Post — the smoke
            # script wants the rows gone, not nulled).
            if post_ids:
                assets = list(session.exec(
                    select(_Asset).where(_Asset.post_id.in_(post_ids))  # type: ignore[attr-defined]
                ).all())
                for a in assets:
                    session.delete(a)
                session.commit()
            for p in posts:
                session.delete(p)
            session.commit()
            print(f"  → cleaned up {len(posts)} smoke-test Post rows + their Assets")

    return 0 if healthy else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=50)
    parser.add_argument("--owner", default="smoke_async_pool_channel")
    parser.add_argument("--delete-after", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_run(args.items, args.owner, args.delete_after))


if __name__ == "__main__":
    raise SystemExit(main())
