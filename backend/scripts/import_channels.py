"""Idempotent UPSERT of a Perplexity-recherchierte Channel-Liste in
``creative_radar.channel``.

Sprint 5.3.X Mini-Run 2. Run via Railway-Dashboard "Run a command":

    python -m scripts.import_channels data/channels_perplexity_2026_05_03.csv
    python -m scripts.import_channels data/channels_perplexity_2026_05_03.csv --dry-run

Behaviour summary (full pre-commitments in the Sprint-Doc):

- UPSERT-Schlüssel: ``(platform, handle)``. Es gibt keinen DB-Unique-Index
  auf diesem Tupel — wir vergleichen in Python via SELECT-then-INSERT/UPDATE.
- Existierender Channel: setze ``category`` und ``import_source``, *aber nur
  wenn das jeweilige Feld bisher NULL ist*. Alle anderen Felder
  (name, notes, market, priority, …) bleiben unangetastet — non-destructive
  UPSERT, weil notes oft Wolf-handgepflegte Inhalte trägt und name in
  Production schon eine bewusste Variante sein kann.
- Neuer Channel: INSERT mit allen CSV-Feldern + ``active=true``.
- ``import_source`` wird beim INSERT immer geschrieben, beim UPDATE nur
  wenn bisher NULL. Wert ist hardcoded ``perplexity_2026_05_03``; ein
  späterer Import schreibt einfach einen neuen Wert.
- Skript filtert NICHT nach ``confidence`` — Wolf filtert vorab beim
  CSV-Erstellen. Falsche confidence-Werte sind also Wolf-Fehler, keine
  Skript-Aufgabe.
- Validation: ``market`` in {DE, US, INT, UNKNOWN}, ``platform`` in
  {instagram, youtube, tiktok}. Ungültige Werte → Row-Skip mit Logzeile,
  kein Abbruch.
- Header-Mismatch oder fehlende Datei → SystemExit(1) bevor irgendwas
  geschrieben wird.
- ``--dry-run`` parst und meldet, committed aber nicht.
- Transaction-Boundary: ein einziger Commit am Ende. Jeder unerwartete
  Fehler mid-Batch löst Rollback aus und liefert Exit-Code 1 — nie
  halb-importierte Listen.

Output pro Zeile:

    [CREATE] (instagram, netflixde) → row 12: name="Netflix Deutschland", market=DE
    [UPDATE] (instagram, netflix)   → set category=streamer, import_source=perplexity_2026_05_03
    [SKIP]   (instagram, sonypictures) → category and import_source already set, no changes needed
    [ERROR]  row 7: invalid market 'EU' (must be one of ['DE', 'INT', 'UNKNOWN', 'US']), skipping

Plus ein Summary-Block am Ende mit created/updated/skipped/errored.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Sequence

from sqlmodel import Session, select

from app.database import engine
from app.models.entities import Channel, Market


EXPECTED_HEADER: tuple[str, ...] = (
    "company_name",
    "category",
    "market",
    "platform",
    "handle",
    "url",
    "confidence",
    "notes",
)
VALID_MARKETS: frozenset[str] = frozenset({"DE", "US", "INT", "UNKNOWN"})
VALID_PLATFORMS: frozenset[str] = frozenset({"instagram", "youtube", "tiktok"})
IMPORT_SOURCE_VALUE: str = "perplexity_2026_05_03"


@dataclass
class Stats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errored: int = 0


# --------------------------------------------------------------------------
# Validation helpers (raise SystemExit(1) on user-fixable errors so the
# Railway-Run terminates loud and clear before any DB write happens).
# --------------------------------------------------------------------------


def _check_csv_path(path: Path) -> None:
    if path.exists():
        return
    example_candidate = Path(str(path) + ".example")
    if example_candidate.exists():
        msg = (
            f"FEHLER: Erwartete Datei {path} fehlt — "
            f"nur {example_candidate} ist vorhanden. "
            f"Die .example-CSV ist eine Demo-Vorlage und wird absichtlich "
            f"nicht importiert. Lege die echte CSV unter dem erwarteten "
            f"Namen ab und führe das Skript erneut aus."
        )
    else:
        msg = f"FEHLER: CSV-Datei {path} nicht gefunden."
    print(msg, file=sys.stderr)
    raise SystemExit(1)


def _validate_header(header: Optional[Sequence[str]]) -> None:
    actual = tuple((h or "").strip() for h in (header or ()))
    if actual != EXPECTED_HEADER:
        print(
            "FEHLER: CSV-Header passt nicht.\n"
            f"  erwartet:  {list(EXPECTED_HEADER)}\n"
            f"  vorhanden: {list(actual)}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _normalize_row(raw: dict) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}


def _validate_row(row: dict) -> Optional[str]:
    """Return a human-readable error string if the row is invalid; else None.

    Order matters: the first failure wins so the operator sees the most
    upstream cause first.
    """
    if not row.get("company_name"):
        return "company_name missing"
    platform = row.get("platform", "")
    if platform not in VALID_PLATFORMS:
        return (
            f"invalid platform {platform!r} "
            f"(must be one of {sorted(VALID_PLATFORMS)})"
        )
    market = row.get("market", "")
    if market not in VALID_MARKETS:
        return (
            f"invalid market {market!r} "
            f"(must be one of {sorted(VALID_MARKETS)})"
        )
    if not row.get("handle"):
        return "handle missing — needed as UPSERT key"
    if not row.get("url"):
        return "url missing"
    return None


# --------------------------------------------------------------------------
# Per-row UPSERT
# --------------------------------------------------------------------------


def _process_row(session: Session, row: dict) -> tuple[str, str]:
    """Apply one CSV row to the session (without committing).

    Returns ``(action, detail)`` where action is one of
    ``"CREATE"`` / ``"UPDATE"`` / ``"SKIP"``.
    """
    platform = row["platform"]
    handle = row["handle"]

    existing = session.exec(
        select(Channel).where(
            Channel.platform == platform,
            Channel.handle == handle,
        )
    ).first()

    if existing is not None:
        diffs: list[str] = []
        if existing.category is None and row.get("category"):
            existing.category = row["category"]
            diffs.append(f"category={row['category']}")
        if existing.import_source is None:
            existing.import_source = IMPORT_SOURCE_VALUE
            diffs.append(f"import_source={IMPORT_SOURCE_VALUE}")
        if diffs:
            session.add(existing)
            return "UPDATE", "set " + ", ".join(diffs)
        return "SKIP", "category and import_source already set, no changes needed"

    new_channel = Channel(
        name=row["company_name"],
        platform=platform,
        url=row["url"],
        handle=handle,
        market=Market(row["market"]),
        active=True,
        category=row.get("category") or None,
        import_source=IMPORT_SOURCE_VALUE,
        notes=row.get("notes") or None,
    )
    session.add(new_channel)
    return (
        "CREATE",
        f"name={new_channel.name!r}, market={row['market']}",
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _iter_rows(csv_path: Path) -> Iterator[tuple[int, dict]]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        _validate_header(reader.fieldnames)
        # ``start=2`` because the header is row 1 in the human counting that
        # operators use when staring at the CSV in a spreadsheet.
        for row_index, raw in enumerate(reader, start=2):
            yield row_index, _normalize_row(raw)


def run(csv_path: Path, *, session: Session) -> Stats:
    """Iterate the CSV and stage all UPSERT writes against ``session``.

    Does NOT commit or rollback — the caller (``main``) owns the
    transaction so a single commit-at-end gives the all-or-nothing
    semantics promised in the Sprint pre-commitments. Raises SystemExit
    for user-fixable input errors (missing file, bad header) and any
    other exception for genuinely unexpected failures.
    """
    _check_csv_path(csv_path)
    stats = Stats()
    for row_index, row in _iter_rows(csv_path):
        err = _validate_row(row)
        if err:
            stats.errored += 1
            print(f"[ERROR]  row {row_index}: {err}, skipping")
            continue
        action, detail = _process_row(session, row)
        key = f"({row['platform']}, {row['handle']})"
        if action == "CREATE":
            stats.created += 1
            print(f"[CREATE] {key} → row {row_index}: {detail}")
        elif action == "UPDATE":
            stats.updated += 1
            print(f"[UPDATE] {key} → {detail}")
        else:
            stats.skipped += 1
            print(f"[SKIP]   {key} → {detail}")
    return stats


def _print_summary(stats: Stats, *, dry_run: bool) -> None:
    label = "Dry-run" if dry_run else "Final"
    print(
        f"\n=== {label} summary ===\n"
        f"  created: {stats.created}\n"
        f"  updated: {stats.updated}\n"
        f"  skipped: {stats.skipped}\n"
        f"  errored: {stats.errored}"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "UPSERT channels from a Perplexity-seed CSV into "
            "creative_radar.channel."
        ),
    )
    parser.add_argument("csv_path", type=Path, help="Path to the CSV file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing to the DB.",
    )
    args = parser.parse_args(argv)

    with Session(engine) as session:
        try:
            stats = run(args.csv_path, session=session)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "\n=== ABORT ===\n"
                f"  {type(exc).__name__}: {exc}\n"
                "  Transaktion rückgängig gemacht — keine Änderungen committed.",
                file=sys.stderr,
            )
            return 1
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
        _print_summary(stats, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
