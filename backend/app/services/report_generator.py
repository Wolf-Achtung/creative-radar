from datetime import date
from sqlmodel import Session, select
from app.models.entities import Asset, Post, Channel, Title, ReviewStatus, WeeklyReport

ASSET_TYPE_LABELS = {
    "Unknown": "Nicht klassifiziert",
    "Trailer Drop": "Trailer-/Release-Post",
    "Release Reminder": "Release Reminder",
    "Franchise / Brand Post": "Franchise-/Brand-Post",
    "Event / Festival": "Event-/Festival-Post",
    "Character / Cast Post": "Cast-/Talent-Post",
    "Ticket CTA": "Ticket-/CTA-Post",
}


def _asset_block(session: Session, asset: Asset) -> str:
    post = session.get(Post, asset.post_id)
    channel = session.get(Channel, post.channel_id) if post else None
    title = session.get(Title, asset.title_id) if asset.title_id else None
    title_name = title.title_original if title else "Unklar"
    channel_name = channel.name if channel else "Unklar"
    market = channel.market if channel else "Unklar"
    type_label = ASSET_TYPE_LABELS.get(str(asset.asset_type.value), str(asset.asset_type.value))
    evidence_image = asset.visual_evidence_url or asset.screenshot_url
    return f"""
    <article class=\"asset\">
      <h3>{title_name} – {type_label}</h3>
      <p><strong>Kanal:</strong> {channel_name} / {market}</p>
      <p><strong>Link:</strong> <a href=\"{post.post_url if post else '#'}\">Originalpost</a></p>
      {'<img src="' + evidence_image + '" alt="Screenshot Evidence" />' if evidence_image else '<p><em>Kein Screenshot verfügbar. Quelle: Caption/Textanalyse.</em></p>'}
      {'<img src="' + asset.visual_crop_title_url + '" alt="Titel/Claim Crop" />' if asset.visual_crop_title_url else ''}
      {'<img src="' + asset.visual_crop_cta_url + '" alt="CTA Crop" />' if asset.visual_crop_cta_url else ''}
      {'<img src="' + asset.visual_crop_kinetic_url + '" alt="Kinetic Crop" />' if asset.visual_crop_kinetic_url else ''}
      <p><strong>KI-Zusammenfassung:</strong> {asset.ai_summary_de or 'Noch keine Zusammenfassung.'}</p>
      <p><strong>Trend-Hinweis:</strong> {asset.ai_trend_notes or 'Noch kein Trend-Hinweis.'}</p>
      <p><strong>Bild/Text-Analyse:</strong> OCR: {asset.ocr_text or 'nicht erkannt'} · Titel/Claim: {asset.placement_title_text or 'nicht erkannt'} · Kinetic: {asset.kinetic_type or 'nicht erkannt'} · CTA: {'erkannt' if asset.asset_type.value in {'CTA Post', 'Ticket CTA'} else 'nicht erkannt'}</p>
      <p><strong>Kuratorennotiz:</strong> {asset.curator_note or '—'}</p>
    </article>
    """


def generate_weekly_report_html(session: Session, week_start: date, week_end: date, include_only_reviewed: bool = True) -> tuple[str, dict]:
    statement = select(Asset).join(Post).where(Post.detected_at >= week_start, Post.detected_at <= week_end)
    assets = list(session.exec(statement).all())
    if include_only_reviewed:
        assets = [a for a in assets if a.include_in_report and a.review_status in {ReviewStatus.APPROVED, ReviewStatus.HIGHLIGHT}]
    highlights = [a for a in assets if a.is_highlight or a.review_status == ReviewStatus.HIGHLIGHT]

    summary = (
        f"Im Zeitraum {week_start} bis {week_end} wurden {len(assets)} freigegebene relevante Creative-Treffer dokumentiert. "
        f"Davon sind {len(highlights)} als Highlights markiert. Der Report beschreibt sichtbare Creative-Muster und enthält keine Klick- oder Performance-Behauptungen."
    )
    trend_summary = "Die Trendbewertung ist in v1 kuratiert: Auffälligkeiten entstehen aus KI-Zusammenfassungen und menschlichen Notizen."

    asset_html = "\n".join(_asset_block(session, a) for a in assets) or "<p>Keine freigegebenen Treffer für diesen Zeitraum.</p>"
    highlight_html = "\n".join(_asset_block(session, a) for a in highlights) or "<p>Keine Highlights markiert.</p>"

    html = f"""
    <!doctype html>
    <html lang=\"de\">
    <head>
      <meta charset=\"utf-8\" />
      <title>Creative Radar – Weekly Report</title>
      <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.5; margin: 40px; color: #172033; }}
        header {{ border-bottom: 2px solid #172033; margin-bottom: 24px; }}
        h1, h2, h3 {{ color: #172033; }}
        .asset {{ border: 1px solid #d8dde8; border-radius: 12px; padding: 16px; margin: 16px 0; }}
        img {{ max-width: 360px; border-radius: 8px; border: 1px solid #ddd; display: block; margin: 12px 0; }}
        .note {{ background: #f5f7fb; padding: 12px; border-radius: 8px; }}
      </style>
    </head>
    <body>
      <header>
        <h1>Creative Radar – Weekly Report</h1>
        <p>Zeitraum: {week_start} bis {week_end}</p>
      </header>
      <section>
        <h2>1. Management Summary</h2>
        <p>{summary}</p>
      </section>
      <section>
        <h2>2. Trend Snapshot</h2>
        <p>{trend_summary}</p>
      </section>
      <section>
        <h2>3. Highlight Assets</h2>
        {highlight_html}
      </section>
      <section>
        <h2>4. Creative Appendix</h2>
        {asset_html}
      </section>
      <section class=\"note\">
        <h2>Datenhinweis</h2>
        <p>Dieser Report basiert auf intern kuratierten öffentlichen Creative-Treffern. Er enthält keine echten Klickdaten und keine belastbaren Performance-Rankings.</p>
      </section>
    </body>
    </html>
    """
    meta = {
        "executive_summary_de": summary,
        "executive_summary_en": "Weekly creative radar draft. No click or performance claims included.",
        "trend_summary_de": trend_summary,
    }
    return html, meta
