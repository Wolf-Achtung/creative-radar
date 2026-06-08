from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.models.entities import Asset, CandidateSource, CandidateStatus, TitleCandidate
from app.services.whitelist_matcher import find_best_title_match


def _candidate_source_from_text(asset: Asset) -> CandidateSource:
    if asset.ocr_text:
        return CandidateSource.OCR
    if asset.detected_keywords:
        return CandidateSource.HASHTAG
    if asset.ai_summary_de or asset.ai_summary_en:
        return CandidateSource.OPENAI
    return CandidateSource.TEXT


def create_candidate_from_asset(
    session: Session,
    asset_id,
    force: bool = False,
    *,
    skip_if_guess_only: bool = True,
) -> Optional[TitleCandidate]:
    """Erstellt fuer ein Asset einen TitleCandidate, wenn der Whitelist-
    Matcher noch keinen sicheren Titel gefunden hat.

    Sprint 28.05.2026 (Variante D): ``skip_if_guess_only`` (Default
    ``True``) blockt den Fall, in dem ``find_best_title_match`` nichts
    aus der Whitelist gefunden hat und nur ein 6-Token-Guess als
    ``suggested_title`` ueberlebt (``match.source == "none"``). Das
    war der Hauptproduzent des Rauschens im Review-Backlog ("Rivals"
    fuer UEFA-Posts etc.). Returns dann ``None`` — der Caller verwirft
    das Ergebnis ohnehin in den automatischen Zuflusspfaden
    (monitor, posts, title_rematch).

    Caller, die einen Candidate explizit gewollt erzeugen — z.B. die
    User-API ``POST /api/titles/candidates/from-asset/{asset_id}`` mit
    nutzergeliefertem ``suggested_title`` — setzen
    ``skip_if_guess_only=False``. Dort ist der Guess nicht das Problem,
    weil der Caller die ``suggested_title`` ohnehin nachtraeglich
    ueberschreibt.

    Echte Whitelist-Matches mit Confidence < 0.95 (Fuzzy-Hits,
    Brand-Whitelist, ambiguous-Multi-Match) bleiben als Candidate
    erhalten — ``match.source`` ist dann nicht "none".
    """
    asset = session.get(Asset, asset_id)
    if not asset:
        raise ValueError("Asset not found")

    existing = session.exec(
        select(TitleCandidate).where(TitleCandidate.asset_id == asset.id, TitleCandidate.status == CandidateStatus.OPEN)
    ).first()
    if existing and not force:
        return existing

    text = " ".join([asset.ocr_text or "", asset.ai_summary_de or "", asset.ai_summary_en or ""])
    match = find_best_title_match(session, text)
    if not match.suggested_title:
        match = find_best_title_match(session, asset.kinetic_text or asset.placement_title_text or "")

    # Variante D + Rausch-Filter: in den automatischen Zuflusspfaden
    # (monitor, posts, title_rematch) werden Match-Quellen ohne Auto-Match-
    # Nutzen NICHT zu Candidates — sie haben keinen title_id-Pfad
    # (is_safe_auto_match scheitert) und keinen Konsumenten außer dem
    # Review-Backlog:
    #   - "none"           → reiner Token-Guess (Variante D, bisher schon)
    #   - "ambiguous"      → mehrere gleichwertige Treffer → "Unklarer Titel"/0.35
    #   - "brand_whitelist"→ Plattform-Marken (z.B. "Netflix Originals")/0.85
    #   - "empty"          → Factory-internes Fallback-Label, wenn der Zweit-
    #     Call (Z. unten, kinetic_text/placement_title_text) keinen Text hat.
    #     Wäscht sonst einen geblockten Erst-Treffer (ambiguous ohne kinetic/
    #     placement → "empty") an diesem Filter vorbei und erzeugt den
    #     "Unklarer Titel"/0.35-Candidate im realen Rematch-Pfad.
    # Echte Whitelist-Treffer (exact, hashtag, unique_text, fuzzy) bleiben
    # erhalten. User-getriebene Calls (skip_if_guess_only=False) sind
    # unberührt — der User-Intent überstimmt den Filter. Der Filter keyed auf
    # match.source, NIE auf confidence (echte OPENAI/0.35-Candidates bleiben).
    if skip_if_guess_only and match.source in ("none", "ambiguous", "brand_whitelist", "empty"):
        return None

    candidate = TitleCandidate(
        asset_id=asset.id,
        suggested_title=match.suggested_title or "Unklarer Titel",
        suggested_franchise=asset.de_us_match_key,
        source=_candidate_source_from_text(asset) if match.source == "none" else CandidateSource.MATCHER,
        confidence=match.confidence if match.confidence > 0 else 0.35,
        status=CandidateStatus.OPEN,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def resolve_open_candidates_for_asset(session: Session, asset_id) -> int:
    candidates = session.exec(
        select(TitleCandidate).where(TitleCandidate.asset_id == asset_id, TitleCandidate.status == CandidateStatus.OPEN)
    ).all()
    for candidate in candidates:
        candidate.status = CandidateStatus.RESOLVED
        session.add(candidate)
    if candidates:
        session.commit()
    return len(candidates)
