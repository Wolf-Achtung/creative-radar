"""Read-only preview of aggregate_title — prints the title aggregation for
one title as JSON so the output shape is inspectable.

    cd backend && railway run python -m scripts.aggregate_title_preview "Mortal Kombat" [window_days]

No LLM, no persistence. If the DB is reachable only via the Railway Data
view (not railway run), use the raw SQL in docs/title_aggregation_preview.sql
instead — it reproduces the same per-platform / per-market / channels / top
breakdowns.
"""
from __future__ import annotations

import dataclasses
import json
import sys

from sqlmodel import Session

from app.database import engine
from app.services.title_aggregation import aggregate_title


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    name = argv[0] if argv else "Mortal Kombat"
    window_days = int(argv[1]) if len(argv) > 1 else 30

    with Session(engine) as session:
        agg = aggregate_title(session, name, window_days=window_days)

    if agg is None:
        print(json.dumps({"error": f"no title matched {name!r}"}, ensure_ascii=False))
        return 1
    print(json.dumps(dataclasses.asdict(agg), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
