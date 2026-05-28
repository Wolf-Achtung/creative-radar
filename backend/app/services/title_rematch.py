from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import Asset, Post, TitleCandidate, CandidateStatus
from app.services.match_key import slugify_match_key
from app.services.title_candidates import create_candidate_from_asset, resolve_open_candidates_for_asset
from app.services.whitelist_matcher import (
    build_normalized_index,
    find_best_title_match,
    is_safe_auto_match,
    load_title_bundle,
)


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


def rematch_unassigned_assets(session: Session, *, commit_batch_size: int = 50) -> RematchSummary:
    assets = session.exec(
        select(Asset).where(Asset.title_id == None).order_by(Asset.created_at.desc())  # noqa: E711
    ).all()
    summary = RematchSummary(checked=len(assets))

    # Sprint 10g: load the active-title bundle and the normalized lookup index
    # exactly once per batch. Previously, find_best_title_match rebuilt both
    # for every asset, which scales as O(titles × assets) DB queries (~3M for
    # ~3k titles × 1k unmatched assets) and timed out the Railway gateway.
    bundle = load_title_bundle(session)
    normalized_index = build_normalized_index(bundle)

    post_ids = [a.post_id for a in assets if a.post_id]
    posts_by_id: dict[UUID, Post] = {}
    if post_ids:
        posts_by_id = {
            p.id: p
            for p in session.exec(select(Post).where(Post.id.in_(post_ids))).all()
        }

    pending_commit = 0
    for asset in assets:
        post = posts_by_id.get(asset.post_id) if asset.post_id else None
        caption = post.caption if post else ""
        match_fields = _build_match_fields(asset, post)
        match = find_best_title_match(
            session,
            caption,
            fields=match_fields,
            cached_bundle=bundle,
            cached_normalized_index=normalized_index,
        )

        if is_safe_auto_match(match) and match.title:
            asset.title_id = match.title.id
            asset.de_us_match_key = slugify_match_key(match.title.franchise or match.title.title_original)
            session.add(asset)
            pending_commit += 1
            if pending_commit >= commit_batch_size:
                session.commit()
                pending_commit = 0
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
            # Sprint 28.05.2026 (Variante D): die Funktion kann jetzt
            # ``None`` zurueckgeben, wenn der Matcher nur ein
            # Token-Guess produziert hat. Den Counter nur dann
            # hochzaehlen, wenn tatsaechlich eine Row geschrieben
            # wurde.
            candidate = create_candidate_from_asset(session, asset.id)
            if candidate is not None:
                summary.candidates_created += 1
        summary.still_unmatched += 1

    if pending_commit > 0:
        session.commit()

    return summary
