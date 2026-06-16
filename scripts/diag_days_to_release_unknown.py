#!/usr/bin/env python3
"""Diagnose Stufe-2 / Weg-1 — Zerlegung des ``days_to_release``-UNKNOWN-Anteils.

NUR DIAGNOSE. Read-only: das Script liest die DB und ruft Engine-Helfer auf,
es schreibt nichts und fasst ``insight_engine.py`` nicht an.

Hintergrund: Der Sicht-Check 16.06. zeigt sehr hohe ``days_to_release="unknown"``-
Anteile (disney 75 %, warnerbros 54 %, sonypictures 36 %). Das untergraebt die
Cross-Tab-Basis des praeskriptiven Moduls. ``_classify_days_to_release`` liefert
UNKNOWN nur, wenn die Tages-Differenz ``None`` ist — und die entsteht ausschliesslich
ueber zwei Pfade in ``_compute_days_to_release_distribution`` (insight_engine.py:2349):

  (a) no_title_match       — der Post hat KEIN nicht-``rejected`` Asset mit ``title_id``
                             (kein Film zugeordnet) -> ``title_by_post`` kennt den Post nicht.
  (b) match_no_release_date — Titel gematcht, aber ``_pick_release_date`` gibt ``None``,
                             d.h. ``release_date_us`` UND ``release_date_de`` sind beide NULL
                             (der Markt-Fallback DE/US/UK greift auf beide Spalten zu).
  (c) no_ref_time           — ``published_at`` UND ``detected_at`` beide NULL. Praktisch
                             unmoeglich (``Post.detected_at`` ist NOT NULL), nur der
                             Vollstaendigkeit halber gezaehlt.

Das Script spiegelt ``_compute_days_to_release_distribution`` 1:1 (gleiches 30-Tage-
Fenster, gleiche Channel-Aufloesung, gleiche Asset/Title-Join-Sortierung) und splittet
den UNKNOWN-Bucket in (a)/(b)/(c) auf, plus die echten Cadence-Buckets als Kontrolle.

Aufruf (lokal gegen Prod, analog Sicht-Check):
    source ~/.creative-radar/db.env && \
        DATABASE_URL="$CR_DB_URL" python -m scripts.diag_days_to_release_unknown
    # optional ein oder mehrere Pairs explizit:
    DATABASE_URL="$CR_DB_URL" python -m scripts.diag_days_to_release_unknown disney
Default ohne Argument: disney, sonypictures, warnerbros nacheinander.

Anker: ``last_completed_iso_week_anchor()`` (dieselbe abgeschlossene KW, die der
Montags-Cron und ``/weekly`` Usern zeigen). Ueberschreibbar via ``--anchor YYYY-MM-DD``.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Das Backend-Package (``app.*``) liegt unter ``<repo>/backend`` — gleicher
# Pfad-Seam wie scripts/brief_diff_harness.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# Default-Pairs des Sicht-Checks (Reihenfolge wie im Befund).
_DEFAULT_PAIRS = ["disney", "sonypictures", "warnerbros"]


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"diag_days_to_release_unknown: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _has_db_config() -> bool:
    """Spiegelt die Quellen, die app.database.resolve_database_url() akzeptiert."""
    if any(os.environ.get(v) for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")):
        return True
    pg = ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE")
    return all(os.environ.get(v) for v in pg)


def _parse_anchor(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        _die(f"--anchor muss ein ISO-Datum/Zeit sein (z.B. 2026-06-14), bekam {raw!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _market_str(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def diagnose_pair(session, pair_key: str, anchor: datetime) -> bool:
    """Wertet einen Pair aus. Gibt True bei Erfolg zurueck, False wenn der
    Channel-Selbsttest fehlschlaegt (0 Channels) — dann wird laut gewarnt
    statt still 0 Posts zu melden."""
    # Lazy-Import: erst NACH der DB-Config-Pruefung, damit ein fehlendes
    # DATABASE_URL nicht in einem kryptischen RuntimeError beim Import endet.
    import sqlalchemy as sa
    from sqlmodel import select

    from app.models.entities import Asset, Channel, Post, Title
    from app.services.insight_engine import (
        PAIRS,
        _classify_days_to_release,
        _pick_release_date,
        _platforms_dict_for,
        _post_age_reference,
        _post_market_for_release_lookup,
    )

    if pair_key not in PAIRS:
        print(f"[{pair_key}] FEHLER: unbekannter Pair-Key. Bekannt: {sorted(PAIRS)}", file=sys.stderr)
        return False

    pair_def = PAIRS[pair_key]
    window_end = anchor
    window_start = anchor - timedelta(days=30)

    # --- Channel-Aufloesung (identisch zu _compute_days_to_release_distribution) ---
    platforms = _platforms_dict_for(pair_def)
    handle_specs = [
        (platform, spec["handle"].lower())
        for platform, specs in platforms.items()
        for spec in specs
        if spec.get("handle")
    ]
    handles = sorted({h for _, h in handle_specs})
    if not handles:
        print(f"[{pair_key}] FEHLER: PAIRS-Definition liefert keine Handles.", file=sys.stderr)
        return False

    channel_rows = session.exec(
        select(Channel.id, Channel.handle, Channel.market)
        .where(sa.func.lower(Channel.handle).in_(handles))
    ).all()

    # --- Robustheits-Selbsttest #1: Handle->Channel-Match verifizieren ---
    matched_handles = {ch_handle.lower() for _, ch_handle, _ in channel_rows}
    missing = [h for h in handles if h not in matched_handles]
    channel_market_map = {cid: _market_str(m) for cid, _h, m in channel_rows}
    channel_ids = list(channel_market_map)

    if not channel_ids:
        # Laut scheitern statt irrefuehrende 0-Posts. Stichprobe echter DB-Handles
        # zur Casing-/Drift-Diagnose mitliefern.
        sample = session.exec(
            select(Channel.handle).order_by(Channel.handle).limit(40)
        ).all()
        print(
            f"[{pair_key}] FEHLER: 0 Channels fuer {len(handles)} PAIRS-Handles gefunden "
            f"(.lower()-Match gegen Channel.handle). "
            f"Erwartete Handles: {handles}. "
            f"DB-Handle-Stichprobe (max 40): {sorted(sample)}",
            file=sys.stderr,
        )
        return False

    if missing:
        # Kein harter Abbruch — Teil-Match ist gueltig (z.B. ein Sub-Brand-Handle
        # ohne Channel-Row), aber sichtbar machen, damit der Anteil nicht still
        # verzerrt wird.
        print(
            f"[{pair_key}] WARNUNG: {len(missing)}/{len(handles)} PAIRS-Handles "
            f"ohne Channel-Row (nicht in die Auswertung eingegangen): {missing}",
            file=sys.stderr,
        )

    # --- Posts im 30-Tage-Fenster (gleicher published_at/detected_at-Fallback) ---
    posts = list(session.exec(
        select(Post).where(Post.channel_id.in_(channel_ids)).where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None),
                        Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None),
                        Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    ).all())

    if not posts:
        print(
            f"[{pair_key}] anchor={window_end.date()} window={window_start.date()}..{window_end.date()} "
            f"channels={len(channel_ids)} (matched_handles={len(matched_handles)}) "
            f"total=0 — keine Posts im Fenster.",
        )
        return True

    # --- Asset+Title pro Post (gleiche Sortierung wie die Engine) ---
    post_ids = [p.id for p in posts]
    prefers_title = sa.case((Asset.title_id.isnot(None), 0), else_=1)
    asset_rows = session.exec(
        select(Asset.post_id, Asset.title_id, Title)
        .join(Title, Asset.title_id == Title.id, isouter=True)
        .where(Asset.post_id.in_(post_ids))
        .where(Asset.review_status != "rejected")
        .order_by(Asset.post_id, prefers_title, Asset.created_at.desc())
    ).all()
    title_by_post: dict = {}
    for post_id, title_id, title in asset_rows:
        if post_id in title_by_post:
            continue
        if title_id is None or title is None:
            continue
        title_by_post[post_id] = title

    # --- Klassifikation + UNKNOWN-Split ---
    cats: Counter = Counter()
    buckets: Counter = Counter()
    no_title_match_ids: list = []
    for post in posts:
        title = title_by_post.get(post.id)
        if title is None:
            cats["a_no_title_match"] += 1
            no_title_match_ids.append(post.id)
            continue
        market = _post_market_for_release_lookup(post, channel_market_map)
        release = _pick_release_date(title, market)
        if release is None:
            cats["b_match_no_release_date"] += 1
            continue
        ref_time = _post_age_reference(post)
        if ref_time is None:
            cats["c_no_ref_time"] += 1
            continue
        post_date = ref_time.date() if hasattr(ref_time, "date") else ref_time
        delta_days = (release - post_date).days
        buckets[_classify_days_to_release(delta_days).value] += 1

    # --- (a)-Unterursachen: WARUM hat der Post kein non-rejected Asset mit title_id? ---
    # Spiegelt die Engine-Bedingung (title_by_post = erstes non-``rejected`` Asset mit
    # title_id+Title). Zerlegt die no_title_match-Posts disjunkt in:
    #   1_no_asset             — Post hat GAR KEIN Asset (Vision-Pipeline nie gelaufen)
    #   2_assets_no_title_id   — Asset(s) vorhanden, aber KEINES hat title_id (Matcher
    #                            fand keinen Titel) — Vision lief, Matching zu schwach
    #   3_title_id_but_filtered — ≥1 Asset MIT title_id vorhanden, aber kein non-rejected
    #                            (alle title_id-Assets rejected). Hier landet auch der
    #                            seltene Orphan-FK-Fall (title_id zeigt auf fehlende
    #                            Title-Row) — unter FK-Constraint praktisch 0.
    sub: Counter = Counter()
    if no_title_match_ids:
        comp_rows = session.exec(
            select(Asset.post_id, Asset.title_id)
            .where(Asset.post_id.in_(no_title_match_ids))
        ).all()
        has_any_asset: set = set()
        has_title_id_asset: set = set()
        for pid, tid in comp_rows:
            has_any_asset.add(pid)
            if tid is not None:
                has_title_id_asset.add(pid)
        for pid in no_title_match_ids:
            if pid not in has_any_asset:
                sub["1_no_asset"] += 1
            elif pid not in has_title_id_asset:
                sub["2_assets_no_title_id"] += 1
            else:
                sub["3_title_id_but_filtered"] += 1

    total = len(posts)
    unknown = cats["a_no_title_match"] + cats["b_match_no_release_date"] + cats["c_no_ref_time"]
    pct = (100.0 * unknown / total) if total else 0.0
    n_a = cats["a_no_title_match"]

    def share(n: int) -> str:
        return f"{n} ({100.0 * n / unknown:.0f}% von unknown)" if unknown else f"{n}"

    def subshare(n: int) -> str:
        return f"{n} ({100.0 * n / n_a:.0f}% von no_title_match)" if n_a else f"{n}"

    print(
        f"[{pair_key}] anchor={window_end.date()} window={window_start.date()}..{window_end.date()} "
        f"channels={len(channel_ids)}"
    )
    print(f"    total_posts          = {total}")
    print(f"    unknown              = {unknown} ({pct:.0f}% von total)")
    print(f"      (a) no_title_match        = {share(cats['a_no_title_match'])}")
    print(f"          ├─ 1 no_asset (kein Asset, Vision nie gelaufen)     = {subshare(sub['1_no_asset'])}")
    print(f"          ├─ 2 assets_no_title_id (Vision ok, Matcher leer)   = {subshare(sub['2_assets_no_title_id'])}")
    print(f"          └─ 3 title_id_but_filtered (gematcht, aber rejected) = {subshare(sub['3_title_id_but_filtered'])}")
    print(f"      (b) match_no_release_date = {share(cats['b_match_no_release_date'])}")
    print(f"      (c) no_ref_time           = {share(cats['c_no_ref_time'])}")
    print(f"    classified (echte Buckets) = {total - unknown}")
    print(f"      buckets = {dict(sorted(buckets.items()))}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="diag_days_to_release_unknown.py",
        description="Zerlegt den days_to_release-UNKNOWN-Anteil in (a) kein Titel-Match, "
        "(b) Match ohne release_date, (c) kein ref_time. Read-only.",
    )
    parser.add_argument(
        "pairs", nargs="*",
        help=f"Pair-Keys (Default: {' '.join(_DEFAULT_PAIRS)}).",
    )
    parser.add_argument(
        "--anchor", default=None,
        help="ISO-Datum/Zeit fuer das Fenster-Ende. Default: last_completed_iso_week_anchor().",
    )
    args = parser.parse_args()

    if not _has_db_config():
        _die(
            "Keine DB-Config in der Umgebung. Setze DATABASE_URL (oder "
            "DATABASE_PRIVATE_URL/PUBLIC_URL bzw. PGHOST/PGUSER/PGPASSWORD/PGDATABASE). "
            "Aufruf: DATABASE_URL=\"$CR_DB_URL\" python -m scripts.diag_days_to_release_unknown"
        )

    # Import erst nach der Config-Pruefung (app.database loest die URL beim Import auf).
    from sqlmodel import Session

    from app.database import engine
    from app.services.insight_engine import last_completed_iso_week_anchor

    anchor = _parse_anchor(args.anchor) if args.anchor else last_completed_iso_week_anchor()
    pairs = args.pairs or _DEFAULT_PAIRS

    print(f"# days_to_release UNKNOWN-Split — anchor={anchor.isoformat()} — pairs={pairs}")
    failures = 0
    with Session(engine) as session:
        for pair_key in pairs:
            # Per-Pair-Isolation: ein DB-/Daten-Fehler bei einem Pair darf die
            # Auswertung der anderen nicht abbrechen (Ziel: alle Pairs in einem
            # Lauf). session.rollback() raeumt eine evtl. fehlgeschlagene
            # Transaktion ab, damit der naechste Pair auf einer sauberen
            # Session weiterliest.
            try:
                ok = diagnose_pair(session, pair_key, anchor)
            except Exception as exc:  # noqa: BLE001 — Diagnose-Tooling, kein Abbruch
                session.rollback()
                print(f"[{pair_key}] FEHLER: {type(exc).__name__}: {exc}", file=sys.stderr)
                ok = False
            if not ok:
                failures += 1
            print()

    if failures:
        _die(f"{failures}/{len(pairs)} Pair(s) mit Selbsttest-Fehler (siehe oben).", code=1)


if __name__ == "__main__":
    main()
