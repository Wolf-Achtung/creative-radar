"""Insight-Engine MVP — Sprint 1 (warnerbros DE+US TikTok).

Single-shot Opus 4.7 weekly briefing for the Trailerhaus creative team.
Deterministic aggregation lives in this module; the LLM call is one
``messages_create_text`` per report, no agent loop.

The pair definition is hardcoded by design (see ``PAIRS`` below) — generalising
to the other six Tier-A pairs is Sprint-2 work and explicitly out-of-scope for
this MVP. Adding a new pair before then is a config-only change in this file.

Cost expectation: with the Sprint-Trailerhaus-Prompt-v1 expanded prompt +
``ganz genau`` mode the per-call shape is roughly 8-12k input + 3-4k output
tokens. At Opus 4.7 list price (~$15 / $75 per Mtok) that's ~$0.35-0.50
per report. Earlier numbers (~$0.20) were for the v0 prompt. The endpoint
still accepts ``dry_run=true`` to skip the LLM call entirely when iterating
on the aggregation or prompt.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import sqlalchemy as sa
from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Post, Title
from app.schemas.insights import (
    Action,
    ChannelStats,
    CrossMarketInsight,
    CrossMarketMatch,
    HashtagFrequency,
    InsightReport,
    LLMReport,
    PairAggregation,
    TitleCoverage,
    TopPost,
    Trend,
)
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    is_anthropic_configured,
    messages_create_text,
)

logger = logging.getLogger(__name__)


# ---------- Pair registry ---------------------------------------------------

# Sprint-2: registry expanded to the seven Tier-A DE+US TikTok pairs from the
# whitelist-expansion migration ``e5d8f1a36b40``. Six pairs are enabled today;
# ``universalpictures`` ships as a disabled placeholder until both channels
# clear the activation threshold (>=3 posts/30d on each side). Endpoint logic
# in ``api/insights.py`` returns 503 with the structured ``reason`` so the
# Frontend can render a "coming soon" state without a config push.
#
# Promotion to a DB-backed config table is still on the roadmap (Sprint-3+);
# until then a new pair is a single PR touching this dict and the Frontend
# fallback label map. ``aggregate_pair`` records missing channels in
# ``notes`` rather than crashing, so a handle drift between this dict and
# the production DB is observable in the report rather than fatal.
#
# Each entry must define:
# - ``label``: human-readable, shown in the Frontend hero ("<key> DE+US").
# - ``platform``: locked to TikTok for the Tier-A scope.
# - ``channels``: list of {handle, market}. Order is irrelevant; lookups
#   pick by market.
# - ``enabled``: bool. When False, the endpoint short-circuits to 503 before
#   touching the DB or the LLM.
# - ``reason``: required when ``enabled=False``. Surfaced to the Frontend
#   as the disable-explanation. May be None when ``enabled=True``.
PAIRS: dict[str, dict[str, Any]] = {
    "warnerbros": {
        "label": "warnerbros DE+US",
        "platform": "tiktok",
        "channels": [
            # Production-confirmed handle (channels_perplexity_2026_05_03.csv).
            {"handle": "warnerbros", "market": "US"},
            # DE handle per Wolf brief; aliasing handled by the case-insensitive
            # lookup. If the actual stored handle differs, ``aggregate_pair``
            # records that in ``notes`` rather than failing.
            {"handle": "warnerbrosdeutschland", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "sonypictures": {
        "label": "sonypictures DE+US",
        "platform": "tiktok",
        "channels": [
            {"handle": "sonypictures", "market": "US"},
            {"handle": "sonypicturesgermany", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "primevideo": {
        "label": "primevideo DE+US",
        "platform": "tiktok",
        "channels": [
            {"handle": "primevideo", "market": "US"},
            {"handle": "primevideode", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "disney": {
        "label": "disney DE+US",
        "platform": "tiktok",
        "channels": [
            # Wolf-spec handle. The whitelist-expansion migration registers
            # ``disneystudios`` and ``disneyanimation`` for US Disney; if
            # ``disney`` is not the production handle for the US side,
            # ``aggregate_pair`` will surface that in ``notes``.
            {"handle": "disney", "market": "US"},
            {"handle": "disneyde", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "netflix": {
        "label": "netflix DE+US",
        "platform": "tiktok",
        "channels": [
            {"handle": "netflix", "market": "US"},
            {"handle": "netflixde", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "paramountpictures": {
        "label": "paramountpictures DE+US",
        "platform": "tiktok",
        "channels": [
            # US handle is ``paramountpics``, not ``paramountpictures``
            # (per migration e5d8f1a36b40 + Wolf brief).
            {"handle": "paramountpics", "market": "US"},
            {"handle": "paramountpicturesgermany", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "universalpictures": {
        "label": "universalpictures DE+US",
        "platform": "tiktok",
        "channels": [
            {"handle": "universalpictures", "market": "US"},
            {"handle": "universalpicturesde", "market": "DE"},
        ],
        "enabled": False,
        "reason": (
            "US-Channel @universalpictures has 0 posts/30d, "
            "pair will activate when both channels have >=3 posts/30d"
        ),
    },
}


# ---------- Model + cost ----------------------------------------------------

# Opus 4.7 alias — the briefing pins the engine to the latest Opus. Override
# via ENV (see config.Settings.anthropic_*_model pattern) if Wolf wants to
# force a datestamped pin, but no override is wired today: one Opus, one call.
OPUS_MODEL_ALIAS = "claude-opus-4-7"

# Public list price as of 2026-05 — Opus 4.7: $15/Mtok input, $75/Mtok output.
# Used only for the cost-estimate field in the response (informational, not
# enforced). Update when Anthropic publishes new pricing.
_OPUS_INPUT_PER_1K_USD = 0.015
_OPUS_OUTPUT_PER_1K_USD = 0.075


# ---------- System prompt ---------------------------------------------------
#
# Sprint-Trailerhaus-Prompt-v1: the prompt is rebuilt around the Trailerhaus
# Voice — "Audiovisual Communication, Made Emotional" — with three concrete
# guardrails baked in:
#
#  1. **Voice anchor**: tone is the experienced AV-Communications-Partner,
#     not a generic strategist. We name that explicitly so the model stops
#     drifting into LLM register.
#
#  2. **Glossary + anti-pattern**: an allow-list of Trailerhaus vocabulary
#     and a block-list of LLM-typical English X-Y hyphen-Floskeln pulled
#     directly from today's outputs (Brand-Storytelling, Engagement-Drivers,
#     Hook-Architektur, …). Without this, Opus reliably reaches for those
#     constructions even with a strong persona.
#
#  3. **Schema enforcement**: all original fields stay required; the six
#     new role-oriented sections (tonalitaet, watch_outs, fuer_cutter,
#     fuer_motion_designer, fuer_creative_producer, vergleichbare_posts)
#     are explicit in the schema with one-line guidance. ``risks`` stays
#     for backwards-compat with old reports.
#
# A few-shot example follows the schema block — fully-realised JSON for a
# small synthetic Warner-Bros pair so the model has a concrete reference
# for the new role sections, not just slot names.
#
# The actual data lives in the user message; the persona can be cached
# server-side once Anthropic prompt-caching is wired (Sprint-2 follow-up).
SYSTEM_PROMPT = """\
Du bist Senior-Trailer-Marketing-Stratege und AV-Communications-Partner bei \
Trailerhaus — einem deutschen Studio für audiovisuelle Kampagnen für Streaming-, \
Theatrical- und Home-Entertainment-Releases. Tagline: "Audiovisual Communication, \
Made Emotional". Du arbeitest seit Jahren mit Disney, Amazon und LEONINE an \
weltweiten Day-and-Date-Releases unter Top-Tier-Security-Standards.

VOICE — wie du schreibst:
- Präzise, hochwertig, emotional wirksam. Kein Marketing-Bullshit, keine \
  LLM-Floskeln.
- Du sprichst die Sprache von Cuttern, Creative Producern und Motion Designern: \
  fachlich, direkt, mit Daten-Anker (Zahl, Asset-URL, Caption-Zitat).
- Du nutzt deutsche Sätze. Englische Fachbegriffe nur, wenn sie etablierte \
  Trailerhaus-Vokabeln sind (siehe Glossar). Keine Anglizismen-Erfindungen.
- Du sagst, was du NICHT belegen kannst, statt zu raten. Lieber ein starker \
  Trend mit Daten-Anker als fünf ohne.

GLOSSAR — diese Begriffe sind erlaubt und erwünscht:
Hook, Pace, Beat, Cut, Cold-Open, L3 (Lower Third), End Card, In-Service, \
Off-Service, BTS (Behind the Scenes), Texted, Textless, Cadence, GSA \
(Germany/Austria/Switzerland), Watch Out, Tonalität, USP, Key Selling Point, \
Logline, Trailer, Teaser, Spot, Pitch.

ANTI-PATTERN — diese englischen X-Y-Hyphen-Konstrukte sind VERBOTEN:
Brand-Storytelling, Engagement-Drivers, Hook-Architektur, Live-Event-Framing, \
Catalog-Nostalgie, Catalog-Reaktivierung, Fan-Service-Loop, \
Brand-Storytelling-Loop, Discovery-Cut. Ebenso verboten: jede neue, frei \
erfundene englische "X-Y"-Konstruktion ohne klare Bedeutung. Wenn du einen \
Begriff brauchst, der nicht im Glossar steht, beschreibe ihn auf Deutsch in \
zwei oder drei Worten.

TONALITÄTS-POOL — wähle 3-5 Adjektive aus diesem Pool, jedes mit \
Daten-Begründung:
authentisch, unbequem, berührend, auffordernd, sophisticated, mysterious, \
cinematisch, hochwertig, emotional, spannend, action-reich, humorvoll, \
präzise, international, erfahren.

LÄNGE — produziere die ausführliche Variante ("ganz genau"-Modus, ca. \
1500-2000 Wörter Gesamtoutput). Das Frontend filtert später für kürzere \
Modi. Gib also alle Sektionen vollständig aus, auch wenn du sie später \
gekürzt sehen würdest.

OUTPUT — AUSSCHLIESSLICH ein JSON-Objekt nach folgendem Schema. Kein \
Vorspann, kein Markdown-Codefence, keine Erklärung — nur das JSON:

{
  "headline": "Eine Zeile, max. 90 Zeichen, präzise statt provokant — \
benennt den Wochenkern",
  "tldr": "3 Sätze: was ist diese Woche anders, was sollte Trailerhaus \
daraus lernen, wo ist die Wette",
  "trends": [
    {
      "name": "kurzer Trend-Name auf Deutsch",
      "evidence": "konkrete Zahl, Asset-URL oder Caption-Zitat aus den Daten",
      "implication_for_creation": "was Trailerhaus konkret in Schnitt, Hook \
oder Pacing ändern sollte"
    }
  ],
  "actions": [
    {
      "what": "konkrete Handlung",
      "why": "Beleg aus den Daten",
      "for_whom": "Cutter / Creative Producer / Motion Designer / Hook-Verantwortlicher"
    }
  ],
  "cross_market_insight": {
    "de_vs_us": "Was unterscheidet die Märkte diese Woche, mit Daten-Anker",
    "transfer_opportunity": "Was sollte aus US für DE adaptiert werden \
oder umgekehrt"
  },
  "risks": ["Kurzfassung als String — bleibt aus Backwards-Compat-Gründen"],
  "data_caveats": ["..."],

  "tonalitaet": [
    {
      "adjektiv": "ein Adjektiv aus dem Tonalitäts-Pool",
      "begruendung": "ein Satz, warum dieses Adjektiv die Woche trifft, mit \
Daten-Anker"
    }
  ],
  "watch_outs": [
    {
      "watch_out": "Beobachtung, die in der Produktion zur Falle werden kann",
      "konsequenz": "was das für den Schnitt oder die Hook bedeutet"
    }
  ],
  "fuer_cutter": {
    "schnitt_pace": "Beobachtung zum Pacing, abgeleitet aus Top-Posts und \
Duration-Buckets",
    "hook_strategie": "welche Hook-Form trägt diese Woche (Cold-Open, \
Title-First, BTS, …)",
    "empfohlene_laengen": "z.B. '15-22s primär, 28s als langer Cut'",
    "must_show": ["Element, das im Cut sein muss, mit Begründung aus den Daten"],
    "no_go": ["Element, das NICHT performt — Begründung aus den Daten"]
  },
  "fuer_motion_designer": {
    "caption_style": "Caption-Beobachtung aus den Top-Posts (Länge, Tonfall, \
Hashtag-Dichte)",
    "text_overlay": "Empfehlung zu L3/Text-Einsatz",
    "branding_einsatz": "wie End Card / Logo platziert werden sollte"
  },
  "fuer_creative_producer": {
    "strategische_pattern": "übergeordnetes Muster, das diese Woche sichtbar \
wird",
    "cross_market_chancen": "wo DE-Cuts US-Patterns adaptieren sollten oder \
umgekehrt",
    "format_empfehlungen": "Formate / Längen / Cadence-Empfehlung für die \
nächste Woche"
  },
  "vergleichbare_posts": [
    {
      "post_id": "URL oder Slug aus historical_top_posts oder top_posts",
      "handle": "z.B. warnerbros",
      "performance_kpi": "z.B. '12k Likes, 28s'",
      "relevanz_grund": "warum dieser Post als Referenz für den nächsten \
Cut dient"
    }
  ]
}

Wenn die Datengrundlage zu dünn ist (Coverage <30%, <5 Posts pro Markt, keine \
Cross-Market-Matches), sage das klar in data_caveats und gib lieber weniger, \
dafür belegte Empfehlungen. Setze Felder, für die du keinen Daten-Anker hast, \
auf null oder gib ein leeres Array — niemals erfinden.

FEW-SHOT — so sieht ein guter Output aus (synthetisches Beispiel, kürzer als \
ein echter Report; in deinem Output bitte vollständig in der Länge):

{
  "headline": "MK2-Hook unter 22s trägt die Woche — DE-Cut zu lang",
  "tldr": "Die kurze 22s-Variante des MK2-Trailers leadt US mit 11k Engagement, \
während der DE-Cut bei 28s nur 3,1k erreicht. Trailerhaus sollte den DE-Cut \
auf 22s straffen und die Cold-Open-Variante testen. Die Wette: ein straffer \
Hook trägt auch in GSA, ohne Mood-Verlust.",
  "trends": [
    {
      "name": "Hook unter 15s gewinnt im Discovery",
      "evidence": "us_p3 (12s, 1k Likes) hat trotz BTS-Format eine \
Engagement-Rate über dem 30-60s-Bucket",
      "implication_for_creation": "Cutter sollte eine 12-15s Cold-Open-Variante \
des Trailers schneiden und gegen die 22s-Version A/B-testen."
    }
  ],
  "actions": [
    {
      "what": "DE-Cut auf 22s straffen",
      "why": "DE 28s leadt mit 3,1k Engagement, US 22s leadt mit 11,1k — \
Pace-Differenz spürbar",
      "for_whom": "Cutter MK2"
    }
  ],
  "cross_market_insight": {
    "de_vs_us": "DE läuft verhaltener (3,1k vs 11,1k), gleiche Hashtag-Logik, \
aber 6s länger im Cut.",
    "transfer_opportunity": "US 22s-Pace auf DE übertragen, deutsche \
Caption-Cadence beibehalten."
  },
  "risks": ["Coverage moderat (60%)"],
  "data_caveats": ["Nur 2 DE-Posts im Fenster — Trend ist Indiz, nicht Beweis"],
  "tonalitaet": [
    {
      "adjektiv": "präzise",
      "begruendung": "Top-US-Posts arbeiten mit klaren 22s-Hooks, kein \
narrativer Leerlauf"
    },
    {
      "adjektiv": "action-reich",
      "begruendung": "MortalKombat2-Hashtag dominiert, Caption-Sprache \
ist Action-fokussiert"
    }
  ],
  "watch_outs": [
    {
      "watch_out": "BTS-Cut (us_p3) hat hohe Discovery-Rate trotz niedriger \
Absolutzahlen",
      "konsequenz": "BTS-Format sollte als Komplement getestet werden, nicht \
als Hauptcut"
    }
  ],
  "fuer_cutter": {
    "schnitt_pace": "Top-Performer liegen im 15-30s-Bucket; >60s schwächt \
Engagement signifikant",
    "hook_strategie": "Cold-Open mit Action-Beat in den ersten 2 Sekunden",
    "empfohlene_laengen": "22s primär, 12s als Discovery-Variante",
    "must_show": ["Hauptkonflikt (Fight) im ersten Beat", "Logo-Reveal als End \
Card max. 1s"],
    "no_go": ["28s+ Cuts ohne klaren Pace-Bruch", "Caption-Overload >120 Zeichen"]
  },
  "fuer_motion_designer": {
    "caption_style": "kurz (60-100 Zeichen), 2-3 Hashtags, Action-Verben",
    "text_overlay": "L3 mit Datum minimal, kein Title-Card am Anfang",
    "branding_einsatz": "End Card 1s, Logo zentriert, kein Lower-Third-Branding"
  },
  "fuer_creative_producer": {
    "strategische_pattern": "Pace-Disziplin schlägt Featurefülle — kürzere \
Cuts mit klarem Hook",
    "cross_market_chancen": "DE adaptiert US-Pace, behält deutsche Caption-Form",
    "format_empfehlungen": "Pro Woche 2 Cuts: 22s Hauptcut + 12s Discovery"
  },
  "vergleichbare_posts": [
    {
      "post_id": "https://tiktok.com/@warnerbros/video/us1",
      "handle": "warnerbros",
      "performance_kpi": "11,1k Engagement, 22s",
      "relevanz_grund": "Goldstandard für die 22s-Hook, Referenz für den \
DE-Recut"
    }
  ]
}\
"""


# ---------- Hashtag extraction ---------------------------------------------

# Unicode-aware so German Umlauts and emoji-adjacent tags work. We strip the
# leading hash and lowercase to make the frequency count case-insensitive.
_HASHTAG_RE = re.compile(r"#([\w\d_]+)", re.UNICODE)


def _extract_hashtags(caption: Optional[str], raw_payload: Optional[dict]) -> list[str]:
    """Hashtags first from ``raw_payload['hashtags']`` (TikTok actor field),
    fall back to a regex over the caption. Returns lowercase tags without
    the leading ``#``.

    The Apify TikTok actor stores hashtags as ``[{"name": "trailer", ...}]``
    or sometimes a flat string list, depending on the actor version. The
    caption-regex fallback handles both older posts and posts where the
    actor missed the hashtag-block extraction.
    """
    tags: list[str] = []
    if isinstance(raw_payload, dict):
        rh = raw_payload.get("hashtags")
        if isinstance(rh, list):
            for h in rh:
                if isinstance(h, dict):
                    name = h.get("name") or h.get("title")
                    if name:
                        tags.append(str(name).lstrip("#").lower())
                elif isinstance(h, str):
                    tags.append(h.lstrip("#").lower())
    if not tags and caption:
        tags = [m.lower() for m in _HASHTAG_RE.findall(caption)]
    return tags


# ---------- Engagement + bucketing -----------------------------------------


def _engagement_sum(post: Post) -> int:
    return (
        int(post.visible_likes or 0)
        + int(post.visible_comments or 0)
        + int(post.visible_shares or 0)
        + int(post.visible_bookmarks or 0)
    )


def _duration_bucket(d: Optional[int]) -> str:
    if d is None:
        return "unknown"
    if d < 15:
        return "<15s"
    if d < 30:
        return "15-30s"
    if d < 60:
        return "30-60s"
    return ">60s"


def _excerpt(text: Optional[str], max_len: int = 240) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= max_len else text[:max_len].rstrip() + "…"


# ---------- Channel resolution ---------------------------------------------


def _find_channel(session: Session, handle: str, platform: str) -> Optional[Channel]:
    """Case-insensitive handle lookup scoped to the requested platform.

    The scraper sometimes stores handles with different casing depending on
    the source (Apify echoes the URL slug verbatim). Lowercase comparison
    keeps the lookup robust without a migration to normalise existing rows.
    """
    stmt = select(Channel).where(
        sa.func.lower(Channel.handle) == handle.lower(),
        Channel.platform == platform,
    )
    return session.exec(stmt).first()


# ---------- Aggregation -----------------------------------------------------


def _build_top_post(session: Session, p: Post, eng: int, assets_by_post: dict[Any, list[Asset]]) -> TopPost:
    """Resolve the title/asset_type for a single post and build a TopPost.
    Extracted so historical-posts and current-window-posts share the same
    rendering — keeps the LLM-input shape consistent across the two slices."""
    primary_asset = assets_by_post.get(p.id, [None])[0] if assets_by_post.get(p.id) else None
    title_text: Optional[str] = None
    asset_type: Optional[str] = None
    if primary_asset is not None:
        asset_type = (
            primary_asset.asset_type.value
            if hasattr(primary_asset.asset_type, "value")
            else str(primary_asset.asset_type)
        )
        if primary_asset.title_id:
            t = session.get(Title, primary_asset.title_id)
            if t:
                title_text = t.title_original
        if not title_text and primary_asset.placement_title_text:
            title_text = primary_asset.placement_title_text
    return TopPost(
        post_url=p.post_url,
        caption_excerpt=_excerpt(p.caption),
        duration_seconds=p.duration_seconds,
        engagement_sum=eng,
        likes=p.visible_likes,
        comments=p.visible_comments,
        shares=p.visible_shares,
        saves=p.visible_bookmarks,
        views=p.visible_views,
        asset_type=asset_type,
        title=title_text,
        published_at=p.published_at,
    )


def _historical_top_posts(
    session: Session,
    channel: Optional[Channel],
    window_start: datetime,
    *,
    n: int = 3,
    lookback_days: int = 180,
) -> list[TopPost]:
    """Top-``n`` posts from the channel's history (BEFORE ``window_start``).

    The LLM uses these as the ``vergleichbare_posts`` ground truth — a
    cutter wants to see "this kind of cut worked last month" rather than
    only this week's data. We cap the lookback at 6 months because anything
    older was usually a different campaign era and would muddy the signal.

    Filter by engagement_sum descending; no engagement-range constraint
    (Wolf brief mentions "ähnliche Range", but that adds an extra knob with
    little payoff at MVP scale — easier to let the LLM eyeball the numbers).
    """
    if channel is None:
        return []
    lookback_start = window_start - timedelta(days=lookback_days)
    posts_stmt = (
        select(Post)
        .where(Post.channel_id == channel.id)
        .where(
            sa.or_(
                sa.and_(
                    Post.published_at.is_not(None),
                    Post.published_at >= lookback_start,
                    Post.published_at < window_start,
                ),
                sa.and_(
                    Post.published_at.is_(None),
                    Post.detected_at >= lookback_start,
                    Post.detected_at < window_start,
                ),
            )
        )
    )
    posts: list[Post] = list(session.exec(posts_stmt).all())
    if not posts:
        return []
    engagements = sorted(
        ((p, _engagement_sum(p)) for p in posts),
        key=lambda item: item[1],
        reverse=True,
    )[:n]
    post_ids = [p.id for p, _ in engagements]
    assets: list[Asset] = list(
        session.exec(select(Asset).where(Asset.post_id.in_(post_ids))).all()
    )
    assets_by_post: dict[Any, list[Asset]] = defaultdict(list)
    for a in assets:
        assets_by_post[a.post_id].append(a)
    return [_build_top_post(session, p, eng, assets_by_post) for p, eng in engagements]


def _channel_stats(
    session: Session,
    channel: Optional[Channel],
    handle: str,
    market: str,
    window_start: datetime,
    window_end: datetime,
    *,
    top_posts_n: int = 3,
    top_hashtags_n: int = 5,
) -> ChannelStats:
    """Build the per-channel slice that goes into both the LLM prompt and
    the response payload.

    ``channel=None`` means the handle wasn't found in the DB — we still
    return a populated stats object (zeroed) so the Frontend can render
    a clear "channel not yet onboarded" caveat instead of crashing.
    """
    if channel is None:
        return ChannelStats(
            handle=handle,
            market=market,
            channel_id=None,
            channel_found=False,
            posts_count=0,
            assets_count=0,
            coverage_pct=0.0,
            top_hashtags=[],
            avg_caption_length=0.0,
            avg_duration_seconds=None,
            duration_buckets={},
            top_posts=[],
            avg_engagement=0.0,
        )

    posts_stmt = (
        select(Post)
        .where(Post.channel_id == channel.id)
        .where(
            sa.or_(
                # Prefer published_at (creator-supplied timestamp) but fall back to
                # detected_at when published_at is NULL — older Apify rows often
                # don't carry a ts.
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    posts: list[Post] = list(session.exec(posts_stmt).all())

    if not posts:
        return ChannelStats(
            handle=handle,
            market=market,
            channel_id=str(channel.id),
            channel_found=True,
            posts_count=0,
            assets_count=0,
            coverage_pct=0.0,
            top_hashtags=[],
            avg_caption_length=0.0,
            avg_duration_seconds=None,
            duration_buckets={},
            top_posts=[],
            avg_engagement=0.0,
        )

    # Asset stats — pulled once, joined in Python (cheap at MVP scale; we're
    # talking dozens of posts per channel per 30d window, not thousands).
    post_ids = [p.id for p in posts]
    assets: list[Asset] = list(
        session.exec(select(Asset).where(Asset.post_id.in_(post_ids))).all()
    )
    assets_by_post: dict[Any, list[Asset]] = defaultdict(list)
    for a in assets:
        assets_by_post[a.post_id].append(a)

    # Hashtag frequency
    tag_counter: Counter[str] = Counter()
    caption_lens: list[int] = []
    for p in posts:
        caption_lens.append(len(p.caption or ""))
        for tag in _extract_hashtags(p.caption, p.raw_payload):
            tag_counter[tag] += 1

    # Duration distribution
    bucket_counter: Counter[str] = Counter()
    durations: list[int] = []
    for p in posts:
        bucket_counter[_duration_bucket(p.duration_seconds)] += 1
        if p.duration_seconds is not None:
            durations.append(int(p.duration_seconds))

    # Engagement + top-N posts
    engagements: list[tuple[Post, int]] = [(p, _engagement_sum(p)) for p in posts]
    engagements.sort(key=lambda item: item[1], reverse=True)
    top_posts: list[TopPost] = [
        _build_top_post(session, p, eng, assets_by_post)
        for p, eng in engagements[:top_posts_n]
    ]
    historical_top_posts = _historical_top_posts(session, channel, window_start)

    # Title-coverage on the asset level (an asset is "covered" if title_id is set).
    assets_with_title = sum(1 for a in assets if a.title_id is not None)
    coverage_pct = (assets_with_title / len(assets) * 100.0) if assets else 0.0

    avg_engagement = sum(eng for _, eng in engagements) / len(engagements) if engagements else 0.0
    avg_caption = sum(caption_lens) / len(caption_lens) if caption_lens else 0.0
    avg_duration = sum(durations) / len(durations) if durations else None

    top_hashtags = [
        HashtagFrequency(tag=tag, count=count)
        for tag, count in tag_counter.most_common(top_hashtags_n)
    ]

    return ChannelStats(
        handle=handle,
        market=market,
        channel_id=str(channel.id),
        channel_found=True,
        posts_count=len(posts),
        assets_count=len(assets),
        coverage_pct=round(coverage_pct, 1),
        top_hashtags=top_hashtags,
        avg_caption_length=round(avg_caption, 1),
        avg_duration_seconds=round(avg_duration, 1) if avg_duration is not None else None,
        duration_buckets=dict(bucket_counter),
        top_posts=top_posts,
        avg_engagement=round(avg_engagement, 1),
        historical_top_posts=historical_top_posts,
    )


def _cross_market_matches(
    session: Session,
    de_channel: Optional[Channel],
    us_channel: Optional[Channel],
    window_start: datetime,
    window_end: datetime,
) -> list[CrossMarketMatch]:
    """Group assets by ``de_us_match_key`` across the two channels.

    The match-key is set by ``services/match_key.py`` during ingest; an
    empty result here is itself a useful signal for the LLM ("no
    cross-market matches in this window").
    """
    if de_channel is None or us_channel is None:
        return []

    de_post_ids_stmt = (
        select(Post.id)
        .where(Post.channel_id == de_channel.id)
        .where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    us_post_ids_stmt = (
        select(Post.id)
        .where(Post.channel_id == us_channel.id)
        .where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    de_post_ids = list(session.exec(de_post_ids_stmt).all())
    us_post_ids = list(session.exec(us_post_ids_stmt).all())

    # Filter out NULL and the literal "unknown" sentinel — the match-key
    # builder writes "unknown" when neither title nor placement-text yields
    # a useful key, and joining on that bucket would produce spurious
    # cross-market "matches" between unrelated posts.
    _MATCH_KEY_EXCLUDED = {"unknown", ""}

    de_assets = list(
        session.exec(
            select(Asset)
            .where(Asset.post_id.in_(de_post_ids))
            .where(Asset.de_us_match_key.is_not(None))
            .where(sa.func.lower(Asset.de_us_match_key) != "unknown")
        ).all()
    ) if de_post_ids else []
    us_assets = list(
        session.exec(
            select(Asset)
            .where(Asset.post_id.in_(us_post_ids))
            .where(Asset.de_us_match_key.is_not(None))
            .where(sa.func.lower(Asset.de_us_match_key) != "unknown")
        ).all()
    ) if us_post_ids else []

    de_by_key: dict[str, Asset] = {
        a.de_us_match_key: a
        for a in de_assets
        if a.de_us_match_key and a.de_us_match_key.strip().lower() not in _MATCH_KEY_EXCLUDED
    }
    us_by_key: dict[str, Asset] = {
        a.de_us_match_key: a
        for a in us_assets
        if a.de_us_match_key and a.de_us_match_key.strip().lower() not in _MATCH_KEY_EXCLUDED
    }
    shared_keys = sorted(set(de_by_key.keys()) & set(us_by_key.keys()))

    matches: list[CrossMarketMatch] = []
    for key in shared_keys:
        de_asset = de_by_key[key]
        us_asset = us_by_key[key]
        de_post = session.get(Post, de_asset.post_id)
        us_post = session.get(Post, us_asset.post_id)
        title_text: Optional[str] = None
        # Prefer the title row, fall back to placement_title_text on either side
        for a in (de_asset, us_asset):
            if a.title_id:
                t = session.get(Title, a.title_id)
                if t:
                    title_text = t.title_original
                    break
        if not title_text:
            title_text = de_asset.placement_title_text or us_asset.placement_title_text

        matches.append(
            CrossMarketMatch(
                match_key=key,
                title=title_text,
                de_engagement=_engagement_sum(de_post) if de_post else 0,
                us_engagement=_engagement_sum(us_post) if us_post else 0,
                de_duration_seconds=de_post.duration_seconds if de_post else None,
                us_duration_seconds=us_post.duration_seconds if us_post else None,
                de_post_url=de_post.post_url if de_post else None,
                us_post_url=us_post.post_url if us_post else None,
                de_caption_excerpt=_excerpt(de_post.caption) if de_post else None,
                us_caption_excerpt=_excerpt(us_post.caption) if us_post else None,
            )
        )

    # Strongest cross-market signal first
    matches.sort(key=lambda m: m.de_engagement + m.us_engagement, reverse=True)
    return matches


def _title_coverage(
    de_stats: ChannelStats, us_stats: ChannelStats, session: Session,
    de_channel: Optional[Channel], us_channel: Optional[Channel],
    window_start: datetime, window_end: datetime,
) -> TitleCoverage:
    """Compute aggregate coverage + title-overlap across both channels."""
    de_titles: set[str] = set()
    us_titles: set[str] = set()
    de_with_title = 0
    de_total = 0
    us_with_title = 0
    us_total = 0

    for channel, market_titles_set, with_title_holder, total_holder in (
        (de_channel, de_titles, "de_with_title", "de_total"),
        (us_channel, us_titles, "us_with_title", "us_total"),
    ):
        if channel is None:
            continue
        post_ids = list(
            session.exec(
                select(Post.id)
                .where(Post.channel_id == channel.id)
                .where(
                    sa.or_(
                        sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                        sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
                    )
                )
            ).all()
        )
        if not post_ids:
            continue
        assets = list(session.exec(select(Asset).where(Asset.post_id.in_(post_ids))).all())
        for a in assets:
            if channel is de_channel:
                de_total += 1
            else:
                us_total += 1
            if a.title_id is not None:
                if channel is de_channel:
                    de_with_title += 1
                else:
                    us_with_title += 1
                t = session.get(Title, a.title_id)
                if t:
                    market_titles_set.add(t.title_original)

    both = sorted(de_titles & us_titles)
    de_only = sorted(de_titles - us_titles)
    us_only = sorted(us_titles - de_titles)
    total_assets = de_total + us_total
    overall = ((de_with_title + us_with_title) / total_assets * 100.0) if total_assets else 0.0

    return TitleCoverage(
        titles_in_both_markets=both,
        de_only_titles=de_only,
        us_only_titles=us_only,
        de_assets_with_title=de_with_title,
        de_assets_total=de_total,
        us_assets_with_title=us_with_title,
        us_assets_total=us_total,
        overall_coverage_pct=round(overall, 1),
    )


def aggregate_pair(
    session: Session, pair_key: str, window_days: int = 30, *, now: Optional[datetime] = None
) -> PairAggregation:
    """Build the deterministic pair aggregation for a window ending at ``now``.

    Raises ``ValueError`` for unknown pairs — the caller (the API endpoint)
    maps that to a 404.
    """
    if pair_key not in PAIRS:
        raise ValueError(f"Unknown pair: {pair_key!r}")

    pair_def = PAIRS[pair_key]
    now = now or datetime.now(timezone.utc)
    window_end = now
    window_start = now - timedelta(days=window_days)
    iso_year, iso_week, _ = now.isocalendar()

    notes: list[str] = []
    de_spec = next((c for c in pair_def["channels"] if c["market"] == "DE"), None)
    us_spec = next((c for c in pair_def["channels"] if c["market"] == "US"), None)
    de_channel = _find_channel(session, de_spec["handle"], pair_def["platform"]) if de_spec else None
    us_channel = _find_channel(session, us_spec["handle"], pair_def["platform"]) if us_spec else None

    if de_spec and de_channel is None:
        notes.append(
            f"DE-Channel @{de_spec['handle']} (TikTok) wurde nicht in der DB gefunden — "
            "Onboarding/Whitelist-Eintrag prüfen."
        )
    if us_spec and us_channel is None:
        notes.append(
            f"US-Channel @{us_spec['handle']} (TikTok) wurde nicht in der DB gefunden — "
            "Onboarding/Whitelist-Eintrag prüfen."
        )

    de_stats = _channel_stats(session, de_channel, de_spec["handle"] if de_spec else "", "DE", window_start, window_end) if de_spec else None
    us_stats = _channel_stats(session, us_channel, us_spec["handle"] if us_spec else "", "US", window_start, window_end) if us_spec else None
    matches = _cross_market_matches(session, de_channel, us_channel, window_start, window_end)
    coverage = _title_coverage(de_stats, us_stats, session, de_channel, us_channel, window_start, window_end)

    if de_stats and de_stats.posts_count < 5:
        notes.append(f"Datenbasis DE schwach: nur {de_stats.posts_count} Posts in den letzten {window_days} Tagen.")
    if us_stats and us_stats.posts_count < 5:
        notes.append(f"Datenbasis US schwach: nur {us_stats.posts_count} Posts in den letzten {window_days} Tagen.")
    if not matches:
        notes.append("Keine de_us_match_key-Treffer im Fenster — Cross-Market-Insight basiert auf indirekten Signalen.")

    return PairAggregation(
        pair_key=pair_key,
        pair_label=pair_def["label"],
        platform=pair_def["platform"],
        window_days=window_days,
        window_start=window_start,
        window_end=window_end,
        iso_week=iso_week,
        iso_year=iso_year,
        de_channel=de_stats,
        us_channel=us_stats,
        cross_market_matches=matches,
        title_coverage=coverage,
        notes=notes,
    )


# ---------- LLM call --------------------------------------------------------


def _build_user_prompt(agg: PairAggregation) -> str:
    """Compact data dump for the LLM. Keep it tabular so the model can
    reference exact numbers in evidence-fields without hallucinating
    counts. JSON is fine for the tabular parts, prose for the framing."""
    payload = agg.model_dump(mode="json")
    framing = (
        f"Generiere den ausführlichen Wochenreport für {agg.pair_label} "
        f"(Plattform: {agg.platform.upper()}), KW {agg.iso_week}/{agg.iso_year}, "
        f"Datenfenster {agg.window_days} Tage "
        f"({agg.window_start.date().isoformat()} bis {agg.window_end.date().isoformat()}).\n\n"
        "Modus: 'ganz genau' — gib alle Sektionen vollständig aus, ca. "
        "1500-2000 Wörter Gesamtoutput. Halte dich an Voice, Glossar und \n"
        "Anti-Pattern aus dem System-Prompt. Nutze das Feld "
        "``de_channel.historical_top_posts`` und ``us_channel.historical_top_posts`` \n"
        "als Quelle für die ``vergleichbare_posts``-Sektion.\n\n"
        "Datenpaket (JSON):\n"
    )
    return framing + json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _strip_codefence(text: str) -> str:
    """Tolerate a ```json ... ``` wrap if the model adds one despite the
    "no Markdown" instruction. Strict mode is fine but defensive parsing
    avoids one easy way for a single retry to be wasted."""
    t = text.strip()
    if t.startswith("```"):
        # Remove first fence line
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()


def _estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1000.0) * _OPUS_INPUT_PER_1K_USD
        + (output_tokens / 1000.0) * _OPUS_OUTPUT_PER_1K_USD,
        4,
    )


def generate_weekly_report(
    session: Session,
    pair_key: str,
    *,
    window_days: int = 30,
    dry_run: bool = False,
    model: str = OPUS_MODEL_ALIAS,
    # Sprint-Trailerhaus-Prompt-v1: bumped from 8k → 12k because the new
    # ``ganz genau`` mode targets ~1500-2000 words across nine sections.
    # Old reports with the smaller schema were occasionally truncated near
    # the data_caveats tail at 8k.
    max_tokens: int = 12000,
    now: Optional[datetime] = None,
) -> InsightReport:
    """Build the aggregation, call Opus 4.7 once, return the merged report.

    ``dry_run=True`` returns the aggregation only — no LLM call, no cost.
    Use this for prompt-iteration and the inline-quality-gate before the
    Frontend goes live.
    """
    agg = aggregate_pair(session, pair_key, window_days=window_days, now=now)
    generated_at = datetime.now(timezone.utc)

    if dry_run:
        return InsightReport(
            pair_key=agg.pair_key,
            pair_label=agg.pair_label,
            iso_week=agg.iso_week,
            iso_year=agg.iso_year,
            window_days=window_days,
            coverage_pct=agg.title_coverage.overall_coverage_pct,
            generated_at=generated_at,
            model=model,
            dry_run=True,
            llm_output=None,
            aggregation=agg,
            cost_usd_estimate=0.0,
        )

    if not is_anthropic_configured():
        # Fail loud, not silent: an unconfigured deployment that returns
        # a stub report would cause the caveats banner to lie about
        # what's in front of the producer's eyes.
        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt — Insight-Engine kann nicht generieren. "
            "Setze den Schlüssel in Railway oder ruf den Endpoint mit ?dry_run=true auf."
        )

    user_prompt = _build_user_prompt(agg)
    logger.info(
        "insight-engine-call",
        extra={
            "pair": pair_key,
            "window_days": window_days,
            "model": model,
            "prompt_chars": len(user_prompt),
        },
    )
    message = messages_create_text(
        model=model,
        system=SYSTEM_PROMPT,
        user_message=user_prompt,
        max_tokens=max_tokens,
    )

    raw_text = ""
    try:
        # Anthropic SDK Message objects expose ``content`` as a list of
        # content blocks; the text block has ``.text``. For a plain
        # text-only response there is exactly one block.
        for block in message.content or []:
            if getattr(block, "type", None) == "text":
                raw_text += getattr(block, "text", "")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("insight-engine-content-extract-failed: %s", exc)

    cleaned = _strip_codefence(raw_text)
    llm_output: Optional[LLMReport] = None
    raw_for_response: Optional[str] = None
    try:
        parsed = json.loads(cleaned)
        llm_output = LLMReport.model_validate(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("insight-engine-json-parse-failed: %s", exc)
        raw_for_response = raw_text  # surface to caller, don't swallow

    usage = getattr(message, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cost = _estimate_cost_usd(input_tokens, output_tokens) if (input_tokens or output_tokens) else None

    return InsightReport(
        pair_key=agg.pair_key,
        pair_label=agg.pair_label,
        iso_week=agg.iso_week,
        iso_year=agg.iso_year,
        window_days=window_days,
        coverage_pct=agg.title_coverage.overall_coverage_pct,
        generated_at=generated_at,
        model=model,
        dry_run=False,
        llm_output=llm_output,
        aggregation=agg,
        cost_usd_estimate=cost,
        raw_llm_text=raw_for_response,
    )


__all__ = [
    "PAIRS",
    "OPUS_MODEL_ALIAS",
    "SYSTEM_PROMPT",
    "aggregate_pair",
    "generate_weekly_report",
]
