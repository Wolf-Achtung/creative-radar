"""Kandidaten-Autopilot — automatisches Abarbeiten offener Titel-Vorschläge.

Sprint Review-Automatisierung 2026-07-20 (Wolf: "jede Menge Dateien zu
prüfen, kann man das nicht automatisieren?").

Ausgangslage: Der Matcher ordnet Assets nur bei Confidence >= 0.95 aus
sicheren Quellen automatisch zu (``is_safe_auto_match``); alles darunter
wird als OPEN-``TitleCandidate`` in die "Treffer prüfen"-Queue gelegt.
Dort sammelten sich ~900 offene Vorschläge, von denen viele exakt so
aussehen wie das, was ein Mensch per Klick bestätigen würde: der
vorgeschlagene Titel steht WORTGLEICH in der Titel-Whitelist ("Exakter
Titel in der Liste — wird beim Bestätigen zugeordnet").

Der Autopilot übernimmt genau diese zwei mechanischen Fälle — nicht
mehr:

1. **Exakt-Treffer bestätigen**: OPEN-Kandidat, dessen ``suggested_title``
   nach Normalisierung (lowercase, Whitespace kollabiert) GENAU EINEM
   aktiven Titel entspricht (title_original oder Alias) UND dessen
   Confidence >= ``candidate_autopilot_min_confidence`` (Default 0.85)
   liegt -> Asset bekommt ``title_id`` + ``de_us_match_key`` (identische
   Zuordnung wie der manuelle Bestätigen-Klick, siehe
   ``api/assets.py::review``), alle OPEN-Kandidaten des Assets werden
   resolved. Mehrdeutige Namen (zwei aktive Titel mit gleichem
   Normalnamen, z. B. Remakes) werden bewusst ÜBERSPRUNGEN — die bleiben
   Menschensache.
2. **Alt-Rauschen schliessen**: OPEN-Kandidaten, die aelter als
   ``candidate_autopilot_stale_days`` (Default 28) sind UND unter
   ``candidate_autopilot_stale_max_confidence`` (Default 0.5) liegen,
   werden auf IGNORED gesetzt. Das sind die Karteileichen, die die Queue
   unlesbar machen; IGNORED ist reversibel (Kandidat bleibt in der DB)
   und das Asset selbst bleibt unangetastet — ein spaeterer Rematch darf
   es weiterhin zuordnen.

Die redaktionellen Entscheidungen (Für Report freigeben / Top-Fund /
Aussortieren) automatisiert der Autopilot bewusst NICHT — das ist
Kuratierung, keine Mechanik.

Laeuft als Cron-Stage direkt nach dem Rematch (frische Titel sind dann
schon in der Whitelist) und on-demand ueber
``POST /api/titles/candidates/autopilot`` (Admin-Button, fuer den
Alt-Backlog). Kill-Switch: ``CANDIDATE_AUTOPILOT_ENABLED=false``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.config import settings
from app.models.entities import (
    Asset,
    CandidateStatus,
    Title,
    TitleCandidate,
    utc_now,
)
from app.services.match_key import slugify_match_key
from app.services.title_candidates import resolve_open_candidates_for_asset

logger = logging.getLogger(__name__)


@dataclass
class AutopilotSummary:
    checked: int = 0
    auto_assigned: int = 0
    resolved_already_assigned: int = 0
    ignored_stale: int = 0
    skipped_ambiguous: int = 0
    skipped_low_confidence: int = 0
    skipped_no_exact_match: int = 0
    assigned_titles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "auto_assigned": self.auto_assigned,
            "resolved_already_assigned": self.resolved_already_assigned,
            "ignored_stale": self.ignored_stale,
            "skipped_ambiguous": self.skipped_ambiguous,
            "skipped_low_confidence": self.skipped_low_confidence,
            "skipped_no_exact_match": self.skipped_no_exact_match,
            # Kurze Beispiel-Liste fuer die Cron-Summary/Admin-Antwort —
            # gekappt, damit die Summary-JSON nicht aufblaeht.
            "assigned_titles_sample": self.assigned_titles[:20],
        }


def _normalize(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _build_exact_title_lookup(session: Session) -> dict[str, Title | None]:
    """Normalname -> Title, oder None wenn der Name mehrdeutig ist.

    Aliases zaehlen mit (der Matcher speist suggested_title u. a. aus
    Alias-Treffern). Kollisionen (zwei aktive Titel, gleicher Normalname)
    werden als ``None`` markiert und vom Autopiloten uebersprungen.
    """
    lookup: dict[str, Title | None] = {}
    titles = session.exec(select(Title).where(Title.active == True)).all()  # noqa: E712
    for title in titles:
        names = {_normalize(title.title_original), _normalize(title.title_local)}
        for alias in title.aliases or []:
            names.add(_normalize(alias))
        names.discard("")
        for name in names:
            if name in lookup and lookup[name] is not None and lookup[name].id != title.id:
                lookup[name] = None  # mehrdeutig -> Menschensache
            elif name not in lookup:
                lookup[name] = title
    return lookup


def run_candidate_autopilot(session: Session, *, commit: bool = True) -> AutopilotSummary:
    """Arbeitet alle OPEN-Kandidaten nach den zwei Autopilot-Regeln ab.

    Idempotent: ein zweiter Lauf direkt danach findet nichts Neues.
    Commit pro Lauf (ein Commit am Ende), nicht pro Kandidat — bei ~1000
    Kandidaten bleibt das im Sekundenbereich.
    """
    summary = AutopilotSummary()
    min_confidence = settings.candidate_autopilot_min_confidence
    stale_cutoff = utc_now() - timedelta(days=settings.candidate_autopilot_stale_days)
    stale_max_conf = settings.candidate_autopilot_stale_max_confidence

    open_candidates = session.exec(
        select(TitleCandidate).where(TitleCandidate.status == CandidateStatus.OPEN)
    ).all()
    if not open_candidates:
        return summary

    lookup = _build_exact_title_lookup(session)

    for candidate in open_candidates:
        summary.checked += 1
        asset = session.get(Asset, candidate.asset_id)

        # Fall 0: Asset ist inzwischen (Rematch/manuell) zugeordnet — der
        # offene Kandidat ist nur noch Queue-Rauschen.
        if asset is not None and asset.title_id is not None:
            resolve_open_candidates_for_asset(session, asset.id, commit=False)
            summary.resolved_already_assigned += 1
            continue

        # Fall 1: Exakt-Treffer mit ausreichender Confidence.
        title = lookup.get(_normalize(candidate.suggested_title))
        if _normalize(candidate.suggested_title) in lookup and title is None:
            summary.skipped_ambiguous += 1
        elif title is not None and asset is not None:
            if candidate.confidence >= min_confidence:
                # Identische Zuordnung wie der manuelle Bestaetigen-Klick
                # (api/assets.py::review): title_id + de_us_match_key.
                asset.title_id = title.id
                asset.de_us_match_key = slugify_match_key(
                    title.franchise or title.title_original
                )
                asset.updated_at = utc_now()
                session.add(asset)
                resolve_open_candidates_for_asset(session, asset.id, commit=False)
                summary.auto_assigned += 1
                summary.assigned_titles.append(title.title_original)
                logger.info(
                    "candidate-autopilot assigned asset=%s title=%s conf=%.2f",
                    asset.id, title.title_original, candidate.confidence,
                )
                continue
            summary.skipped_low_confidence += 1
        else:
            summary.skipped_no_exact_match += 1

        # Fall 2: Karteileiche — alt UND schwach.
        created = _as_utc(candidate.created_at)
        if created is not None and created < stale_cutoff and candidate.confidence < stale_max_conf:
            candidate.status = CandidateStatus.IGNORED
            candidate.updated_at = utc_now()
            session.add(candidate)
            summary.ignored_stale += 1

    if commit:
        session.commit()
    logger.info(
        "candidate-autopilot done checked=%s auto_assigned=%s already=%s stale_ignored=%s "
        "ambiguous=%s low_conf=%s no_exact=%s",
        summary.checked, summary.auto_assigned, summary.resolved_already_assigned,
        summary.ignored_stale, summary.skipped_ambiguous,
        summary.skipped_low_confidence, summary.skipped_no_exact_match,
    )
    return summary
