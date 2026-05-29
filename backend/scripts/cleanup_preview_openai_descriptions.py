"""OPENAI-Beschreibungs-Cleanup-VORSCHAU (Sprint 29.05.2026).

Wolf-owned Read-only-Script. Diagnose-Befund 3.3 vom 29.05.2026:
1.771 historische OPEN+OPENAI ``TitleCandidate``-Rows, die in Wahrheit
LLM-Beschreibungen sind (``_extract_title_guess`` zog die ersten 6
Tokens aus ``ai_summary_de``, das Source-Label ``OPENAI`` zeigt nur an,
dass der Asset-Text aus dem OpenAI-Pfad kam — kein Titel-Prompt).

Dieses Skript teilt die Zielmenge auf:

- **H1-Treffer**: ``suggested_title`` beginnt mit einem der Praefixe in
  ``DESCRIPTION_PREFIXES`` (case-insensitive). Eindeutige Cleanup-
  Kandidaten — kein echter Filmtitel beginnt so.
- **Restmenge**: alles andere. Braucht separate Wolf-Sicht (echte
  Titel-Fragmente vs. weitere Beschreibungs-Klassen).

Default = Vorschau. **Kein** ``--apply`` in diesem Skript — das
Cleanup selbst (Status-Wechsel auf ``DISMISSED``) wird, falls Wolf
nach Vorschau zustimmt, in einem separaten PR gebaut. Analog zu
Trennung Vorschau/Apply bei #201.

Aufruf:

    cd backend && python -m scripts.cleanup_preview_openai_descriptions

Auf Railway:

    railway run sh -c 'DATABASE_URL="$CR_DB_URL" \\
        python -m scripts.cleanup_preview_openai_descriptions'
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlmodel import Session, select

from app.database import engine
from app.models.entities import CandidateSource, CandidateStatus, TitleCandidate


# ---- Praefix-Liste (Diagnose-Befund 3.3) ------------------------------
# Wichtig: ``Der ``/``Die ``/``Das ``/``Ein ``/``Eine `` brauchen das
# Trailing-Space, sonst matchen sie Filmtitel wie ``Derek``, ``Die Hard``,
# ``Dashes`` oder ``Einsamkeit``. Praefixe ohne Trailing-Space sind
# kompound-Begriffe wie ``Instagram-Post``, die als ganzes Wort am
# Anfang stehen — da ist das Token bereits selbst-abgeschlossen.

DESCRIPTION_PREFIXES: tuple[str, ...] = (
    # Deutsch — Artikel mit Trailing-Space, sonst false-positives.
    "Der ", "Die ", "Das ", "Ein ", "Eine ",
    # Deutsch — Plattform-Beschreibungs-Header.
    "Instagram-Post", "Instagram Post", "Instagram Reel",
    "TikTok-Video", "TikTok von", "TikTok Post",
    "YouTube-Video", "YouTube von",
    "Post von", "Video von", "Clip von",
    # Englisch — Artikel mit Trailing-Space.
    "The Post", "A Video", "An Image", "A Post",
    "Image of", "Picture of", "Screenshot", "Description",
)

SAMPLE_SIZE = 15


# ---- Klassifizierung -------------------------------------------------


def _matched_prefix(suggested_title: str | None) -> str | None:
    """Gibt den ersten passenden Praefix aus ``DESCRIPTION_PREFIXES``
    zurueck (case-insensitive Vergleich auf den Anfang des Titels) —
    oder ``None``, wenn nichts matched.

    Trailing-Space-Logik: Wer in der Liste ``Der `` (mit Space) hat,
    matched ``Der Post stellt...`` aber NICHT ``Derek``. Das Skript
    delegiert die Trailing-Space-Verantwortung an die Liste selbst:
    der Vergleich ist ein dummer ``str.startswith`` (lowercased).
    """
    if not suggested_title:
        return None
    title_lower = suggested_title.lower()
    for prefix in DESCRIPTION_PREFIXES:
        if title_lower.startswith(prefix.lower()):
            return prefix
    return None


# ---- Vorschau --------------------------------------------------------


def _print_preview(session: Session, *, seed: int | None = None) -> int:
    """Druckt die zwei-Mengen-Aufteilung. Returns die Anzahl der
    H1-Treffer (fuer Tests/Caller; das Skript selbst druckt nur)."""
    now = datetime.now(timezone.utc)

    rows = session.exec(
        select(TitleCandidate).where(
            TitleCandidate.status == CandidateStatus.OPEN,
            TitleCandidate.source == CandidateSource.OPENAI,
        )
    ).all()
    total = len(rows)

    h1_hits: list[TitleCandidate] = []
    rest: list[TitleCandidate] = []
    prefix_counter: Counter[str] = Counter()

    for c in rows:
        match = _matched_prefix(c.suggested_title)
        if match:
            h1_hits.append(c)
            prefix_counter[match] += 1
        else:
            rest.append(c)

    h1_n = len(h1_hits)
    rest_n = len(rest)

    print("=" * 70)
    print("OPENAI-BESCHREIBUNGS-CLEANUP — VORSCHAU")
    print("=" * 70)
    print(f"Stichzeit:               {now.isoformat()}")
    print(f"Zielmenge:               status='OPEN' AND source='OPENAI'")
    print("-" * 70)
    print(f"Total OPEN+OPENAI:       {total:,}")
    if total > 0:
        h1_pct = h1_n * 100.0 / total
        rest_pct = rest_n * 100.0 / total
        print(f"H1-Praefix-Treffer:      {h1_n:,} ({h1_pct:.1f} %)")
        print(f"Restmenge:               {rest_n:,} ({rest_pct:.1f} %)")
    else:
        print(f"H1-Praefix-Treffer:      {h1_n:,}")
        print(f"Restmenge:               {rest_n:,}")
    print("-" * 70)

    if total == 0:
        print()
        print("Keine OPEN+OPENAI-Rows in der DB — nichts zu sichten.")
        print("=" * 70)
        return 0

    print()
    print("## Top-Treffer-Praefixe (H1)")
    print()
    if prefix_counter:
        print("| Praefix | Anzahl |")
        print("|---|---|")
        for prefix, n in prefix_counter.most_common():
            display = prefix.replace(" ", "·") if prefix.endswith(" ") else prefix
            print(f"| `{display}` | {n} |")
    else:
        print("_(keine H1-Treffer in der Zielmenge)_")
    print()

    rng = random.Random(seed) if seed is not None else random.Random()

    print("## Stichprobe — H1-Treffer (15 random)")
    print()
    h1_sample = _sample(rng, h1_hits, SAMPLE_SIZE)
    if h1_sample:
        for c in h1_sample:
            print(f"- {_fmt_candidate(c)}")
    else:
        print("_(keine H1-Treffer)_")
    print()

    print("## Stichprobe — Restmenge (15 random)")
    print()
    rest_sample = _sample(rng, rest, SAMPLE_SIZE)
    if rest_sample:
        for c in rest_sample:
            print(f"- {_fmt_candidate(c)}")
    else:
        print("_(keine Restmenge)_")
    print()

    print("-" * 70)
    print(
        "Naechster Schritt: Wolf sichtet beide Stichproben. Wenn H1-"
        "Treffer sauber Beschreibungen sind und Restmenge echte Titel-"
        "Fragmente, ist eine Cleanup-PR mit Status-Wechsel auf "
        "DISMISSED gerechtfertigt."
    )
    print("=" * 70)
    return h1_n


def _sample(rng: random.Random, items: list[TitleCandidate], n: int) -> list[TitleCandidate]:
    if not items:
        return []
    if len(items) <= n:
        return list(items)
    return rng.sample(items, n)


def _fmt_candidate(c: TitleCandidate) -> str:
    """Einzeiler fuer die Stichproben-Liste. Confidence + Datum
    optional anhaengen, je nachdem ob beide gesetzt sind."""
    title = c.suggested_title or "(leer)"
    conf = f"{c.confidence:.2f}" if c.confidence is not None else "—"
    created = c.created_at.date().isoformat() if c.created_at else "—"
    return f'"{title}" (conf {conf}, {created})'


# ---- CLI -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Default und einziger Modus: Vorschau. Kein ``--apply``."""
    with Session(engine) as session:
        _print_preview(session)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
