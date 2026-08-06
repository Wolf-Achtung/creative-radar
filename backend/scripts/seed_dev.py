"""Reproduzierbarer synthetischer Datensatz fuer Lokal + Cloud-Staging
(Staging-Briefing 2026-08-06, Abschnitt 2 Schritt 2).

    python -m scripts.seed_dev                      # Default: disney + netflix
    python -m scripts.seed_dev --pairs lionsgate    # andere Pairs
    python -m scripts.seed_dev --posts-per-channel 8

Was entsteht:
- Channels exakt nach dem ``PAIRS``-Dict (source of truth, kein Duplikat
  der Handle-Listen hier) fuer die gewaehlten Pairs, plus sechs
  Segment-Channels (einer je ``ChannelSegment``) fuer den Roundup-Pfad.
- Posts ueber ``mock_fixtures`` + die ECHTEN ``normalize_*``-Funktionen —
  derselbe Code-Pfad, den ein Mock-Cron-Lauf nimmt. Gleiche Handles
  erzeugen dieselben ``post_url``s, ein spaeterer Mock-Scrape kollidiert
  also idempotent mit dem Seed statt zu duplizieren.
- Titel + Keywords aus ``mock_fixtures.SYNTHETIC_TITLES`` (die Captions
  zitieren genau diese Titel — Matching/Whitelist haben echte Treffer).
- Ein ``insight_report`` (Wochen-Brief) pro Pair fuer die laufende
  ISO-Woche: Aggregation via echtem ``aggregate_pair``, ``llm_output``
  synthetisch, aber Pydantic-validiert (``LLMReport``) und mit Zitaten,
  die auf real existierende Post-URLs der Aggregation zeigen. Kein
  LLM-Call, keine Kosten — die Startseite ist trotzdem voll.

Idempotenz: jeder Lauf loescht zuerst alles, was ein frueherer Lauf
angelegt hat (Marker: ``import_source='seed_dev'`` an Channels,
``source='seed_dev'`` an Titeln, ``model='seed-dev'`` an Briefs, Posts
ueber ihre Seed-Channels), und baut dann neu auf. Zweiter Lauf = Reset,
keine Duplikate. Prod-Schutz: bricht ab, wenn ``APP_ENV=production``.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from sqlmodel import Session, delete, select

from app.config import settings
from app.database import engine
from app.models.entities import (
    Asset,
    Channel,
    ChannelSegment,
    InsightReport,
    Market,
    Post,
    Priority,
    Title,
    TitleKeyword,
)
from app.schemas.insights import LLMReport
from app.services.apify_connector import normalize_public_item, normalize_tiktok_item
from app.services.insight_engine import PAIRS, aggregate_pair
from app.services.mock_fixtures import (
    SYNTHETIC_TITLES,
    mock_instagram_items,
    mock_tiktok_items,
    mock_youtube_channel_videos,
)
from app.services.youtube_connector import normalize_youtube_video

SEED_MARKER = "seed_dev"
DEFAULT_PAIRS = ["disney", "netflix"]

# Non-Pair-Channels fuer den Roundup-Pfad — einer je Segment.
SEGMENT_CHANNELS: list[tuple[str, str, ChannelSegment, Market]] = [
    ("Mock Pictures US", "mockpicturesus", ChannelSegment.US_MAJOR, Market.US),
    ("Mock Indie US", "mockindieus", ChannelSegment.US_INDEPENDENT, Market.US),
    ("Mock Films UK", "mockfilmsuk", ChannelSegment.UK_MAJOR, Market.UK),
    ("Mock Indie UK", "mockindieuk", ChannelSegment.UK_INDEPENDENT, Market.UK),
    ("Mock Verleih DE", "mockverleihde", ChannelSegment.DE_VERLEIH, Market.DE),
    ("Mock Indie DE", "mockindiede", ChannelSegment.DE_INDEPENDENT, Market.DE),
]


def _fail_if_production() -> None:
    if settings.app_env == "production":
        print("ABBRUCH: seed_dev laeuft nicht gegen APP_ENV=production.", file=sys.stderr)
        sys.exit(1)


def _wipe_previous_seed(session: Session) -> None:
    """Alles aus frueheren Laeufen entfernen — Reihenfolge FK-sicher."""
    seed_channel_ids = list(
        session.exec(select(Channel.id).where(Channel.import_source == SEED_MARKER)).all()
    )
    if seed_channel_ids:
        seed_post_ids = list(
            session.exec(select(Post.id).where(Post.channel_id.in_(seed_channel_ids))).all()
        )
        if seed_post_ids:
            session.exec(delete(Asset).where(Asset.post_id.in_(seed_post_ids)))
            session.exec(delete(Post).where(Post.id.in_(seed_post_ids)))
        session.exec(delete(Channel).where(Channel.id.in_(seed_channel_ids)))

    seed_title_ids = list(
        session.exec(select(Title.id).where(Title.source == SEED_MARKER)).all()
    )
    if seed_title_ids:
        session.exec(delete(TitleKeyword).where(TitleKeyword.title_id.in_(seed_title_ids)))
        session.exec(delete(Title).where(Title.id.in_(seed_title_ids)))

    session.exec(delete(InsightReport).where(InsightReport.model == "seed-dev"))
    session.commit()


def _seed_titles(session: Session) -> int:
    today = date.today()
    for offset, (title_original, tag) in enumerate(SYNTHETIC_TITLES):
        title = Title(
            title_original=title_original,
            title_local=title_original,
            content_type="Film",
            market_relevance=Market.MIXED,
            release_date_de=today + timedelta(days=30 + offset * 14),
            release_date_us=today + timedelta(days=23 + offset * 14),
            source=SEED_MARKER,
            priority=Priority.A,
            active=True,
        )
        session.add(title)
        session.flush()
        for keyword in {title_original.lower(), tag}:
            session.add(TitleKeyword(title_id=title.id, keyword=keyword))
    session.commit()
    return len(SYNTHETIC_TITLES)


def _channel_url(platform: str, handle: str) -> str:
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    return f"https://www.youtube.com/@{handle}"


def _seed_pair_channels(session: Session, pair_keys: list[str]) -> dict[str, Channel]:
    """Channels exakt nach PAIRS anlegen. Key: (platform, handle-lower)."""
    by_key: dict[str, Channel] = {}
    for pair_key in pair_keys:
        pair_def = PAIRS[pair_key]
        for platform, entries in pair_def.get("platforms", {}).items():
            for entry in entries:
                handle = entry["handle"]
                dict_key = f"{platform}:{handle.lower()}"
                if dict_key in by_key:
                    continue
                channel = Channel(
                    name=f"{handle} ({pair_def['display_name']})",
                    platform=platform,
                    url=_channel_url(platform, handle),
                    handle=handle,
                    market=Market(entry["market"]),
                    channel_type="Studio",
                    priority=Priority.A,
                    active=True,
                    import_source=SEED_MARKER,
                    notes=f"seed_dev pair={pair_key}",
                )
                session.add(channel)
                by_key[dict_key] = channel
    session.commit()
    return by_key


def _seed_segment_channels(session: Session) -> list[Channel]:
    channels: list[Channel] = []
    for name, handle, segment, market in SEGMENT_CHANNELS:
        channel = Channel(
            name=name,
            platform="instagram",
            url=_channel_url("instagram", handle),
            handle=handle,
            market=market,
            channel_type="Verleih" if "verleih" in handle else "Studio",
            priority=Priority.B,
            active=True,
            segment=segment,
            import_source=SEED_MARKER,
            notes="seed_dev segment-channel",
        )
        session.add(channel)
        channels.append(channel)
    session.commit()
    return channels


def _insert_posts(session: Session, channel: Channel, normalized: list[dict]) -> int:
    inserted = 0
    for item in normalized:
        if not item["post_url"]:
            continue
        session.add(Post(
            channel_id=channel.id,
            platform=item["platform"],
            post_url=item["post_url"],
            external_id=item.get("external_id"),
            published_at=item["published_at"],
            caption=item["caption"],
            raw_payload=item["raw"],
            visible_likes=item["visible_likes"],
            visible_comments=item["visible_comments"],
            visible_views=item["visible_views"],
            visible_shares=item["visible_shares"],
            visible_bookmarks=item["visible_bookmarks"],
            duration_seconds=item["duration_seconds"],
            media_type=None,
            status="new",
        ))
        inserted += 1
    return inserted


def _seed_posts(session: Session, channels: list[Channel], posts_per_channel: int) -> int:
    total = 0
    for channel in channels:
        if channel.platform == "instagram":
            raw = mock_instagram_items([channel.url], posts_per_channel)
            normalized = [normalize_public_item(i) for i in raw]
        elif channel.platform == "tiktok":
            raw = mock_tiktok_items([channel.handle or ""], posts_per_channel)
            normalized = [normalize_tiktok_item(i) for i in raw]
        else:
            _meta, raw = mock_youtube_channel_videos(channel.handle or "", posts_per_channel)
            normalized = [normalize_youtube_video(i) for i in raw]
        total += _insert_posts(session, channel, normalized)
    session.commit()
    return total


def _build_llm_output(pair_label: str, aggregation: dict) -> dict:
    """Schema-valides synthetisches Brief-JSON. Zitate zeigen auf real
    existierende Post-URLs aus der Aggregation — dieselbe Invariante, die
    die Zitat-Validierung der echten Brief-Generierung erzwingt."""
    cited: list[str] = []
    for stats_key in ("de_channel", "us_channel", "uk_channel"):
        stats = aggregation.get(stats_key) or {}
        for top_post in (stats.get("top_posts") or [])[:2]:
            if top_post.get("post_url"):
                cited.append(top_post["post_url"])
    report = LLMReport.model_validate({
        "headline": f"[SEED] {pair_label}: Kurze Teaser dominieren die Woche",
        "tldr": (
            "Synthetischer Dev-Brief aus seed_dev.py — keine echte Analyse. "
            "Die Testdaten enthalten bewusst ein Muster: Clips unter 20 "
            "Sekunden holen ein Mehrfaches der Views laengerer Cuts."
        ),
        "trends": [{
            "name": "Teaser unter 20 Sekunden ueberperformen",
            "evidence": "In den Seed-Daten liegen kurze Clips ~3x ueber laengeren Formaten.",
            "implication_for_creation": "Fuer Tests: Kurzformate zuerst pruefen.",
            "cited_post_ids": cited[:2],
        }],
        "actions": [{
            "what": "Muster-Aggregation gegen diesen Datensatz laufen lassen",
            "why": "Die eingebauten Muster (Teaser x3, US x2) muessen wiedergefunden werden.",
            "for_whom": "Entwicklung",
            "cited_post_ids": cited[:1],
        }],
        "cross_market_insight": {
            "de_vs_us": "US-Seed-Channels performen konstruktionsbedingt ~2x ueber DE.",
            "transfer_opportunity": "Kein Transfer — synthetische Daten, Muster ist konstruiert.",
        },
        "risks": ["Synthetische Daten — keine inhaltlichen Schluesse ziehen."],
        "data_caveats": ["Alle Werte aus scripts/seed_dev.py generiert."],
    }, context={"has_de_data": bool(aggregation.get("de_channel")), "has_cross_market": False})
    return report.model_dump(mode="json")


def _seed_briefs(session: Session, pair_keys: list[str]) -> int:
    created = 0
    for pair_key in pair_keys:
        aggregation_model = aggregate_pair(session, pair_key, window_days=7)
        aggregation = aggregation_model.model_dump(mode="json")
        session.add(InsightReport(
            pair_key=pair_key,
            iso_year=aggregation_model.iso_year,
            iso_week=aggregation_model.iso_week,
            aggregation=aggregation,
            llm_output=_build_llm_output(aggregation_model.pair_label, aggregation),
            model="seed-dev",
        ))
        created += 1
    session.commit()
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS),
                        help=f"Kommagetrennte Pair-Keys (Default: {','.join(DEFAULT_PAIRS)})")
    parser.add_argument("--posts-per-channel", type=int, default=6)
    args = parser.parse_args()

    pair_keys = [p.strip() for p in args.pairs.split(",") if p.strip()]
    unknown = [p for p in pair_keys if p not in PAIRS]
    if unknown:
        print(f"Unbekannte Pairs: {unknown}. Verfuegbar: {list(PAIRS)}", file=sys.stderr)
        sys.exit(1)

    _fail_if_production()

    with Session(engine) as session:
        _wipe_previous_seed(session)
        titles = _seed_titles(session)
        pair_channels = _seed_pair_channels(session, pair_keys)
        segment_channels = _seed_segment_channels(session)
        all_channels = list(pair_channels.values()) + segment_channels
        posts = _seed_posts(session, all_channels, args.posts_per_channel)
        briefs = _seed_briefs(session, pair_keys)

    print(
        f"seed_dev fertig: {titles} Titel, {len(pair_channels)} Pair-Channels, "
        f"{len(segment_channels)} Segment-Channels, {posts} Posts, {briefs} Briefs "
        f"(Pairs: {', '.join(pair_keys)})"
    )


if __name__ == "__main__":
    main()
