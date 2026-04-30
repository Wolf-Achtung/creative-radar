from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.entities import Asset, Channel, Market, Post, Title
from app.services.report_selector import (
    EVIDENCE_LABELS,
    _displayable_image_url,
    _evidence_quality,
)

ASSET_TYPE_LABELS = {
    "Unknown": "Nicht klassifiziert",
    "Trailer Drop": "Trailer-/Release-Post",
    "Release Reminder": "Release Reminder",
    "Franchise / Brand Post": "Franchise-/Brand-Post",
    "Event / Festival": "Event-/Festival-Post",
    "Character / Cast Post": "Character-Quote",
    "Ticket CTA": "Ticket-/CTA-Post",
    "CTA Post": "Ticket-/CTA-Post",
}


def _label(asset: Asset) -> str:
    return ASSET_TYPE_LABELS.get(str(asset.asset_type.value), "Nicht klassifiziert")


def _why_relevant(quality: str, has_signal: bool) -> str:
    if quality == "secure" and has_signal:
        return "Gesichertes Evidence-Bild und starkes Textsignal."
    if quality == "secure":
        return "Gesichertes Evidence-Bild."
    if quality == "external":
        return "Kontextfund mit externer Bildquelle (nicht dauerhaft gesichert)."
    if quality == "source_only":
        return "Kontextfund mit Vorschau-Bildquelle, nicht intern gespeichert."
    return "Kontextfund ohne belastbare Bildevidenz."


def _pair_group_key(asset: Asset, title: Title | None) -> str:
    """Mirror of report_selector._asset_title_key — single source of truth lives there.

    Kept simple here because the renderer must group the same way the selector did,
    using the same precedence (title_id > title_original > placement_title_text >
    kinetic_text). If the selector logic changes, update both.
    """
    if asset.title_id:
        return str(asset.title_id)
    if title and title.title_original:
        return str(title.title_original)
    for field in ("placement_title_text", "kinetic_text"):
        value = getattr(asset, field, None)
        if value:
            return str(value)
    return ""


def _row(session: Session, asset: Asset) -> dict[str, Any]:
    post = session.get(Post, asset.post_id)
    channel = session.get(Channel, post.channel_id) if post else None
    title = session.get(Title, asset.title_id) if asset.title_id else None
    quality = _evidence_quality(asset)
    return {
        "asset": asset,
        "post": post,
        "channel": channel,
        "title": title.title_original if title else "Nicht klassifiziert",
        "market": channel.market.value if channel else "UNKNOWN",
        "channel_name": channel.name if channel else "Unklar",
        "evidence_quality": quality,
        "evidence_label": EVIDENCE_LABELS[quality],
        "display_image_url": _displayable_image_url(asset),
        "secure": quality == "secure",
        "label": _label(asset),
        "pair_group_key": _pair_group_key(asset, title),
    }


def _finding_html(r: dict[str, Any]) -> str:
    a = r["asset"]
    p = r["post"]
    has_signal = bool(a.ocr_text or a.has_kinetic or a.has_title_placement)
    evidence = (
        f'<img src="{r["display_image_url"]}" alt="Evidence" />'
        if r["display_image_url"]
        else ""
    )
    return f"""
    <article class=\"asset\">
      <h3>{r['title']} – {r['label']}</h3>
      <p><strong>Kanal/Markt:</strong> {r['channel_name']} / {r['market']}</p>
      <p><strong>Creative-Mechanik:</strong> {a.ai_trend_notes or a.ai_summary_de or 'Kurzer Hook mit CTA-/Release-Fokus.'}</p>
      <p><strong>Warum relevant:</strong> {_why_relevant(r['evidence_quality'], has_signal)}</p>
      <p><strong>Quelle:</strong> {r['evidence_label']}</p>
      {evidence}
      <p><a href=\"{p.post_url if p else '#'}\">Originalpost</a></p>
    </article>
    """


def generate_report_html(session: Session, report_type: str, asset_ids: list[UUID], date_from: date, date_to: date) -> tuple[str, dict]:
    rows = [_row(session, session.get(Asset, aid)) for aid in asset_ids if session.get(Asset, aid)]
    # 'strong' bevorzugt secure-Evidence + Titel + Textsignal. Solange SECURE_STORAGE_ENABLED
    # off ist, fällt 'strong' leer und wir nutzen alle rows; das ist gewollt.
    strong = [
        r for r in rows
        if r["secure"]
        and r["title"] != "Nicht klassifiziert"
        and (r["asset"].ocr_text or r["asset"].has_kinetic or r["asset"].has_title_placement)
    ]
    top = (strong or rows)[:5]
    data_gaps = [
        r for r in rows
        if r["evidence_quality"] != "secure"
        or r["title"] == "Nicht klassifiziert"
        or not r["display_image_url"]
        or (r["asset"].visual_analysis_status in {"no_source", "fetch_failed", "error"})
    ]

    pattern_counter = Counter(r["label"] for r in rows)
    patterns = "".join(f"<li>{k}: {v}</li>" for k, v in pattern_counter.most_common(6)) or "<li>Keine Muster erkannt.</li>"

    summary_de = "Diese Woche dominieren Trailer-/Release-Posts mit kurzem Hook, klaren Claims und CTA-Nähe; hochwertige Beispiele zeigen wiederkehrende Ticket- und Event-Mechaniken."
    trend_de = "Wiederkehrend sind Trailer-Drops, Release-Erinnerungen und CTA-Formulierungen; belastbare visuelle Evidenz ist noch nicht in allen Treffern vorhanden."

    if report_type == "de_us_comparison":
        # Trust the selector: every row passed in here was already accepted as part of
        # a DE/US pair (selector's pair_group_key + pair_market). The renderer only
        # groups by the same key for layout — it does not re-decide what is or isn't a pair.
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: {"DE": [], "US": [], "INT": []}
        )
        for r in rows:
            key = r["pair_group_key"] or r["title"]
            bucket = r["market"] if r["market"] in {"DE", "US", "INT"} else "INT"
            grouped[key][bucket].append(r)
        pair_blocks = []
        for _, g in grouped.items():
            if not (g["DE"] and (g["US"] or g["INT"])):
                continue
            intl = g["US"] + g["INT"]
            display_title = g["DE"][0]["title"]
            pair_blocks.append(
                f"<article class='asset'><h3>{display_title}</h3>"
                f"<p><strong>DE:</strong> {g['DE'][0]['label']} · {g['DE'][0]['asset'].placement_title_text or 'lokalisierter Claim'}</p>"
                f"<p><strong>US/INT:</strong> {intl[0]['label']} · {intl[0]['asset'].placement_title_text or 'event-/ticketing-näher'}</p></article>"
            )
        body = "".join(pair_blocks) or "<p>Keine belastbaren DE/US-Paare im Zeitraum.</p>"
        title = "Creative Radar – DE/US Vergleich"
        main = f"<section><h2>2. Titel-/Franchise-Vergleiche</h2>{body}</section><section><h2>3. CTA-/Text-/Kinetic-Vergleich</h2><p>Vergleich basiert auf ausgewählten reportfähigen Assets ({len(top)} von {len(rows)}).</p></section>"
    elif report_type == "visual_kinetics":
        title = "Creative Radar – Bild/Text & Kinetics"
        body = "".join(_finding_html(r) for r in top) or "<p>Keine belastbaren Bild/Text-Signale.</p>"
        main = f"<section><h2>2. Sichtbarer Text / OCR</h2>{body}</section><section><h2>3. Titel-/Claim-Platzierungen & CTA-Muster</h2><ul>{patterns}</ul></section>"
    else:
        title = "Creative Radar – Wochenüberblick"
        findings = "".join(_finding_html(r) for r in top) or "<p>Keine Top Findings im Zeitraum.</p>"
        main = f"<section><h2>2. Top Creative Findings</h2>{findings}</section><section><h2>3. Wiederkehrende Muster</h2><ul>{patterns}</ul></section><section><h2>4. Auffällige Kanäle / Märkte</h2><p>Report basiert auf {len(rows)} vorgeschlagenen Assets.</p></section>"

    gaps = "".join(
        f"<li>{r['title']} ({r['channel_name']}): {r['evidence_label']}</li>"
        for r in data_gaps
    ) or "<li>Keine Datenlücken im ausgewählten Set.</li>"

    html = f"""<!doctype html><html lang='de'><head><meta charset='utf-8' /><title>{title}</title>
    <style>body{{font-family:Arial,sans-serif;line-height:1.5;margin:40px;color:#172033}} .asset{{border:1px solid #d8dde8;border-radius:12px;padding:16px;margin:16px 0}} img{{max-width:360px;border-radius:8px;border:1px solid #ddd;display:block;margin:12px 0}}</style></head>
    <body><header><h1>{title}</h1><p>Zeitraum: {date_from} bis {date_to}</p></header>
    <section><h2>1. Executive Summary</h2><p>{summary_de}</p></section>{main}
    <section><h2>Datenqualität & Lücken</h2><ul>{gaps}</ul></section></body></html>"""
    meta = {
        "executive_summary_de": summary_de,
        "executive_summary_en": "Creative highlights with report-type specific structure and evidence quality filtering.",
        "trend_summary_de": trend_de,
        "report_type": report_type,
    }
    return html, meta
