from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import Asset, Post, TitleCandidate, CandidateStatus
from app.services.match_key import slugify_match_key
from app.services.title_candidates import create_candidate_from_asset, resolve_open_candidates_for_asset
from app.services.whitelist_matcher import (
    build_compact_index,
    build_normalized_index,
    build_token_index,
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
        # Recall-Fix (Post-#277): die Sprint-5.3.1-Vision-Pipeline schreibt ihren
        # Output nach ``vision_description`` — der Matcher las dieses Feld bisher
        # NICHT (Lese-Lücke). Titel, die nur dort stehen, waren unsichtbar →
        # source=none trotz erreichbarem Katalog. Jetzt als Match-Feld geführt.
        "vision_description": asset.vision_description,
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
    # Post-#277: token-inverted index once per batch, so the per-asset matcher
    # prefilters substring/fuzzy candidates to token-overlap instead of scanning
    # all ~19k keys with SequenceMatcher (the 14.7k-catalog rematch hang).
    token_index = build_token_index(normalized_index)
    # Post-#280-merge: also cache the compact hashtag index once per batch. The
    # candidate path re-runs the matcher per asset; without these caches it
    # reloaded the full 14.7k-title bundle + rebuilt every index per asset
    # (~1 asset/s, cron-untauglich).
    compact_index = build_compact_index(normalized_index)
    _caches = dict(
        cached_bundle=bundle,
        cached_normalized_index=normalized_index,
        cached_token_index=token_index,
        cached_compact_index=compact_index,
    )

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
            published_at=post.published_at if post else None,
            **_caches,
        )

        if is_safe_auto_match(match) and match.title:
            asset.title_id = match.title.id
            asset.de_us_match_key = slugify_match_key(match.title.franchise or match.title.title_original)
            session.add(asset)
            # Batch the writes (perf fix): resolve the now-stale OPEN candidates in
            # the same transaction instead of committing per asset.
            resolve_open_candidates_for_asset(session, asset.id, commit=False)
            summary.auto_matched += 1
            pending_commit += 1
        else:
            existing_open = session.exec(
                select(TitleCandidate).where(
                    TitleCandidate.asset_id == asset.id,
                    TitleCandidate.status == CandidateStatus.OPEN,
                )
            ).first()
            if not existing_open:
                # Sprint 28.05.2026 (Variante D): die Funktion kann ``None``
                # zurueckgeben, wenn der Matcher nur einen Token-Guess produziert
                # hat. Caches durchreichen (kein Bundle-Reload pro Asset) und
                # NICHT pro Candidate committen (Batch).
                candidate = create_candidate_from_asset(
                    session, asset.id, commit=False, **_caches
                )
                if candidate is not None:
                    summary.candidates_created += 1
                    pending_commit += 1
            summary.still_unmatched += 1

        if pending_commit >= commit_batch_size:
            session.commit()
            pending_commit = 0

    if pending_commit > 0:
        session.commit()

    return summary
