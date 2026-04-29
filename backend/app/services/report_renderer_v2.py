from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.entities import Asset, Channel, Market, Post, Title

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


def _is_secure(url: str | None) -> bool:
    return bool(url and str(url).startswith("/storage/evidence/"))


def _label(asset: Asset) -> str:
    return ASSET_TYPE_LABELS.get(str(asset.asset_type.value), "Nicht klassifiziert")


def _row(session: Session, asset: Asset) -> dict[str, Any]:
    post = session.get(Post, asset.post_id)
    channel = session.get(Channel, post.channel_id) if post else None
    title = session.get(Title, asset.title_id) if asset.title_id else None
    evidence_url = asset.visual_evidence_url or asset.screenshot_url or asset.thumbnail_url
    secure = _is_secure(asset.visual_evidence_url)
    return {
        "asset": asset,
        "post": post,
        "channel": channel,
        "title": title.title_original if title else "Nicht klassifiziert",
        "market": channel.market.value if channel else "UNKNOWN",
        "channel_name": channel.name if channel else "Unklar",
        "evidence": evidence_url,
        "secure": secure,
        "label": _label(asset),
    }


def _finding_html(r: dict[str, Any]) -> str:
    a = r["asset"]
    p = r["post"]
    evidence = f'<img src="{r["evidence"]}" alt="Evidence" />' if r["evidence"] else ""
    return f"""
    <article class=\"asset\"> 
      <h3>{r['title']} – {r['label']}</h3>
      <p><strong>Kanal/Markt:</strong> {r['channel_name']} / {r['market']}</p>
      <p><strong>Creative-Mechanik:</strong> {a.ai_trend_notes or a.ai_summary_de or 'Kurzer Hook mit CTA-/Release-Fokus.'}</p>
      <p><strong>Warum relevant:</strong> {'Gesichertes Evidence-Bild und starkes Textsignal.' if r['secure'] else 'Als Kontextfund mit eingeschränkter Evidenz.'}</p>
      {evidence}
      <p><a href=\"{p.post_url if p else '#'}\">Originalpost</a></p>
    </article>
    """


def generate_report_html(session: Session, report_type: str, asset_ids: list[UUID], date_from: date, date_to: date) -> tuple[str, dict]:
    rows = [_row(session, session.get(Asset, aid)) for aid in asset_ids if session.get(Asset, aid)]
    strong = [r for r in rows if r["secure"] and (r["asset"].ocr_text or r["asset"].has_kinetic or r["asset"].has_title_placement)]
    top = (strong or rows)[:5]
    data_gaps = [r for r in rows if not r["secure"]]

    pattern_counter = Counter(r["label"] for r in rows)
    patterns = "".join(f"<li>{k}: {v}</li>" for k, v in pattern_counter.most_common(6)) or "<li>Keine Muster erkannt.</li>"

    summary_de = "Diese Woche dominieren Trailer-/Release-Posts mit kurzem Hook, klaren Claims und CTA-Nähe; hochwertige Beispiele zeigen wiederkehrende Ticket- und Event-Mechaniken."
    trend_de = "Wiederkehrend sind Trailer-Drops, Release-Erinnerungen und CTA-Formulierungen; belastbare visuelle Evidenz ist noch nicht in allen Treffern vorhanden."

    if report_type == "de_us_comparison":
        grouped = defaultdict(lambda: {"DE": [], "US": [], "INT": []})
        for r in rows:
            grouped[r["title"]][r["market"] if r["market"] in {"DE", "US", "INT"} else "INT"].append(r)
        pair_blocks = []
        for title, g in grouped.items():
            if g["DE"] and (g["US"] or g["INT"]):
                intl = g["US"] + g["INT"]
                pair_blocks.append(f"<article class='asset'><h3>{title}</h3><p><strong>DE:</strong> {g['DE'][0]['label']} · {g['DE'][0]['asset'].placement_title_text or 'lokalisierter Claim'}</p><p><strong>US/INT:</strong> {intl[0]['label']} · {intl[0]['asset'].placement_title_text or 'event-/ticketing-näher'}</p></article>")
        body = "".join(pair_blocks) or "<p>Keine belastbaren DE/US-Paare im Zeitraum.</p>"
        title = "Creative Radar – DE/US Vergleich"
        main = f"<section><h2>2. Vergleich nach Titel/Franchise</h2>{body}</section>"
    elif report_type == "visual_kinetics":
        title = "Creative Radar – Bild/Text & Kinetics"
        body = "".join(_finding_html(r) for r in top) or "<p>Keine belastbaren Bild/Text-Signale.</p>"
        main = f"<section><h2>2. Sichtbarer Text / OCR</h2>{body}</section>"
    else:
        title = "Creative Radar – Wochenüberblick"
        findings = "".join(_finding_html(r) for r in top) or "<p>Keine Top Findings im Zeitraum.</p>"
        main = f"<section><h2>2. Top Creative Findings</h2>{findings}</section><section><h2>3. Wiederkehrende Muster</h2><ul>{patterns}</ul></section>"

    gaps = "".join(f"<li>{r['title']} ({r['channel_name']}): {'Bildquelle nicht intern gesichert' if r['evidence'] else 'kein gesichertes Bild'}</li>" for r in data_gaps) or "<li>Keine Datenlücken im ausgewählten Set.</li>"

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
