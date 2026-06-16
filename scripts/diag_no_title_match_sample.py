#!/usr/bin/env python3
"""Diagnose Stufe-2 / Weg-1 — WARUM matcht der Matcher die sub2-Posts nicht?

NUR DIAGNOSE. Read-only. ``find_best_title_match`` liest nur (Title-Bundle),
schreibt nichts; dieses Script committet nichts und fasst keine Engine an.

Kontext: Die sub2-Kohorte (``assets_no_title_id``) dominiert no_title_match zu
96-97 %. Eine erste naive Substring-Katalog-Probe lieferte irrefuehrend "100 % D
(kein Treffer)" — WEIL die naive Probe selbst KEINE Hashtag-Normalisierung macht
(``#ToyStory5`` ist kein Wortgrenzen-Treffer von "toy story 5"). Genau diese
Normalisierung ist aber Teil des echten Matchers.

Dieses Script laeuft daher den ECHTEN Matcher (``find_best_title_match`` mit
denselben Feldern wie ``title_rematch._build_match_fields``) pro Asset und zeigt,
was er zurueckgibt — plus eine HASHTAG-BEWUSSTE Katalog-Probe (mit
``_split_hashtag`` + Compact-Form, wie der Matcher selbst), um Recall-Miss
(Titel im Katalog, Matcher findet ihn nicht) von Katalog-Luecke (Titel nicht in
der Title-Tabelle) zu trennen.

Klassifikation pro Post (auf Basis des ECHTEN Matcher-Ergebnisses):
  A empty_input            — keine Matcher-Felder UND kein vision_description
  B vision_only            — Matcher-Felder leer, aber vision_description gefuellt
                             (der Matcher liest dieses Sprint-5.3.1-Feld NICHT)
  E found_but_unsafe       — Matcher liefert einen Titel, aber is_safe_auto_match
                             ist False (confidence < 0.95, unsichere source, oder
                             only_from_placement) → Recall/Schwellen-Defekt
  C recall_miss            — Matcher liefert KEINEN Titel, aber die hashtag-bewusste
                             Katalog-Probe findet einen aktiven Titel im Text
                             → Matching-Logik verfehlt ihn (Recall)
  D catalog_gap_or_nofilm  — Matcher leer UND Katalog-Probe leer → Titel nicht im
                             Katalog ODER legitim ohne Filmbezug (BTS/Brand) —
                             Wolf-Auge entscheidet welches von beiden

Aufruf:
    source ~/.creative-radar/db.env && \\
        DATABASE_URL="$CR_DB_URL" python -m scripts.diag_no_title_match_sample
    DATABASE_URL="$CR_DB_URL" python -m scripts.diag_no_title_match_sample --pair disney --limit 15
"""
from __future__ import annotations

import argparse
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
    return all(os.environ.get(v) for v in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE"))


def _truncate(s, n: int = 200) -> str:
    if s is None:
        return "<NULL>"
    text = re.sub(r"\s+", " ", str(s).strip())
    if not text:
        return "<leer>"
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
        description="Laeuft den echten Matcher auf sub2-Posts + hashtag-bewusste "
                    "Katalog-Probe. Read-only.",
    )
    parser.add_argument("--pair", default="disney", help="Pair-Key (Default: disney).")
    parser.add_argument("--limit", type=int, default=15, help="Wie viele Posts (Default: 15).")
    parser.add_argument("--anchor", default=None,
                        help="ISO-Datum/Zeit; Default: last_completed_iso_week_anchor().")
    parser.add_argument("--title-probe", default=None,
                        help="Komma-Liste von Titel-Namen (z.B. 'luca,hoppers,loki'). Prueft "
                             "DIREKT, ob sie im aktiven Katalog liegen (beantwortet die "
                             "Katalog-Gap-vs-Recall-Frage). Laeuft vor dem Sample.")
    args = parser.parse_args()

    if not _has_db_config():
        _die("Keine DB-Config in der Umgebung. Setze DATABASE_URL.")

    import sqlalchemy as sa
    from sqlmodel import Session, select

    from app.database import engine
    from app.models.entities import Asset, Channel, Post, ReviewStatus, Title
    from app.services.insight_engine import (
        PAIRS, _platforms_dict_for, last_completed_iso_week_anchor,
    )
    from app.services.title_rematch import _build_match_fields
    from app.services.whitelist_matcher import (
        _normalize_text,
        _split_hashtag,
        build_normalized_index,
        find_best_title_match,
        is_safe_auto_match,
        load_title_bundle,
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

        # sub2 = >=1 Asset, aber KEINES mit title_id (egal welcher review_status).
        comp_rows = session.exec(
            select(Asset.post_id, Asset.title_id).where(Asset.post_id.in_(post_ids))
        ).all()
        has_any_asset: set = set()
        has_title_id_asset: set = set()
        for pid, tid in comp_rows:
            has_any_asset.add(pid)
            if tid is not None:
                has_title_id_asset.add(pid)
        sub2_posts = [p for p in posts
                      if p.id in has_any_asset and p.id not in has_title_id_asset]
        if not sub2_posts:
            print(f"# Keine sub2-Posts (assets_no_title_id) fuer {args.pair} im Fenster.")
            return
        sample = sub2_posts[: args.limit]

        # Matcher-Bundle EINMAL laden (wie der Cron-Rematch).
        bundle = load_title_bundle(session)
        norm_index = build_normalized_index(bundle)
        # Compact-Index (space-stripped) fuer die hashtag-bewusste Katalog-Probe.
        compact_to_norm = {n.replace(" ", ""): n for n in norm_index}

        # --- Punkt 2: direkte Katalog-Mitgliedschafts-Probe ---
        # Beantwortet "Steht <Titel> im aktiven Katalog?" unabhaengig von der
        # Matcher-Logik. Wenn ein vom Auge erkannter Titel hier FEHLT → Katalog-
        # Luecke (behebbar via TMDb-Nachzug). Wenn er DA ist, der Matcher ihn aber
        # nicht setzt → Matcher-Recall-Defekt (z.B. Kurz-Titel-Guard, Ziffern-Hashtag).
        if args.title_probe:
            terms = [t.strip() for t in args.title_probe.split(",") if t.strip()]
            print("# Katalog-Mitgliedschafts-Probe (aktive Titles):")
            for term in terms:
                nt = _normalize_text(term)
                matches: list[str] = []
                for title, cmap in bundle:
                    for srckey, vals in cmap.items():
                        for v in vals:
                            nv = _normalize_text(v)
                            if not nv:
                                continue
                            exact = nv == nt
                            contains = (
                                len(nt) >= 3 and re.search(r"\b" + re.escape(nt) + r"\b", nv)
                            ) or (
                                len(nv) >= 3 and re.search(r"\b" + re.escape(nv) + r"\b", nt)
                            )
                            if exact or contains:
                                matches.append(f"{title.title_original!r}[{srckey}:{v!r}]")
                uniq = sorted(set(matches))
                verdict = f"IM KATALOG → {uniq[:5]}" if uniq else "NICHT im aktiven Katalog (Luecke)"
                print(f"#   {term!r:>16}: {verdict}")
            print()

        # Non-rejected Assets je sample-Post (das, was der Rematch wieder anfasst).
        sample_post_ids = [p.id for p in sample]
        sample_assets = list(session.exec(
            select(Asset).where(Asset.post_id.in_(sample_post_ids))
            .where(Asset.review_status != ReviewStatus.REJECTED)
            .order_by(Asset.post_id, Asset.created_at.asc())
        ).all())
        assets_by_post: dict = {}
        for a in sample_assets:
            assets_by_post.setdefault(a.post_id, []).append(a)

        def catalog_reachable(text_blob: str) -> list[str]:
            """Hashtag-bewusste Katalog-Probe: spiegelt _split_hashtag + Compact-
            Form. Liefert die aktiven Titel, die der Matcher ueber Hashtag-Split
            ODER Wortgrenzen-Substring SEHEN koennte. Wenn nicht-leer und der echte
            Matcher liefert dennoch nichts → Recall-Miss."""
            hits: list[str] = []
            seen: set = set()
            norm_blob = _normalize_text(text_blob)
            # 1) Hashtag-Split-Form jedes Tags gegen den Norm-Index.
            split_forms = {_split_hashtag(raw) for raw in re.findall(r"#[\wÀ-ÿ]+", text_blob)}
            split_forms.discard("")
            for sf in split_forms:
                if sf in norm_index and sf not in seen:
                    hits.append(sf); seen.add(sf)
                compact = sf.replace(" ", "")
                if len(compact) > 4:
                    for ck, nk in compact_to_norm.items():
                        if len(ck) > 4 and ck in compact and len(ck) / len(compact) >= 0.5 and nk not in seen:
                            hits.append(nk); seen.add(nk)
            # 2) Wortgrenzen-Substring im normalisierten Gesamttext.
            for nk in norm_index:
                if len(nk) >= 4 and nk not in seen:
                    if re.search(r"\b" + re.escape(nk) + r"\b", norm_blob):
                        hits.append(nk); seen.add(nk)
            return hits[:6]

        print(f"# sub2 Matcher-Diagnose — pair={args.pair} anchor={window_end.date()} "
              f"window={window_start.date()}..{window_end.date()}")
        print(f"# sub2_total={len(sub2_posts)}  showing={len(sample)}  "
              f"active_titles={len(bundle)}  norm_index_keys={len(norm_index)}")
        print()

        stats = {"A_empty": 0, "B_vision_only": 0, "E_found_unsafe": 0,
                 "C_recall_miss": 0, "D_brand": 0, "D_none": 0, "X_would_match": 0}

        for i, p in enumerate(sample, start=1):
            assets = assets_by_post.get(p.id, [])
            mkt = channel_market_map.get(p.channel_id, "?")

            # Echten Matcher pro Asset laufen lassen, bestes Ergebnis behalten.
            best = None  # (is_safe, confidence, MatchResult, asset)
            any_matcher_input = False
            any_vision_desc = False
            for a in assets:
                fields = _build_match_fields(a, p)
                if any(
                    (isinstance(v, str) and v.strip()) or (isinstance(v, list) and v)
                    for v in fields.values()
                ):
                    any_matcher_input = True
                if a.vision_description and a.vision_description.strip():
                    any_vision_desc = True
                m = find_best_title_match(
                    session, p.caption or "", fields=fields,
                    published_at=p.published_at,
                    cached_bundle=bundle, cached_normalized_index=norm_index,
                )
                safe = is_safe_auto_match(m)
                key = (1 if safe else 0, m.confidence)
                if best is None or key > (1 if best[0] else 0, best[1].confidence):
                    best = (safe, m, a)
            safe, m, _a = best if best else (False, None, None)

            blob_parts = [p.caption or ""]
            for a in assets:
                for f in (a.ocr_text, a.ai_summary_de, a.ai_summary_en,
                          a.placement_title_text, a.visual_notes, a.vision_description):
                    if f:
                        blob_parts.append(str(f))
                if isinstance(a.detected_keywords, list):
                    blob_parts.extend(str(k) for k in a.detected_keywords if k)
            reachable = catalog_reachable(" ".join(blob_parts))

            # Klassifikation auf Basis des echten Matcher-Ergebnisses.
            if m is not None and m.title is not None and safe:
                klass = "X would_match (Matcher liefert sicheren Treffer — Rematch wuerde setzen!)"
                stats["X_would_match"] += 1
            elif m is not None and m.title is not None and not safe:
                klass = (f"E found_but_unsafe (source={m.source} conf={m.confidence:.2f} "
                         f"→ unter Auto-Schwelle / unsicher)")
                stats["E_found_unsafe"] += 1
            elif not any_matcher_input and any_vision_desc:
                klass = "B vision_only (Matcher-Felder leer, vision_description gefuellt → Lese-Luecke)"
                stats["B_vision_only"] += 1
            elif not any_matcher_input and not any_vision_desc:
                klass = "A empty_input (kein Text fuer den Matcher)"
                stats["A_empty"] += 1
            elif reachable:
                klass = f"C recall_miss (Katalog erreichbar: {reachable[:3]} — Matcher fand nichts)"
                stats["C_recall_miss"] += 1
            elif m is not None and m.source == "brand_whitelist":
                # Brand ist LETZTER Fallback (whitelist_matcher.py:399-415): er feuert NUR,
                # wenn kein strong/fuzzy Titel-Treffer da war. Brand ueberschattet also
                # KEINEN Titel — er ist Symptom: "Film-/Streaming-Promo erkannt, aber kein
                # Katalog-Titel gefunden". Starkes Indiz fuer Katalog-Luecke (behebbar via
                # TMDb-Nachzug), NICHT fuer titellos.
                klass = (f"D_brand catalog_gap (brand_whitelist={m.suggested_title!r} feuerte als "
                         f"Fallback → Film-Promo, aber kein Katalog-Titel)")
                stats["D_brand"] += 1
            else:
                klass = "D_none catalog_gap_or_nofilm (kein Titel-/Brand-Treffer → Luecke ODER titellos)"
                stats["D_none"] += 1

            statuses = sorted({a.visual_analysis_status for a in assets})
            analyzed = sorted({a.analyzed_at.date().isoformat() for a in assets if a.analyzed_at})
            print(f"--- [{i:>2}/{len(sample)}] {args.pair} {mkt} "
                  f"published_at={p.published_at.isoformat() if p.published_at else '<NULL>'} ---")
            print(f"  post_url       : {p.post_url}")
            print(f"  assets         : n={len(assets)} visual_status={statuses} "
                  f"analyzed_at={analyzed or '<keiner>'}")
            print(f"  caption        : {_truncate(p.caption)}")
            for j, a in enumerate(assets, start=1):
                kw = a.detected_keywords if isinstance(a.detected_keywords, list) else []
                print(f"  asset[{j}] type={getattr(a.asset_type,'value',a.asset_type)}")
                print(f"    placement_title_text: {_truncate(a.placement_title_text)}")
                print(f"    ocr_text            : {_truncate(a.ocr_text)}")
                print(f"    detected_keywords   : {kw[:8] if kw else '<leer>'}")
                print(f"    ai_summary_de       : {_truncate(a.ai_summary_de)}")
                print(f"    ai_summary_en       : {_truncate(a.ai_summary_en)}")
                print(f"    vision_description  : {_truncate(a.vision_description)}  [NICHT im Matcher]")
            if m is not None:
                print(f"  MATCHER (echt) : source={m.source} confidence={m.confidence:.2f} "
                      f"safe={safe} title={m.title.title_original if m.title else None!r} "
                      f"suggested={m.suggested_title!r}")
            print(f"  katalog-probe  : {reachable if reachable else 'keine erreichbaren Titel'}")
            print(f"  KLASSE         : {klass}")
            print()

        total = len(sample)
        def pct(n): return f"{n} ({100.0 * n / total:.0f}%)" if total else f"{n}"
        print("# Klassen-Verteilung der Stichprobe (echter Matcher):")
        print(f"#   A empty_input              : {pct(stats['A_empty'])}   (Vision-Output leer)")
        print(f"#   B vision_only              : {pct(stats['B_vision_only'])}   (Matcher-Lese-Luecke)")
        print(f"#   E found_but_unsafe         : {pct(stats['E_found_unsafe'])}   (Recall/Schwelle)")
        print(f"#   C recall_miss              : {pct(stats['C_recall_miss'])}   (Logik-Recall: Titel im Katalog, Matcher verfehlt)")
        print(f"#   D_brand catalog_gap        : {pct(stats['D_brand'])}   (brand_whitelist-Fallback: Film-Promo, kein Katalog-Titel → Luecke)")
        print(f"#   D_none gap_or_titellos     : {pct(stats['D_none'])}   (kein Titel/Brand: Katalog-Luecke ODER echt titellos)")
        print(f"#   X would_match (Anomalie)   : {pct(stats['X_would_match'])}   (Matcher koennte setzen → Rematch-Anwendungs-Luecke)")
        print("#")
        print("# Behebbar (Matcher/Katalog): A B C E + D_brand (+ Katalog-Luecken-Teil von D_none).")
        print("# Echt unvermeidbar: nur der titellose Teil von D_none (BTS/Brand-Posts ohne Film).")
        print("# brand_whitelist ueberschattet KEINEN Titel — es ist letzter Fallback")
        print("# (whitelist_matcher.py:399-415); D_brand markiert also Katalog-Luecke, nicht Brand-Bug.")
        print("# Tipp: --title-probe 'luca,hoppers,loki' prueft direkt die Katalog-Mitgliedschaft.")


if __name__ == "__main__":
    main()
