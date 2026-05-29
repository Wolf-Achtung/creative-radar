"""Sicht-Check-Markdown-Snippet fuer den Stufe-2-PR-C-Empfehlungs-Output.

Wolf-Briefing-Pflicht-Ping vor PR-C-Merge: Markdown-Snippet generieren
fuer Disney, Sony, Warner — pro Pair zeigen, was die Cross-Tabs liefern,
welche Bausteine durchkommen, welche an der Ehrlich-Klausel scheitern.

Read-only — ruft ``aggregate_pair`` fuer die drei Pairs auf und rendert
das Ergebnis als Markdown. Kein Schreibvorgang auf der DB.

Aufruf (im Railway-Shell):

    cd backend && python -m scripts.sicht_check_recommendations

Output (Markdown) hier in den Chat zurueckspielen — Claude Code
analysiert die Sample-Groessen, Effect-Sizes und entscheidet, ob die
Cross-Tabs trennscharf laufen oder ob die Ehrlich-Klausel zu streng
greift.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from sqlmodel import Session

from app.database import engine
from app.services.insight_engine import (
    PAIRS,
    _compute_recommendation_candidates,
    aggregate_pair,
)


SICHT_CHECK_PAIRS = ["disney", "sonypictures", "warnerbros"]


def _print_pair_block(session: Session, pair_key: str, now: datetime) -> None:
    """Pro Pair: full PairAggregation aufrufen, days_to_release_distribution
    + recommendation_candidates ausgeben."""
    if pair_key not in PAIRS:
        print(f"### {pair_key}")
        print()
        print(f"Pair-Key `{pair_key}` nicht in PAIRS — uebersprungen.")
        print()
        return

    agg = aggregate_pair(session, pair_key, window_days=7, now=now)

    print(f"### {pair_key}")
    print()
    # Distribution-Summary
    dist = agg.days_to_release_distribution or {}
    if dist:
        total = sum(dist.values())
        print(f"**days_to_release-Distribution (7d-Window, {total} Posts):**")
        print()
        for bucket in [">4w_pre", "1-4w_pre", "release_week",
                       "1-4w_post", ">4w_post", "evergreen", "unknown"]:
            n = dist.get(bucket, 0)
            if n > 0:
                pct = n * 100.0 / total if total else 0.0
                print(f"- {bucket}: {n} ({pct:.0f} %)")
        print()
    else:
        print("**days_to_release-Distribution:** leer")
        print()

    # Recommendation-Bausteine
    recs = agg.recommendation_candidates or []
    if not recs:
        print(
            "**Recommendation-Bausteine:** keine — nichts hat die "
            "Ehrlich-Klausel passiert (Confidence >= 0.7, "
            "Sample-Size >= 3, Effect-Size > 1.5x oder < 0.5x Baseline)."
        )
        print()
        return

    print(f"**Recommendation-Bausteine ({len(recs)} qualifiziert):**")
    print()
    print("| Dimension | Value | Sample | Conf-Avg | Activation | Baseline | Effect |")
    print("|---|---|---|---|---|---|---|")
    for r in recs:
        # Effect-Size aus evidence-Strings rekonstruieren ist haesslich;
        # wir nutzen die Strings direkt.
        print(
            f"| {r.dimension} | {r.recommended_value} | "
            f"{r.sample_size} | {r.confidence_avg:.2f} | "
            f"{r.evidence_metric} | {r.evidence_baseline} | — |"
        )
    print()
    # Cited Posts pro Empfehlung
    print("**Cited Posts (3-5 pro Empfehlung):**")
    print()
    for r in recs:
        print(f"- *{r.dimension}/{r.recommended_value}*:")
        for cid in r.cited_post_ids:
            print(f"  - {cid}")
        print()


def main() -> int:
    with Session(engine) as session:
        now = datetime.now(timezone.utc)
        print("# Stufe-2 PR-C — Sicht-Check Empfehlungs-Bausteine")
        print()
        print(f"Stichzeit: `{now.isoformat()}`")
        print()
        print(
            "Drei Beispiel-Pairs (Disney, Sony, Warner) — die mit der "
            "hoechsten Sample-Groesse und Title-Kopplung laut Phase-0-Befund."
        )
        print()
        print(
            "Ehrlich-Klausel: nur Bausteine, die ALLE Filter passieren "
            "(Confidence >= 0.7, Sample-Size >= 3, Effect-Size > 1.5x "
            "Baseline ODER < 0.5x)."
        )
        print()
        for pair_key in SICHT_CHECK_PAIRS:
            _print_pair_block(session, pair_key, now)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
