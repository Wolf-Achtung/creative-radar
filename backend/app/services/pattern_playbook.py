"""Playbook-Montags-Mail (Sprung 2 der Hook-Intelligence, 20.08.2026).

Empfehlungen, die ankommen, statt abgeholt werden zu muessen: nach dem
Montags-Cron geht eine Mail an das Team — die staerksten Befunde, die
Bewegungen gegenueber der Vorwoche und (falls diese Woche generiert)
die Text-Bausteine mit ihren Belegen. Alles darin ist deterministisch
aus bereits berechneten bzw. persistierten Daten gebaut — die Mail
loest selbst KEINEN LLM-Call aus.

Auswahl-Logik = dieselben Regeln wie die Panel-Karten (Befunde:
over/under nach |z|; Bewegungen: Verdikt-Wechsel vor Neuzugaengen).
Die deutschen Anzeige-Namen sind der Zwilling von WERT_LABEL /
DIMENSION_LABEL in ``frontend/src/PatternsBlock.jsx`` — wer dort
umbenennt, benennt hier mit um.

Versand-Gates, in dieser Reihenfolge:
1. ``FEATURE_TRAILER_INTELLIGENCE_ENABLED`` aus — kein Playbook.
2. ``PLAYBOOK_MAIL_RECIPIENTS`` leer — kein Versand (Deploy-
   Entscheidung, kein Code-Default auf eine Person).
3. Nichts zu berichten (keine Befunde, keine Bewegung, keine
   Bausteine) — keine Mail. Eine leere Mail trainiert Ignorieren.
``DISABLE_EMAILS`` greift zusaetzlich im Mailer selbst.
"""
from __future__ import annotations

import logging
from html import escape as _esc
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from app.config import settings
from app.core.feature_flags import is_trailer_intelligence_enabled
from app.models.entities import PatternBriefing
from app.services.mailer import send_mail
from app.services.trailer_patterns import (
    TREND_WINDOW_SHIFT_DAYS,
    apply_weekly_trend,
    compute_trailer_patterns,
)

logger = logging.getLogger(__name__)

PLAYBOOK_MAX_BEFUNDE = 5
PLAYBOOK_MAX_BEWEGUNGEN = 4
PLAYBOOK_MAX_BAUSTEINE_JE_EBENE = 2

# Zwilling von PatternsBlock.jsx (DIMENSION_LABEL / WERT_LABEL) — nur
# die Werte, die in Mail-Saetzen vorkommen; unbekannte Werte erscheinen
# unveraendert (Genres, Titel).
DIMENSION_LABEL = {
    "genre": "Genre",
    "format": "Format",
    "format_class": "Formatklasse",
    "tone": "Tonalitaet",
    "lifecycle_stage": "Kampagnenphase",
    "duration_bucket": "Laenge",
    "music_kind": "Musik",
    "cover_titel": "Cover: Titel im Bild",
    "cover_kinetik": "Cover: Bildtext & Kinetik",
    "caption_frage": "Caption: Frage",
    "caption_cta": "Caption: Call-to-Action",
    "caption_laenge": "Caption-Laenge",
    "caption_hashtags": "Hashtags",
}
WERT_LABEL = {
    "behind_the_scenes": "Behind-the-Scenes",
    "langform": "Langform",
    "kurzform": "Kurzform",
    "uebergang_60_90s": "Uebergang 60-90s",
    "pre_launch": "Pre-Launch (vor dem Start)",
    "launch": "Zum Start",
    "post_launch": "Nach dem Start",
    "evergreen": "Evergreen",
    "unclear": "Phase unklar",
    "licensed_track": "Lizenzierter Track",
    "original_sound": "Original-Sound",
    "mit_titel": "Titel im Bild",
    "ohne_titel": "Ohne Titel im Bild",
    "ohne_kinetik": "Ohne Bildtext",
    "text_overlay": "Text-Overlay",
    "title_card": "Title-Card",
    "animated_text": "Animierte Schrift",
    "motion_graphic": "Motion-Graphic",
    "mit_frage": "Caption mit Frage",
    "ohne_frage": "Caption ohne Frage",
    "mit_cta": "Mit Call-to-Action",
    "ohne_cta": "Ohne Call-to-Action",
    "kurz": "Kurze Caption",
    "mittel": "Mittlere Caption",
    "lang": "Lange Caption",
    "keine": "Keine Hashtags",
    "1-3": "1-3 Hashtags",
    "4+": "4+ Hashtags",
}
VERDICT_WORT = {
    "over": "läuft über Schnitt",
    "under": "läuft unter Schnitt",
    "neutral": "unauffällig",
}


# Themen-Woerter in Alltagssprache — Zwilling von THEMA_LABEL in
# PatternsBlock.jsx. Die Mail spricht wie die Empfehlungs-Karten.
THEMA_LABEL = {
    "genre": "Genre",
    "format": "Format",
    "format_class": "Länge",
    "duration_bucket": "Länge",
    "tone": "Tonfall",
    "lifecycle_stage": "Timing",
    "music_kind": "Musik",
    "cover_titel": "Cover",
    "cover_kinetik": "Cover",
    "caption_frage": "Bildunterschrift",
    "caption_cta": "Bildunterschrift",
    "caption_laenge": "Bildunterschrift",
    "caption_hashtags": "Hashtags",
}

# Werkstatt-Vorlagen — Zwilling von WERKSTATT_VORLAGEN in
# PatternsBlock.jsx (wer dort umformuliert, formuliert hier mit um).
# Schluessel (dimension, wert); Wert: faktor -> (titel, satz).
_WERKSTATT_VORLAGEN = {
    ("cover_kinetik", "title_card"): lambda f: (
        "Cover mit Titel-Tafel bauen",
        f"Posts mit gestalteter Titel-Tafel im Cover liegen {f}-mal öfter weit über dem Kanal-Schnitt als Posts ohne.",
    ),
    ("lifecycle_stage", "pre_launch"): lambda f: (
        "Vor dem Start posten",
        f"Die stärksten Posts entstehen vor dem Kinostart ({f}-mal öfter als erwartet). Baut die Reichweite auf, bevor der Film läuft.",
    ),
    ("lifecycle_stage", "launch"): lambda f: (
        "Zum Start reicht Routine nicht",
        "Posts rund um den Starttag bleiben öfter unter dem Kanal-Schnitt. Plant für den Start einen eigenen Aufhänger.",
    ),
    ("lifecycle_stage", "evergreen"): lambda f: (
        "Ohne Anlass bringt ein Post wenig",
        "Posts ohne aktuellen Anlass erreichen am seltensten große Reichweite. Koppelt sie an einen Termin: Start, Jubiläum, Heimkino.",
    ),
    ("tone", "humorous"): lambda f: (
        "Humor zieht nicht von allein",
        "Lustige Posts bleiben öfter unter dem Kanal-Schnitt. Nutzt Humor mit einem starken Aufhänger, nicht als Selbstläufer.",
    ),
    ("format", "behind_the_scenes"): lambda f: (
        "Mehr Blicke hinter die Kulissen",
        f"Posts vom Set oder aus der Produktion liegen {f}-mal öfter weit über dem Kanal-Schnitt. Nähe schlägt Hochglanz.",
    ),
    ("format", "clip"): lambda f: (
        "Szenen-Clips brauchen einen Rahmen",
        "Ein roher Film-Ausschnitt bleibt öfter unter dem Kanal-Schnitt. Gebt dem Clip einen Einstieg: Hook, Kontext oder Anlass.",
    ),
    ("format_class", "langform"): lambda f: (
        "Lange Videos funktionieren",
        f"Videos über 90 Sekunden liegen {f}-mal öfter weit über dem Kanal-Schnitt. Länge schreckt nicht ab.",
    ),
}


def _werkstatt_empfehlung(dim: str, cell: dict) -> tuple[str, str]:
    """(titel, satz) in Werkstatt-Sprache — Vorlage falls bekannt,
    sonst ein generischer, ehrlicher Fallback."""
    expected = cell.get("expected_breakout_rate") or 0
    faktor = (
        f"{cell['breakout_rate'] / expected:.1f}" if expected else None
    )
    vorlage = _WERKSTATT_VORLAGEN.get((dim, cell["value"]))
    if vorlage:
        return vorlage(faktor)
    wert = _wert(cell["value"])
    if cell["breakout_verdict"] == "over":
        oefter = f"{faktor}-mal öfter" if faktor else "öfter"
        return (
            f"{wert}: öfter testen",
            f"Posts mit diesem Merkmal liegen {oefter} weit über dem Kanal-Schnitt.",
        )
    return (
        f"{wert}: sparsam einsetzen",
        "Posts mit diesem Merkmal bleiben öfter unter dem Kanal-Schnitt.",
    )


def _wert(value: str) -> str:
    return WERT_LABEL.get(value, value)


def _dim(name: str) -> str:
    return DIMENSION_LABEL.get(name, name)


def _prozent(wert: float) -> str:
    return f"{wert * 100:.1f} %"


def staerkste_befunde(
    trend_data: dict, max_befunde: int = PLAYBOOK_MAX_BEFUNDE
) -> list[dict]:
    """over/under-Zellen ueber alle Dimensionen, |z|-sortiert — dieselbe
    Auswahl wie die "Das Wichtigste zuerst"-Karten im Panel."""
    alle: list[dict] = []
    for dim, cells in (trend_data.get("dimensions") or {}).items():
        for cell in cells:
            if cell.get("breakout_verdict") in ("over", "under") and cell.get(
                "breakout_z"
            ) is not None:
                alle.append({"dim": dim, "cell": cell})
    alle.sort(key=lambda e: abs(e["cell"]["breakout_z"]), reverse=True)
    return alle[:max_befunde]


def bewegungen(
    trend_data: dict, max_bewegungen: int = PLAYBOOK_MAX_BEWEGUNGEN
) -> list[dict]:
    """Verdikt-Wechsel vor Neuzugaengen mit Befund — dieselbe Auswahl
    wie die "Bewegung diese Woche"-Karten im Panel."""
    alle: list[dict] = []
    for dim, cells in (trend_data.get("dimensions") or {}).items():
        for cell in cells:
            if cell.get("trend") == "gewechselt":
                alle.append({"dim": dim, "cell": cell, "art": "gewechselt"})
            elif cell.get("trend") == "neu" and cell.get("breakout_verdict") in (
                "over",
                "under",
            ):
                alle.append({"dim": dim, "cell": cell, "art": "neu"})
    alle.sort(
        key=lambda e: (
            0 if e["art"] == "gewechselt" else 1,
            -abs(e["cell"].get("breakout_z") or 0),
        )
    )
    return alle[:max_bewegungen]


def _bausteine_der_woche(
    session: Session, iso_year: int, iso_week: int
) -> dict[str, list[dict]]:
    """Text-Bausteine aus den persistierten Briefings DIESER Woche —
    aeltere Wochen gehoeren nicht in die Montags-Mail (die stand schon
    in der letzten)."""
    ergebnis: dict[str, list[dict]] = {}
    rows = session.exec(
        select(PatternBriefing).where(
            PatternBriefing.iso_year == iso_year,
            PatternBriefing.iso_week == iso_week,
        )
    ).all()
    for row in rows:
        llm = row.llm_output if isinstance(row.llm_output, dict) else None
        bausteine = (llm or {}).get("bausteine") or []
        if bausteine:
            ergebnis[row.mode] = bausteine[:PLAYBOOK_MAX_BAUSTEINE_JE_EBENE]
    return ergebnis


def build_playbook(session: Session, *, now: Optional[datetime] = None) -> dict:
    """Alle Playbook-Inhalte, rein lesend/deterministisch."""
    now = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    current = compute_trailer_patterns(session, now=now)
    previous = compute_trailer_patterns(
        session, now=now - timedelta(days=TREND_WINDOW_SHIFT_DAYS)
    )
    trend_data = apply_weekly_trend(current, previous)
    return {
        "iso_year": iso_year,
        "iso_week": iso_week,
        "window_days": trend_data["window_days"],
        "posts_with_baseline": trend_data["posts_with_baseline"],
        "channels_covered": trend_data["channels_covered"],
        "befunde": staerkste_befunde(trend_data),
        "bewegungen": bewegungen(trend_data),
        "bausteine": _bausteine_der_woche(session, iso_year, iso_week),
        "notes": list(trend_data.get("notes") or []),
    }


def _befund_zeile(eintrag: dict) -> str:
    cell = eintrag["cell"]
    titel, satz = _werkstatt_empfehlung(eintrag["dim"], cell)
    return (
        f"- {titel} — {satz} "
        f"({cell['sample_size']} Posts, {cell['channel_count']} Kanäle)"
    )


def _bewegungs_zeile(eintrag: dict) -> str:
    cell = eintrag["cell"]
    if eintrag["art"] == "gewechselt":
        vorher = (cell.get("vorwoche") or {}).get("breakout_verdict", "neutral")
        return (
            f"- {_wert(cell['value'])} ({_dim(eintrag['dim'])}): "
            f"{VERDICT_WORT.get(vorher, vorher)} → "
            f"{VERDICT_WORT[cell['breakout_verdict']]} "
            f"(Quote {_prozent(cell['breakout_rate'])})."
        )
    return (
        f"- {_wert(cell['value'])} ({_dim(eintrag['dim'])}): neu belastbar — "
        f"{VERDICT_WORT[cell['breakout_verdict']]} "
        f"({_prozent(cell['breakout_rate'])})."
    )


def render_playbook(playbook: dict) -> tuple[str, str, str]:
    """(subject, text, html). Text bleibt vollstaendig — jede Mail-App
    zeigt ihn; das HTML ist der Panel-Look fuers Postfach.

    Bewusst OHNE die Berichts-Notes: die Mail traegt Handlung, die
    Methodik und alle Einordnungen stehen im Dashboard-Tab
    "Zahlen & Methode" (Design-Entscheidung 21.08.2026 nach Wolfs
    erster Test-Mail — "etwas nuechtern").
    """
    kw = f"KW {playbook['iso_week']}/{playbook['iso_year']}"
    machen = [e for e in playbook["befunde"] if e["cell"]["breakout_verdict"] == "over"]
    vorsicht = [e for e in playbook["befunde"] if e["cell"]["breakout_verdict"] != "over"]

    if playbook["befunde"]:
        top_titel, _ = _werkstatt_empfehlung(
            playbook["befunde"][0]["dim"], playbook["befunde"][0]["cell"]
        )
        subject = f"Creative Radar Playbook {kw}: {top_titel}"
    else:
        subject = f"Creative Radar Playbook {kw}"

    teile: list[str] = [f"Creative Radar — Playbook {kw}"]
    if machen:
        teile += ["", "MACHEN", ""]
        teile += [_befund_zeile(e) for e in machen]
    if vorsicht:
        teile += ["", "VORSICHT", ""]
        teile += [_befund_zeile(e) for e in vorsicht]
    if playbook["bewegungen"]:
        teile += ["", "BEWEGUNG DIESE WOCHE", ""]
        teile += [_bewegungs_zeile(e) for e in playbook["bewegungen"]]
    for mode, label in (("genre", "TEXT-BAUSTEINE (GENRE-MUSTER)"),
                        ("title", "TEXT-BAUSTEINE (JE TITEL)")):
        bausteine = playbook["bausteine"].get(mode) or []
        if not bausteine:
            continue
        teile += ["", label, ""]
        for baustein in bausteine:
            teile.append(f"* {baustein.get('muster', '?')}")
            for hook in (baustein.get("hooks_de") or [])[:3]:
                teile.append(f"    Hook: {hook}")
            for url in (baustein.get("cited_post_ids") or [])[:3]:
                teile.append(f"    Beleg: {url}")
            teile.append("")
    dashboard = (settings.frontend_url or "https://app.creative-radar.de").rstrip("/")
    teile += [
        "",
        f"Alle Zahlen, Beispiel-Posts und die Methodik: {dashboard}",
        f"Datenbasis: {playbook['posts_with_baseline']} Posts aus "
        f"{playbook['channels_covered']} Kanälen, Fenster {playbook['window_days']} Tage.",
        "Gemessene Korrelationen im eigenen Bestand — kein Wirkungsbeweis.",
    ]
    return subject, "\n".join(teile), _render_html(playbook, kw, machen, vorsicht)


def _html_karte(eintrag: dict) -> str:
    cell = eintrag["cell"]
    over = cell["breakout_verdict"] == "over"
    farbe = "#1f7a45" if over else "#b03d2e"
    chip = "Machen" if over else "Vorsicht"
    thema = THEMA_LABEL.get(eintrag["dim"], _dim(eintrag["dim"]))
    titel, satz = _werkstatt_empfehlung(eintrag["dim"], cell)
    return (
        f'<div style="background:#fdf8ef;border-radius:10px;'
        f'border-left:4px solid {farbe};padding:12px 16px;margin:0 0 10px;">'
        f'<p style="margin:0 0 2px;font-size:11px;letter-spacing:.05em;'
        f'text-transform:uppercase;font-weight:600;">'
        f'<span style="color:{farbe};">{chip}</span>'
        f'<span style="color:#6b6b6b;"> &middot; {_esc(thema)}</span></p>'
        f'<p style="margin:0 0 4px;font-weight:700;font-size:15px;color:#1c1c1a;">{_esc(titel)}</p>'
        f'<p style="margin:0;font-size:13px;color:#4a4a44;">{_esc(satz)}</p>'
        f'<p style="margin:4px 0 0;font-size:11px;color:#6b6b6b;">'
        f'Basis: {cell["sample_size"]} Posts von {cell["channel_count"]} Kanälen.</p>'
        f"</div>"
    )


def _render_html(playbook: dict, kw: str, machen: list, vorsicht: list) -> str:
    dashboard = (settings.frontend_url or "https://app.creative-radar.de").rstrip("/")
    bloecke: list[str] = []
    for eintrag in machen + vorsicht:
        bloecke.append(_html_karte(eintrag))
    if playbook["bewegungen"]:
        zeilen = "".join(
            f'<p style="margin:0 0 4px;font-size:13px;color:#4a4a44;">'
            f"{_esc(_bewegungs_zeile(e)[2:])}</p>"
            for e in playbook["bewegungen"]
        )
        bloecke.append(
            '<p style="margin:14px 0 6px;font-weight:700;font-size:13px;'
            'color:#1c1c1a;">Bewegung diese Woche</p>' + zeilen
        )
    for mode, label in (("genre", "Text-Bausteine (Genre-Muster)"),
                        ("title", "Text-Bausteine (je Titel)")):
        bausteine = playbook["bausteine"].get(mode) or []
        if not bausteine:
            continue
        inner = ""
        for baustein in bausteine:
            hooks = "".join(
                f'<li style="margin:0 0 2px;">{_esc(hook)}</li>'
                for hook in (baustein.get("hooks_de") or [])[:3]
            )
            inner += (
                f'<p style="margin:8px 0 2px;font-weight:700;font-size:13px;'
                f'color:#1c1c1a;">{_esc(baustein.get("muster", "?"))}</p>'
                f'<ul style="margin:0;padding-left:18px;font-size:13px;'
                f'color:#4a4a44;">{hooks}</ul>'
            )
        bloecke.append(
            f'<p style="margin:14px 0 0;font-weight:700;font-size:13px;'
            f'color:#1c1c1a;">{label}</p>' + inner
        )
    inhalt = "".join(bloecke) or (
        '<p style="margin:0;font-size:13px;color:#4a4a44;">'
        "Diese Woche keine belastbaren Befunde.</p>"
    )
    return (
        '<div style="margin:0;padding:16px;background:#efe9db;'
        "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        '<div style="max-width:560px;margin:0 auto;">'
        '<div style="background:#1f4d4d;border-radius:12px 12px 0 0;padding:18px 20px;">'
        f'<p style="margin:0;color:#ffa294;font-size:11px;letter-spacing:.05em;'
        f'font-weight:600;text-transform:uppercase;">Trailer-Intelligence &middot; {kw}</p>'
        '<p style="margin:4px 0 0;color:#ffffff;font-size:19px;font-weight:700;">'
        "Playbook — was diese Woche zählt</p></div>"
        '<div style="background:#f4efe4;border-radius:0 0 12px 12px;padding:14px 16px;">'
        + inhalt
        + f'<p style="margin:14px 0 0;font-size:12px;">'
        f'<a href="{dashboard}" style="color:#1f7a45;font-weight:600;">'
        "Alle Zahlen, Beispiel-Posts und die Methodik im Dashboard</a></p>"
        f'<p style="margin:6px 0 0;font-size:11px;color:#6b6b6b;">'
        f"Datenbasis: {playbook['posts_with_baseline']} Posts aus "
        f"{playbook['channels_covered']} Kanälen, Fenster {playbook['window_days']} Tage. "
        "Gemessene Korrelationen im eigenen Bestand — kein Wirkungsbeweis.</p>"
        "</div></div></div>"
    )


async def send_pattern_playbook(
    session: Session, *, now: Optional[datetime] = None, force: bool = False
) -> dict:
    """Bauen, rendern, versenden — mit den drei Gates aus dem
    Modul-Docstring. Rueckgabe ist das Cron-Summary-Dict.

    ``force=True`` ueberspringt NUR das Flag-Gate — fuer den
    Admin-Test-Trigger (21.08.2026): Wolf prueft den Versand in
    Produktion, BEVOR das TI-Flag faellt. Empfaenger- und Inhalts-Gate
    gelten weiter; eine leere Empfaengerliste oder ein leerer Bericht
    kommen als ``reason`` zurueck statt als stille Nicht-Mail."""
    summary: dict[str, Any] = {"skipped": False, "sent": 0, "failed": 0}
    if not force and not is_trailer_intelligence_enabled():
        summary["skipped"] = True
        summary["reason"] = "feature_flag_off"
        return summary
    recipients = [
        r.strip()
        for r in (settings.playbook_mail_recipients or "").split(",")
        if r.strip()
    ]
    if not recipients:
        summary["skipped"] = True
        summary["reason"] = "no_recipients"
        logger.info("pattern_playbook.skipped reason=no_recipients")
        return summary

    playbook = build_playbook(session, now=now)
    if (
        not playbook["befunde"]
        and not playbook["bewegungen"]
        and not playbook["bausteine"]
    ):
        summary["skipped"] = True
        summary["reason"] = "nichts_zu_berichten"
        logger.info("pattern_playbook.skipped reason=nichts_zu_berichten")
        return summary

    subject, text, html = render_playbook(playbook)
    for recipient in recipients:
        try:
            await send_mail(to=recipient, subject=subject, text=text, html=html)
            summary["sent"] += 1
        except Exception:  # noqa: BLE001 — ein kaputter Empfaenger darf
            # die uebrigen nicht kosten; der Mailer loggt die Details.
            logger.exception("pattern_playbook.send_failed")
            summary["failed"] += 1
    summary["befunde"] = len(playbook["befunde"])
    summary["bewegungen"] = len(playbook["bewegungen"])
    summary["bausteine_ebenen"] = sorted(playbook["bausteine"].keys())
    return summary


__all__ = [
    "bewegungen",
    "build_playbook",
    "render_playbook",
    "send_pattern_playbook",
    "staerkste_befunde",
]
