"""Idempotent backfill: set ``title_id`` on each ``aktuell_im_fokus`` item of
already-persisted briefs (V3 Sprint 1, Commit 4).

Fresh briefs get ``title_id`` via the post-LLM enrichment in
``generate_weekly_report``. Briefs persisted BEFORE that change lack it — this
script fills them in over the SAME deterministic chain
``post_url -> Post -> Asset.title_id`` (never by name; keeps the #230/#231
MK/MKII determinism).

Not run by pytest — touches the real DB. Default is a NON-DESTRUCTIVE preview:

    cd backend && python -m scripts.backfill_fokus_title_ids            # dry-run
    cd backend && python -m scripts.backfill_fokus_title_ids --apply    # writes

Dry-run prints, per brief and focus item, which title_id WOULD be set (or
"stays None"). Nothing is written. ``--apply`` performs the write.

Idempotent: a focus item that already carries a ``title_id`` is left untouched.
Re-running after a real apply is a no-op for already-set items.

JSON-column note: ``InsightReport.llm_output`` is a plain ``Column(JSON)`` (no
MutableDict). We deep-copy the blob, set only the new key, and reassign the
attribute so SQLAlchemy detects the change — the rest of the structure stays
byte-for-byte intact.

Exit code: 0 always (per-row issues are reported, not raised).
"""

from __future__ import annotations

import argparse
import copy
import sys

from sqlmodel import Session, select

from app.database import engine
from app.models.entities import InsightReport as InsightReportRow
from app.services.insight_engine import _resolve_title_id_for_post_url


def run(session: Session, *, apply: bool) -> dict:
    rows = list(session.exec(select(InsightReportRow)).all())
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] {len(rows)} brief(s) to scan\n")

    briefs_changed = 0
    items_set = 0
    items_already = 0
    items_stay_none = 0

    for row in rows:
        llm = row.llm_output or {}
        fokus = llm.get("aktuell_im_fokus") or []
        if not fokus:
            continue

        label = f"{row.pair_key} {row.iso_year}-W{row.iso_week}"
        pending: list[tuple[int, str | None, str | None]] = []  # (idx, titel, resolved)
        for idx, item in enumerate(fokus):
            if not isinstance(item, dict):
                continue
            if item.get("title_id"):
                items_already += 1
                continue
            resolved = _resolve_title_id_for_post_url(session, item.get("post_url"))
            pending.append((idx, item.get("titel"), resolved))

        if not pending:
            continue

        print(f"--- {label} ---")
        for idx, titel, resolved in pending:
            if resolved:
                print(f"  [{idx}] {titel!r} -> {resolved}")
                items_set += 1
            else:
                print(f"  [{idx}] {titel!r} -> bleibt None")
                items_stay_none += 1

        # Only the resolvable ones cause a write.
        to_write = [(idx, resolved) for idx, _, resolved in pending if resolved]
        if apply and to_write:
            new_llm = copy.deepcopy(llm)
            for idx, resolved in to_write:
                new_llm["aktuell_im_fokus"][idx]["title_id"] = resolved
            row.llm_output = new_llm  # new object ref -> change detected
            session.add(row)
            session.commit()
            briefs_changed += 1

    summary = {
        "briefs_scanned": len(rows),
        "briefs_changed": briefs_changed if apply else 0,
        "items_set": items_set,
        "items_already": items_already,
        "items_stay_none": items_stay_none,
    }
    print(
        f"\n=== {mode} summary ===\n"
        f"  briefs scanned:     {summary['briefs_scanned']}\n"
        f"  items would set:    {items_set}" + ("" if apply else "  (not written — dry-run)") + "\n"
        f"  items stay None:    {items_stay_none}\n"
        f"  items already set:  {items_already}\n"
        f"  briefs written:     {summary['briefs_changed']}"
    )
    if not apply and items_set:
        print("\nRe-run with --apply to write these title_ids.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the resolved title_ids. Without this flag the script is a "
             "non-destructive dry-run preview (default).",
    )
    args = parser.parse_args()
    with Session(engine) as session:
        run(session, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
