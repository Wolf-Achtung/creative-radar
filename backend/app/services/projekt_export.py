"""Projekt-One-Pager (Roadmap Schritt 4, 25.08.2026) — das Radar im Pitch.

Eine eigenstaendige, druckbare HTML-Datei je Wir-Projekt: Start-Brief-
Empfehlungen mit Referenz-Posts, Release-Countdown mit Markt-Benchmark
und — sobald Messwochen existieren — der Beweis-Loop-Stand. Kein
Dashboard-Login noetig: die Datei laesst sich per Mail an den Kunden
schicken oder im Pitch oeffnen; im Browser gedruckt wird daraus das
PDF.

Bewusst reine KOMPOSITION vorhandener Auswertungen — Start-Brief
(``projekt_start_brief``), Countdown (``release_countdown``) und
Beweis (``beweis_loop``) rechnen; dieses Modul rendert nur. Eine
Zahl, die hier steht, steht genauso im Admin-Bereich.

Bilder referenzieren den oeffentlichen Thumbnail-Proxy absolut
(``{api_base}/api/thumbnails/…`` — der Pfad ist in beiden
Auth-Schichten public, sonst koennte kein <img> ihn laden). Alles
Dynamische laeuft durch ``html.escape``.

Rein lesend, LLM-frei. Gate: ``FEATURE_PROJEKT_EXPORT_ENABLED``.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlmodel import Session

from app.services.beweis_loop import compute_beweis_loop
from app.services.projekt_start_brief import compute_projekt_start_brief
from app.services.release_countdown import compute_release_countdown

logger = logging.getLogger(__name__)

# Der One-Pager ist eine Auswahl, kein zweiter Bericht.
MAX_EMPFEHLUNGEN = 5
MAX_BEISPIELE_JE_EMPFEHLUNG = 2

# Anzeige-Namen wie im Dashboard (Zwilling der Frontend-Labels in
# PatternsBlock.jsx — bewusst nur die Teilmenge, die der One-Pager
# braucht; unbekannte Werte erscheinen roh, nie falsch uebersetzt).
_DIMENSION_LABEL = {
    "genre": "Genre",
    "format": "Format",
    "format_class": "Formatklasse",
    "tone": "Tonalität",
    "lifecycle_stage": "Kampagnenphase",
    "duration_bucket": "Länge",
    "music_kind": "Musik",
    "cover_titel": "Cover: Titel im Bild",
    "cover_kinetik": "Cover: Bildtext & Kinetik",
    "caption_frage": "Caption: Frage",
    "caption_cta": "Caption: Call-to-Action",
    "caption_laenge": "Caption-Länge",
    "caption_hashtags": "Hashtags",
}
_PHASE_LABEL = {
    "pre_launch": "Pre-Launch (vor dem Start)",
    "launch": "Release-Woche",
    "post_launch": "Nach dem Start",
}


def dateiname_fuer(titel_name: str) -> str:
    """Dateiname aus dem Titel: ASCII, Bindestriche, nichts Gefaehrliches
    fuer Content-Disposition."""
    stamm = re.sub(r"[^a-z0-9]+", "-", titel_name.lower()).strip("-") or "projekt"
    return f"one-pager-{stamm}.html"


def _esc(wert) -> str:
    return html.escape(str(wert)) if wert is not None else ""


def _wochen(tage: float) -> str:
    n = abs(round(tage / 7))
    return f"{n} Woche" if n == 1 else f"{n} Wochen"


def _countdown_satz(zeile: dict, markt: dict) -> str:
    """Die Einordnung als Satz — serverseitiger Zwilling von
    ``formatReleaseEinordnung`` (format.js), auf die Faelle reduziert,
    die im Export vorkommen (eine Zeile MIT Release-Datum)."""
    tage = zeile["tage_bis_release"]
    median_tage = markt.get("median_vorlauf_tage")
    posts = zeile["eigene_posts"]
    start = zeile["eigener_start_vorlauf_tage"]
    if tage < -7:
        return f"Release war vor {_wochen(tage)}."
    if posts > 0 and start is not None:
        satz = f"Die Kampagne läuft seit {_wochen(start)} vor Release ({posts} Posts)."
        if median_tage is None:
            return satz
        vergleich = (
            "früher dran als der Markt"
            if start >= median_tage
            else "später gestartet als der Markt"
        )
        return f"{satz} Markt-Median: {_wochen(median_tage)} vor Release — {vergleich}."
    satz = (
        "Release-Woche."
        if tage <= 7
        else f"Release in {_wochen(tage)}."
    )
    if median_tage is None:
        return satz
    return (
        f"{satz} Der Markt startet vergleichbare Kampagnen im Median "
        f"{_wochen(median_tage)} vor Release."
    )


def render_projekt_one_pager(
    session: Session,
    title_id: UUID,
    *,
    api_base: str,
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """HTML des One-Pagers plus Dateiname. ``ValueError``, wenn der
    Titel fehlt (der Endpoint macht 404 daraus)."""
    now = now or datetime.now(timezone.utc)
    brief = compute_projekt_start_brief(session, title_id, now=now)
    countdown = compute_release_countdown(session, now=now)
    beweis = compute_beweis_loop(session, now=now)

    titel = brief["title"]
    zeile = next(
        (z for z in countdown["projekte"] if z["title_id"] == str(title_id)),
        None,
    )
    api_base = api_base.rstrip("/")

    teile: list[str] = []
    teile.append(
        '<header><p class="kicker">Creative Radar · Trailerhaus</p>'
        f"<h1>{_esc(titel['title_original'])}</h1>"
        '<p class="meta">'
        + " · ".join(
            _esc(x) for x in [
                ", ".join(titel["genres"]) if titel["genres"] else None,
                (
                    f"Release {zeile['release_date']} ({zeile['release_markt']})"
                    if zeile and zeile.get("release_date")
                    else None
                ),
                f"Stand {now.date().isoformat()}",
            ] if x
        )
        + "</p></header>"
    )

    if zeile is not None and zeile.get("release_date"):
        phase = _PHASE_LABEL.get(zeile["phase"], zeile["phase"])
        teile.append(
            '<section><h2>Wo die Kampagne steht</h2>'
            f'<p><strong>{_esc(phase)}</strong> — '
            f"{_esc(_countdown_satz(zeile, countdown['markt_kampagnenstart']))}</p>"
            "</section>"
        )

    empfehlungen = brief["empfehlungen"][:MAX_EMPFEHLUNGEN]
    if empfehlungen:
        karten = []
        for e in empfehlungen:
            beispiele = []
            for b in e["beispiele"][:MAX_BEISPIELE_JE_EMPFEHLUNG]:
                bild = (
                    f'<img src="{_esc(api_base)}/api/thumbnails/{_esc(b["asset_id"])}" alt="">'
                    if b.get("asset_id")
                    else ""
                )
                beispiele.append(
                    f'<div class="beispiel">{bild}<p>'
                    f'<strong>{_esc(b["lift"])}x</strong>'
                    + (f" @{_esc(b['handle'])}" if b.get("handle") else "")
                    + (
                        f' · <a href="{_esc(b["post_url"])}">Post</a>'
                        if b.get("post_url")
                        else ""
                    )
                    + "</p></div>"
                )
            karten.append(
                '<div class="karte">'
                f'<p class="dim">{_esc(_DIMENSION_LABEL.get(e["dimension"], e["dimension"]))}</p>'
                f"<h3>{_esc(e['value'])}</h3>"
                f'<p class="zahl">{_esc(e["sample_size"])} Posts im Markt, '
                f"Median-Lift {_esc(e['median_lift'])}</p>"
                + "".join(beispiele)
                + "</div>"
            )
        teile.append(
            "<section><h2>Was im Markt gerade überperformt</h2>"
            '<div class="karten">' + "".join(karten) + "</div></section>"
        )

    if beweis["summe"]["umgesetzt"] > 0:
        s = beweis["summe"]
        teile.append(
            "<section><h2>Empfehlungen, die sich bewiesen haben</h2>"
            f"<p>{_esc(s['umgesetzt'])} der {_esc(s['empfehlungen'])} "
            "eingefrorenen Wochen-Empfehlungen wurden in der jeweiligen "
            f"Folgewoche umgesetzt — {_esc(s['gewirkt'])} davon liefen "
            "über dem eigenen Kanal-Schnitt.</p></section>"
        )

    teile.append(
        '<footer><p>Jeder Post wird an seinem eigenen Kanal-Schnitt '
        "gemessen (Lift); Empfehlungen beschreiben Markt-Korrelationen, "
        "keine Kausalität. Quelle: Creative Radar, "
        f"{_esc(brief['posts_im_fenster'])} analysierte Posts der letzten "
        f"{_esc(brief['window_days'])} Tage.</p></footer>"
    )

    css = """
    body { font-family: Georgia, 'Times New Roman', serif; color: #1c2a24;
           max-width: 52rem; margin: 2rem auto; padding: 0 1.5rem; }
    .kicker { text-transform: uppercase; letter-spacing: 0.08em;
              font-size: 0.75rem; color: #b03d2e; margin: 0; }
    h1 { margin: 0.25rem 0; } h2 { border-bottom: 2px solid #1f4d4d;
         padding-bottom: 0.25rem; margin-top: 2rem; }
    .meta { color: #5a6b60; margin: 0; }
    .karten { display: flex; flex-wrap: wrap; gap: 1rem; }
    .karte { flex: 1 1 15rem; border: 1px solid #d8d2c2;
             border-radius: 8px; padding: 0.75rem 1rem; }
    .karte .dim { text-transform: uppercase; font-size: 0.7rem;
                  letter-spacing: 0.05em; color: #5a6b60; margin: 0; }
    .karte h3 { margin: 0.15rem 0 0.35rem; }
    .karte .zahl { color: #5a6b60; font-size: 0.85rem; margin: 0 0 0.5rem; }
    .beispiel { display: flex; align-items: center; gap: 0.5rem;
                margin: 0.35rem 0; }
    .beispiel img { width: 72px; height: 44px; object-fit: cover;
                    border-radius: 4px; }
    .beispiel p { margin: 0; font-size: 0.85rem; }
    footer { margin-top: 2.5rem; border-top: 1px solid #d8d2c2;
             padding-top: 0.75rem; color: #5a6b60; font-size: 0.8rem; }
    a { color: #1f7a45; }
    @media print { body { margin: 0 auto; } a { color: inherit; } }
    """
    dokument = (
        "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
        f"<title>{_esc(titel['title_original'])} — Creative Radar</title>"
        f"<style>{css}</style></head><body>"
        + "".join(teile)
        + "</body></html>"
    )
    logger.info(
        "projekt_export.rendered title=%s empfehlungen=%s bytes=%s",
        title_id, len(empfehlungen), len(dokument),
    )
    return dokument, dateiname_fuer(titel["title_original"])
