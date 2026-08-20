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
    "over": "laeuft ueber Schnitt",
    "under": "laeuft unter Schnitt",
    "neutral": "unauffaellig",
}


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
    over = cell["breakout_verdict"] == "over"
    richtung = "Mehr davon testen" if over else "Sparsam einsetzen"
    return (
        f"- {_wert(cell['value'])} ({_dim(eintrag['dim'])}): {richtung} — "
        f"Ausreisser-Quote {_prozent(cell['breakout_rate'])} statt erwarteter "
        f"{_prozent(cell['expected_breakout_rate'])} "
        f"({cell['sample_size']} Posts, {cell['channel_count']} Kanaele)."
    )


def _bewegungs_zeile(eintrag: dict) -> str:
    cell = eintrag["cell"]
    if eintrag["art"] == "gewechselt":
        vorher = (cell.get("vorwoche") or {}).get("breakout_verdict", "neutral")
        return (
            f"- {_wert(cell['value'])} ({_dim(eintrag['dim'])}): "
            f"{VERDICT_WORT.get(vorher, vorher)} -> "
            f"{VERDICT_WORT[cell['breakout_verdict']]} "
            f"(Quote {_prozent(cell['breakout_rate'])})."
        )
    return (
        f"- {_wert(cell['value'])} ({_dim(eintrag['dim'])}): neu belastbar — "
        f"{VERDICT_WORT[cell['breakout_verdict']]} "
        f"({_prozent(cell['breakout_rate'])})."
    )


def render_playbook(playbook: dict) -> tuple[str, str]:
    """(subject, text) — bewusst reiner Text: jede Mail-App zeigt ihn,
    nichts kann am HTML scheitern, und der Inhalt ist Listen-Prosa."""
    kw = f"KW {playbook['iso_week']}/{playbook['iso_year']}"
    if playbook["befunde"]:
        top = playbook["befunde"][0]["cell"]
        subject = f"Creative Radar Playbook {kw}: {_wert(top['value'])} & mehr"
    else:
        subject = f"Creative Radar Playbook {kw}"

    teile: list[str] = [
        f"Creative Radar — Playbook {kw}",
        "",
        f"Datenbasis: {playbook['posts_with_baseline']} Posts mit "
        f"Kanal-Baseline aus {playbook['channels_covered']} Kanaelen, "
        f"Fenster {playbook['window_days']} Tage.",
    ]
    if playbook["befunde"]:
        teile += ["", "DIE STAERKSTEN BEFUNDE", ""]
        teile += [_befund_zeile(e) for e in playbook["befunde"]]
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
    if playbook["notes"]:
        teile += ["", "EINORDNUNG", ""]
        teile += [f"- {note}" for note in playbook["notes"]]
    teile += [
        "",
        "Gemessene Korrelationen im eigenen Bestand — kein Beweis fuer "
        "Ursache und Wirkung. Details und Beispiel-Posts im Dashboard.",
    ]
    return subject, "\n".join(teile)


async def send_pattern_playbook(
    session: Session, *, now: Optional[datetime] = None
) -> dict:
    """Bauen, rendern, versenden — mit den drei Gates aus dem
    Modul-Docstring. Rueckgabe ist das Cron-Summary-Dict."""
    summary: dict[str, Any] = {"skipped": False, "sent": 0, "failed": 0}
    if not is_trailer_intelligence_enabled():
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

    subject, text = render_playbook(playbook)
    for recipient in recipients:
        try:
            await send_mail(to=recipient, subject=subject, text=text)
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
