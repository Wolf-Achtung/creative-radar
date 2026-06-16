#!/usr/bin/env python3
"""Diagnose Stufe-2 / Weg-1 — Sample der ``no_title_match`` sub2-Posts.

NUR DIAGNOSE. Read-only. Schreibt nichts, fasst keine Engine an.

Hintergrund: Die UNKNOWN-Zerlegung (scripts/diag_days_to_release_unknown.py)
zeigt zu 96-97 % ``sub2 = assets_no_title_id`` — der Post hat Assets, aber
keines davon hat eine ``title_id`` gesetzt. Vision laeuft, der Matcher
verwirft nichts, aber er findet keinen Titel.

Dieses Script zieht eine Stichprobe der sub2-Posts und zeigt fuer jeden
Post **genau die Felder, die der Matcher liest** (siehe
``title_rematch._build_match_fields``):

    caption (Post)               — post.caption
    ocr_text (Asset)             — asset.ocr_text
    detected_keywords (Asset)    — asset.detected_keywords (JSON-Liste)
    ai_summary_de (Asset)        — asset.ai_summary_de
    ai_summary_en (Asset)        — asset.ai_summary_en
    placement_title_text (Asset) — asset.placement_title_text  (suggested_title)
    visual_notes (Asset)         — asset.visual_notes

Zusatzfelder, die der Matcher NICHT liest, aber fuer die Diagnose relevant
sind:

    vision_description (Asset)   — neue Sprint-5.3.1-Pipeline. Wenn voll
                                   und die obigen Felder leer sind: der
                                   Matcher haette einen Input, sieht ihn
                                   aber nicht (Code-Lese-Luecke).
    visual_analysis_status       — pending / analyzed / text_fallback / ...
    analyzed_at                  — wann die neue Pipeline lief

Plus eine **naive Katalog-Probe**: gibt es im Title-Katalog einen
Eintrag, dessen ``title_original`` oder ``title_local`` als word-bounded
Substring in einem der Matcher-Eingabefelder vorkommt? Wenn ja, liegt der
Titel in der DB und ein einfacher Substring-Matcher haette ihn gefunden —
das deutet auf Recall-Schwaeche der Matching-Logik (Fall 3) statt
Katalog-Luecke (Fall 2).

Output je Post: post_url / published_at / Matcher-Eingaben / vision_description /
naive Katalog-Hits / kurze Heuristik-Klassifikation am Ende.

Aufruf:
    source ~/.creative-radar/db.env && \\
        DATABASE_URL="$CR_DB_URL" python -m scripts.diag_no_title_match_sample
    # optional: explizit pair + limit
    DATABASE_URL="$CR_DB_URL" python -m scripts.diag_no_title_match_sample --pair disney --limit 12
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"diag_no_title_match_sample: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _has_db_config() -> bool:
    if any(os.environ.get(v) for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")):
        return True
    pg = ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE")
    return all(os.environ.get(v) for v in pg)


def _truncate(s, n: int = 220) -> str:
    if s is None:
        return "<NULL>"
    text = str(s).strip()
    if not text:
        return "<leer>"
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= n else text[:n].rstrip() + " …"


def _market_str(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _parse_anchor(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        _die(f"--anchor muss ISO-Datum/Zeit sein, bekam {raw!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="diag_no_title_match_sample.py",
        description="Sample der sub2 'assets_no_title_id'-Posts mit den Feldern, die "
                    "der Matcher liest. Read-only.",
    )
    parser.add_argument("--pair", default="disney", help="Pair-Key (Default: disney).")
    parser.add_argument("--limit", type=int, default=12,
                        help="Wie viele Posts zeigen (Default: 12).")
    parser.add_argument("--anchor", default=None,
                        help="ISO-Datum/Zeit; Default: last_completed_iso_week_anchor().")
    args = parser.parse_args()

    if not _has_db_config():
        _die("Keine DB-Config in der Umgebung. Setze DATABASE_URL.")

    import sqlalchemy as sa
    from sqlmodel import Session, select

    from app.database import engine
    from app.models.entities import Asset, Channel, Post, Title
    from app.services.insight_engine import (
        PAIRS, _platforms_dict_for, last_completed_iso_week_anchor,
    )

    if args.pair not in PAIRS:
        _die(f"Unbekannter Pair-Key: {args.pair!r}. Bekannt: {sorted(PAIRS)}")
    pair_def = PAIRS[args.pair]
    anchor = _parse_anchor(args.anchor) if args.anchor else last_completed_iso_week_anchor()
    window_end = anchor
    window_start = anchor - timedelta(days=30)

    platforms = _platforms_dict_for(pair_def)
    handles = sorted({s["handle"].lower()
                      for specs in platforms.values()
                      for s in specs if s.get("handle")})
    if not handles:
        _die(f"PAIRS[{args.pair}] liefert keine Handles.")

    with Session(engine) as session:
        channel_rows = session.exec(
            select(Channel.id, Channel.market)
            .where(sa.func.lower(Channel.handle).in_(handles))
        ).all()
        channel_market_map = {cid: _market_str(m) for cid, m in channel_rows}
        channel_ids = list(channel_market_map)
        if not channel_ids:
            _die(f"FEHLER: 0 Channels fuer {len(handles)} PAIRS-Handles "
                 f"(.lower()-Match gegen Channel.handle).", code=1)

        # --- sub2-Posts identifizieren (spiegelt die Engine-Bedingung) ---
        # 1) alle Posts im 30d-Fenster fuer das Pair
        posts = list(session.exec(
            select(Post).where(Post.channel_id.in_(channel_ids)).where(
                sa.or_(
                    sa.and_(Post.published_at.is_not(None),
                            Post.published_at >= window_start, Post.published_at <= window_end),
                    sa.and_(Post.published_at.is_(None),
                            Post.detected_at >= window_start, Post.detected_at <= window_end),
                )
            ).order_by(Post.published_at.desc())
        ).all())
        if not posts:
            print(f"# Keine Posts im Fenster fuer {args.pair} "
                  f"({window_start.date()}..{window_end.date()}).")
            return

        post_ids = [p.id for p in posts]

        # 2) sub2 = Post hat >=1 Asset, ABER keines mit title_id (egal welcher review_status).
        #    (Wenn ein title_id-Asset existiert, faellt der Post entweder unter classified
        #    [wenn non-rejected] oder unter sub3 [wenn alle rejected] — beide raus aus sub2.)
        asset_rows = session.exec(
            select(Asset.post_id, Asset.title_id)
            .where(Asset.post_id.in_(post_ids))
        ).all()
        has_any_asset: set = set()
        has_title_id_asset: set = set()
        for pid, tid in asset_rows:
            has_any_asset.add(pid)
            if tid is not None:
                has_title_id_asset.add(pid)
        sub2_posts = [p for p in posts
                      if p.id in has_any_asset and p.id not in has_title_id_asset]

        # Wichtig: das schliesst auch Posts mit ein, deren title_id-Asset evtl. rejected
        # war (sub3) — nein, die haben title_id != NULL, sind also raus. Sauber sub2.

        # Posts, die der ``title_by_post``-Filter der Engine DENNOCH gefangen haette
        # (classified) sind nicht in sub2_posts.

        if not sub2_posts:
            print(f"# Keine sub2-Posts (assets_no_title_id) fuer {args.pair} im Fenster.")
            return

        sample = sub2_posts[: args.limit]

        # --- Title-Katalog vorladen fuer die naive Substring-Probe ---
        # Pool aller aktiven Titles, deren ``title_original`` mindestens 4 Zeichen
        # hat (kuerzere geben zu viele False-Positives). Aliases werden flach
        # aufgeloest. Die Probe ist absichtlich naiv (word-boundary lowercased),
        # damit sie die Frage beantwortet: "Wuerde ein duemmster-Substring-Matcher
        # einen Titel finden?". Wenn ja und der echte Matcher hat NICHT gefunden,
        # ist die Logik schuld, nicht der Katalog.
        titles = list(session.exec(
            select(Title.title_original, Title.title_local, Title.aliases, Title.franchise)
            .where(Title.active == True)  # noqa: E712
        ).all())
        catalog_entries: list[tuple[str, re.Pattern]] = []
        for original, local, aliases, franchise in titles:
            names: list[str] = []
            for v in (original, local, franchise):
                if v and isinstance(v, str) and len(v.strip()) >= 4:
                    names.append(v.strip())
            if isinstance(aliases, list):
                for a in aliases:
                    if isinstance(a, str) and len(a.strip()) >= 4:
                        names.append(a.strip())
            elif isinstance(aliases, str):
                # JSON koennte als String ankommen
                try:
                    parsed = json.loads(aliases)
                    if isinstance(parsed, list):
                        for a in parsed:
                            if isinstance(a, str) and len(a.strip()) >= 4:
                                names.append(a.strip())
                except (ValueError, TypeError):
                    pass
            for name in names:
                # word-boundary lowercased
                try:
                    pattern = re.compile(r"\b" + re.escape(name.lower()) + r"\b")
                except re.error:
                    continue
                catalog_entries.append((name, pattern))

        # --- Assets pro sample-Post laden (non-rejected, weil die wuerde der
        # Matcher beim Re-Match wieder anfassen) ---
        sample_post_ids = [p.id for p in sample]
        from app.models.entities import ReviewStatus
        sample_assets = list(session.exec(
            select(Asset).where(Asset.post_id.in_(sample_post_ids))
            .where(Asset.review_status != ReviewStatus.REJECTED)
            .order_by(Asset.post_id, Asset.created_at.asc())
        ).all())
        assets_by_post: dict = {}
        for a in sample_assets:
            assets_by_post.setdefault(a.post_id, []).append(a)

        # --- Ausgabe ---
        print(f"# sub2-Sample — pair={args.pair} anchor={window_end.date()} "
              f"window={window_start.date()}..{window_end.date()}")
        print(f"# sub2_total={len(sub2_posts)}  showing={len(sample)}  "
              f"catalog_active_titles={len(titles)}  catalog_name_patterns={len(catalog_entries)}")
        print()

        stat_no_input_text = 0
        stat_text_no_catalog_hit = 0
        stat_text_with_catalog_hit = 0
        stat_vision_desc_only = 0

        for i, p in enumerate(sample, start=1):
            mkt = channel_market_map.get(p.channel_id, "?")
            assets = assets_by_post.get(p.id, [])
            # Engine-Sicht: irgendein Matcher-Feld im Asset gesetzt?
            matcher_text_parts: list[str] = []
            vision_desc_parts: list[str] = []
            for a in assets:
                for f in (a.ocr_text, a.ai_summary_de, a.ai_summary_en,
                          a.placement_title_text, a.visual_notes):
                    if f and isinstance(f, str) and f.strip():
                        matcher_text_parts.append(f.strip())
                if isinstance(a.detected_keywords, list) and a.detected_keywords:
                    matcher_text_parts.extend(str(k) for k in a.detected_keywords if k)
                if a.vision_description and a.vision_description.strip():
                    vision_desc_parts.append(a.vision_description.strip())
            if p.caption and p.caption.strip():
                matcher_text_parts.append(p.caption.strip())

            matcher_blob = " | ".join(matcher_text_parts).lower()
            vision_blob = " | ".join(vision_desc_parts).lower()
            search_blob = (matcher_blob + " " + vision_blob).strip()

            catalog_hits: list[str] = []
            seen = set()
            for name, pat in catalog_entries:
                if name.lower() in seen:
                    continue
                if pat.search(search_blob):
                    catalog_hits.append(name)
                    seen.add(name.lower())
                    if len(catalog_hits) >= 5:
                        break

            # Heuristik-Klassifikation
            if not matcher_text_parts and not vision_desc_parts:
                klass = "A leerer_input (kein Text in Matcher-Feldern UND kein vision_description)"
                stat_no_input_text += 1
            elif not matcher_text_parts and vision_desc_parts:
                klass = "B nur_vision_description (Matcher liest dieses Feld NICHT)"
                stat_vision_desc_only += 1
            elif catalog_hits:
                klass = f"C text+katalog-treffer (naiver Substring fand: {catalog_hits[:3]}) → Logik-Recall"
                stat_text_with_catalog_hit += 1
            else:
                klass = "D text_ohne_katalog-treffer (Katalog-Luecke ODER kein Filmbezug)"
                stat_text_no_catalog_hit += 1

            statuses = sorted({a.visual_analysis_status for a in assets})
            analyzed = sorted({a.analyzed_at.date().isoformat() for a in assets
                               if a.analyzed_at is not None})
            print(f"--- [{i:>2}/{len(sample)}] {args.pair} {mkt} "
                  f"published_at={p.published_at.isoformat() if p.published_at else '<NULL>'} ---")
            print(f"  post_url       : {p.post_url}")
            print(f"  assets         : n={len(assets)} status={statuses} "
                  f"analyzed_at={analyzed or '<keiner>'}")
            print(f"  caption        : {_truncate(p.caption)}")
            for j, a in enumerate(assets, start=1):
                print(f"  asset[{j}]")
                print(f"    asset_type            : {getattr(a.asset_type, 'value', a.asset_type)}")
                print(f"    placement_title_text  : {_truncate(a.placement_title_text)}")
                print(f"    ocr_text              : {_truncate(a.ocr_text)}")
                if isinstance(a.detected_keywords, list) and a.detected_keywords:
                    print(f"    detected_keywords     : {a.detected_keywords[:8]}"
                          f"{' …' if len(a.detected_keywords) > 8 else ''}")
                else:
                    print(f"    detected_keywords     : <leer>")
                print(f"    ai_summary_de         : {_truncate(a.ai_summary_de)}")
                print(f"    ai_summary_en         : {_truncate(a.ai_summary_en)}")
                print(f"    visual_notes          : {_truncate(a.visual_notes)}")
                print(f"    vision_description    : {_truncate(a.vision_description)}  "
                      f"[nicht im Matcher]")
            print(f"  katalog-probe  : {('hits=' + str(catalog_hits)) if catalog_hits else 'keine Treffer'}")
            print(f"  klass-heuristik: {klass}")
            print()

        total = len(sample)
        def pct(n): return f"{n} ({100.0 * n / total:.0f}%)" if total else f"{n}"
        print("# Stichproben-Heuristik (NICHT die finale Klassifikation — Wolf-Auge entscheidet):")
        print(f"#   A leerer_input                : {pct(stat_no_input_text)}")
        print(f"#   B nur_vision_description      : {pct(stat_vision_desc_only)}")
        print(f"#   C text + katalog-treffer      : {pct(stat_text_with_catalog_hit)}")
        print(f"#   D text ohne katalog-treffer   : {pct(stat_text_no_catalog_hit)}")
        print("#")
        print("# Lese-Schluessel:")
        print("#   A → Vision liefert keinen Text → Vision-Output-Qualitaet / OCR-Pipeline")
        print("#   B → Vision liefert Text, aber im Feld, das der Matcher nicht liest → Lese-Luecke im Matcher")
        print("#   C → Matcher haette Treffer, hat aber nicht gefunden → Matching-Logik (Recall)")
        print("#   D → Kein Treffer in der Katalog-Probe → Katalog-Luecke ODER legitim ohne Filmbezug (BTS/Brand)")


if __name__ == "__main__":
    main()
