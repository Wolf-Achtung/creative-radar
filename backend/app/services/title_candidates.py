from __future__ import annotations

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


def create_candidate_from_asset(session: Session, asset_id, force: bool = False) -> TitleCandidate:
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
