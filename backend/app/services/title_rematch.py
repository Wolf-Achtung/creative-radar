from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import Asset, Post, TitleCandidate, CandidateStatus, utc_now
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
    # Soft-Deadline (Cron-Run 16421771, 20.07.2026): ``partial=True`` heisst,
    # das Zeitbudget lief ab, bevor alle Assets geprueft waren — ``remaining``
    # zaehlt die diesmal nicht mehr erreichten Assets (naechster Lauf
    # versucht sie erneut). ``checked`` zaehlt nur tatsaechlich verarbeitete.
    partial: bool = False
    remaining: int = 0
    # Zeitmessung (24.08.2026): Die Stage schaffte im Montagslauf 781 Assets
    # in 28 Minuten — rund zwei Sekunden pro Asset, obwohl Bundle und Indizes
    # nur EINMAL pro Lauf gebaut werden. Wohin die Zeit geht, war nicht
    # bekannt; den Deckel anzuheben behandelt das Symptom. Diese Felder
    # trennen die drei Verdaechtigen, damit die naechste Entscheidung auf
    # einer Messung steht statt auf einer Vermutung.
    setup_seconds: float = 0.0      # Bundle + die drei Indizes, einmalig
    match_seconds: float = 0.0      # Summe aller find_best_title_match
    candidate_seconds: float = 0.0  # Kandidaten-Pfad inkl. Bestands-Query
    commit_seconds: float = 0.0     # Summe aller Batch-Commits

    def to_dict(self) -> dict[str, int | bool | float]:
        return {
            "checked": self.checked,
            "auto_matched": self.auto_matched,
            "candidates_created": self.candidates_created,
            "still_unmatched": self.still_unmatched,
            "partial": self.partial,
            "remaining": self.remaining,
            "setup_seconds": round(self.setup_seconds, 1),
            "match_seconds": round(self.match_seconds, 1),
            "candidate_seconds": round(self.candidate_seconds, 1),
            "commit_seconds": round(self.commit_seconds, 1),
            "assets_pro_sekunde": (
                round(self.checked / self.match_seconds, 2)
                if self.match_seconds > 0
                else None
            ),
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


def rematch_unassigned_assets(
    session: Session,
    *,
    commit_batch_size: int = 50,
    time_budget_seconds: float | None = None,
) -> RematchSummary:
    """Re-matcht alle Assets ohne Title-Zuordnung gegen den Whitelist-Katalog.

    Soft-Deadline (Cron-Run 16421771, 20.07.2026): der Katalog ist auf ~29k
    Titel gewachsen und der unmatched-Bestand waechst woechentlich — die
    Stage lief in ihr hartes ``asyncio.wait_for``-Timeout (1800s), das den
    ``to_thread``-Worker nicht abbrechen kann. Der Zombie-Thread lief dann
    parallel zur Brief-Stage auf DERSELBEN Session weiter (Sessions sind
    nicht threadsafe). ``time_budget_seconds`` (gemessen ab Funktionsstart,
    inkl. Bundle-/Index-Aufbau) laesst die Schleife stattdessen SELBST
    sauber abbrechen: Teilstand wird committet, ``partial``/``remaining``
    landen in der Summary, der Rest ist beim naechsten Lauf dran (Assets
    werden newest-first verarbeitet — die aktuelle Woche zuerst).
    ``None`` = unbegrenzt (manueller Pfad ``POST /api/titles/rematch-assets``
    bleibt unveraendert).

    Rotation statt fester Kopf (31.08.2026): vorher lud jeder Lauf ALLE
    titellosen Assets neueste-zuerst — die vorderen ~1.200 wurden jede
    Woche neu geprueft, die hinteren 2.639 nie erreicht. Jetzt kommen
    NIE gepruefte zuerst (darunter neueste zuerst — frische Kampagnen
    speisen die Montags-Queue), danach die am laengsten nicht
    geprueften; jeder angefasste Kandidat bekommt ``last_rematch_at``
    und rueckt ans Ende. Der Backlog laeuft so in wenigen Wochen einmal
    komplett durch statt gar nicht.
    """
    started = time.monotonic()
    assets = session.exec(
        select(Asset)
        .where(Asset.title_id == None)  # noqa: E711
        .order_by(
            Asset.last_rematch_at.asc().nulls_first(),
            Asset.created_at.desc(),
        )
    ).all()
    summary = RematchSummary()

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
    summary.setup_seconds = time.monotonic() - started
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
    for index, asset in enumerate(assets):
        if (
            time_budget_seconds is not None
            and time.monotonic() - started >= time_budget_seconds
        ):
            summary.partial = True
            summary.remaining = len(assets) - index
            break
        summary.checked += 1
        # Rotations-Stempel: auch ein erfolgloser Check zaehlt als
        # geprueft — sonst stuende derselbe hoffnungslose Fall naechste
        # Woche wieder vorn. Commit laeuft ueber den Batch unten.
        asset.last_rematch_at = utc_now()
        session.add(asset)
        pending_commit += 1
        post = posts_by_id.get(asset.post_id) if asset.post_id else None
        caption = post.caption if post else ""
        match_fields = _build_match_fields(asset, post)
        _match_start = time.monotonic()
        match = find_best_title_match(
            session,
            caption,
            fields=match_fields,
            published_at=post.published_at if post else None,
            **_caches,
        )
        summary.match_seconds += time.monotonic() - _match_start

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
            _kandidat_start = time.monotonic()
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
            summary.candidate_seconds += time.monotonic() - _kandidat_start
            summary.still_unmatched += 1

        if pending_commit >= commit_batch_size:
            _commit_start = time.monotonic()
            session.commit()
            summary.commit_seconds += time.monotonic() - _commit_start
            pending_commit = 0

    if pending_commit > 0:
        _commit_start = time.monotonic()
        session.commit()
        summary.commit_seconds += time.monotonic() - _commit_start

    return summary
