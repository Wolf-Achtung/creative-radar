from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.entities import Asset, Post, TitleCandidate, CandidateStatus
from app.services.title_candidates import create_candidate_from_asset, resolve_open_candidates_for_asset
from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match


@dataclass
class RematchSummary:
    checked: int = 0
    auto_matched: int = 0
    candidates_created: int = 0
    still_unmatched: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "checked": self.checked,
            "auto_matched": self.auto_matched,
            "candidates_created": self.candidates_created,
            "still_unmatched": self.still_unmatched,
        }


def _build_match_fields(asset: Asset, post: Post | None) -> dict[str, str | list[str] | None]:
    return {
        "caption": post.caption if post else None,
        "ocr_text": asset.ocr_text,
        "detected_keywords": asset.detected_keywords or [],
        "ai_summary_de": asset.ai_summary_de,
        "ai_summary_en": asset.ai_summary_en,
        "suggested_title": asset.placement_title_text,
        "visual_notes": asset.visual_notes,
    }


def rematch_unassigned_assets(session: Session) -> RematchSummary:
    assets = session.exec(select(Asset).where(Asset.title_id == None).order_by(Asset.created_at.desc())).all()  # noqa: E711
    summary = RematchSummary(checked=len(assets))

    for asset in assets:
        post = session.get(Post, asset.post_id)
        caption = post.caption if post else ""
        match_fields = _build_match_fields(asset, post)
        match = find_best_title_match(session, caption, fields=match_fields)

        if is_safe_auto_match(match) and match.title:
            asset.title_id = match.title.id
            asset.de_us_match_key = match.title.franchise or match.title.title_original
            session.add(asset)
            session.commit()
            resolve_open_candidates_for_asset(session, asset.id)
            summary.auto_matched += 1
            continue

        existing_open = session.exec(
            select(TitleCandidate).where(
                TitleCandidate.asset_id == asset.id,
                TitleCandidate.status == CandidateStatus.OPEN,
            )
        ).first()
        if not existing_open:
            create_candidate_from_asset(session, asset.id)
            summary.candidates_created += 1
        summary.still_unmatched += 1

    return summary
