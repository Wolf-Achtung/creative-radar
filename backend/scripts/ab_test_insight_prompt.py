"""A/B-Test runner for the Trailerhaus-Prompt-Sprint v1.

Compares the Sprint-1 SYSTEM_PROMPT (v0) against the Sprint-Trailerhaus-
Prompt-v1 SYSTEM_PROMPT (v1) on a single Pair, with the same data window.
Saves both raw LLM responses + the token usage to ``ab_test_outputs/`` and
prints a side-by-side markdown table to stdout (which Wolf can paste into
the PR body for the quality-gate review).

Usage:

    cd backend
    ANTHROPIC_API_KEY=... DATABASE_URL=postgresql+psycopg://... \\
        python -m scripts.ab_test_insight_prompt --pair warnerbros

Cost expectation: ~$0.50-0.80 for both calls (v0 ~$0.20, v1 ~$0.35).
Use ``--dry-run-v1`` to skip the v1 LLM call when only validating the
aggregation flow / new prompt construction. Use ``--no-write`` to skip
the JSON dumps (handy in CI).

The v0 prompt is captured inline from git history (see ``_V0_PROMPT``).
We don't import it from the module because the module has been replaced;
inlining keeps the A/B comparison self-contained.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from app.database import engine
from app.services import insight_engine
from app.services.anthropic_client import (
    is_anthropic_configured,
    messages_create_text,
)


# Sprint-1 / v0 prompt — captured verbatim from git history at commit
# 013f87d (PR #77, before the Trailerhaus-Prompt-v1 refactor). DO NOT
# refactor this string in line with the live prompt — its purpose is to
# serve as the historical baseline for the A/B comparison.
_V0_PROMPT = """\
Du bist ein Senior-Trailer-Marketing-Stratege bei Trailerhaus, einem deutschen \
Kino-Trailer-Produktionsstudio. Du analysierst Social-Media-Daten von Filmverleihen, \
um konkrete kreative TODOs für Schneide- und Hook-Entscheidungen abzuleiten. Du \
sprichst die Sprache von Trailer-Producern: direkt, fachlich, ohne Marketing-\
Bullshit. Du gibst KEINE Allgemeinplätze ("Engagement ist wichtig") und KEINE \
Hashtag-Listen ohne Kontext, sondern handfeste Beobachtungen mit Daten-Anker \
(Zahl, Asset-URL oder konkretes Beispiel aus dem Datenpaket).

Dein Output ist AUSSCHLIESSLICH ein JSON-Objekt nach folgendem Schema. \
Kein Vorspann, kein Markdown-Codefence, keine Erklärung — nur das JSON:

{
  "headline": "Eine Zeile, provokant, max. 90 Zeichen",
  "tldr": "3 Sätze: was ist diese Woche bei Warner anders, was sollte Trailerhaus daraus lernen, wo ist die Wette",
  "trends": [
    { "name": "...", "evidence": "konkrete Zahl oder Asset-Bezug aus den Daten", "implication_for_creation": "was Trailerhaus konkret in der Schnittarbeit ändern sollte" }
  ],
  "actions": [
    { "what": "konkrete Handlung", "why": "Beleg aus den Daten", "for_whom": "z.B. Cutter, Creative Producer, Hook-Designer" }
  ],
  "cross_market_insight": {
    "de_vs_us": "Was unterscheidet die Märkte diese Woche, mit Daten-Anker",
    "transfer_opportunity": "Was sollte aus US für DE adaptiert werden oder umgekehrt"
  },
  "risks": [ "..." ],
  "data_caveats": [ "..." ]
}

Wenn die Datengrundlage zu dünn ist (Coverage <30%, weniger als 5 Posts pro Markt, \
oder keine Cross-Market-Matches), sage das klar im Feld data_caveats und schlage \
NICHT vor, was du nicht aus den Daten ableiten kannst. Lieber 1 starker Trend mit \
Beleg als 5 Trends ohne Daten-Anker.\
"""


def _call(system: str, user: str, *, max_tokens: int) -> tuple[str, int, int]:
    msg = messages_create_text(
        model=insight_engine.OPUS_MODEL_ALIAS,
        system=system,
        user_message=user,
        max_tokens=max_tokens,
    )
    text = ""
    for block in getattr(msg, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text += getattr(block, "text", "")
    usage = getattr(msg, "usage", None)
    return (
        text,
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


def _section_summary(parsed: dict | None) -> dict[str, str]:
    """Compact one-cell-per-section summary for the markdown table."""
    if not parsed:
        return {"_status": "PARSE-FAILED"}
    sections = {}
    for key in (
        "headline",
        "tldr",
        "trends",
        "actions",
        "cross_market_insight",
        "risks",
        "data_caveats",
        "tonalitaet",
        "watch_outs",
        "fuer_cutter",
        "fuer_motion_designer",
        "fuer_creative_producer",
        "vergleichbare_posts",
    ):
        v = parsed.get(key)
        if v is None:
            sections[key] = "—"
        elif isinstance(v, list):
            sections[key] = f"{len(v)} items"
        elif isinstance(v, dict):
            filled = sum(1 for vv in v.values() if vv)
            sections[key] = f"dict ({filled}/{len(v)} filled)"
        elif isinstance(v, str):
            sections[key] = f"{len(v)} chars"
        else:
            sections[key] = str(type(v).__name__)
    return sections


def _print_table(v0: dict, v1: dict, v0_cost: float, v1_cost: float) -> None:
    v0s = _section_summary(v0)
    v1s = _section_summary(v1)
    keys = sorted(set(v0s.keys()) | set(v1s.keys()))
    print("\n## A/B-Test — Section-Coverage\n")
    print("| Section | v0 (Sprint-1) | v1 (Trailerhaus-Prompt) |")
    print("|---|---|---|")
    for k in keys:
        print(f"| `{k}` | {v0s.get(k, '—')} | {v1s.get(k, '—')} |")
    print(f"\n**Cost**: v0 ≈ ${v0_cost:.4f}, v1 ≈ ${v1_cost:.4f}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default="warnerbros")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--dry-run-v1", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    if not is_anthropic_configured():
        print("ANTHROPIC_API_KEY missing — set it before running this A/B test.", file=sys.stderr)
        return 2

    out_dir = Path("ab_test_outputs")
    if not args.no_write:
        out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    with Session(engine) as session:
        agg = insight_engine.aggregate_pair(
            session, args.pair, window_days=args.window_days
        )
        user_prompt = insight_engine._build_user_prompt(agg)

        print(f"# A/B test for pair={args.pair}, window={args.window_days}d")
        print(f"# prompt-chars: {len(user_prompt)}")

        # v0 — historical baseline. Use 8000 max_tokens to match the v0 default.
        print("\n→ Calling v0 (Sprint-1 prompt) …")
        v0_text, v0_in, v0_out = _call(_V0_PROMPT, user_prompt, max_tokens=8000)
        try:
            v0_parsed = json.loads(insight_engine._strip_codefence(v0_text))
        except (json.JSONDecodeError, ValueError):
            v0_parsed = None

        if args.dry_run_v1:
            v1_text, v1_in, v1_out, v1_parsed = "(skipped)", 0, 0, None
        else:
            print("→ Calling v1 (Trailerhaus-Prompt-v1) …")
            v1_text, v1_in, v1_out = _call(
                insight_engine.SYSTEM_PROMPT, user_prompt, max_tokens=12000
            )
            try:
                v1_parsed = json.loads(insight_engine._strip_codefence(v1_text))
            except (json.JSONDecodeError, ValueError):
                v1_parsed = None

        v0_cost = (v0_in / 1000.0) * 0.015 + (v0_out / 1000.0) * 0.075
        v1_cost = (v1_in / 1000.0) * 0.015 + (v1_out / 1000.0) * 0.075

        if not args.no_write:
            (out_dir / f"{args.pair}-v0-{ts}.json").write_text(
                json.dumps(
                    {
                        "raw": v0_text,
                        "parsed": v0_parsed,
                        "input_tokens": v0_in,
                        "output_tokens": v0_out,
                        "cost_usd": v0_cost,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (out_dir / f"{args.pair}-v1-{ts}.json").write_text(
                json.dumps(
                    {
                        "raw": v1_text,
                        "parsed": v1_parsed,
                        "input_tokens": v1_in,
                        "output_tokens": v1_out,
                        "cost_usd": v1_cost,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        _print_table(v0_parsed, v1_parsed, v0_cost, v1_cost)

        # Anti-pattern audit on v1 — count how many of Wolf's blocked
        # X-Y-Floskeln still appear in the v1 output. >0 means the prompt
        # needs another iteration before promotion.
        if v1_parsed:
            blob = json.dumps(v1_parsed, ensure_ascii=False)
            blocked = [
                "Brand-Storytelling",
                "Engagement-Drivers",
                "Hook-Architektur",
                "Live-Event-Framing",
                "Catalog-Nostalgie",
                "Catalog-Reaktivierung",
                "Fan-Service-Loop",
                "Discovery-Cut",
            ]
            hits = [w for w in blocked if w in blob]
            if hits:
                print(f"⚠ v1 contains blocked terms: {hits}")
            else:
                print("✓ v1 contains none of the blocked terms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
