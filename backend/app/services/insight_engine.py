"""Insight-Engine MVP — Sprint 1 (warnerbros DE+US TikTok).

Single-shot Opus 4.7 weekly briefing for the Trailerhaus creative team.
Deterministic aggregation lives in this module; the LLM call is one
``messages_create_strict_json`` (Tool-Use mit forciertem ``tool_choice``,
seit 28.05.2026 — vorher ``messages_create_text``) per report, no agent
loop.

The pair definition is hardcoded by design (see ``PAIRS`` below) — generalising
to the other six Tier-A pairs is Sprint-2 work and explicitly out-of-scope for
this MVP. Adding a new pair before then is a config-only change in this file.

Cost expectation: with the Sprint-Trailerhaus-Prompt-v1 expanded prompt +
``ganz genau`` mode the per-call shape is roughly 8-12k input + 3-4k output
tokens. At Opus 4.8 list price ($5 / $25 per Mtok, corrected 2026-07-01 —
the $15/$75 figure here previously was stale Opus-4/4.1-era pricing) that's
~$0.12-0.16 per report. The endpoint still accepts ``dry_run=true`` to skip
the LLM call entirely when iterating on the aggregation or prompt.
"""
from __future__ import annotations

import functools
import json
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Iterable, NamedTuple, Optional

import sqlalchemy as sa
from sqlmodel import Session, select

from app.models.entities import Asset, Channel, InsightReport as InsightReportRow, Post, Title
from app.schemas.insights import (
    Action,
    BreakoutScore,
    ChannelStats,
    CrossMarketInsight,
    CrossMarketMatch,
    HashtagFrequency,
    InsightReport,
    LLMReport,
    PairAggregation,
    PlatformAggregation,
    RankedPost,
    RecommendedAction,
    TitleCoverage,
    TopPost,
    Trend,
)

# Module-internal alias for the SQLModel persistence row (re-export the
# Pydantic ``InsightReport`` from app.schemas.insights). The two share a
# name; we disambiguate with the import alias above.
from app.config import settings
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    _unwrap_single_key,
    is_anthropic_configured,
    messages_create_strict_json,
)
from app.services.cost_log import record_anthropic_call

logger = logging.getLogger(__name__)


# ---------- Pair registry ---------------------------------------------------

# Sprint-2: registry expanded to the seven Tier-A DE+US TikTok pairs from the
# whitelist-expansion migration ``e5d8f1a36b40``. Sprint 2026-05-12: alle
# Pairs sind enabled — ``universalpictures`` reaktiviert nach DE/US/UK-
# Channel-Aktivitäts-Check (DE-Seite 30 Posts/30d, US-Pool aktiv via
# YT-Master + IG-Sub-Brand @universalhorror, UK seit Phase A registered).
# Endpoint logic in ``api/insights.py`` returns 503 with the structured
# ``reason`` für künftige disabled Pairs ohne Config-Push.
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
        "display_name": "Warner Bros",
        "markets": ["DE", "US", "UK"],
        "label": "warnerbros DE+US+UK",
        # Sprint-4 multi-platform v2a: ``platforms`` is the source of truth
        # going forward. Each key is a platform with a list of {handle, market}
        # specs. ``platform`` and ``channels`` mirror the first platform
        # (always TikTok in this sprint) so backwards-compat code paths
        # — including the LLM ``_build_user_prompt`` and any test fixtures
        # that still read ``pair_def["channels"]`` directly — keep working
        # without an audit. Sprint-5 voice refactor will start consuming
        # ``platforms`` directly and let the legacy mirror fields wither.
        "platforms": {
            "tiktok": [
                # Sprint 10h: US-Seite ist Multi-Channel-Pool. Warner verteilt
                # Theatrical-Marketing zwischen @warnerbros (Hauptstudio) und
                # @dc (DC Studios Sub-Brand). _aggregate_platform pooled beide.
                {"handle": "warnerbros", "market": "US"},
                {"handle": "dc", "market": "US"},
                # DE handle per Wolf brief; aliasing handled by the case-insensitive
                # lookup. If the actual stored handle differs, ``aggregate_pair``
                # records that in ``notes`` rather than failing.
                {"handle": "warnerbrosdeutschland", "market": "DE"},
                # Sprint UK-B1: UK-Schwestermarkt ergänzt (Phase A).
                {"handle": "warnerbrosuk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "warnerbros", "market": "US"},
                # IG-Handle für DC ist @dcofficial (TT-Handle @dc ist nur dort).
                {"handle": "dcofficial", "market": "US"},
                {"handle": "warnerbrosde", "market": "DE"},
                {"handle": "warnerbrosuk", "market": "UK"},
            ],
            "youtube": [
                {"handle": "WarnerBrosPictures", "market": "US"},
                {"handle": "dcofficial", "market": "US"},
                {"handle": "WarnerBrosDE", "market": "DE"},
                {"handle": "WarnerBrosUK", "market": "UK"},
            ],
        },
        # Backwards-Compat mirror — TikTok = first platform.
        "platform": "tiktok",
        "channels": [
            {"handle": "warnerbros", "market": "US"},
            {"handle": "dc", "market": "US"},
            {"handle": "warnerbrosdeutschland", "market": "DE"},
            {"handle": "warnerbrosuk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "sonypictures": {
        "display_name": "Sony Pictures",
        "markets": ["DE", "US", "UK"],
        "label": "sonypictures DE+US+UK",
        "platforms": {
            "tiktok": [
                # Sprint 10h: US-Seite ist Multi-Channel-Pool. Sony Pictures
                # Animation (@sonypicturesanimation) postet aktiv im Theatrical-
                # Marketing parallel zu @sonypictures — beide Pools werden
                # in _aggregate_platform vereinigt.
                {"handle": "sonypictures", "market": "US"},
                {"handle": "sonypicturesanimation", "market": "US"},
                {"handle": "sonypicturesgermany", "market": "DE"},
                # Sprint UK-B1: Sony's UK-Handle ist @sonypictures.uk
                # (Punkt-Suffix, kein Underscore wie bei Paramount-IG).
                {"handle": "sonypictures.uk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "sonypictures", "market": "US"},
                # IG-Handle für Sony Pictures Animation ist @sonyanimation
                # (kürzer als der TT-Handle @sonypicturesanimation).
                {"handle": "sonyanimation", "market": "US"},
                {"handle": "sonypicturesde", "market": "DE"},
                # Sprint UK-Channel-Integration Phase 1 (PR #175): der historische
                # Pair-IG-Handle ``sonypictures.uk`` zeigt auf eine inaktive
                # Bestands-Row (b1770de9-…, "profile not available"). Der lebende
                # IG-Account ist @sonypicturesuk (Browser-verifiziert, 530K). Ohne
                # diesen Fix sucht ``_find_channels`` für Sony-IG-UK weiter auf der
                # toten Row und liefert keine UK-IG-Posts in den Pair-Brief.
                {"handle": "sonypicturesuk", "market": "UK"},
            ],
            "youtube": [
                {"handle": "SonyPicturesEntertainment", "market": "US"},
                {"handle": "sonyanimation", "market": "US"},
                {"handle": "SonyPicturesGermany", "market": "DE"},
                {"handle": "SonyPicsUK", "market": "UK"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "sonypictures", "market": "US"},
            {"handle": "sonypicturesanimation", "market": "US"},
            {"handle": "sonypicturesgermany", "market": "DE"},
            {"handle": "sonypictures.uk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "primevideo": {
        "display_name": "Prime Video",
        "markets": ["DE", "US", "UK"],
        "label": "primevideo DE+US+UK",
        "platforms": {
            "tiktok": [
                # Sprint 10c: US-Seite auf Cinema-Master @amazonmgmstudios
                # umgestellt (war @primevideo Streaming-Catalog). DB-Channel
                # für TT/IG/YT US wurde in Sprint 10c-pre per SQL angelegt.
                {"handle": "amazonmgmstudios", "market": "US"},
                {"handle": "primevideode", "market": "DE"},
                # Sprint UK-B1: UK-Schwester via @primevideouk (Phase A).
                {"handle": "primevideouk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "amazonmgmstudios", "market": "US"},
                {"handle": "primevideode", "market": "DE"},
                {"handle": "primevideouk", "market": "UK"},
            ],
            # No DE-side or UK-side YouTube channel for Prime — single-channel
            # platform entry. ``_aggregate_platform`` handles the missing-market
            # case by leaving ``de_channel`` / ``uk_channel`` None. Phase A
            # hat für Prime kein YT-UK angelegt; Aufnahme in B1 separat.
            "youtube": [
                {"handle": "AmazonMGMStudios", "market": "US"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "amazonmgmstudios", "market": "US"},
            {"handle": "primevideode", "market": "DE"},
            {"handle": "primevideouk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "disney": {
        "display_name": "Disney",
        "markets": ["DE", "US", "UK"],
        "label": "disney DE+US+UK",
        "platforms": {
            "tiktok": [
                # Sprint 10d: US-Seite ist Multi-Channel-Pool (Cinema-Sub-Brands).
                # Disney verteilt Theatrical-Marketing über @disneystudios,
                # @marvelstudios, @pixar, @starwars und @20thcentury — wenn ein
                # Channel Catalog postet, postet ein anderer ggf. Trailer.
                # _aggregate_platform bündelt alle US-Posts in einen Pool.
                {"handle": "disneystudios", "market": "US"},
                {"handle": "marvelstudios", "market": "US"},
                {"handle": "pixar", "market": "US"},
                {"handle": "starwars", "market": "US"},
                {"handle": "20thcentury", "market": "US"},
                {"handle": "disneyde", "market": "DE"},
                # Sprint UK-B1: TT-UK ist single-handle @disneyuk (Phase A
                # hat MarvelUK/StarWarsUK auf TT noch nicht angelegt — IG/YT
                # haben den 3-Pool, TT bleibt erstmal single).
                {"handle": "disneyuk", "market": "UK"},
            ],
            "instagram": [
                # IG-Casing für 20th Century weicht von TT ab: "20thcenturystudios".
                {"handle": "disneystudios", "market": "US"},
                {"handle": "marvelstudios", "market": "US"},
                {"handle": "pixar", "market": "US"},
                {"handle": "starwars", "market": "US"},
                {"handle": "20thcenturystudios", "market": "US"},
                # IG-DE handle differs from TikTok (``disneyde``) — Disney runs
                # ``disneydeutschland`` on Instagram.
                {"handle": "disneydeutschland", "market": "DE"},
                # Sprint UK-B1: UK-Pool analog US (Master + Sub-Brands).
                # Sprint 2026-05-12: @starwarsuk via
                # sprint_disney_uk_subbrand_gap_2026_05_12 nachgezogen
                # (108k Follower, IG-only — kein TT-Pendant, YT-Pool hat
                # StarWarsUK schon). Disney IG-UK = @disneyuk +
                # @disneystudiosuk + @marvel_uk + @starwarsuk.
                # (Underscore in marvel_uk ist der echte DB-Handle aus Phase A).
                {"handle": "disneyuk", "market": "UK"},
                {"handle": "disneystudiosuk", "market": "UK"},
                {"handle": "marvel_uk", "market": "UK"},
                {"handle": "starwarsuk", "market": "UK"},
            ],
            # Sprint 10j: US-Seite ist Multi-Channel-Pool analog TT/IG.
            # Marvel-Trailer landen auf @marvel, Pixar-Promos auf @pixar,
            # Lucasfilm-Content auf @StarWars, 20th-Century-Releases auf
            # @20thCenturyStudios — alle vier sub-brand YT-Channels gehören
            # zusammen mit @WaltDisneyStudios in den US-Pool.
            # DE-Seite bleibt single-market (kein DE-Cinema-Marketing-Account).
            # _find_channels nutzt lowercase-handle-match, daher case-mix
            # (StarWars/20thCenturyStudios) verträglich mit der DB-Form.
            "youtube": [
                {"handle": "WaltDisneyStudios", "market": "US"},
                {"handle": "marvel", "market": "US"},
                {"handle": "pixar", "market": "US"},
                {"handle": "StarWars", "market": "US"},
                {"handle": "20thCenturyStudios", "market": "US"},
                # Sprint UK-B1: YT-UK-Pool analog US (Master + 2 Sub-Brands).
                # Pixar-UK / 20thCentury-UK gibt es in Phase A nicht — Lücke
                # ist akzeptabel, Sub-Brands sind UK-seitig weniger aktiv.
                {"handle": "DisneyUK", "market": "UK"},
                {"handle": "MarvelUK", "market": "UK"},
                {"handle": "StarWarsUK", "market": "UK"},
            ],
        },
        "platform": "tiktok",
        # Sprint 10d: legacy ``channels`` mirror tracks platforms["tiktok"]
        # (the first platform). Multi-channel-aware so the
        # ``test_pairs_backwards_compat_mirror_first_platform`` invariant
        # and the ``_platforms_dict_for`` fallback both hold.
        "channels": [
            {"handle": "disneystudios", "market": "US"},
            {"handle": "marvelstudios", "market": "US"},
            {"handle": "pixar", "market": "US"},
            {"handle": "starwars", "market": "US"},
            {"handle": "20thcentury", "market": "US"},
            {"handle": "disneyde", "market": "DE"},
            {"handle": "disneyuk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "netflix": {
        "display_name": "Netflix",
        "markets": ["DE", "US", "UK"],
        "label": "netflix DE+US+UK",
        "platforms": {
            "tiktok": [
                {"handle": "netflix", "market": "US"},
                {"handle": "netflixde", "market": "DE"},
                # Sprint UK-B1: Netflix-UK via sprint_uk_inventory_gap_2026_05_11
                # angelegt (1.5M Followers TT, 5M IG, separater YT-Channel).
                {"handle": "netflixuk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "netflix", "market": "US"},
                {"handle": "netflixde", "market": "DE"},
                {"handle": "netflixuk", "market": "UK"},
            ],
            "youtube": [
                {"handle": "Netflix", "market": "US"},
                {"handle": "NetflixDE", "market": "DE"},
                {"handle": "NetflixUK", "market": "UK"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "netflix", "market": "US"},
            {"handle": "netflixde", "market": "DE"},
            {"handle": "netflixuk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "paramountpictures": {
        "display_name": "Paramount",
        "markets": ["DE", "US", "UK"],
        "label": "paramountpictures DE+US+UK",
        "platforms": {
            "tiktok": [
                # US handle is ``paramountpics``, not ``paramountpictures``
                # (per migration e5d8f1a36b40 + Wolf brief).
                {"handle": "paramountpics", "market": "US"},
                {"handle": "paramountpicturesgermany", "market": "DE"},
                # Sprint UK-B1: UK-Schwester via @paramountpicturesuk (Phase A).
                {"handle": "paramountpicturesuk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "paramountpics", "market": "US"},
                # IG-DE uses underscores: ``paramount_pictures_germany``.
                {"handle": "paramount_pictures_germany", "market": "DE"},
                # Sprint UK-Channel-Integration Phase 1 (PR #175): der historische
                # Pair-IG-Handle ``paramountpicturesuk`` zeigt auf eine inaktive
                # Bestands-Row (f0d76915-…, "profile not available"). Der lebende
                # IG-Account ist @paramountuk (Browser-verifiziert, 1.3M). Ohne
                # diesen Fix sucht ``_find_channels`` für Paramount-IG-UK weiter
                # auf der toten Row und liefert keine UK-IG-Posts in den Pair-Brief.
                {"handle": "paramountuk", "market": "UK"},
            ],
            # No DE-side YouTube channel for Paramount Pictures. UK exists
            # via @ParamountPicturesUK (case-mix verträglich mit
            # lowercase-handle-match in _find_channels).
            "youtube": [
                {"handle": "ParamountPictures", "market": "US"},
                {"handle": "ParamountPicturesUK", "market": "UK"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "paramountpics", "market": "US"},
            {"handle": "paramountpicturesgermany", "market": "DE"},
            {"handle": "paramountpicturesuk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "universalpictures": {
        "display_name": "Universal Pictures",
        "markets": ["DE", "US", "UK"],
        "label": "universalpictures DE+US+UK",
        # Sprint 2026-05-12: voll-Pair reaktiviert nach Diagnose (DE 30
        # Posts/30d, US-Pool aktiv, UK seit Phase A registered). US-Seite
        # ist Multi-Channel-Pool analog warnerbros/disney/sonypictures —
        # @universalpictures (Master) + @universalhorror (Sub-Brand für
        # Horror-Slate, IG-only, kein TT/YT-Pendant). YT-Master ist sehr
        # aktiv (10 Posts/30d), TT-Master tot (0 Posts) bleibt drin für
        # Cron-Erfassung, falls reaktiviert. UK postet noch nicht (Cron
        # Sa 17.05. wird Daten liefern), Channels sind aber registered.
        "platforms": {
            "tiktok": [
                {"handle": "universalpictures", "market": "US"},
                {"handle": "universalpicturesde", "market": "DE"},
                {"handle": "universalpicturesuk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "universalpictures", "market": "US"},
                # @universalhorror = Sub-Brand-Pool für Horror-Slate
                # (Blumhouse/Monkeypaw-Releases). IG-only, kein TT/YT.
                {"handle": "universalhorror", "market": "US"},
                {"handle": "universalpicturesde", "market": "DE"},
                {"handle": "universalpicturesuk", "market": "UK"},
            ],
            "youtube": [
                {"handle": "UniversalPictures", "market": "US"},
                {"handle": "UniversalPicturesDE", "market": "DE"},
                {"handle": "UniversalPicturesUK", "market": "UK"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "universalpictures", "market": "US"},
            {"handle": "universalpicturesde", "market": "DE"},
            {"handle": "universalpicturesuk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "paramountplus": {
        "display_name": "Paramount+",
        "markets": ["DE", "US", "UK"],
        "label": "paramountplus DE+US+UK",
        # Sprint 2026-05-12: voll-Pair über alle drei Märkte. Handle-
        # Casing der YT-Channels behalten wir aus der Wolf-SQL-Anlage,
        # _find_channels macht den Case-insensitiven Match. UK fehlt
        # auf YouTube — Paramount+ hat dort keinen separaten Channel.
        "platforms": {
            "tiktok": [
                {"handle": "paramountplus", "market": "US"},
                {"handle": "paramountplusde", "market": "DE"},
                {"handle": "paramountplusuk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "paramountplus", "market": "US"},
                {"handle": "paramountplusde", "market": "DE"},
                {"handle": "paramountplusuk", "market": "UK"},
            ],
            "youtube": [
                {"handle": "paramountplus", "market": "US"},
                {"handle": "ParamountPlusDE", "market": "DE"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "paramountplus", "market": "US"},
            {"handle": "paramountplusde", "market": "DE"},
            {"handle": "paramountplusuk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
    "lionsgate": {
        "display_name": "Lionsgate",
        "markets": ["US", "UK"],
        "label": "lionsgate US+UK",
        # Sprint 2026-05-12: US+UK-only Pair. Lionsgate hat keinen
        # deutschen Social-Media-Auftritt — der DE-Vertrieb läuft via
        # Leonine Studios und Studiocanal, also kein eigenes
        # Lionsgate-DE-Profil. Voice-Brief wird die "Datenbasis DE
        # schwach"-Note für DE auslassen (keine DE-Channel-Definition,
        # also kein leerer DE-Block). UK-YouTube fehlt ebenfalls —
        # Lionsgate UK postet nur auf IG/TT.
        "platforms": {
            "tiktok": [
                {"handle": "lionsgate", "market": "US"},
                {"handle": "lionsgateuk", "market": "UK"},
            ],
            "instagram": [
                {"handle": "lionsgate", "market": "US"},
                {"handle": "lionsgateuk", "market": "UK"},
            ],
            "youtube": [
                {"handle": "LionsgateMovies", "market": "US"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "lionsgate", "market": "US"},
            {"handle": "lionsgateuk", "market": "UK"},
        ],
        "enabled": True,
        "reason": None,
    },
}


# ---------- Briefing cadence ------------------------------------------------

# Global, pair-agnostic briefing cadence. Used by ``GET /api/pairs`` and any
# future Frontend surface that needs to label a pair's rhythm. If a future
# pair switches to bi-weekly or monthly, lift this to a per-pair field in
# the PAIRS dict and keep this constant as the default.
INSIGHT_FREQUENCY_LABEL = "wöchentlich"

# Stable visualisation order for market codes on the landing-page card and
# anywhere a "DE + US + UK"-style join is rendered. Independent of insertion
# order in any ``channels`` list — what the user sees is what this constant
# spells.
MARKETS_DISPLAY_ORDER: tuple[str, ...] = ("DE", "US", "UK")


# ---------- Model + cost ----------------------------------------------------

# Opus model alias used across every Opus-tier call (weekly briefs, segment
# roundups, cutter-weekly, title briefs, ER forecast — see the cross-module
# imports of this name). Sicherheits-Audit 2026-07-01: this used to be a
# hardcoded literal, completely disconnected from
# ``settings.anthropic_opus_model`` despite the ``.env.example``/config.py
# comments implying ``ANTHROPIC_OPUS_MODEL`` was the override knob — it
# never was. Now sourced from settings, so the ENV var actually reaches the
# most expensive LLM call path in the system.
OPUS_MODEL_ALIAS = settings.anthropic_opus_model


# ---------- Structured-Outputs-Tool (Sprint 28.05.2026) ------------------
#
# API-erzwungenes JSON via Tool-Use mit forciertem ``tool_choice`` —
# Wolf-Entscheid: Tool-Use ist die reifere/sicherere Form gegenueber
# ``output_config.format.json_schema``. Das Schema bauen wir lazy aus
# ``LLMReport.model_json_schema()``, damit jede Schema-Erweiterung
# (Sprint-Trailerhaus-Prompt-Felder, kuenftige Evidenz-Block-Felder)
# automatisch durchschlaegt — kein Hand-Pflege-Duplikat.
#
# ``_BRIEF_TOOL_INPUT_SCHEMA`` wird beim Modul-Import einmal aus Pydantic
# extrahiert und ist danach konstant. Pydantic v2 ``model_json_schema``
# erzeugt Draft-2020-12 mit ``$defs`` fuer geschachtelte Sub-Modelle;
# Anthropic Tool-Input-Schemas unterstuetzen das nativ.

_BRIEF_TOOL_NAME = "submit_weekly_brief"
_BRIEF_TOOL_DESCRIPTION = (
    "Submit the structured weekly pair brief. Call this tool exactly once. "
    "Pass the report fields DIRECTLY as the top-level tool arguments "
    "(headline, tldr, trends, actions, cross_market_insight, risks, "
    "data_caveats, plus the optional sections) — do NOT nest them under any "
    "wrapper key such as 'what', 'report' or 'payload'. Do not return any "
    "prose outside the tool call."
)
_BRIEF_TOOL_INPUT_SCHEMA: dict[str, Any] = LLMReport.model_json_schema()

# Opus 4.7 pricing reads from ``settings.anthropic_opus_*_per_1k_usd`` —
# previously hardcoded here AND implicit in ``record_anthropic_call``,
# which gave us a drift-window where the brief-frontend estimate and the
# costlog row could disagree. One source of truth (config.py) now feeds
# both the frontend ``cost_usd_estimate`` field and the persisted row.


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
Du bist Analyst bei Trailerhaus, einem Münchner Studio für Trailer und Spots. Du wertest aus, was die Konkurrenz diese Woche im Social-Media-Marketing gemacht hat, und berichtest die Beobachtungen sachlich für Geschäftsführung, Creative Direction und das Schnitt-Team. Du schreibst einen nüchternen Marktbericht — kein Pitch-Deck, keine Berater-Folie und kein Schnittraum-Geplauder.

BERICHTSTON — die sechs Regeln, sie stehen über allen Listen und Beispielen weiter unten:

1. SACHLICH BERICHTEN, NICHT WERTEN. Benenne Beobachtungen neutral. Keine Lenk- oder Wertungsformeln ("spannend ist", "auffällig stark", "beeindruckend", "knallt", "zerlegt", "sitzt"). Eine neutrale Überleitung wie "Auffällig ist die unterschiedliche Strategie: …" ist erlaubt; wertende Verstärker sind es nicht.

2. KEIN SZENE- ODER BRANCHENJARGON. Schreibe Begriffe aus oder erkläre sie, statt Fachkürzel zu verwenden. Nicht "Meet-Cute-Anriss", sondern "Clip zum ersten Kennenlernen der Hauptfiguren". Keine englischen Schnitt-Begriffe wie Hook, Beat, Cold-Open, Cast-Reaction, BTS, L3, End Card, Establisher-Shot — beschreibe stattdessen, was zu sehen ist ("der Anfang des Clips", "ein kurzer Ausschnitt mit den Hauptdarstellern", "eine eingeblendete Texttafel", "Material vom Set"). Ebenso keine englische Wort-mit-Bindestrich-Konstruktion (X-Moment, Cast-Y, Z-Reminder) und kein Substantiv-Ungetüm ("Aktivierungsverhalten", "Reichweitendynamik"): nenne die Sache beim einfachen Namen.

3. ZAHLEN AUSSCHREIBEN, NICHT ABKÜRZEN. "2,3 Millionen", "128.000", "445.000" — nicht "2,3 Mio" oder "128k". Deutsche Tausender-Punkte, Komma als Dezimaltrenner. Das gilt im gesamten Fließtext.

4. LÄNDER IM FLIESSTEXT. Ausgeschriebene Namen ("Großbritannien", "Deutschland", "die USA") und die Kürzel ("UK", "DE", "US") sind beide erlaubt und gleichwertig — wähle nach Lesefluss. In Tabellen und Labels bleiben die Kürzel.

5. GANZE, RUHIGE SÄTZE mit erklärenden Konjunktionen. Kein Fragment-Stakkato ("Erste 2 Sekunden: Fight-Beat. Kein Logo."). Schreibe ausformulierte Sätze, die die Beobachtung erklären, statt sie in Aufzählungs-Bruchstücke zu zerlegen.

6. KEINE DRAMATISIERUNG ODER ÜBERTREIBUNG. "konsequent weiter ausgespielt" → "weiterhin aktiv beworben"; "mitten in der Post-Release-Welle" → schlichte Beschreibung ("auch nach dem Release weiterhin beworben").

VORHER / NACHHER — so klingt der Zielstil:

VERMEIDEN: "UK hat *Off Campus* nach dem Release konsequent weiter ausgespielt — der Meet-Cute-Anriss steht bei 2,3 Mio Aufrufen, ein Cast-Clip ein paar Wochen später holt nochmal rund 128k Likes. … Spannend ist die Phasen-Differenz: UK ist mitten in der Post-Release-Welle, DE im Episoden-Push, US bleibt bei Einzel-Schnipseln ohne Serien-Anker."

ZIELSTIL: "Großbritannien bewirbt Off Campus auch nach dem Release weiterhin aktiv. Ein Clip zum ersten Kennenlernen der Hauptfiguren wurde bereits 2,3 Millionen Mal angesehen, und ein weiterer Clip mit den Hauptdarstellern erreichte einige Wochen später noch einmal rund 128.000 Likes. In Deutschland steht aktuell die neue Staffel von LOL im Mittelpunkt. Der Clip „Hamster-Date – Teil 2" kommt sowohl auf TikTok als auch auf Instagram sehr gut an und erzielt dort 445.000 beziehungsweise 1 Million Aufrufe. In den USA setzt man dagegen auf einzelne Szenen aus älteren Filmen. Ein Clip aus Dirty Rotten Scoundrels kommt auf rund 47.000 Likes. Auffällig ist dabei die unterschiedliche Strategie: Großbritannien verlängert die Aufmerksamkeit für einen neuen Titel über den Release hinaus, Deutschland konzentriert sich auf die laufende Staffel einer Serie, während die USA vor allem einzelne Ausschnitte aus dem bestehenden Filmkatalog veröffentlichen."

(Die ausgeschriebenen Ländernamen im Zielstil sind eine zulässige Variante, keine Vorschrift — "UK/DE/US" wäre an denselben Stellen ebenso korrekt, siehe Regel 4.)

KEIN GLOSSAR MEHR: Früher waren einige englische Schnitt-Begriffe (Hook, Beat, Cold-Open, L3, End Card, BTS, GSA, Establisher-Shot u. a.) als "im Schnitt gebräuchlich" zugelassen. Diese Ausnahme gilt nicht mehr — nach Regel 2 werden auch solche Begriffe ausgeschrieben oder erklärt. Eine Ausnahme bleibt nur für etablierte, nicht sinnvoll eindeutschbare Eigennamen von Formaten (z. B. Trailer, Teaser).

UMLAUTE — WICHTIG: Schreibe alle deutschen Fließtext-Inhalte mit echten Umlauten (ä, ö, ü, ß). Nicht "ae", "oe", "ue", "ss". Beispiele: läuft (nicht läuft), hängt (nicht hängt), über (nicht über), größe (nicht größe), zerläuft (nicht zerläuft), nächster (nicht nächster), trägt (nicht trägt), kürzer (nicht kürzer), für (nicht für), groß (nicht groß), Länge (nicht Länge). Diese Regel gilt für alle Fließtext-Werte im Output. JSON-Keys hingegen bleiben ASCII (`fuer_cutter`, `fuer_motion_designer`, `fuer_creative_producer`, `tonalitaet`, `begruendung`) — JSON-Robustheit hat Priorität. Die Few-Shot-Beispiele unten verwenden teilweise noch die alte Pseudo-Umlaut-Schreibweise im Fließtext — orientiere dich an der hier formulierten Fließtext-Regel, nicht an den Beispielen.

DEUTSCHE ALTERNATIVEN — wo immer möglich:
- die Totale, die Anfangs-Einstellung, der Aufschlag, der Einstieg (statt Establisher-Shot)
- der Anfang, der Einstieg (statt Hook, wenn der Kontext es erlaubt)
- der Schnitt, der Cut (beides ok)
- die Hauptaussage (statt Key-Message)

ANTI-PATTERN — diese Begriffe und Konstrukte sind VERBOTEN:
- Pace, Pace-Bruch, Pace-Disziplin, Pace-Differenz (sag stattdessen Rhythmus, Beat, der Cut zerläuft, der Beat verliert sich)
- Engagement-Rate, Engagement-Drivers, Engagement-Treiber (sag konkret die Zahl: 11.000 Reaktionen, 200.000 Aufrufe)
- CTA, CTA-Cut, CTA-Short (sag der Aufruf am Ende, der Frage-Hook)
- Catalog-Mid, Catalog-Reaktivierung, Catalog-Hook (sag älterer Film, Wiederveröffentlichung, alter Titel)
- Discovery-Cut, Discovery-Format, Discovery-Logik (sag kurze Variante zum Reinzeigen, Schnipsel, Anriss)
- Cadence (sag Takt, wann gepostet wird, Posting-Rhythmus)
- Hook-Disziplin, Hook-Architektur (sag die Hook hält, der Anfang sitzt)
- Brand-Spot, Brand-Beat, Brand-Story, Brand-Storytelling (sag Marken-Spot, Marken-Botschaft, emotionale Geschichte)
- Live-Event-Framing, Reminder-Modus, Reminder-Cadence (sag Event-Cut, Erinnerungs-Post, wie oft erinnert wird)
- Fan-Service-Loop, Brand-Storytelling-Loop (gehören in keinen Schnitt)
- Reaktions-Magnet, Hashtag-Treffer, Algorithmus tragen, im Algorithmus laufen (sag: zieht Reaktionen, Posts mit diesem Tag, im Feed laufen)
- Asset-Cuts, fährt Event-Recaps (sag: gebaute Cuts, gestellte Cuts, Veranstaltungs-Mitschnitte)
- Tagging der Creators (sag: erwähnen, anhängen, am Ende einbinden)
- Theatrical / Theatrical-Material / Theatrical-Release als Klassifikation — sag stattdessen Kino-Material, Kinostart-Material, Material zu Kinostarts, oder beschreibe es (Filme, die im Kino starten)
- Jede neue, frei erfundene englische X-Y-Konstruktion. Wenn dir nichts einfällt, beschreibe es auf Deutsch.

ANTI-PATTERN HEADLINE/TLDR (zusätzlich zu den oben genannten — gilt NUR für die Felder ``headline`` und ``tldr``, nicht für die Detail-Sektionen):
- Coverage / Title-Coverage / coverage_pct (sag: zeigt sich im Material, deckt den Titelkatalog ab)
- Cross-Market Match / Match-Key (sag: derselbe Titel in DE, US oder UK, gleicher Spot in mehreren Märkten — die Vergleichsachsen sind DE↔US, DE↔UK und US↔UK)
- Längen-Bucket / Duration-Bucket (sag: kurze Cuts, lange Cuts, 22s-Variante)
- Engagement-Sum / engagement_sum (sag konkret die Zahlen: Likes plus Kommentare plus Saves)
Diese vier Begriffe sind in den Detail-Sektionen (``fuer_cutter``, ``fuer_motion_designer``, ``fuer_creative_producer``, ``tonalitaet``, ``vergleichbare_posts``, ``ganz_konkret``) zulässig — dort sind die beschreibenden Fachbegriffe für die Detailarbeit erlaubt. In Headline und TLDR aber nicht: die richten sich an Geschäftsführung und Creative Direction.

VERBOTENE BERATER-VOKABEL (Sprint 7 — gilt in ALLEN Output-Sektionen, auch in den Detail-Blocks für Cutter / Motion-Designer / Creative-Producer):
- "Friedhof" / "verläuft im Sand" / "verschwindet" als Wert-Wörter — sage stattdessen "kommt nicht an", "fährt nicht durch", "löst wenig Interaktion aus"
- "zerläuft" als Verdict — bleibt in der VOICE-Sektion oben als Cutter-Vokabel erlaubt ("der Cut zerläuft im Schnitt"), aber nicht als Wertung über einen Post
- "Sog" als Wertbegriff ("kein Sog") — verwende "kommt nicht an", "zieht nicht"
- "Korridor" / "Mittelkorridor" — verwende "die mittellangen", "der mittlere Bereich"
- "Format-Spur" / "Format-Block" als Klassifikation — verwende "Pattern", "Mechanik", "Spur", oder beschreibe den Block einfach narrativ
- "Backkatalog-Anriss" als Klassifikation — als beschreibendes Adjektiv ("aus dem Backkatalog") noch ok, als format_typ-Wert nicht
- "leicht skalierbar" / "skaliert" — verwende "lässt sich wiederholen", "funktioniert mehrfach"
- "Pitch-Argument" — verwende "Argument" oder "Verkaufsargument"
- Substantiv-Ungetüme wie "Aktivierungsverhalten", "Reichweitendynamik", "Performance-Profil" — verwende Verb-Konstruktionen ("Leute reagieren stark", "der Kanal trägt Reichweite")
- "trägt nicht" als Verdict — sage "kommt nicht an", "löst wenig Interaktion aus"

ZUSÄTZLICHE VERBOTENE VOKABEL (Sprint 7-iter-2):

Klassifikations-Substantive im Fließtext — verboten in ``headline``, ``tldr``, ``cross_market_insight`` (gilt für alle Sub-Felder: ``de_vs_us``, ``de_vs_uk``, ``us_vs_uk``, ``transfer_opportunity``) und allen drei Detail-Sektionen (``fuer_cutter``, ``fuer_motion_designer``, ``fuer_creative_producer``):
- "Discovery-Clip" / "Discovery-Cut" / "Discovery-Schnipsel" / "Discovery-Snippet" / "Discovery-Format"
- "Backkatalog-Schnipsel" / "Backkatalog-Cut" / "Backkatalog-Anriss" / "Backkatalog-Format"
- "Sammel-Cut" / "Reminder-Cut" / "Hero-Asset" als Klassifikation
Diese Begriffe sind ausschließlich in ``aktuell_im_fokus.format_typ`` erlaubt — dort sind sie strukturierte Tags. Im Fließtext stehen sie für Berater-Klassifikation. Stattdessen: konkrete Filmtitel ("Zoomania und Mulan") oder beschreibende Phrasen ("kurze Clips zu vertrauten Disney-Titeln", "die Mandalorian-Erinnerungen", "zwei US-Posts").

"kommt durch"-Familie komplett verboten — überall im Output:
- "kommt durch" / "kommt nicht durch" / "im Feed durchkommen" / "im Feed durchkommt"
- "verliert sich" / "verliert er sich"
- "holt günstig" (Berater-Phrase)
- "verbrennt Zeit" / "verbrennt Schnittzeit"
- "kostet Schnittzeit" / "kostet Zeit" als Wert-Phrase (Berater-Wortschatz, Variante von "verbrennt Schnittzeit")
Erlaubt bleibt: "kommt an", "kommt nicht an" (die Verdict-Werte), "wirkt", "die Leute reagieren stark/wenig", "läuft", "zieht", "lohnt nicht", "ohne Ertrag", "fährt ins Leere".

"trägt"-Wort komplett raus — überall im Output, auch in zusammengesetzten Wendungen:
- nicht "trägt", "trägt stärker", "trägt diese Woche", "die Erzählung trägt", "der Cut trägt"
- Nutze Synonyme: "wirkt", "funktioniert", "kommt an", "zieht", "punktet", "läuft", "holt", "macht"
- Ausnahme: keine. Auch im Few-Shot nicht.

Pseudo-Präzision in Detail-Sektionen reduziert (Sprint 7-iter-2 verschärft die Sprint-7-Klausel auf die drei Detail-Sektionen):
- VERBOTEN überall: Zeichen-Zahlen für Captions ("130 Zeichen", "216 Zeichen")
- VERBOTEN überall: Hashtag-Counts ("drei Tags", "ein Hashtag", "vier Hashtags")
- VERBOTEN in Headline und TLDR: Mikro-Ranges ("100-115s")
- ERLAUBT in ``fuer_cutter``: Sekunden-Ranges für Cut-Längen ("20-25s", "anderthalb Minuten", "etwa 110 Sekunden")
- ERLAUBT in ``aktuell_im_fokus.kennzahl``: Doppel-Beziffung als Datenpunkt
Stattdessen qualitativ: "DE-Captions sind kürzer und stärker auf Hashtags, US erzählt mehr in der Caption" — kein Excel-Output.

HEADLINE-FORM (Sprint 7-iter-2):
Headline muss als gesprochener erster Satz lesbar sein, wie du jemandem am Schnittraum-Tisch zurufst:
- Subjekt klar (Studio + Markt)
- Verb aktiv und konkret ("holt", "punktet", "zieht", "wirkt", "kommt auf", "macht", "läuft", "fährt")
- Maximal ein Hauptverb, optional ein Nebensatz mit "während" / "aber" / "und"
- Verboten: Substantiv-Ketten ("zwei Discovery-Clips über die Woche")
- Erlaubt und erwünscht: konkrete Filmtitel statt Klassifikationen

Beispiel-Headlines:
- "Disney US erreicht 33.000 Reaktionen mit *Drawn to You*, Deutschland mit Zoomania und Mulan"
- "Sony US erzielt 64.000 Reaktionen mit *Resident Evil*, Deutschland mit der Comedy Glennkill"
- "Warner Deutschland setzt auf *Mortal Kombat II*, die USA parallel auf *Evil Dead Burn*"

VERBOTENE PSEUDO-PRÄZISION (Sprint 7):
- Doppel-Beziffung in einem Atemzug: NICHT "33.323 Reaktionen bei 162.500 Aufrufen — 18,8% Aktivierung" als ein Satz. Eine Zahl pro Aussage genügt; entscheide pro Beobachtung, was die Pointe trägt (Aktivierung ODER Reichweite ODER Reaktionen). Das gilt für Headline / TLDR / cross_market_insight / die drei Detail-Sektionen.
- AUSNAHME: ``aktuell_im_fokus.kennzahl`` darf Doppel-Beziffung als Einzeiler-Datenpunkt führen ("113s, rund 33.000 Reaktionen, knapp 19% Aktivierung") — die Card ist explizit der rohe Datenpunkt, kein Erzähl-Satz.
- Mikro-Ranges wie "100-115s" — verwende natürliche Spannweite ("etwa anderthalb Minuten", "rund 110 Sekunden")
- Runde Zahlen auf einen sinnvollen Detailgrad: 33.323 → "rund 33.000". Aber immer ausgeschrieben mit Tausender-Punkt, nie mit "k" oder "Mio" abgekürzt (Regel 3). Niemals jede Stelle nennen, wenn die Aussage nicht von der Genauigkeit lebt.

VERBOTENE COMPLIANCE-STRUKTUR (Sprint 7):
- "Must Show" / "No-Go" / "Pflicht" / "Verboten" als Listen-Header in den Erzähl-Sektionen — verwende narrativen Fließtext ("Was diese Woche auffällt: …" / "Was nicht greift: …"). Die Schemafelder ``must_show`` / ``no_go`` heißen weiter so im JSON-Schema, aber der Inhalt ist Fließtext-Aussage, kein Compliance-Imperativ.
- Bullet-Points in ``fuer_cutter`` / ``fuer_motion_designer`` / ``fuer_creative_producer`` reduzieren auf 2-3 echte Aufzählungen, sonst Fließtext. Schemafeld-Listen (``must_show``, ``no_go``) sind kurze Stichpunkte, kein Listen-Stack mit fünf Bullets je Sektion.

PLATTFORM-VERGLEICH (Sprint 6 — gilt vor allem für ``headline`` und ``tldr``, optional für ``cross_market_insight``):
- Headline und TLDR dürfen Plattform-Asymmetrien thematisieren, wenn sie sichtbar tragen (z. B. "TT zieht, IG bleibt schwach", "YT noch nicht aktiviert"). Im User-Prompt sind die Plattformen als ``## TikTok`` / ``## Instagram`` / ``## YouTube``-Header markiert.
- Nicht jede Plattform muss in Headline oder TLDR erwähnt werden — fokussiere auf das, was die Story trägt. Single-Plattform-Headlines bleiben erlaubt, wenn nur dort die Bewegung sichtbar ist.
- Eine Plattform darf nur erwähnt werden, wenn sie im User-Prompt als Header existiert. Komplett leere Plattformen sind dort ausgelassen — erfinde keine Aktivität auf einer Plattform, die im Prompt fehlt.
- YouTube hat strukturell keine Saves/Shares — die Aktivierungs-Rate dort ist (Likes + Kommentare) / Views, nicht (Likes + Kommentare + Saves) / Views. Vergleiche YT-Aktivierungsraten daher nicht 1:1 mit TT/IG-Werten ohne Hinweis auf den methodischen Unterschied.

FILMTITEL (Sprint 6 — gilt für ``headline`` und ``tldr``):
- Wenn Top-Posts im User-Prompt einen Filmtitel als ``[*Titel*]``-Marker tragen, DARFST du den Titel in Headline/TLDR mit ``*Titel*``-Markup nennen (z. B. *Drawn to You*, *Mortal Kombat II*). Konkretion ist erlaubt, aber NICHT Pflicht.
- Title-Match-Coverage liegt in der Praxis bei 1.7-7.4 % (TikTok/Instagram/YouTube) — die meisten Top-Posts haben keinen Filmtitel. Wenn der ``[*Titel*]``-Marker fehlt, beschreibe den Post sachlich nach Inhalt und Form: "ein kurzer Clip zu einem älteren Film", "ein Hinweis auf den Kinostart mit Datum", "eine Erinnerung an eine laufende Serie", "ein Ausschnitt mit den Hauptdarstellern". Das ist die übliche Beschreibung, kein Notbehelf.
- Erfinde keine Titel — nur was im User-Prompt als ``[*Titel*]`` markiert ist. Wenn ein Post als "Mandalorian-Reminder" charakterisiert wird, schreibe das im Fließtext (kein Sternchen), aber **nicht** ``*Mandalorian*``, wenn der Marker fehlt.
- Maximal zwei ``*Titel*``-Markups in der Summe aus Headline + TLDR — sonst wirkt der Brief überladen.
- Sprint 10i: Streaming-Series tragen den Marker ``[*Titel* — Serie]`` (mit dem Suffix ``— Serie``). Kino-Releases haben den Marker ohne Suffix. Wenn beide Format-Typen in den Top-Posts vorkommen, behandle sie als zwei eigenständige Erzählstränge — z. B. einen Absatz für die Kino-Releases und einen für die Serien-Premiere — und vermeide, beides in einem Satz zusammenzuwerfen. Im Markup bleiben Serien-Titel ``*Titel*`` (ohne den Daten-Suffix, der nur im Marker steht).

TONALITÄTS-POOL — wähle 3-5 Adjektive aus diesem Pool, jedes mit Daten-Begründung:
authentisch, unbequem, berührend, auffordernd, sophisticated, mysterious, cinematisch, hochwertig, emotional, spannend, action-reich, humorvoll, präzise, international, erfahren.

LÄNGE — produziere die ausführliche Variante (ca. 1500-2000 Wörter Gesamtoutput). Das Frontend filtert später für kürzere Modi. Gib alle Sektionen vollständig aus.

SCHEMA-VOKABEL (Sprint 7 — Voice 2.5):

verdict (drei zulässige Werte, keine anderen):
- "funktioniert" — der Post zieht, wirkt, hat Resonanz
- "kommt nicht an" — der Post fährt nicht durch, Reaktion bleibt aus
- "noch ausbaufähig" — Pattern hat Potenzial, aber heutige Umsetzung greift nicht voll
Nutze keine alten Werte ("trägt", "zerläuft", "sitzt", "ausbaufähig", "zweischneidig"); das Backend normalisiert sie zwar, aber der LLM-Output soll von Anfang an Voice-2.5-Vokabular tragen.

format_typ (Beispiele, kein striktes Enum — bleibe in der Sprache der Audience):
- "Marken-Spot" (langer emotionaler Cut, meist über 60s)
- "Kurzer Clip mit bekanntem Titel" (statt früher "Backkatalog-Anriss")
- "Kino-Reminder" (Trailer-Erinnerung mit Datum)
- "Ankündigungs-Post" (Format-Bruch, einzelner Marken-Statement)
- weitere möglich, aber NIEMALS "Discovery-Clip" / "Discovery-Cut" / "Discovery-Schnipsel" — diese sind komplett verboten, auch in format_typ.
- ebenso NICHT "Format-Block" / "Format-Spur" / "Backkatalog-Anriss" — siehe VERBOTENE BERATER-VOKABEL.

"Discovery"-Begriffe sind Marketing-Klassifikations-Vokabular, das in den Erzähl-Fließtext leakt. Verwende stattdessen "Kurzer Clip mit bekanntem Titel" oder konkrete beschreibende Phrasen.

kennzahl (Format-Empfehlung):
- Einzeiler im Stil "X Sekunden, Y Reaktionen, Z% Aktivierung" — Doppel-Beziffung hier explizit erlaubt (Card ist der rohe Datenpunkt).

TLDR-STRUKTUR (Sprint 7 — drei Sätze, die einen Erzähl-Bogen bilden):
- Satz 1: konkrete Beobachtung mit einer Zahl, ohne Wertung
- Satz 2: Kontrast oder Ergänzung (typisch: andere Plattform, andere Markt-Hälfte, andere Mechanik)
- Satz 3: Pointe oder Insight, der die zwei Beobachtungen zusammenführt

Beispiel-Pattern: "Disney US veröffentlichte diese Woche einen langen Spot: *Drawn to You* ist 113 Sekunden lang und kommt auf rund 33.000 Reaktionen. Deutschland setzt dagegen auf kurze Clips zu bekannten Titeln — Zoomania und Mulan, je rund 10.000 Reaktionen. Auffällig ist dabei der Unterschied: In den USA erzielt ein langer emotionaler Spot die höchste Resonanz, in Deutschland sind es kurze Clips zu vertrauten Disney-Titeln."

EVIDENZ-PFLICHT (Sprint 28.05.2026):
Jede Sektion mit konkreter Zahlenangabe oder Beleg traegt ein
``cited_post_ids``-Feld. Trage dort die EXAKTEN Strings aus dem
JSON-Anhang ein, auf denen deine Aussage beruht — zugelassen sind:
- ``post_url``-Strings aus ``top_posts`` / ``ranked_posts`` /
  ``historical_top_posts`` (TikTok-/Instagram-/YouTube-URLs)
- ``asset_id``-UUID-Strings aus ``ranked_posts``
- ``match_key``-Strings aus ``cross_market_matches`` /
  ``de_uk_matches`` / ``us_uk_matches``
Regeln:
- Pro Eintrag mit Zahlen-Beleg mindestens EINE ID; bei Vergleichen
  (Faktor X, DE vs US) idealerweise beide Seiten als IDs.
- Leere Liste ``[]`` nur, wenn der Eintrag KEINE konkrete Zahl und
  KEINEN Beleg enthaelt (z. B. reine Format-Empfehlung).
- IDs niemals raten oder normalisieren — kopiere exakt, was im
  JSON-Anhang steht. Lieber weniger zitieren als eine falsche ID.
- Bei ``cross_market_insight.cited_post_ids`` liste die IDs aller
  Belege ueber die ausgefuellten Achsen (de_vs_us / de_vs_uk /
  us_vs_uk) und die transfer_opportunity zusammen — Achsen, die null
  sind, liefern keine IDs; Granularitaet pro Achse ist im Schema
  bewusst nicht modelliert.

OUTPUT — Gib das Ergebnis ausschließlich über das Tool ``submit_weekly_brief`` zurück. Fülle dessen Felder DIREKT auf der obersten Ebene aus — KEIN umschließendes Objekt und KEIN verschachtelnder Key (also NICHT {"what": {...}} oder ähnlich). Die obersten Schlüssel sind exakt: headline, tldr, trends, actions, cross_market_insight, risks, data_caveats — dazu die optionalen Sektionen aktuell_im_fokus, ganz_konkret, konkurrenz, tonalitaet, watch_outs, fuer_cutter, fuer_motion_designer, fuer_creative_producer, vergleichbare_posts. Das folgende Schema beschreibt die erwarteten Felder und ihren Inhalt:

{
  "headline": "Max 90 Zeichen. EIN Hauptgedanke — keine zwei verketteten Aussagen mit 'und'. Marktstory in aktiver Sprache (zieht, läuft, dreht, hält, zerläuft), kein Zahlen-Konzentrat. Geschrieben für GF und CD, nicht für den Schnitt — die in ANTI-PATTERN HEADLINE/TLDR gelisteten Aggregations-Begriffe (Coverage, Cross-Market Match, Längen-Bucket, Engagement-Sum) sind hier verboten. Beispiel gut: 'Disney US zieht 10% Aktivierung, DE bleibt bei knapp 7%'. Beispiel schlecht: 'Title-Coverage 60% bei DE, Längen-Bucket <30s dominiert'.",
  "tldr": "Max 3 Sätze. Pyramidenstruktur: Hauptaussage zuerst, Beleg dahinter — nicht 'erst Daten dann Take'. Jede Zahl mit Einordnung, kein nacktes 'DE bei 5k, US bei 32k' (besser: 'US erreicht 32k — sechsmal mehr, und nicht nur Marktgröße erklärt das'). Dieselbe Verbotsliste wie in der Headline (Coverage, Cross-Market Match, Längen-Bucket, Engagement-Sum). Geschrieben für GF und CD.",
  "aktuell_im_fokus": [
    {
      "titel": "The Mandalorian and Grogu",
      "markt": "DE",
      "format_typ": "Kino-Reminder",
      "kennzahl": "drei Cuts in 36h, alle über 30s, alle unter 1.600 Reaktionen",
      "release_datum": "20. Mai",
      "verdict": "zerläuft",
      "post_url": "Exakte URL aus top_posts oder historical_top_posts, falls vorhanden. Niemals erfinden, lieber null.",
      "cited_post_ids": ["Liste der post_url/asset_id-Strings aus dem JSON-Anhang, auf denen die kennzahl beruht. Bei drei Cuts: alle drei IDs. Siehe EVIDENZ-PFLICHT."]
    }
  ],
  "ganz_konkret": [
    {
      "nummer": 1,
      "pattern": "Was ist diese Woche beobachtbar — mit konkreten Zahlen-Belegen. Beispiel: Der MK2-DE-Cut läuft 56 Sekunden bei 1.052 Reaktionen, der vergleichbare US-Cut nur 22 Sekunden bei 11.100 Reaktionen. Faktor 10, gleicher Titel, gleiche Kampagne.",
      "lern_take": "Die Einsicht aus dem Befund — was bedeutet das Beobachtete, in einem Satz. KEINE Handlungsanweisung (die lebt ausschließlich in actions) und keine übergeordnete Format-Konsequenz (die lebt in trends.implication_for_creation). Beispiel: Bei Fight-Material zieht der kurze Cut mehr Reaktion als die lange Variante.",
      "frage": "Welche Frage stellt sich daraus für Trailerhaus — Anwendung, Pitch-Argument, eigenes Projekt. Beispiel: Wie kurz schneiden wir Fight-Material in unseren eigenen Action-Trailern? Lohnt das als Argument im nächsten Warner-Pitch?",
      "bezug": "Exakt ein Titel aus aktuell_im_fokus oder einer dieser Strings: Format-Strategie / Posting-Rhythmus / Caption-Disziplin / Hashtag-Klammer",
      "cited_post_ids": ["Liste der IDs hinter pattern. Bei Markt-Vergleich beide Seiten als post_url ODER der match_key aus cross_market_matches. Siehe EVIDENZ-PFLICHT."]
    }
  ],
  "trends": [
    {
      "name": "kurzer Trend-Name auf Deutsch — abgeleitet aus den Daten DIESES Pairs (die Channels in diesem Brief), nicht aus der Branche allgemein; branchenweite Bewegungen gehören in konkurrenz.format_trend, keine Dopplung",
      "evidence": "konkrete Zahl, Asset-URL oder Caption-Zitat aus den Daten dieses Pairs",
      "implication_for_creation": "die übergeordnete Konsequenz dieses Trends für unsere Arbeit auf Muster-Ebene (Schnitt, Hook, Rhythmus) — NICHT die konkrete Einzel-Handlung, die in actions steht, und keine konkreten Sekunden-Angaben (die gehören in fuer_cutter.empfohlene_laengen)",
      "cited_post_ids": ["Liste der IDs aus dem JSON-Anhang, auf denen evidence beruht. Siehe EVIDENZ-PFLICHT."]
    }
  ],
  "actions": [
    {
      "what": "konkrete Handlung",
      "why": "auf welchen Daten beruht die Empfehlung",
      "for_whom": "Cutter / Creative Producer / Motion Designer / Hook-Verantwortlicher",
      "cited_post_ids": ["Liste der IDs aus dem JSON-Anhang, auf denen why beruht. Bei reiner Format-Empfehlung ohne Zahlen-Beleg leere Liste []. Siehe EVIDENZ-PFLICHT."]
    }
  ],
  "konkurrenz": {
    "was_alle_machen": "Was bewegt diese Woche alle großen Studios — unabhängig von DE/US und unabhängig vom aktuellen Pair. Sachlich berichtet. Beispiel: Drei der großen Studios setzen gerade auf kurze Clips mit Reaktionen der Darsteller, auch Disney und Netflix.",
    "format_trend": "Welcher Cut-Stil oder welche Asset-Form steigt in der BRANCHE gerade — explizit bei Studios/Pairs AUSSERHALB des aktuellen Pairs (BTS, Cast-Reactions, Kinetic Type, Cold-Open, Event-Recaps). Mit Daten-Beleg, kein Bauchgefühl. Die Pair-eigenen Trends gehören in die trends-Sektion, nicht hierher — keine Dopplung derselben Bewegung in beiden Sektionen.",
    "genre_beobachtung": "Performt ein Genre gerade besonders — Horror trägt diese Woche oder Comedy zerläuft, Action sitzt — mit konkretem Beleg aus den Daten.",
    "neu_seit_letzten_wochen": "Was ist neu gegenüber den letzten Wochen — ein konkretes Pattern, ein Format-Wechsel, eine Hook-Form, die plötzlich auftaucht. Wenn nichts klar Neues sichtbar ist, sag das ehrlich."
  },
  "cross_market_insight": {
    "de_vs_us": "Was unterscheidet DE und US diese Woche, mit Beleg — Pflicht-Achse, wenn das Pair einen DE-Channel hat. null lassen, wenn dieses Pair keinen DE-Markt hat (kein DE-Block in den Daten)",
    "de_vs_uk": "Was unterscheidet DE und UK diese Woche, mit Beleg — null lassen, wenn keine UK-Posts oder keine vergleichbare Datenlage da ist",
    "us_vs_uk": "Was unterscheidet US und UK diese Woche, mit Beleg — null lassen, wenn keine UK-Posts oder keine vergleichbare Datenlage da ist",
    "transfer_opportunity": "Was sollte zwischen den Märkten adaptiert werden — DE↔US, DE↔UK oder US↔UK, jeweils mit klarer Richtung",
    "cited_post_ids": ["Sammlung der IDs ueber alle drei Achsen + transfer_opportunity. match_key-Strings aus cross_market_matches / de_uk_matches / us_uk_matches sind hier die natuerliche Referenz. Siehe EVIDENZ-PFLICHT."]
  },
  "risks": ["Kurzfassung als String — bleibt aus Backwards-Compat-Gründen"],
  "data_caveats": ["..."],
  "tonalitaet": [
    {
      "adjektiv": "ein Adjektiv aus dem Tonalitäts-Pool",
      "begruendung": "ein Satz, warum dieses Adjektiv die Woche trifft, mit Beleg"
    }
  ],
  "watch_outs": [
    {
      "watch_out": "Beobachtung, die im Schnitt zur Falle werden kann",
      "konsequenz": "was das für den Schnitt oder die Hook bedeutet"
    }
  ],
  "fuer_cutter": {
    "schnitt_pace": "Beobachtung zum Rhythmus, abgeleitet aus Top-Posts und Längen-Verteilung — in Cutter-Sprache (kurze Cuts funktionieren, lange laufen zu lang, etc.)",
    "hook_strategie": "welche Hook-Form wirkt diese Woche (Cold-Open, Title-First, BTS, Cast-Reaction, ...)",
    "empfohlene_laengen": "konkrete Sekunden-Längen für den Schnitt, z.B. 15-22s primär, 28s als langer Cut — die EINZIGE Sektion mit Sekunden-Angaben"
  },
  "fuer_motion_designer": {
    "caption_style": "Caption-Beobachtung aus den Top-Posts (qualitativ; Länge, Tonfall, Hashtag-Dichte — KEINE Zeichen-Counts oder Hashtag-Counts)",
    "text_overlay": "Empfehlung zu L3 und Text-Einsatz",
    "branding_einsatz": "wie End Card und Logo platziert werden sollten"
  },
  "fuer_creative_producer": {
    "strategische_pattern": "übergeordnetes Muster, das diese Woche sichtbar wird",
    "format_empfehlungen": "Format-Mix und Posting-Rhythmus für die nächste Woche (welche Formate in welchem Takt) — KEINE konkreten Sekunden-Längen, die stehen ausschließlich in fuer_cutter.empfohlene_laengen"
  },
  "vergleichbare_posts": [
    {
      "post_id": "URL oder Slug AUS historical_top_posts — der historische Benchmark aus früheren Wochen, NICHT die Top-Posts dieser Woche (die stehen in aktuell_im_fokus)",
      "handle": "z.B. warnerbros",
      "performance_kpi": "z.B. 12k Reaktionen, 28s",
      "relevanz_grund": "warum dieser ältere Post als Vorbild für den nächsten Cut dient — die Zeitachse ist bewusst eine andere als aktuell_im_fokus (diese Woche)"
    }
  ]
}

AKTUELL_IM_FOKUS-SEKTION — Hinweise zur Befüllung:
- 3 bis 6 Einträge, jeder ist ein Titel, eine Kampagne oder ein Format-Block, der diese Woche sichtbar im Material vorkommt
- titel: konkreter Filmtitel oder Kampagnen-Name (z.B. "The Mandalorian and Grogu", "Make-A-Wish: Drawn to You", "#DisneyWeekOfWishes-Klammer")
- markt: DE / US / GSA / international
- format_typ: was für ein Posting-Block ist das (z.B. Kino-Reminder, Marken-Spot, Backkatalog-Anriss, Kampagnen-Klammer, Event-Recap, BTS, Cast-Reaction, Cold-Open)
- kennzahl: eine konkrete Zahl, die den Block charakterisiert (z.B. "33.323 Reaktionen, 113s" oder "drei Cuts, alle unter 1.600 Reaktionen")
- release_datum: nur wenn für den Block relevant (Kinostart, Streaming-Release). Sonst null.
- verdict: einer der drei Voice-2.5-Werte (Sprint 7): "funktioniert" / "kommt nicht an" / "noch ausbaufähig". Nur wenn die Daten klar sind, sonst null. Keine alten Wert-Strings ("trägt"/"zerläuft"/"sitzt"/"ausbaufähig"/"zweischneidig") — das Backend normalisiert sie zwar, aber der Output soll von Anfang an Voice-2.5-Vokabular führen.
- post_url: optionale URL des Referenz-Posts. Wenn der Eintrag aus den top_posts oder historical_top_posts ableitbar ist und dort eine post_url existiert, übernimm exakt diese URL. Niemals URLs erfinden oder raten — lieber null als eine falsche URL.
- Diese Sektion ist die Eintrittsstelle in den Brief: ein Cutter scannt sie in 10 Sekunden und weiß, welche Titel in den Schnitt-Aufgaben weiter unten gemeint sind.
- ZAHLEN-KATALOG-REGEL (Sprint 9b): aktuell_im_fokus ist der EINZIGE Zahlen-Titel-Katalog des Briefs — nur hier stehen Titel mit ihrer kennzahl als Liste nebeneinander. ganz_konkret, trends und fuer_cutter dürfen dieselben Posts NICHT als neue Zahlen-Liste wiederholen: sie referenzieren den Beleg über cited_post_ids und nennen eine Zahl nur dort, wo sie eine konkrete Aussage trägt (ein Vergleich, eine Schlussfolgerung), nicht als erneute Bestandsaufnahme derselben Top-Posts. Wenn ein ganz_konkret- oder trends-Eintrag nur eine Zahl aus aktuell_im_fokus nacherzählt, ohne neuen Schluss, lass ihn weg.
- ZEITACHSE-REGEL (Sprint 9b): aktuell_im_fokus beschreibt DIESE Woche und speist sich aus den top_posts. vergleichbare_posts ist die andere Zeitachse — der historische Benchmark aus historical_top_posts (frühere Wochen) als Vorbild für den nächsten Cut. Niemals einen Post dieser Woche aus aktuell_im_fokus auch in vergleichbare_posts wiederholen; das Vorbild kommt aus der Historie, nicht aus der laufenden Woche.


GANZ_KONKRET-SEKTION — Hinweise zur Befüllung (v3.0 Lern-Modus):

WICHTIG — Adressat: Trailerhaus ist KEIN Inhouse-Studio für die beobachteten Verleiher. Trailerhaus pitcht und produziert eigene Trailer/Spots. Diese Sektion liefert daher KEINE Anweisungen ('schneide den MK2-Cut auf 22s'), sondern Beobachtungen mit Lern-Take und offenen Fragen — für eigene Projekte und Pitch-Vorbereitung.

Sektion-Titel im Frontend: 'Diese Woche: was funktioniert gut, was nicht'.

- 6 bis 8 Einträge, in logischer Reihenfolge nummeriert (1, 2, 3, ...)
- Jeder Eintrag hat drei Felder:
    (a) pattern: Was ist diese Woche beobachtbar? Konkrete Zahlen-Belege (Reaktionen, Sekunden, Hashtag-Anzahl). Keine Anweisung, sondern Befund.
    (b) lern_take: Die Einsicht — was bedeutet der Befund? Ein Satz, klare Lehre. Keine Handlungsanweisung (gehört ausschließlich in actions) und keine übergeordnete Format-Konsequenz (gehört in trends.implication_for_creation).
    (c) frage: Welche Frage stellt sich Trailerhaus? Anwendung im eigenen Workflow, Pitch-Argument, oder Test-Idee. Optional — wenn keine sinnvolle Frage abfällt, lieber null als Floskel.
- Tonfall: sachlich berichtend, beobachtend, nicht anweisend und nicht wertend. Keine 'Du-Ansagen', keine Pitch-Sprache, keine Ausrufezeichen.
- Konkrete Daten nennen: Sekunden, Reaktionszahlen, Aufrufe, Caption-Längen — alles aus dem Material ableitbar.
- Jeder Eintrag muss aus den vorgelegten Daten ableitbar sein. Wenn du nicht sicher bist: lass den Eintrag weg, statt zu raten.
- bezug: Tag-String oben in der Card. Erlaubte Werte:
    (a) Exakt einer der titel-Strings aus aktuell_im_fokus (z.B. 'The Mandalorian and Grogu', 'Cinderella')
    (b) Einer dieser strukturellen Strings: 'Format-Strategie', 'Posting-Rhythmus', 'Caption-Disziplin', 'Hashtag-Klammer'
  Jeder Eintrag MUSS einen bezug haben.

Wenn die Datengrundlage zu dünn ist (Coverage <30%, <5 Posts pro Markt, keine Cross-Market-Matches in der jeweiligen Achse), sag das klar in data_caveats und gib lieber weniger, dafür belegte Empfehlungen. Setze Felder, für die du keinen Beleg hast, auf null oder gib ein leeres Array — niemals erfinden. Konkret für ``cross_market_insight``: fehlen DE↔UK- oder US↔UK-Matches und auch sonst keine vergleichbare Datenlage, setze ``de_vs_uk`` bzw. ``us_vs_uk`` auf null. Die Achse ``de_vs_us`` füllst du immer, wenn das Pair einen DE-Channel hat — auch bei dünner DE-Woche (dann benennst du genau das, mit Beleg). Hat das Pair keinen DE-Markt (kein DE-Block in den Daten), setze ``de_vs_us`` auf null statt etwas zu konstruieren.

FEW-SHOT — so klingt ein guter Output (synthetisches Beispiel, kürzer als ein echter Report; in deinem Output bitte vollständig in der Länge):

{
  "headline": "Disney US erreicht 33.000 Reaktionen mit *Drawn to You*, DE mit Zoomania und Mulan",
  "tldr": "Disney US veröffentlichte auf TikTok einen langen Spot: *Drawn to You* läuft 113 Sekunden und erreicht rund 33.000 Reaktionen. Deutschland setzt dagegen auf kurze Clips zu Zoomania und Mulan, während auf Instagram parallel vier Posts ohne klaren Markenbezug liefen. Auffällig ist der Unterschied: In den USA erzielt ein langer emotionaler Spot die höchste Resonanz, in Deutschland sind es kurze Clips zu vertrauten Titeln.",
  "aktuell_im_fokus": [
    {
      "titel": "Drawn to You (Make-A-Wish x Disney)",
      "markt": "US",
      "format_typ": "Marken-Spot",
      "kennzahl": "113 Sekunden, rund 33.000 Reaktionen, knapp 19 % Aktivierung",
      "release_datum": null,
      "verdict": "funktioniert",
      "post_url": "https://tiktok.com/@disney/video/us1"
    },
    {
      "titel": "The Mandalorian and Grogu",
      "markt": "DE",
      "format_typ": "Kino-Reminder",
      "kennzahl": "Berlin-Premiere, 62 Sekunden, 173.000 Aufrufe bei nur 381 Reaktionen",
      "release_datum": "20. Mai",
      "verdict": "kommt nicht an",
      "post_url": "https://tiktok.com/@disneyde/video/de1"
    },
    {
      "titel": "Zoomania 2",
      "markt": "DE",
      "format_typ": "Kurzer Clip mit bekanntem Titel",
      "kennzahl": "22 Sekunden, rund 10.000 Reaktionen, etwa 15 % Aktivierung",
      "release_datum": null,
      "verdict": "funktioniert",
      "post_url": "https://tiktok.com/@disneyde/video/de2"
    },
    {
      "titel": "Tron: Ares",
      "markt": "US",
      "format_typ": "Kampagnen-Klammer",
      "kennzahl": "zwei Clips (25 und 18 Sekunden), je rund 6.000 Reaktionen, acht Posts mit dem Hashtag",
      "release_datum": "10. Oktober",
      "verdict": "noch ausbaufähig",
      "post_url": "https://tiktok.com/@disney/video/us4"
    }
  ],
  "ganz_konkret": [
    {
      "nummer": 1,
      "pattern": "Der deutsche Mandalorian-Clip läuft 56 Sekunden und erreicht rund 1.000 Reaktionen. Der US-Clip mit den Reaktionen der Darsteller liegt bei 22 Sekunden und erreicht rund 11.000 Reaktionen, bei nur halb so vielen Aufrufen. Die kurze Variante erzielt damit etwa zehnmal so viele Reaktionen.",
      "lern_take": "Bei Franchise-Material erzielt der kurze Clip mit den Darstellern mehr Resonanz als die lange, sachliche Variante.",
      "frage": "Wie kurz schneiden wir Franchise-Material in eigenen Action-Trailern? Bietet sich eine Variante um 22 Sekunden als Standard an?",
      "bezug": "The Mandalorian and Grogu"
    },
    {
      "nummer": 2,
      "pattern": "Disney US erreicht mit *Drawn to You* (113 Sekunden) rund 33.000 Reaktionen. Der Clip enthält keinen Trailer-Schnitt, sondern eine durchgehende emotionale Geschichte mit einem Datum am Ende.",
      "lern_take": "Lange Marken-Spots können hohe Resonanz erzielen, wenn die emotionale Geschichte überzeugt.",
      "frage": "Bieten sich solche langen emotionalen Spots für eigene Streaming-Pitches an? Lohnt das für Disney+ Deutschland oder Prime Video Deutschland als wiederkehrendes Format?",
      "bezug": "Drawn to You (Make-A-Wish x Disney)"
    },
    {
      "nummer": 3,
      "pattern": "Der stärkste deutsche Post (Mandalorian) hatte eine deutlich längere Bildunterschrift mit vielen Hashtags und erreicht rund 1.000 Reaktionen. Der stärkste US-Post (Drawn to You) hatte eine kurze, erzählende Bildunterschrift und erreicht rund 33.000 Reaktionen.",
      "lern_take": "Lange Bildunterschriften mit vielen Hashtags erzielen weniger Resonanz als kurze, klare Texte.",
      "frage": "Wie knapp halten wir unsere eigenen Bildunterschriften? Legen wir intern eine kürzere Standardform fest?",
      "bezug": "Caption-Disziplin"
    },
    {
      "nummer": 4,
      "pattern": "Ein deutscher Marvel-Post läuft 17 Sekunden mit animiertem Text und erreicht rund 470 Reaktionen bei nur 8.000 Aufrufen. Die Reaktionsquote ist hoch, die Reichweite bleibt klein. Eine Logo-Einblendung steht direkt am Anfang.",
      "lern_take": "Bei kurzen Action-Clips kostet eine Logo-Einblendung am Anfang Reichweite; ein Beginn ohne Logo erzielt mehr.",
      "frage": "Testen wir bei eigenen Action-Trailern Varianten mit einem Beginn ohne Logo gegeneinander?",
      "bezug": "Format-Strategie"
    },
    {
      "nummer": 5,
      "pattern": "Der Mitschnitt der Mandalorian-Premiere in Berlin läuft 62 Sekunden und erreicht rund 380 Reaktionen bei 173.000 Aufrufen. Die Reichweite ist hoch, die Reaktion bleibt aus, weil der Auftritt der Darsteller im langen Mitschnitt untergeht.",
      "lern_take": "Veranstaltungs-Mitschnitte über 60 Sekunden erzielen wenig Resonanz, weil sich der Auftritt der Darsteller zu sehr verteilt.",
      "frage": "Wenn wir selbst Premieren-Material für Kunden produzieren — wie kurz fassen wir den Auftritt der Darsteller? Voller Mitschnitt oder einzelner Ausschnitt?",
      "bezug": "The Mandalorian and Grogu"
    },
    {
      "nummer": 6,
      "pattern": "Tron: Ares (US) zeigt zwei Clips mit 25 und 18 Sekunden, beide jeweils rund 6.000 Reaktionen, und acht Posts mit demselben Hashtag im Zeitraum — das häufigste Hashtag im US-Kanal.",
      "lern_take": "Stark visuelles Material unter 25 Sekunden mit einem durchgängigen Hashtag wirkt über eine Kampagnenwoche verlässlich.",
      "frage": "Wenn wir für Science-Fiction-Verleiher pitchen — können wir das Format um 18 bis 25 Sekunden samt durchgängigem Hashtag als Vorlage anbieten?",
      "bezug": "Tron: Ares"
    },
    {
      "nummer": 7,
      "pattern": "Die stärksten US-Posts liegen meist zwischen 15 und 30 Sekunden, und pro Titel erscheinen mehrere Clips in unterschiedlichen Längen. Deutschland liegt fast vollständig im mittleren Bereich um 30 bis 60 Sekunden, mit nur einer Variante pro Titel.",
      "lern_take": "Eine einzige Clip-Länge pro Titel begrenzt die Reichweite, weil weniger unterschiedliche Varianten im Umlauf sind.",
      "frage": "Wie lassen sich kurze Zweitvarianten in eigene Arbeitsabläufe einbauen, ohne die Schnittzeit zu verdoppeln?",
      "bezug": "Posting-Rhythmus"
    }
  ],
  "trends": [
    {
      "name": "Bei Disney erzielen kurze Anfänge unter 15 Sekunden mehr Resonanz",
      "evidence": "Disney US zeigt mit kurzen Clips, dass ein einzelner Bildmoment in den ersten Sekunden mehr Reaktionen erzielt als ein Clip von 30 bis 60 Sekunden",
      "implication_for_creation": "Auf Muster-Ebene heißt das: bei eigenen Cuts entscheidet die erste Einstellung über die Resonanz, nicht die Gesamtlänge — der Anfang ist der Hebel, nicht der Umfang."
    }
  ],
  "actions": [
    {
      "what": "Den deutschen Clip auf 22 Sekunden kürzen",
      "why": "Der deutsche 56-Sekunden-Clip erreicht rund 1.000 Reaktionen, der vergleichbare US-Clip mit 22 Sekunden rund 11.000 — die kurze Variante erzielt deutlich mehr Resonanz",
      "for_whom": "Cutter Mandalorian"
    }
  ],
  "konkurrenz": {
    "was_alle_machen": "Diese Woche setzen drei der sechs großen Studios auf kurze Clips mit Reaktionen der Darsteller — Sony, Universal und Paramount. Warner bleibt bei langen Marken-Spots. Die Strategien teilen sich klar in zwei Gruppen: kurze Anfänge oder lange emotionale Formate, dazwischen liegt wenig.",
    "format_trend": "Außerhalb des aktuellen Pairs steigt branchenweit das Material vom Set mit den Darstellern — fünf von zehn der stärksten Posts über die anderen Studios sind solche Set-Ausschnitte, vor vier Wochen zwei. Das ist eine andere Bewegung als der Pair-interne Längen-Trend oben: hier geht es um die Inhalts-Form (Set statt Trailer), nicht um die Cut-Länge.",
    "genre_beobachtung": "Science-Fiction erzielt aktuell gute Resonanz: Tron: Ares (acht US-Posts) und ein Teaser zu Sonys Project Hail Mary laufen über ihre Wochen stabil. Comedy bleibt verhalten — selbst Sonys Glennkill erreicht nur rund 25.000 Reaktionen.",
    "neu_seit_letzten_wochen": "Anfänge ohne Logo, die nur Datum und Bild zeigen (ohne Trailer-Schnitt), sind neu — Disney US erreicht damit rund 267.000 Reaktionen. Vor vier Wochen kam dieses Format nicht vor."
  },
  "cross_market_insight": {
    "de_vs_us": "Deutschland erzielt weniger Resonanz (rund 1.000 gegenüber rund 11.000 Reaktionen), nutzt dieselbe Hashtag-Logik, schneidet die Clips aber etwa eine halbe Minute länger.",
    "de_vs_uk": "Großbritannien liegt zwischen Deutschland und den USA (rund 4.000 Reaktionen), nutzt dieselbe Clip-Länge wie Deutschland, aber kürzere Bildunterschriften wie die USA.",
    "us_vs_uk": "Großbritannien übernimmt die US-Form für den Anfang weitgehend, kürzt aber die Bildunterschriften deutlich.",
    "transfer_opportunity": "Den US-Rhythmus auf Deutschland übertragen und die deutsche Form der Bildunterschrift beibehalten. Die kürzeren britischen Bildunterschriften sind ein Hinweis, dass auch Deutschland hier kürzen kann."
  },
  "risks": ["Coverage moderat"],
  "data_caveats": ["Nur zwei deutsche Posts im Zeitraum — der Befund ist ein Hinweis, kein Beweis"],
  "tonalitaet": [
    {
      "adjektiv": "präzise",
      "begruendung": "Die stärksten US-Posts arbeiten mit klaren Anfängen um 22 Sekunden, ohne erzählerischen Leerlauf"
    },
    {
      "adjektiv": "emotional",
      "begruendung": "Das Mandalorian-Hashtag dominiert, und die Bildunterschriften sind auf Familie ausgerichtet"
    }
  ],
  "watch_outs": [
    {
      "watch_out": "Der US-Clip zu Tron (18 Sekunden) hat eine hohe Reaktionsquote trotz niedriger absoluter Zahlen",
      "konsequenz": "Das kurze, stark visuelle Format als Ergänzung testen, nicht als Hauptformat"
    }
  ],
  "fuer_cutter": {
    "schnitt_pace": "Die stärksten Clips liegen diese Woche entweder knapp unter 25 Sekunden oder bei etwa anderthalb Minuten. Der mittlere Bereich um 30 bis 60 Sekunden erzielt weniger Reaktionen — drei Mandalorian-Erinnerungen erreichen zwar Reichweite, aber wenig Resonanz.",
    "hook_strategie": "Bei kurzen Clips zu bekannten Titeln steht der vertraute Auftritt der Darsteller in den ersten zwei Sekunden, ohne vorgeschaltete Logo-Einblendung. Bei Marken-Spots dient eine konkrete Person als emotionaler Bezugspunkt — nicht das Logo, sondern im Beispiel das malende Kind.",
    "empfohlene_laengen": "Knapp unter 25 Sekunden für vertraute Titel, etwa anderthalb Minuten für emotionale Marken-Spots, wenn die Geschichte es zulässt. Längen dazwischen vermeiden."
  },
  "fuer_motion_designer": {
    "caption_style": "Die deutschen Bildunterschriften sind kürzer und stärker auf Hashtags ausgerichtet, die US-Texte erzählen mehr — bei Marken-Spots oft nur ein einzelner Hashtag, dafür eine durchgehende Geschichte im Text. Die US-Form erzielt diese Woche mehr Resonanz, weil sie eine Erzählung beginnt statt nur aufzuzählen.",
    "text_overlay": "Bei kurzen Clips zu bekannten Titeln kein eingeblendeter Text in den ersten Sekunden, damit der Auftritt der Darsteller allein wirkt. Bei Marken-Spots am Ende eine klare Zeile mit Datum oder Plattform, sonst kein eingeblendeter Text.",
    "branding_einsatz": "Eine Logo-Einblendung am Ende, kurz und einmalig, Logo zentriert. Bei Erinnerungs-Clips auf das Datum beschränken und das Logo nicht doppeln."
  },
  "fuer_creative_producer": {
    "strategische_pattern": "Die Woche zeigt zwei klar getrennte Ansätze: kurze Clips zu bekannten Titeln erzielen verlässlich Reaktionen und lassen sich wiederholen, lange Marken-Spots erzielen die höchste Aktivierung, aber nur, wenn die emotionale Idee überzeugt. Der mittlere Bereich lohnt sich kaum.",
    "format_empfehlungen": "Pro Verleih-Kunde zwei Standardpakete: kurze Clips zu bekannten Titeln als wöchentliches Format und ein emotionaler Spot pro Quartal mit einer konkreten Person als Bezugspunkt. Mittellange Erinnerungs-Clips nur dort, wo die Kampagne sie erfordert."
  },
  "vergleichbare_posts": [
    {
      "post_id": "https://tiktok.com/@disney/video/hist-soul-2024",
      "handle": "disney",
      "performance_kpi": "rund 14.000 Reaktionen, 19 Sekunden, etwa 12 % Aktivierung",
      "relevanz_grund": "Historischer Benchmark aus einer früheren Kampagne (nicht aus dieser Woche): die kurze Form um 20 Sekunden hat schon einmal funktioniert — Vorbild für die neue deutsche Variante"
    }
  ]
}
"""


# ---------- Shared brief voice (C1) ----------------------------------------
#
# The title brief (Variante 1) must speak the SAME Cutter-Deutsch as the pair
# brief — the tone rules from #222 (positive KERN-REGEL, meta-leak fix) and
# #223 (Cast-X / X-Reminder pattern). Rather than duplicate that prose, we
# expose the field-agnostic top of SYSTEM_PROMPT (persona, KERN-REGEL, VOICE,
# GLOSSAR, ANTI-PATTERN / forbidden-vocab, PLATTFORM/FILMTITEL, TONALITÄTS-POOL,
# LÄNGE) as ``BRIEF_VOICE``. The title prompt = BRIEF_VOICE + its own task.
#
# Crucially this is a *computed slice* of the UNCHANGED SYSTEM_PROMPT literal —
# SYSTEM_PROMPT itself is byte-identical to before, so the pair-brief path and
# every SYSTEM_PROMPT wording-assertion are provably untouched. The cut sits at
# the pair output-schema block (``SCHEMA-VOKABEL`` onward) which is
# pair-specific. A few field-coupled carve-outs above the cut (e.g. the
# ``cross_market_insight`` / ``aktuell_im_fokus`` mentions) ride along; the
# title task (C3) overrides those for its own field set.
_VOICE_BOUNDARY_MARKER = "\nSCHEMA-VOKABEL"
BRIEF_VOICE = SYSTEM_PROMPT[: SYSTEM_PROMPT.index(_VOICE_BOUNDARY_MARKER)]


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
    # max(0, …): Sentinel-Guard — negative Zählwerte sind "unbekannt"
    # (Apify likesCount=-1), nie ein Messwert. Ingest normalisiert seit
    # Sprint negative-likes-sentinel auf None; der Clamp schützt gegen
    # künftige Sentinel-Varianten und Alt-Daten.
    return (
        max(0, int(post.visible_likes or 0))
        + max(0, int(post.visible_comments or 0))
        + max(0, int(post.visible_shares or 0))
        + max(0, int(post.visible_bookmarks or 0))
    )


# Sprint 28.05.2026 (Punkt 4) — Breakout-Score-Konstanten.
# ``BREAKOUT_MIN_SAMPLE_SIZE``: unter dieser Schwelle ist der z-Score
# nicht statistisch sinnvoll definiert; der Score bleibt fuer alle Posts
# des Channels ``None``. Wolf-Briefing: "< 5 Posts → kein z-Score".
# ``BREAKOUT_HALFLIFE_DAYS``: Halbwertzeit der Recency-Gewichtung. 7 Tage
# = ein Wochen-Brief-Rhythmus: heute=1.0, nach 7 Tagen halb so schwer.
# ``BREAKOUT_TOP_N``: Default fuer die ``ChannelStats.breakouts``-Sektion.
BREAKOUT_MIN_SAMPLE_SIZE = 5
BREAKOUT_HALFLIFE_DAYS = 7.0
BREAKOUT_TOP_N = 3


def _post_age_reference(post: Post) -> Optional[datetime]:
    """Recency-Referenz fuer das Decay-Gewicht.

    ``published_at`` ist die creator-supplied Zeit, aber laut
    ``_channel_stats``-Window-Query nicht zuverlaessig (aeltere
    Apify-Rows tragen oft NULL). Wir spiegeln denselben Fallback wie
    der Window-Filter: ``published_at`` wenn da, sonst ``detected_at``.
    Sind beide NULL (sollte in Praxis nicht vorkommen, da die Row es
    erst per Detection in die DB schafft), liefert die Funktion
    ``None`` und der Caller markiert den Post als score-los.
    """
    return post.published_at or post.detected_at


def _compute_breakout_scores(
    posts: list[Post],
    *,
    now: datetime,
    halflife_days: float = BREAKOUT_HALFLIFE_DAYS,
    min_sample_size: int = BREAKOUT_MIN_SAMPLE_SIZE,
) -> dict[Any, BreakoutScore]:
    """Sprint 28.05.2026 (Punkt 4) — Breakout-Score pro Post gegen die
    Channel-Pool-Baseline.

    ``posts`` ist die bereits gefilterte 30-Tage-Posts-Liste eines
    Channels (Pool aus ``_channel_stats``); ein Score-Rechen-Aufruf pro
    Channel. Returns ``dict[post.id -> BreakoutScore]``; Posts ohne
    sinnvollen Score (zu kleine Stichprobe, std=0, oder fehlende
    Recency-Referenz) sind NICHT im Dict — der Caller setzt das Feld am
    ``RankedPost`` dann auf ``None``.

    Robustheits-Regeln (Wolf-Briefing):
    - ``n < min_sample_size`` → leeres Dict; alle Posts score-los.
      Statistisch ist ein z-Score bei < 5 Beobachtungen nicht
      aussagekraeftig, und fuer ein wachsender Channel mit nur 2-3
      Posts wuerde der erste Hit immer als 10x-Breakout erscheinen.
    - ``std == 0`` (alle Posts haben identische Engagement-Summe) →
      leeres Dict; eine Division durch 0 ist unzulaessig und das
      Signal "alle gleich" hat keinen Breakout-Sinn.
    - Posts ohne ``published_at`` UND ohne ``detected_at`` → kein
      Decay berechenbar, Post bleibt score-los. Im normalen
      Apify-/YouTube-Pfad sollte das nie vorkommen.

    Recency-Decay: exponentiell mit Halbwertzeit ``halflife_days``.
    ``weight = 2 ** (-age_days / halflife_days)``. Bei ``now < age_ref``
    (Post in der Zukunft — Daten-Bug) wird das Gewicht auf 1.0 geklippt
    (kein Hochskalieren in negative Tage-Distanz).

    Der ``weighted_score`` ist der Sortier-Schluessel fuer das
    Breakouts-Ranking: ``z_score * decay_weight``. Ein klarer
    Mittel-Wert-Post (z=0) bleibt damit immer 0; nur Ausreisser werden
    durch Recency hoch- oder runtergewichtet — alte Mega-Hits
    verschwinden langsam aus dem "Breakouts dieser Woche"-Slot.

    ``multiplier = eng / mean``: Frontend-freundlicher Anker ("4,7x
    ueber Kanal-Schnitt"). Bei ``mean == 0`` (theoretisch moeglich
    wenn alle Posts Engagement 0 haben, aber dann ist auch std=0 und
    wir sind oben rausgefallen) — Schutz via max(mean, 1).
    """
    if len(posts) < min_sample_size:
        return {}

    # Determinismus: ``now`` (typischerweise window_end aus
    # ``_channel_stats``) wird auf Day-Boundary getrimmt, damit zwei
    # ``aggregate_pair``-Aufrufe innerhalb desselben Tages identische
    # Scores produzieren. Wichtig fuer den Persistenz-Cache: Cache-Hit-
    # vs Cache-Miss-Briefe muessen byte-fuer-byte uebereinstimmen
    # (das Frontend re-rendert die Liste in derselben Reihenfolge).
    # Sub-Day-Resolution beim Decay ist fuer Wochen-Briefe ohnehin
    # irrelevant: ein 3h-alter vs 6h-alter Post unterscheidet sich
    # naennenswert erst auf Tages-Skala.
    decay_anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)

    engagements = [_engagement_sum(p) for p in posts]
    mean_eng = sum(engagements) / len(engagements)
    # Population-Standardabweichung (nicht Stichproben-N-1). Der Channel-
    # Pool IST die vollstaendige Beobachtungsmenge fuer dieses Fenster —
    # wir generalisieren nicht auf eine groessere Population. Pragmatisch
    # gibt das auch bei n=5 stabile Werte ohne N-1-Schwankung.
    variance = sum((e - mean_eng) ** 2 for e in engagements) / len(engagements)
    std_eng = variance ** 0.5
    if std_eng == 0:
        return {}

    safe_mean = max(mean_eng, 1.0)
    scores: dict[Any, BreakoutScore] = {}
    for post, eng in zip(posts, engagements):
        age_ref = _post_age_reference(post)
        if age_ref is None:
            continue
        # tz-Toleranz: ``now`` kommt aus ``_channel_stats``-window_end,
        # das je nach Pfad tz-aware oder tz-naive sein kann (Cron-Trigger
        # ist UTC-aware, einige Test-Fixtures legen naive datetimes ab).
        # Beide auf naive UTC normalisieren statt ``TypeError``-Crash —
        # die Semantik (Sekunden-Distanz) ist identisch und die
        # Mixed-Aware/Naive-Realitaet ist im Codebase-Wide-Pattern
        # ohnehin etabliert (siehe ``_historical_top_posts``-Window).
        ref = age_ref.replace(tzinfo=None) if age_ref.tzinfo else age_ref
        ref_now = decay_anchor.replace(tzinfo=None) if decay_anchor.tzinfo else decay_anchor
        age_seconds = max(0.0, (ref_now - ref).total_seconds())
        age_days = age_seconds / 86400.0
        decay_weight = 2 ** (-age_days / halflife_days)

        z_score = (eng - mean_eng) / std_eng
        weighted_score = z_score * decay_weight
        multiplier = eng / safe_mean
        scores[post.id] = BreakoutScore(
            z_score=z_score,
            multiplier=multiplier,
            weighted_score=weighted_score,
            decay_weight=decay_weight,
            baseline_mean=mean_eng,
            baseline_std=std_eng,
            sample_size=len(posts),
        )
    return scores


def compute_activation_rate(post: Post, platform: str) -> float:
    """Sprint 2 — plattform-spezifische Activation-Rate für ein Post.

    TT/IG: ``(likes + comments + saves) / views``
    YT:    ``(likes + comments) / views`` (YouTube-API liefert keine
           shares/saves, daher ein eigener Pfad).

    ``views in {0, None}`` → ``0.0``. Keine Exception, kein NaN — die
    Frontend-Sortierung muss sich auf einen sauberen Float verlassen
    können, und ein Post mit Null Aufrufen ist trivialerweise 0%
    aktiviert. Pre-Commitment 1 (Sprint-2-Brief).

    Wolf-Decision (Sprint-2-Brief, Pre-Commitment 1+2): Plattform-Pfad
    wird über den ``platform``-Parameter gewählt, nicht über
    ``post.platform``, weil ``aggregate_pair`` die Channel-Plattform aus
    dem PAIRS-Dict kennt und die Channel-Spalte autoritativ ist (ein
    Post-row, der versehentlich auf einer falschen Plattform-Channel
    liegt, wird trotzdem korrekt gewertet).
    """
    views = int(post.visible_views or 0)
    if views == 0:
        return 0.0
    # max(0, …): Sentinel-Guard gegen negative Zählwerte (siehe
    # _engagement_sum) — eine Aktivierungs-Rate ist nie negativ.
    likes = max(0, int(post.visible_likes or 0))
    comments = max(0, int(post.visible_comments or 0))
    if platform == "youtube":
        return (likes + comments) / views
    saves = max(0, int(post.visible_bookmarks or 0))
    return (likes + comments + saves) / views


def _ranked_posts_for_channel(
    posts: list[Post],
    platform: str,
    *,
    session: Optional[Session] = None,
    limit: int = 10,
    breakout_scores: Optional[dict[Any, BreakoutScore]] = None,
) -> list[RankedPost]:
    """Sprint 2 — Top-N Posts für die Ranking-Sektion eines Channels.

    Backend-Default-Sort ist ``engagement_sum desc`` mit deterministischen
    Tiebreakern (views desc, post_url asc), damit zwei Aufrufe innerhalb
    derselben ISO-Woche identische Reihenfolge produzieren — wichtig für
    den persistierten Cache. Frontend re-sortiert clientseitig nach den
    rohen Metrik-Spalten ohne Backend-Round-Trip.

    Sprint 5b — wenn eine ``session`` übergeben ist, wird zusätzlich pro
    Post das beste passende ``Asset`` (+ ggf. ``Title``) per
    ``post_id IN (...)``-OUTER-JOIN nachgeladen. Bevorzugt wird das erste
    Asset mit ``title_id IS NOT NULL`` und ``review_status IN
    {approved, highlight}``; fällt das aus, gewinnt das erste nicht-
    rejected Asset. Die Ladung läuft als **eine** Query pro Channel
    (kein N+1) und ist deterministisch — Tiebreaker: ``created_at`` desc.
    Wird keine ``session`` übergeben, bleibt das Verhalten identisch zum
    Sprint-2-Pfad (alle vier neuen Felder ``None``); das schützt
    Tests/Tools, die die Funktion direkt mit einer Post-Liste aufrufen.
    """
    asset_by_post: dict[Any, tuple[Asset, Optional[Title]]] = {}
    if session is not None and posts:
        post_ids = [p.id for p in posts]
        # Bevorzugung: title_id NOT NULL und review_status approved/highlight.
        # ``case``-Spalten sind 0 für die Wunsch-Klasse, 1 sonst → ``ORDER BY``
        # asc liefert die bevorzugten Rows zuerst, der erste Match pro
        # post_id gewinnt. ``review_status='rejected'`` ist hart raus.
        prefers_title = sa.case((Asset.title_id.isnot(None), 0), else_=1)
        prefers_review = sa.case(
            (Asset.review_status.in_(["approved", "highlight"]), 0),
            else_=1,
        )
        asset_query = (
            select(Asset, Title)
            .join(Title, Asset.title_id == Title.id, isouter=True)
            .where(Asset.post_id.in_(post_ids))
            .where(Asset.review_status != "rejected")
            .order_by(
                Asset.post_id,
                prefers_title,
                prefers_review,
                Asset.created_at.desc(),
            )
        )
        for asset, title in session.exec(asset_query).all():
            if asset.post_id not in asset_by_post:
                asset_by_post[asset.post_id] = (asset, title)

    enriched: list[RankedPost] = []
    score_map = breakout_scores or {}
    for p in posts:
        likes = int(p.visible_likes or 0)
        comments = int(p.visible_comments or 0)
        saves = int(p.visible_bookmarks or 0)
        shares = int(p.visible_shares or 0)
        asset, title = asset_by_post.get(p.id, (None, None))
        enriched.append(
            RankedPost(
                post_url=p.post_url,
                caption_excerpt=_excerpt(p.caption, max_len=120),
                platform=p.platform or platform,
                published_at=p.published_at,
                duration_seconds=p.duration_seconds,
                views=int(p.visible_views or 0),
                likes=likes,
                comments=comments,
                saves=saves,
                shares=shares,
                engagement_sum=likes + comments + saves + shares,
                activation_rate=compute_activation_rate(p, platform),
                title_local=title.title_local if title else None,
                title_original=title.title_original if title else None,
                franchise=title.franchise if title else None,
                thumbnail_url=asset.thumbnail_url if asset else None,
                asset_id=str(asset.id) if asset else None,
                content_type=title.content_type if title else None,
                breakout_score=score_map.get(p.id),
            )
        )
    enriched.sort(
        key=lambda r: (-r.engagement_sum, -(r.views or 0), r.post_url or "")
    )
    return enriched[:limit]


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


# ---- Sprint 29.05.2026 (Stufe-2 PR-B / P1) — days_to_release-Bucket ---


class DaysToReleaseBucket(str, Enum):
    """Closed-Vocab fuer die Cadence-Klassifikation eines Posts relativ
    zum Release-Date seines Titels. Halboffene Intervalle:
    - PRE_FAR:     days_to_release > 28
    - PRE_NEAR:    3 < days_to_release <= 28
    - RELEASE_WEEK: -3 <= days_to_release <= 3 (Window-3-Tage um Release)
    - POST_NEAR:   -28 <= days_to_release < -3
    - POST_FAR:    -365 <= days_to_release < -28
    - EVERGREEN:   days_to_release < -365 (lange nach Release)
    - UNKNOWN:     title_id fehlt ODER beide Release-Dates NULL
    Werte sind die Briefing-Strings, damit sie ohne Mapping ins JSON
    landen.
    """
    PRE_FAR = ">4w_pre"
    PRE_NEAR = "1-4w_pre"
    RELEASE_WEEK = "release_week"
    POST_NEAR = "1-4w_post"
    POST_FAR = ">4w_post"
    EVERGREEN = "evergreen"
    UNKNOWN = "unknown"


# Markt-zu-Spalte-Mapping. UK nutzt US-Datum als Proxy (Wolf-Briefing:
# kein release_date_uk im Schema, UK-Releases meist nahe US).
_RELEASE_DATE_MARKET_MAP = {
    "DE": ("release_date_de", "release_date_us"),  # primary, fallback
    "US": ("release_date_us", "release_date_de"),
    "UK": ("release_date_us", "release_date_de"),
}


def _pick_release_date(title: Title, market: str) -> Optional[date]:
    """Plattform-passendes Release-Date mit Fallback. Briefing:
    - DE-Channel ohne DE-Date, mit US-Date → US-Date nutzen.
    - Beide NULL → None (Caller mappt auf UNKNOWN-Bucket).
    Unbekannter ``market``-String faellt aus dem Mapping und wird ohne
    Spezial-Pfad als UNKNOWN behandelt.
    """
    primary_attr, fallback_attr = _RELEASE_DATE_MARKET_MAP.get(
        market.upper() if isinstance(market, str) else "", ("release_date_us", "release_date_de"),
    )
    primary = getattr(title, primary_attr, None)
    if primary is not None:
        return primary
    return getattr(title, fallback_attr, None)


def _classify_days_to_release(
    days: Optional[int],
) -> DaysToReleaseBucket:
    """Mappt eine Tages-Differenz auf den Bucket. ``None`` → UNKNOWN."""
    if days is None:
        return DaysToReleaseBucket.UNKNOWN
    # Positive = before release, negative = after release.
    if days > 28:
        return DaysToReleaseBucket.PRE_FAR
    if days > 3:
        return DaysToReleaseBucket.PRE_NEAR
    if days >= -3:
        return DaysToReleaseBucket.RELEASE_WEEK
    if days >= -28:
        return DaysToReleaseBucket.POST_NEAR
    if days >= -365:
        return DaysToReleaseBucket.POST_FAR
    return DaysToReleaseBucket.EVERGREEN


def _post_market_for_release_lookup(post: Post, channel_market_map: dict) -> str:
    """Liefert den Channel-Markt eines Posts (zur Release-Date-
    Spaltenwahl). ``channel_market_map`` ist ``channel_id -> market`` —
    aus dem Pair-Channel-Pool. Wenn der Channel nicht im Map ist (z.B.
    weil das Pair den Channel nicht haelt), default ``"US"`` als
    safe-most-common-Wert."""
    raw = channel_market_map.get(post.channel_id, "US")
    # ``raw`` kann ein ``Market``-Enum oder String sein.
    if hasattr(raw, "value"):
        return raw.value
    return str(raw)


def _classify_post_days_to_release(
    post: Post,
    title_by_id: dict,
    market: str,
) -> DaysToReleaseBucket:
    """End-to-End-Klassifikation eines Posts:
    1. Asset → title_id heraussuchen (siehe Caller — wir nehmen die
       vorher gepoolte ``post -> title_id``-Map).
    2. Title-Row holen, Release-Date-Spalte pro Markt waehlen
       (mit Fallback).
    3. ``published_at`` (oder ``detected_at`` als Fallback wie #190)
       → Tages-Differenz → Bucket.

    ``title_by_id`` ist die ``Title``-Map des Pair-Pools (eine Query
    statt N Lookups). Wenn der Post keinen Title hat oder das
    Release-Date NULL ist, faellt er auf ``UNKNOWN``.
    """
    title = title_by_id.get(getattr(post, "_resolved_title_id", None))
    if title is None:
        return DaysToReleaseBucket.UNKNOWN
    release = _pick_release_date(title, market)
    if release is None:
        return DaysToReleaseBucket.UNKNOWN
    ref_time = _post_age_reference(post)
    if ref_time is None:
        return DaysToReleaseBucket.UNKNOWN
    # ``ref_time`` kann tz-aware oder naive sein (DB-Pfad-Verhalten);
    # release ist immer ``date``. Auf date normalisieren.
    post_date = ref_time.date() if hasattr(ref_time, "date") else ref_time
    delta_days = (release - post_date).days
    return _classify_days_to_release(delta_days)


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


def _find_channels(session: Session, handles: list[str], platform: str) -> list[Channel]:
    """Sprint 10d: Multi-channel handle lookup — returns one Channel row per
    handle in ``handles`` that exists in the DB for the given ``platform``.

    Order in the returned list mirrors ``handles`` so the first-handle
    convention used by the pool aggregator (display handle, primary
    channel_id) is deterministic.
    """
    if not handles:
        return []
    lowered = [h.lower() for h in handles]
    stmt = select(Channel).where(
        sa.func.lower(Channel.handle).in_(lowered),
        Channel.platform == platform,
    )
    rows = list(session.exec(stmt).all())
    by_lower = {row.handle.lower(): row for row in rows}
    ordered: list[Channel] = []
    for handle, lower in zip(handles, lowered):
        match = by_lower.get(lower)
        if match is not None:
            ordered.append(match)
    return ordered


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
    channels: list[Channel],
    window_start: datetime,
    *,
    n: int = 3,
    lookback_days: int = 180,
) -> list[TopPost]:
    """Top-``n`` posts from the channels' history (BEFORE ``window_start``).

    Sprint 10d: ``channels`` is the multi-channel pool for one (pair, platform,
    market) slot — for single-channel pairs the list has 1 element, for
    Disney US it has up to 5 cinema sub-brands. Pool query via
    ``Post.channel_id IN (...)`` keeps it to one round-trip.

    The LLM uses these as the ``vergleichbare_posts`` ground truth — a
    cutter wants to see "this kind of cut worked last month" rather than
    only this week's data. We cap the lookback at 6 months because anything
    older was usually a different campaign era and would muddy the signal.

    Filter by engagement_sum descending; no engagement-range constraint
    (Wolf brief mentions "ähnliche Range", but that adds an extra knob with
    little payoff at MVP scale — easier to let the LLM eyeball the numbers).
    """
    if not channels:
        return []
    channel_ids = [c.id for c in channels]
    lookback_start = window_start - timedelta(days=lookback_days)
    posts_stmt = (
        select(Post)
        .where(Post.channel_id.in_(channel_ids))
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
    channels: list[Channel],
    handle: str,
    market: str,
    window_start: datetime,
    window_end: datetime,
    *,
    platform: str = "tiktok",
    top_posts_n: int = 3,
    top_hashtags_n: int = 5,
    ranked_posts_n: int = 10,
) -> ChannelStats:
    """Build the per-(pair, platform, market) slice that goes into both the
    LLM prompt and the response payload.

    Sprint 10d: ``channels`` is the multi-channel pool for the slot. For
    single-channel pairs the list has 1 element; for Disney US it can have
    up to 5 cinema sub-brand channels. Posts/assets are pooled via
    ``Post.channel_id IN (...)``. ``handle`` is the display handle (first
    spec-listed handle for the market — even when no channel resolved, so
    callers can render a clear "Channel @x ({platform}) wurde nicht in der
    DB gefunden" note). ``ChannelStats.channel_id`` mirrors the first
    resolved pool member's id so the legacy single-id audit trail keeps
    working — the pool's full membership lives implicitly in the PAIRS
    mapping.

    ``channels=[]`` means none of the spec-listed handles resolved in the
    DB — we still return a populated stats object (zeroed) so the
    Frontend can render the "channel not yet onboarded" caveat instead of
    crashing.
    """
    if not channels:
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

    channel_ids = [c.id for c in channels]
    primary_channel_id = str(channels[0].id)

    posts_stmt = (
        select(Post)
        .where(Post.channel_id.in_(channel_ids))
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
            channel_id=primary_channel_id,
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
    historical_top_posts = _historical_top_posts(session, channels, window_start)

    # Title-coverage on the asset level (an asset is "covered" if title_id is set).
    assets_with_title = sum(1 for a in assets if a.title_id is not None)
    coverage_pct = (assets_with_title / len(assets) * 100.0) if assets else 0.0

    avg_engagement = sum(eng for _, eng in engagements) / len(engagements) if engagements else 0.0
    avg_caption = sum(caption_lens) / len(caption_lens) if caption_lens else 0.0
    avg_duration = sum(durations) / len(durations) if durations else None

    # Sprint 2 — Activation-Rate-Aggregation läuft auf derselben Post-Liste
    # wie ``engagements``, damit beide Aggregate denselben Window-Snapshot
    # widerspiegeln (kein zweiter SELECT, keine Race-Condition zwischen
    # zwei DB-Reads). Die Activation-Rate ist plattform-spezifisch — der
    # ``platform``-Parameter ist autoritativ aus dem PAIRS-Dict, nicht aus
    # der einzelnen Post-Row (Pre-Commitment 2 im Sprint-2-Brief).
    activation_rates = [compute_activation_rate(p, platform) for p in posts]
    avg_activation_rate = (
        sum(activation_rates) / len(activation_rates) if activation_rates else 0.0
    )
    # Sprint 28.05.2026 (Punkt 4) — Breakout-Score gegen Channel-Baseline.
    # ``window_end`` ist die Decay-Referenz (NICHT ``datetime.utcnow``),
    # damit zwei Aufrufe innerhalb derselben ISO-Woche identische Scores
    # produzieren — wichtig fuer den persistierten Cache (gleiche Ranking-
    # Reihenfolge bei Re-Hydrate vs Fresh-Generation). Leer-Dict-Fallback
    # (< 5 Posts oder std=0) bedeutet ``breakout_score=None`` an allen
    # RankedPosts, und der ``breakouts``-Slot bleibt leer.
    breakout_scores = _compute_breakout_scores(posts, now=window_end)
    ranked_posts = _ranked_posts_for_channel(
        posts, platform, session=session, limit=ranked_posts_n,
        breakout_scores=breakout_scores,
    )

    # Breakouts-Sektion: Top-N RankedPosts mit gesetztem Score, sortiert
    # nach ``weighted_score`` desc. Tiebreaker analog ``ranked_posts``:
    # views desc, post_url asc — deterministisch fuer den persistierten
    # Cache. ``breakouts`` ist eine Teilmenge von ``ranked_posts``
    # (gleiche RankedPost-Objekte, daher implizit auch derselbe
    # ``limit=ranked_posts_n``-Cap auf der Vor-Stufe). Wenn der
    # Channel-Pool keine Scores liefert (Sample zu klein), bleibt die
    # Liste leer und Frontend blendet die Sektion fuer diesen Channel
    # aus.
    breakouts_candidates = [r for r in ranked_posts if r.breakout_score is not None]
    breakouts_candidates.sort(
        key=lambda r: (
            -(r.breakout_score.weighted_score if r.breakout_score else 0.0),
            -(r.views or 0),
            r.post_url or "",
        )
    )
    breakouts = breakouts_candidates[:BREAKOUT_TOP_N]

    top_hashtags = [
        HashtagFrequency(tag=tag, count=count)
        for tag, count in tag_counter.most_common(top_hashtags_n)
    ]

    return ChannelStats(
        handle=handle,
        market=market,
        channel_id=primary_channel_id,
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
        avg_activation_rate=round(avg_activation_rate, 4),
        historical_top_posts=historical_top_posts,
        ranked_posts=ranked_posts,
        breakouts=breakouts,
    )


def _cross_market_matches(
    session: Session,
    pool_a: list[Channel],
    pool_b: list[Channel],
    window_start: datetime,
    window_end: datetime,
) -> list[CrossMarketMatch]:
    """Group assets by ``Asset.de_us_match_key`` across two channel pools.

    Sprint B2 (27.05.2026): die Funktion ist generisch pairwise — sie
    nimmt zwei Channel-Pools (``pool_a``/``pool_b``) und liefert deren
    Schnittmenge auf dem Match-Key. Der Markt-Slot im Output-Schema
    ``CrossMarketMatch`` heisst weiter ``de_*``/``us_*`` (historisch
    bedingt, persistierte Briefe vor B2 wuerden bei einem Rename
    nicht mehr validieren). Beim Aufruf gilt die Konvention: ``pool_a``-
    Daten fuellen den ``de_*``-Slot, ``pool_b``-Daten den ``us_*``-Slot.
    Caller (``_aggregate_platform``) verantwortet die Markt-Reihenfolge
    und ordnet das Ergebnis der passenden Liste zu (``cross_market_matches``
    fuer DE↔US, ``de_uk_matches`` fuer DE↔UK, ``us_uk_matches`` fuer
    US↔UK).

    Sprint 10d-Erbe: die Pools koennen Multi-Channel sein (Disney US
    pooled 5 Cinema-Sub-Brands). Pool-Query via ``Post.channel_id IN
    (...)`` haelt das auf einer Round-Trip pro Pool.

    Match-Key wird in ``services/match_key.py`` waehrend Ingest gesetzt
    und ist inhaltsbasiert (Slug aus Title/Franchise/Placement-Text) —
    Markt-agnostisch, weshalb der Pairwise-Match ueberhaupt funktioniert,
    egal welche zwei Markt-Pools verglichen werden. Leeres Resultat ist
    selbst ein Signal fuer die LLM ("keine Cross-Market-Matches im
    Fenster").
    """
    if not pool_a or not pool_b:
        return []

    pool_a_channel_ids = [c.id for c in pool_a]
    pool_b_channel_ids = [c.id for c in pool_b]

    pool_a_post_ids_stmt = (
        select(Post.id)
        .where(Post.channel_id.in_(pool_a_channel_ids))
        .where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    pool_b_post_ids_stmt = (
        select(Post.id)
        .where(Post.channel_id.in_(pool_b_channel_ids))
        .where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    pool_a_post_ids = list(session.exec(pool_a_post_ids_stmt).all())
    pool_b_post_ids = list(session.exec(pool_b_post_ids_stmt).all())

    # Filter out NULL and the literal "unknown" sentinel — the match-key
    # builder writes "unknown" when neither title nor placement-text yields
    # a useful key, and joining on that bucket would produce spurious
    # cross-market "matches" between unrelated posts.
    _MATCH_KEY_EXCLUDED = {"unknown", ""}

    pool_a_assets = list(
        session.exec(
            select(Asset)
            .where(Asset.post_id.in_(pool_a_post_ids))
            .where(Asset.de_us_match_key.is_not(None))
            .where(sa.func.lower(Asset.de_us_match_key) != "unknown")
        ).all()
    ) if pool_a_post_ids else []
    pool_b_assets = list(
        session.exec(
            select(Asset)
            .where(Asset.post_id.in_(pool_b_post_ids))
            .where(Asset.de_us_match_key.is_not(None))
            .where(sa.func.lower(Asset.de_us_match_key) != "unknown")
        ).all()
    ) if pool_b_post_ids else []

    pool_a_by_key: dict[str, Asset] = {
        a.de_us_match_key: a
        for a in pool_a_assets
        if a.de_us_match_key and a.de_us_match_key.strip().lower() not in _MATCH_KEY_EXCLUDED
    }
    pool_b_by_key: dict[str, Asset] = {
        a.de_us_match_key: a
        for a in pool_b_assets
        if a.de_us_match_key and a.de_us_match_key.strip().lower() not in _MATCH_KEY_EXCLUDED
    }
    shared_keys = sorted(set(pool_a_by_key.keys()) & set(pool_b_by_key.keys()))

    matches: list[CrossMarketMatch] = []
    for key in shared_keys:
        a_asset = pool_a_by_key[key]
        b_asset = pool_b_by_key[key]
        a_post = session.get(Post, a_asset.post_id)
        b_post = session.get(Post, b_asset.post_id)
        title_text: Optional[str] = None
        # Prefer the title row, fall back to placement_title_text on either side
        for a in (a_asset, b_asset):
            if a.title_id:
                t = session.get(Title, a.title_id)
                if t:
                    title_text = t.title_original
                    break
        if not title_text:
            title_text = a_asset.placement_title_text or b_asset.placement_title_text

        matches.append(
            CrossMarketMatch(
                match_key=key,
                title=title_text,
                de_engagement=_engagement_sum(a_post) if a_post else 0,
                us_engagement=_engagement_sum(b_post) if b_post else 0,
                de_duration_seconds=a_post.duration_seconds if a_post else None,
                us_duration_seconds=b_post.duration_seconds if b_post else None,
                de_post_url=a_post.post_url if a_post else None,
                us_post_url=b_post.post_url if b_post else None,
                de_caption_excerpt=_excerpt(a_post.caption) if a_post else None,
                us_caption_excerpt=_excerpt(b_post.caption) if b_post else None,
            )
        )

    # Strongest cross-market signal first
    matches.sort(key=lambda m: m.de_engagement + m.us_engagement, reverse=True)
    return matches


def _title_coverage(
    de_stats: Optional[ChannelStats], us_stats: Optional[ChannelStats],
    uk_stats: Optional[ChannelStats],
    session: Session,
    de_channels: list[Channel], us_channels: list[Channel],
    uk_channels: list[Channel],
    window_start: datetime, window_end: datetime,
) -> TitleCoverage:
    """Compute aggregate coverage + title-overlap across the market pools.

    Sprint 10d: pooled across all channels per market — for Disney US this
    means combined assets from disneystudios + marvelstudios + pixar +
    starwars + 20thcentury(studios). Coverage = pooled_with_title /
    pooled_total per market, no per-channel breakdown.

    Sprint UK-B1 (2026-05-12): drittes Markt-Bucket UK. ``titles_in_both_markets``
    behält die DE∩US-Semantik aus Sprint 1; ``uk_only_titles`` zählt
    Titel, die ausschließlich UK-seitig aufgetaucht sind. Triple-
    Intersection (DE∩UK / US∩UK / DE∩US∩UK) ist B2-Scope. Wenn
    ``uk_channels`` leer ist, bleiben alle UK-Counter auf 0 — kein Crash.
    """
    de_titles: set[str] = set()
    us_titles: set[str] = set()
    uk_titles: set[str] = set()
    de_with_title = 0
    de_total = 0
    us_with_title = 0
    us_total = 0
    uk_with_title = 0
    uk_total = 0

    market_buckets = (
        (de_channels, de_titles, "DE"),
        (us_channels, us_titles, "US"),
        (uk_channels, uk_titles, "UK"),
    )
    for channels, market_titles_set, market_label in market_buckets:
        if not channels:
            continue
        channel_ids = [c.id for c in channels]
        post_ids = list(
            session.exec(
                select(Post.id)
                .where(Post.channel_id.in_(channel_ids))
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
            if market_label == "DE":
                de_total += 1
            elif market_label == "US":
                us_total += 1
            else:
                uk_total += 1
            if a.title_id is not None:
                if market_label == "DE":
                    de_with_title += 1
                elif market_label == "US":
                    us_with_title += 1
                else:
                    uk_with_title += 1
                t = session.get(Title, a.title_id)
                if t:
                    market_titles_set.add(t.title_original)

    both = sorted(de_titles & us_titles)
    de_only = sorted(de_titles - us_titles - uk_titles)
    us_only = sorted(us_titles - de_titles - uk_titles)
    uk_only = sorted(uk_titles - de_titles - us_titles)
    total_assets = de_total + us_total + uk_total
    overall = (
        ((de_with_title + us_with_title + uk_with_title) / total_assets * 100.0)
        if total_assets else 0.0
    )

    return TitleCoverage(
        titles_in_both_markets=both,
        de_only_titles=de_only,
        us_only_titles=us_only,
        de_assets_with_title=de_with_title,
        de_assets_total=de_total,
        us_assets_with_title=us_with_title,
        us_assets_total=us_total,
        uk_only_titles=uk_only,
        uk_assets_with_title=uk_with_title,
        uk_assets_total=uk_total,
        overall_coverage_pct=round(overall, 1),
    )


_PLATFORM_LABELS = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}


def _aggregate_platform(
    session: Session,
    platform: str,
    channel_specs: list[dict],
    window_start: datetime,
    window_end: datetime,
    *,
    window_days: int,
) -> PlatformAggregation:
    """Sprint-4 — aggregate one platform inside a pair.

    Mirrors the old single-platform path: lookup DE + US channels by
    handle, compute ChannelStats, compute cross-market matches and title
    coverage. Pairs that only ship a US channel for a given platform
    (Disney/Prime/Paramount YouTube) leave ``de_channel=None`` — every
    downstream consumer must tolerate that. ``_cross_market_matches``
    naturally returns an empty list when one side is missing.

    The "Datenbasis schwach"-note keeps the < 5-posts gate from Sprint-1
    so the LLM caveat banner still fires — but per platform now, with
    the platform label in the message so a thin IG window can be
    distinguished from a thin TT window.
    """
    label = _PLATFORM_LABELS.get(platform, platform.capitalize())
    # Sprint 10d: ``channel_specs`` may list multiple US (or DE) entries —
    # one per cinema sub-brand for the Disney pair. Group by market and
    # resolve each pool with a single IN-query via _find_channels.
    # Sprint UK-B1: UK als 3. Markt additiv ergänzt; ``uk_specs`` ist
    # leer für Pairs vor B1, dann bleibt ``uk_channel=None`` und kein
    # Code-Pfad ändert sein Verhalten.
    de_specs = [c for c in channel_specs if c["market"] == "DE"]
    us_specs = [c for c in channel_specs if c["market"] == "US"]
    uk_specs = [c for c in channel_specs if c["market"] == "UK"]
    de_handles = [s["handle"] for s in de_specs]
    us_handles = [s["handle"] for s in us_specs]
    uk_handles = [s["handle"] for s in uk_specs]
    de_channels = _find_channels(session, de_handles, platform)
    us_channels = _find_channels(session, us_handles, platform)
    uk_channels = _find_channels(session, uk_handles, platform)

    # Map resolved channels back to handles to surface per-handle gaps.
    de_resolved = {c.handle.lower() for c in de_channels}
    us_resolved = {c.handle.lower() for c in us_channels}
    uk_resolved = {c.handle.lower() for c in uk_channels}

    notes: list[str] = []
    for spec in de_specs:
        if spec["handle"].lower() not in de_resolved:
            notes.append(
                f"DE-Channel @{spec['handle']} ({label}) wurde nicht in der DB gefunden — "
                "Onboarding/Whitelist-Eintrag prüfen."
            )
    for spec in us_specs:
        if spec["handle"].lower() not in us_resolved:
            notes.append(
                f"US-Channel @{spec['handle']} ({label}) wurde nicht in der DB gefunden — "
                "Onboarding/Whitelist-Eintrag prüfen."
            )
    for spec in uk_specs:
        if spec["handle"].lower() not in uk_resolved:
            notes.append(
                f"UK-Channel @{spec['handle']} ({label}) wurde nicht in der DB gefunden — "
                "Onboarding/Whitelist-Eintrag prüfen."
            )

    # Display handle = first spec-listed handle for the market. For
    # single-channel pairs it's the only handle; for the Disney US pool
    # it's "disneystudios" (the lead cinema-master). Stats render
    # "@disneystudios" as the pool's representative marker.
    de_display_handle = de_specs[0]["handle"] if de_specs else ""
    us_display_handle = us_specs[0]["handle"] if us_specs else ""
    uk_display_handle = uk_specs[0]["handle"] if uk_specs else ""

    de_stats = (
        _channel_stats(
            session, de_channels,
            de_display_handle,
            "DE", window_start, window_end, platform=platform,
        ) if de_specs else None
    )
    us_stats = (
        _channel_stats(
            session, us_channels,
            us_display_handle,
            "US", window_start, window_end, platform=platform,
        ) if us_specs else None
    )
    uk_stats = (
        _channel_stats(
            session, uk_channels,
            uk_display_handle,
            "UK", window_start, window_end, platform=platform,
        ) if uk_specs else None
    )
    # Sprint B2 (27.05.2026): drei pairwise Match-Listen statt vorher
    # nur DE↔US. ``cross_market_matches`` haelt weiter den DE↔US-Slot
    # (Backwards-Compat-Name, persistierte Briefe vor B2 validieren
    # gegen das gleiche Feld); die zwei UK-Achsen sind additiv via
    # ``de_uk_matches`` / ``us_uk_matches`` ergaenzt. Strategie (a) aus
    # der Phase-1-Diagnose: drei pairwise Listen, NICHT Triple-Match
    # (verliert DE+UK-ohne-US-Konstellationen) und NICHT der flexible
    # Multi-Markt-Bucket (Schema-Breaking-Change). Doppelung eines
    # DE+US+UK-Triple-Matches in allen drei Listen ist im
    # Praxis-Datenbestand (typisch <10 Matches pro Pair/30d) akzeptabel.
    matches = _cross_market_matches(session, de_channels, us_channels, window_start, window_end)
    de_uk_matches_list = _cross_market_matches(session, de_channels, uk_channels, window_start, window_end)
    us_uk_matches_list = _cross_market_matches(session, us_channels, uk_channels, window_start, window_end)
    coverage = _title_coverage(
        de_stats, us_stats, uk_stats, session,
        de_channels, us_channels, uk_channels,
        window_start, window_end,
    )

    if de_stats and de_stats.posts_count < 5:
        notes.append(
            f"Datenbasis DE schwach ({label}): nur {de_stats.posts_count} Posts "
            f"in den letzten {window_days} Tagen."
        )
    if us_stats and us_stats.posts_count < 5:
        notes.append(
            f"Datenbasis US schwach ({label}): nur {us_stats.posts_count} Posts "
            f"in den letzten {window_days} Tagen."
        )
    if uk_stats and uk_stats.posts_count < 5:
        notes.append(
            f"Datenbasis UK schwach ({label}): nur {uk_stats.posts_count} Posts "
            f"in den letzten {window_days} Tagen."
        )
    if de_channels and us_channels and not matches:
        notes.append(
            f"Im {label}-Fenster gab es keine Posts, die denselben Titel parallel "
            "in DE und US aufgreifen — der Markt-Vergleich stützt sich auf "
            "indirekte Signale (Tonalität, Format-Länge, Hashtags)."
        )

    return PlatformAggregation(
        platform=platform,
        de_channel=de_stats,
        us_channel=us_stats,
        uk_channel=uk_stats,
        cross_market_matches=matches,
        de_uk_matches=de_uk_matches_list,
        us_uk_matches=us_uk_matches_list,
        title_coverage=coverage,
        notes=notes,
    )


def _platforms_dict_for(pair_def: dict) -> dict[str, list[dict]]:
    """Return the ``platforms`` dict for a pair, falling back to a synthetic
    single-platform entry built from the legacy ``platform``/``channels``
    fields. Lets any pair that hasn't been migrated to the new
    structure still aggregate."""
    if "platforms" in pair_def and pair_def["platforms"]:
        return pair_def["platforms"]
    return {pair_def["platform"]: pair_def["channels"]}


def _compute_days_to_release_distribution(
    session: Session,
    pair_def: dict,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, int]:
    """Sprint 29.05.2026 (Stufe-2 PR-B / P1) — Verteilung der Posts ueber
    die ``DaysToReleaseBucket``-Klassen, gepoolt ueber alle Plattformen
    und Channels des Pairs im Window.

    Drei Queries, kein N+1:
    1. Channels: alle Pair-Channels ueber alle Plattformen → ``channel_id
       -> market``-Map.
    2. Posts: alle Pair-Posts im Window mit
       ``published_at OR detected_at``-Fallback wie ``_channel_stats``.
    3. Assets+Titles: pro Post das erste Asset mit ``title_id``, das
       Title-Objekt mit Release-Dates. Asset-Join + Title-Join in einer
       Query.

    Streaming-Pairs (Phase-0-Befund: niedrige Title-Kopplung) bekommen
    erwartungsgemaess hohe ``unknown``-Anteile — das ist Datenrealitaet,
    kein Bug.
    """
    counter: Counter[str] = Counter()

    platforms = _platforms_dict_for(pair_def)
    if not platforms:
        return dict(counter)

    # Bauteil 1: alle Pair-Channels ueber alle Plattformen.
    all_specs: list[tuple[str, str]] = []
    for platform, specs in platforms.items():
        for spec in specs:
            h = spec.get("handle")
            if h:
                all_specs.append((platform, h.lower()))
    if not all_specs:
        return dict(counter)

    handles = [h for _, h in all_specs]
    channel_rows = session.exec(
        select(Channel.id, Channel.handle, Channel.platform, Channel.market)
        .where(sa.func.lower(Channel.handle).in_(handles))
    ).all()
    channel_market_map: dict = {}
    channel_ids: list = []
    for cid, ch_handle, ch_platform, ch_market in channel_rows:
        # ``ch_market`` ist Market-Enum oder str. Auf String normalisieren
        # damit ``_pick_release_date`` einheitlich arbeitet.
        market_str = ch_market.value if hasattr(ch_market, "value") else str(ch_market)
        channel_market_map[cid] = market_str
        channel_ids.append(cid)

    if not channel_ids:
        return dict(counter)

    # Bauteil 2: Posts im Window.
    posts = list(session.exec(
        select(Post).where(
            Post.channel_id.in_(channel_ids)
        ).where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    ).all())
    if not posts:
        return dict(counter)

    # Bauteil 3: Asset + Title pro Post in einer Query. Pro Post das
    # erste Asset mit gesetzem ``title_id`` (Sortierung: ``title_id IS
    # NOT NULL`` zuerst, dann ``created_at`` desc — gleicher Pattern
    # wie ``_ranked_posts_for_channel``).
    post_ids = [p.id for p in posts]
    prefers_title = sa.case((Asset.title_id.isnot(None), 0), else_=1)
    asset_rows = session.exec(
        select(Asset.post_id, Asset.title_id, Title)
        .join(Title, Asset.title_id == Title.id, isouter=True)
        .where(Asset.post_id.in_(post_ids))
        .where(Asset.review_status != "rejected")
        .order_by(Asset.post_id, prefers_title, Asset.created_at.desc())
    ).all()
    # Map: post_id -> Title (das jeweils erste Asset mit title_id pro post)
    title_by_post: dict = {}
    for post_id, title_id, title in asset_rows:
        if post_id in title_by_post:
            continue
        if title_id is None or title is None:
            # erstes Asset hat noch keinen Title; Marker setzen damit
            # spaetere Assets fuer denselben Post nicht ueberschrieben werden,
            # ABER wir wollen tatsaechlich die naechste Row mit title_id
            # nehmen, falls vorhanden. Loesung: NUR mit title gesetzten
            # Rows in die Map schreiben; Posts ohne title bleiben out.
            continue
        title_by_post[post_id] = title

    # Bauteil 4: Klassifizieren + zaehlen.
    for post in posts:
        title = title_by_post.get(post.id)
        if title is None:
            counter[DaysToReleaseBucket.UNKNOWN.value] += 1
            continue
        market = _post_market_for_release_lookup(post, channel_market_map)
        release = _pick_release_date(title, market)
        if release is None:
            counter[DaysToReleaseBucket.UNKNOWN.value] += 1
            continue
        ref_time = _post_age_reference(post)
        if ref_time is None:
            counter[DaysToReleaseBucket.UNKNOWN.value] += 1
            continue
        post_date = ref_time.date() if hasattr(ref_time, "date") else ref_time
        delta_days = (release - post_date).days
        bucket = _classify_days_to_release(delta_days)
        counter[bucket.value] += 1

    return dict(counter)


# ---- Sprint 29.05.2026 (Stufe-2 PR-C / P3) — Recommendation-Cross-Tabs ---


# Wolf-Briefing-Schwelle, hart, nicht verhandelbar.
_RECOMMENDATION_CONFIDENCE_THRESHOLD = 0.7

# Sprint 29.05.2026 (Iteration nach #206-Sicht-Check) — Dedup.
# Bei Cited-Posts-Ueberlappung Jaccard >= 0.8 wird nur ein Baustein
# ausgegeben.
_RECOMMENDATION_JACCARD_DEDUP_THRESHOLD = 0.8

# Cross-Tab-Quelle: in welcher der vier Cross-Tabs hat der Baustein
# entstanden? Ist nicht im Output sichtbar (RecommendedAction kennt
# nur ``dimension``), aber fuer den Tie-Breaker noetig — wir merken
# uns das beim Build-Step via Annotation am internen Tupel.
_CROSS_TAB_SOURCE_ORDER = {
    # Wolf-Briefing-Entscheidung (Default ohne weitere Wolf-Antwort):
    # Bei Sample- und Effect-Size-Gleichstand zwischen format_vocab
    # und duration_bucket gewinnt format_vocab — handlungsleitender
    # fuer den Cutter ("mach mehr Trailer" > "mach laengere Videos").
    # Eindeutig ueber lifecycle vs days_to_release: bucket ist
    # faktischer (Date-Diff), lifecycle ist LLM-klassifiziert →
    # bucket gewinnt bei Gleichstand.
    "format_vocab": 0,
    "lifecycle_stage": 0,
    "duration_bucket": 1,
    "days_to_release_bucket": 1,
}


def _jaccard_index(a: list[str], b: list[str]) -> float:
    """Jaccard-Index ueber zwei Listen — Schnittmenge geteilt durch
    Vereinigungsmenge. Werte 0.0 bis 1.0. Bei zwei leeren Listen
    geben wir 0.0 zurueck (kein Overlap), nicht 1.0 — leere Sets
    ueberlappen sich nicht aussagekraeftig."""
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def _pick_dedup_winner(
    a: RecommendedAction, a_source: str,
    b: RecommendedAction, b_source: str,
) -> tuple[RecommendedAction, str, RecommendedAction, str]:
    """Tie-Breaker-Reihenfolge laut Briefing (in dieser Hierarchie):

    1. Hoehere Sample-Size gewinnt.
    2. Bei Gleichstand: groesserer absoluter Effect-Size gewinnt
       (``|effect_size - 1.0|`` desc).
    3. Bei Gleichstand: spezifischere Dimension gewinnt — Reihenfolge
       via ``_CROSS_TAB_SOURCE_ORDER`` (format_vocab > duration_bucket,
       lifecycle_stage > days_to_release_bucket). Niedrigere
       Order-Zahl = spezifischer = gewinnt.

    Returns ``(winner, winner_source, loser, loser_source)``.
    """
    if a.sample_size != b.sample_size:
        if a.sample_size > b.sample_size:
            return a, a_source, b, b_source
        return b, b_source, a, a_source

    eff_a = abs(a.effect_size - 1.0)
    eff_b = abs(b.effect_size - 1.0)
    if eff_a != eff_b:
        if eff_a > eff_b:
            return a, a_source, b, b_source
        return b, b_source, a, a_source

    rank_a = _CROSS_TAB_SOURCE_ORDER.get(a_source, 99)
    rank_b = _CROSS_TAB_SOURCE_ORDER.get(b_source, 99)
    if rank_a <= rank_b:
        return a, a_source, b, b_source
    return b, b_source, a, a_source


def _dedup_recommendation_candidates(
    recs_with_source: list[tuple[RecommendedAction, str]],
) -> tuple[list[RecommendedAction], list[RecommendedAction]]:
    """Wendet die Jaccard-Dedup-Logik auf eine Liste an. Returns
    ``(winners, suppressed)``.

    Algorithmus: O(n^2) Vergleich — pro Paar Jaccard berechnen.
    Bei N=4 (vier Cross-Tabs mit je hoechstens 4-8 Werten = 16-32
    Bausteine) ist das vertretbar. Wenn N>>100 wird, lohnt sich
    Min-Hash, aber das ist hier nicht der Engpass.

    ``suppressed_by`` wird am Verlierer auf
    ``"{winner.dimension}/{winner.recommended_value}"`` gesetzt — fuer
    den Sicht-Check transparent.
    """
    n = len(recs_with_source)
    if n <= 1:
        return [rec for rec, _ in recs_with_source], []

    suppressed_indices: set[int] = set()
    suppressed_by_map: dict[int, str] = {}
    for i in range(n):
        if i in suppressed_indices:
            continue
        rec_i, src_i = recs_with_source[i]
        for j in range(i + 1, n):
            if j in suppressed_indices:
                continue
            rec_j, src_j = recs_with_source[j]
            jaccard = _jaccard_index(rec_i.cited_post_ids, rec_j.cited_post_ids)
            if jaccard < _RECOMMENDATION_JACCARD_DEDUP_THRESHOLD:
                continue
            winner, _, loser, _ = _pick_dedup_winner(
                rec_i, src_i, rec_j, src_j,
            )
            winner_label = f"{winner.dimension}/{winner.recommended_value}"
            if winner is rec_i:
                suppressed_indices.add(j)
                suppressed_by_map[j] = winner_label
            else:
                suppressed_indices.add(i)
                suppressed_by_map[i] = winner_label
                # i ist jetzt Verlierer — wir haben verglichen, aber
                # weitere j-Vergleiche fuer i sind nicht mehr noetig.
                break

    winners: list[RecommendedAction] = []
    suppressed: list[RecommendedAction] = []
    for idx, (rec, _) in enumerate(recs_with_source):
        if idx in suppressed_indices:
            rec.suppressed_by = suppressed_by_map[idx]
            suppressed.append(rec)
        else:
            winners.append(rec)
    return winners, suppressed

# Cross-Tab-Dimensions (Briefing-Mapping):
# - format_vocab + duration_bucket → dimension="format"
# - lifecycle_stage + days_to_release_bucket → dimension="cadence"
_RECOMMENDATION_DIMENSION_MAP = {
    "format_vocab": "format",
    "duration_bucket": "format",
    "lifecycle_stage": "cadence",
    "days_to_release_bucket": "cadence",
}


def _median(values: list[float]) -> float:
    """Median ueber eine Liste. Bei leerer Liste 0.0 (Caller filtert
    Empty schon ueber Sample-Size-Check)."""
    n = len(values)
    if n == 0:
        return 0.0
    s = sorted(values)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _post_confidence(post: Post) -> Optional[float]:
    """Extrahiert ``analysis.confidence`` als float, robust gegen
    fehlende/ungueltige Werte."""
    analysis = post.analysis
    if not isinstance(analysis, dict):
        return None
    conf = analysis.get("confidence")
    if conf is None:
        return None
    try:
        return float(conf)
    except (TypeError, ValueError):
        return None


def _format_pct(value: float) -> str:
    """Activation als deutsche Prozent-Notation, eine Nachkommastelle."""
    return f"{value * 100:.1f} %".replace(".", ",")


def _build_recommendation_candidates_with_source(
    session: Session,
    pair_def: dict,
    window_end: datetime,
) -> list[tuple[RecommendedAction, str]]:
    """Sprint 29.05.2026 (Stufe-2 PR-C / P3) — Empfehlungs-Bausteine
    pro Pair aus dem 7d-Window, mit Source-Tag fuer Dedup-Tie-Breaker.

    Liefert die Bausteine UNSORTIERT und UNDEDUPLIZIERT — Sort + Dedup
    erfolgt im Caller. So koennen die zwei Public-Wrapper
    (``_compute_recommendation_candidates`` und
    ``_compute_recommendation_pair``) denselben Build-Step teilen ohne
    Duplikat-Code.

    Strikte Ehrlich-Klausel:
    1. Nur Posts mit ``analysis.confidence >= 0.7``.
    2. Pro Cross-Tab-Wert Sample-Size >= 3.
    3. Effect-Size > 1.5x Baseline ODER < 0.5x Baseline.
    4. Baseline = Median activation_rate des Pairs (Pair-spezifisch,
       nicht Channel, nicht Global).

    Vier Dimensions liefern Bausteine:
    - ``format``-Vocab (``post.analysis['format']``)
    - ``duration_bucket`` (aus ``_duration_bucket``)
    - ``lifecycle_stage`` (``post.analysis['lifecycle_stage']``)
    - ``days_to_release_bucket`` (PR-B-Logik, mit Markt-Fallback)

    Wenn ALLE Bausteine durchfallen → leere Liste. Kein Notfall-Eintrag.
    """
    window_start_7d = window_end - timedelta(days=7)

    # Channel-Lookup (gleicher Pattern wie days_to_release).
    platforms = _platforms_dict_for(pair_def)
    if not platforms:
        return []
    all_specs: list[tuple[str, str]] = []
    for platform, specs in platforms.items():
        for spec in specs:
            h = spec.get("handle")
            if h:
                all_specs.append((platform, h.lower()))
    if not all_specs:
        return []
    handles = [h for _, h in all_specs]
    channel_rows = session.exec(
        select(Channel.id, Channel.handle, Channel.platform, Channel.market)
        .where(sa.func.lower(Channel.handle).in_(handles))
    ).all()
    channel_market_map: dict = {}
    channel_platform_map: dict = {}
    channel_ids: list = []
    for cid, ch_handle, ch_platform, ch_market in channel_rows:
        m = ch_market.value if hasattr(ch_market, "value") else str(ch_market)
        channel_market_map[cid] = m
        channel_platform_map[cid] = ch_platform
        channel_ids.append(cid)
    if not channel_ids:
        return []

    # Posts im 7d-Window (gleicher Window-Filter wie _channel_stats).
    posts = list(session.exec(
        select(Post).where(
            Post.channel_id.in_(channel_ids)
        ).where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None),
                        Post.published_at >= window_start_7d,
                        Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None),
                        Post.detected_at >= window_start_7d,
                        Post.detected_at <= window_end),
            )
        )
    ).all())
    if not posts:
        return []

    # Confidence-Filter (>= 0.7).
    qualifying: list[tuple[Post, float, float]] = []  # (post, confidence, activation)
    for post in posts:
        conf = _post_confidence(post)
        if conf is None or conf < _RECOMMENDATION_CONFIDENCE_THRESHOLD:
            continue
        platform = channel_platform_map.get(post.channel_id, "tiktok")
        activation = compute_activation_rate(post, platform)
        qualifying.append((post, conf, activation))
    if len(qualifying) < 3:
        # Pair-Baseline waere nicht stabil schaetzbar; ohne Median
        # keine Effect-Size-Aussage. Ehrlich-Klausel: nichts ausgeben.
        return []

    # Baseline: Pair-Median.
    baseline_median = _median([a for _, _, a in qualifying])

    # Asset-Title-Join fuer days_to_release-Klassifikation (kompakt).
    post_ids = [p.id for p in qualifying_to_ids(qualifying)]
    prefers_title = sa.case((Asset.title_id.isnot(None), 0), else_=1)
    asset_rows = session.exec(
        select(Asset.post_id, Asset.title_id, Title)
        .join(Title, Asset.title_id == Title.id, isouter=True)
        .where(Asset.post_id.in_(post_ids))
        .where(Asset.review_status != "rejected")
        .order_by(Asset.post_id, prefers_title, Asset.created_at.desc())
    ).all()
    title_by_post: dict = {}
    for post_id, title_id, title in asset_rows:
        if post_id in title_by_post:
            continue
        if title_id is None or title is None:
            continue
        title_by_post[post_id] = title

    # Cross-Tab-Sammlung. Pro Dimension ein Dict
    # ``value -> list[(post, conf, activation)]``.
    cross_tabs: dict[str, dict[str, list]] = {
        "format_vocab": defaultdict(list),
        "duration_bucket": defaultdict(list),
        "lifecycle_stage": defaultdict(list),
        "days_to_release_bucket": defaultdict(list),
    }
    for post, conf, activation in qualifying:
        analysis = post.analysis if isinstance(post.analysis, dict) else {}
        format_val = analysis.get("format")
        if isinstance(format_val, str) and format_val:
            cross_tabs["format_vocab"][format_val].append((post, conf, activation))
        cross_tabs["duration_bucket"][_duration_bucket(post.duration_seconds)].append(
            (post, conf, activation)
        )
        lc = analysis.get("lifecycle_stage")
        if isinstance(lc, str) and lc:
            cross_tabs["lifecycle_stage"][lc].append((post, conf, activation))
        market = channel_market_map.get(post.channel_id, "US")
        title = title_by_post.get(post.id)
        if title is None:
            bucket = DaysToReleaseBucket.UNKNOWN
        else:
            release = _pick_release_date(title, market)
            if release is None:
                bucket = DaysToReleaseBucket.UNKNOWN
            else:
                ref = _post_age_reference(post)
                if ref is None:
                    bucket = DaysToReleaseBucket.UNKNOWN
                else:
                    post_date = ref.date() if hasattr(ref, "date") else ref
                    bucket = _classify_days_to_release((release - post_date).days)
        cross_tabs["days_to_release_bucket"][bucket.value].append(
            (post, conf, activation)
        )

    # Filter + RecommendedAction-Build. Wir merken uns pro Eintrag den
    # ``cross_tab_name`` als Source-Tag — der Tie-Breaker im Dedup
    # nutzt das (format_vocab vs duration_bucket etc.).
    out: list[tuple[RecommendedAction, str]] = []
    for cross_tab_name, value_map in cross_tabs.items():
        dim_label = _RECOMMENDATION_DIMENSION_MAP[cross_tab_name]
        for value, entries in value_map.items():
            sample_size = len(entries)
            if sample_size < 3:
                continue
            activations = [a for _, _, a in entries]
            value_median = _median(activations)
            if baseline_median == 0:
                # Kein sinnvoller Effect-Size-Quotient — Baseline ist Null
                # (kann passieren wenn alle Posts views=0 haben). Skippen.
                continue
            ratio = value_median / baseline_median
            if not (ratio > 1.5 or ratio < 0.5):
                continue

            # Top-N (3-5) Posts nach Activation desc als cited_post_ids.
            entries_sorted = sorted(entries, key=lambda e: -e[2])
            top_n = entries_sorted[:5]
            if len(top_n) < 3:
                # Defensive: sollte nicht passieren (sample_size >= 3),
                # aber explizit.
                continue
            cited_ids = [
                p.post_url for p, _, _ in top_n if p.post_url
            ]
            if len(cited_ids) < 3:
                # Posts ohne post_url koennen nicht zitiert werden.
                continue
            conf_avg = sum(c for _, c, _ in top_n) / len(top_n)

            out.append((
                RecommendedAction(
                    dimension=dim_label,
                    recommended_value=value,
                    evidence_metric=f"Activation {_format_pct(value_median)}",
                    evidence_baseline=f"Pair-Median {_format_pct(baseline_median)}",
                    effect_size=round(ratio, 3),
                    cited_post_ids=cited_ids,
                    sample_size=sample_size,
                    confidence_avg=round(conf_avg, 3),
                ),
                cross_tab_name,  # Source-Tag fuer Tie-Breaker
            ))

    return out


def _compute_recommendation_pair(
    session: Session,
    pair_def: dict,
    window_end: datetime,
) -> tuple[list[RecommendedAction], list[RecommendedAction]]:
    """Sprint 29.05.2026 (Iteration nach #206-Sicht-Check) — Hauptpfad
    fuer ``aggregate_pair``. Liefert ``(winners, suppressed)``.

    - ``winners``: deduplizierte Sieger, sortiert nach Dimension +
      |effect_size - 1.0| desc.
    - ``suppressed``: Verworfene mit gesetztem
      ``suppressed_by="dimension/value"``-Tag des jeweiligen Gewinners.
    """
    out_with_source = _build_recommendation_candidates_with_source(
        session, pair_def, window_end,
    )
    # Sortierung VOR der Dedup-Schleife — der Dedup laeuft O(n^2) und
    # ist sortier-unabhaengig, aber das winners-Output muss
    # deterministisch sortiert sein.
    out_with_source.sort(key=lambda pair: (
        pair[0].dimension,
        -abs(pair[0].effect_size - 1.0),
        pair[0].recommended_value,
    ))
    return _dedup_recommendation_candidates(out_with_source)


def _compute_recommendation_candidates(
    session: Session,
    pair_def: dict,
    window_end: datetime,
) -> list[RecommendedAction]:
    """Legacy-Wrapper (Test-Vertrag aus PR-C/#206): gibt nur die
    deduplizierten Sieger zurueck. Neue Caller sollten
    ``_compute_recommendation_pair`` nutzen, um auch die
    ``suppressed``-Liste fuer Debug/Sicht-Check zu bekommen.
    """
    winners, _ = _compute_recommendation_pair(session, pair_def, window_end)
    return winners


def qualifying_to_ids(qualifying: list[tuple]) -> list:
    """Schmaler Helper: liefert die Post-Objekte aus dem
    ``qualifying``-Tupel-List heraus. Vermeidet doppelte Iteration."""
    return [p for p, _, _ in qualifying]


def last_completed_iso_week_anchor(now: Optional[datetime] = None) -> datetime:
    """Return a UTC anchor inside the most recently COMPLETED ISO week.

    The result is always the Sunday that ended the previous ISO week
    (``now - now.isoweekday()`` days), so it lands in week ``KW-1`` on
    *every* weekday — not just Monday. Read paths feed this as ``now`` to
    ``aggregate_pair`` / ``generate_and_persist_report`` so the detail page
    shows the last completed week (the one the Monday cron persisted),
    instead of the in-progress current week.

    Note on the Monday cron (``cron.py``): it uses ``now - 1 day`` and runs
    on Mondays, where ``isoweekday() == 1`` makes this helper compute the
    exact same anchor — so the cron could adopt this helper without any
    behaviour change (left unchanged in this sprint by design).
    """
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=now.isoweekday())


def aggregate_pair(
    session: Session, pair_key: str, window_days: int = 30, *, now: Optional[datetime] = None
) -> PairAggregation:
    """Build the deterministic pair aggregation for a window ending at ``now``.

    Sprint-4: iterates over ``pair_def["platforms"]`` and produces one
    PlatformAggregation per platform. The legacy fields (``platform``,
    ``de_channel``, ``us_channel``, ``cross_market_matches``,
    ``title_coverage``) mirror the first platform — TikTok by convention
    in the current PAIRS layout — so the LLM ``_build_user_prompt`` and
    the Frontend backwards-compat render path keep working unchanged.

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

    platforms = _platforms_dict_for(pair_def)
    per_platform: list[PlatformAggregation] = [
        _aggregate_platform(
            session, platform, specs, window_start, window_end,
            window_days=window_days,
        )
        for platform, specs in platforms.items()
    ]

    # Backwards-Compat mirror: the legacy fields reflect the first platform
    # (= TikTok in the current PAIRS layout). Same data, different shape —
    # downstream consumers that haven't been multi-platform-aware yet
    # (LLM user prompt, old Frontend render path) keep their existing
    # access pattern.
    first = per_platform[0] if per_platform else None
    notes = [n for p in per_platform for n in p.notes]

    # Sprint 29.05.2026 (Stufe-2 PR-B / P1) — days_to_release-Distribution.
    # Pool ueber alle Pair-Channels aller Plattformen, drei Queries
    # (Channels + Posts + Asset-Title-Join). Kein N+1.
    days_to_release_distribution = _compute_days_to_release_distribution(
        session, pair_def, window_start, window_end,
    )

    # Sprint 29.05.2026 (Stufe-2 PR-C / P3) — Recommendation-Bausteine
    # aus vier Cross-Tabs ueber dem 7d-Window. Ehrlich-Klausel ist hart:
    # leere Liste, wenn nichts den Confidence-/Sample-Size-/Effect-Size-
    # Filter passiert. Iteration nach #206-Sicht-Check: Dedup via
    # Jaccard >= 0.8 + Verworfene-Liste fuer Debug/Sicht-Check.
    recommendation_winners, recommendation_suppressed = _compute_recommendation_pair(
        session, pair_def, window_end,
    )

    return PairAggregation(
        pair_key=pair_key,
        pair_label=pair_def["label"],
        platform=first.platform if first else pair_def.get("platform", ""),
        window_days=window_days,
        window_start=window_start,
        window_end=window_end,
        iso_week=iso_week,
        iso_year=iso_year,
        de_channel=first.de_channel if first else None,
        us_channel=first.us_channel if first else None,
        uk_channel=first.uk_channel if first else None,
        cross_market_matches=first.cross_market_matches if first else [],
        de_uk_matches=first.de_uk_matches if first else [],
        us_uk_matches=first.us_uk_matches if first else [],
        title_coverage=first.title_coverage if first else _empty_title_coverage(),
        notes=notes,
        per_platform=per_platform,
        days_to_release_distribution=days_to_release_distribution,
        recommendation_candidates=recommendation_winners,
        recommendation_suppressed=recommendation_suppressed,
    )


BREAKOUT_FEED_DEFAULT_LIMIT = 20
BREAKOUT_FEED_MIN_MULTIPLIER = 2.0


def compute_breakout_feed(
    session: Session,
    *,
    window_days: int = 30,
    now: Optional[datetime] = None,
    limit: int = BREAKOUT_FEED_DEFAULT_LIMIT,
    min_multiplier: float = BREAKOUT_FEED_MIN_MULTIPLIER,
) -> list[dict]:
    """Platin 4 — Pair-übergreifender Breakout-Feed fürs Admin Ops-Dashboard.

    Wiederverwendet ``aggregate_pair`` (rein DB-basiert, KEIN LLM-Call) und
    sammelt die darin bereits berechneten ``ChannelStats.breakouts``
    (Sprint 28.05.2026, Punkt 4 — Z-Score gegen die Channel-Baseline,
    Recency-decayed) über ALLE aktivierten Pairs/Plattformen/Märkte ein.

    Die per-Channel-``breakouts``-Liste selbst hat keine Mindestschwelle
    (Top-3 nach ``weighted_score``, auch wenn keiner davon wirklich
    heraussticht) — für einen pair-übergreifenden Feed wollen wir nur
    echte Ausreisser, daher der zusätzliche ``multiplier >= min_multiplier``
    Filter (Default 2x Kanal-Schnitt).

    Rein lesend, keine neuen Kosten (kein Anthropic-/OpenAI-Call) — kann
    beliebig oft aufgerufen werden, z.B. für Dashboard-Polling. Ein
    Aggregations-Fehler bei einem einzelnen Pair (Daten-Edge-Case) wird
    geloggt und übersprungen, statt den ganzen Feed zu leeren — dieselbe
    Isolations-Konvention wie beim ``pair=all``-Regenerate-Loop.
    """
    window_end = now or datetime.now(timezone.utc)
    entries: list[dict] = []
    for pair_key, pair_def in PAIRS.items():
        if not pair_def.get("enabled", False):
            continue
        try:
            agg = aggregate_pair(session, pair_key, window_days=window_days, now=window_end)
        except Exception:
            logger.exception("breakout_feed.aggregate_pair_failed pair=%s", pair_key)
            continue
        for platform_agg in agg.per_platform:
            for market, channel in (
                ("DE", platform_agg.de_channel),
                ("US", platform_agg.us_channel),
                ("UK", platform_agg.uk_channel),
            ):
                if channel is None:
                    continue
                for post in channel.breakouts:
                    score = post.breakout_score
                    if score is None or score.multiplier < min_multiplier:
                        continue
                    entries.append({
                        "pair_key": pair_key,
                        "pair_label": pair_def.get("label", pair_key),
                        "platform": platform_agg.platform,
                        "market": market,
                        "post_url": post.post_url,
                        "caption_excerpt": post.caption_excerpt,
                        "views": post.views,
                        "engagement_sum": post.engagement_sum,
                        "published_at": post.published_at.isoformat() if post.published_at else None,
                        "multiplier": round(score.multiplier, 2),
                        "weighted_score": round(score.weighted_score, 3),
                        "z_score": round(score.z_score, 2),
                    })

    entries.sort(key=lambda e: (-e["weighted_score"], -(e["views"] or 0), e["post_url"] or ""))
    return entries[:limit]


def _empty_title_coverage() -> TitleCoverage:
    """Zero-valued TitleCoverage. Used as the fallback when a pair has no
    platforms configured at all (defensive — every enabled pair has at
    least one platform after Sprint-4)."""
    return TitleCoverage(
        titles_in_both_markets=[],
        de_only_titles=[],
        us_only_titles=[],
        de_assets_with_title=0,
        de_assets_total=0,
        us_assets_with_title=0,
        us_assets_total=0,
        uk_only_titles=[],
        uk_assets_with_title=0,
        uk_assets_total=0,
        overall_coverage_pct=0.0,
    )


# ---------- LLM call --------------------------------------------------------


def _format_ranked_post_line(idx: int, p: RankedPost) -> str:
    """Sprint 6 — kompakte Top-Posts-Zeile pro Plattform mit
    ``[*Filmtitel*]``-Marker, wenn ``title_local`` gesetzt ist.

    Format: ``  i. Xk views, Yk likes, Z.Z% akt., {duration}s [*Titel*]``
    Caption-Auszug folgt eingerückt darunter (max 80 Zeichen).

    Sprint 10i — wenn ``content_type == 'Series'``, hängt der Marker
    ``— Serie`` an: ``[*Title* — Serie]``. Damit kann das LLM in
    Headline/TLDR/aktuell_im_fokus Streaming-Series natürlich von
    Theatrical-Releases trennen, ohne dass die Schema-Form sich ändert.
    Films bleiben unmarkiert (Default-Layout)."""
    views = int(p.views or 0)
    likes = int(p.likes or 0)
    akt_pct = (p.activation_rate or 0.0) * 100
    duration = f", {p.duration_seconds}s" if p.duration_seconds else ""
    if p.title_local:
        if p.content_type == "Series":
            title_marker = f" [*{p.title_local}* — Serie]"
        else:
            title_marker = f" [*{p.title_local}*]"
    else:
        title_marker = ""
    line = (
        f"  {idx}. {views:,} views, {likes:,} likes, "
        f"{akt_pct:.1f}% akt.{duration}{title_marker}"
    )
    if p.caption_excerpt:
        excerpt = p.caption_excerpt.strip()
        if len(excerpt) > 80:
            excerpt = excerpt[:80].rstrip() + "…"
        line += f"\n     \"{excerpt}\""
    return line


def _format_channel_section(market: str, stats: ChannelStats, platform: str) -> str:
    """Sprint 6 — kompakte Channel-Sektion: Header mit aggregierten
    Kennzahlen, dann Top-5 Ranked Posts (limitiert für Token-Budget),
    dann Top-Hashtags inline. Historische Top-Posts wandern in den
    JSON-Anhang (siehe ``_build_user_prompt``), damit
    ``vergleichbare_posts`` darauf zurückgreifen kann."""
    avg_eng = float(stats.avg_engagement or 0.0)
    avg_act_pct = (stats.avg_activation_rate or 0.0) * 100
    coverage = stats.coverage_pct
    lines = [
        f"### {market}: @{stats.handle} — {stats.posts_count} Posts, "
        f"avg engagement {avg_eng:.0f}, avg activation {avg_act_pct:.1f}%, "
        f"coverage {coverage:.0f}%",
    ]
    ranked = stats.ranked_posts[:5]
    if ranked:
        lines.append("Top Posts:")
        for i, p in enumerate(ranked, 1):
            lines.append(_format_ranked_post_line(i, p))
    if stats.top_hashtags:
        tags = ", ".join(f"#{h.tag} ({h.count})" for h in stats.top_hashtags[:5])
        lines.append(f"Top-Hashtags: {tags}")
    return "\n".join(lines)


def _format_cross_market_block(matches: list[CrossMarketMatch]) -> str:
    """Sprint 6 — Cross-Market-Matches kompakt unter dem jeweiligen
    Plattform-Block. Maximum 5 Einträge, fehlende Engagement-Werte fallen
    auf 0 zurück."""
    if not matches:
        return ""
    lines = ["Cross-Market Matches:"]
    for m in matches[:5]:
        title = m.title or "[ohne Titel]"
        lines.append(
            f"  - {title}: DE {m.de_engagement} vs. US {m.us_engagement}"
        )
    return "\n".join(lines)


_PLATFORM_HEADER_LABEL = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}


# ---- Anti-Repetition: Vorgaenger-Brief-Kontext (Sprint 17.05.2026) -------
# Reduziert Headline-Repetition zwischen aufeinanderfolgenden Briefs
# desselben Pairs. Hintergrund: das 30-Tage-Rolling-Window haelt dominante
# Top-Posts (Mandalorian-Kampagne, Muttertag etc.) mehrere Briefs lang an
# der Performance-Spitze. Anthropic schreibt aktuell jeden Brief from
# scratch und reproduziert das Frame der Vorwoche fast 1:1. Mit dem
# Previous-Context-Block bekommt der LLM (a) die Vorgaenger-Headline und
# (b) einen Set-Diff der Top-Post-Identitaeten als "Anker, wovon du dich
# absetzen sollst". Out-of-Scope laut Sprint-Briefing: Persona-Voice (SYSTEM_
# PROMPT), Brief-Body-Sektionen ausserhalb Headline/TL;DR, Schema-Aenderungen.


def _extract_ranked_post_identities(agg: PairAggregation) -> dict[str, str]:
    """Walkt eine ``PairAggregation`` ueber alle Plattformen und Channels
    und liefert einen ordered ``{identity: caption_excerpt}``-Map zurueck.
    Identitaet ist ``RankedPost.asset_id`` (Sprint 5c, Asset-UUID) mit
    ``post_url`` als Fallback fuer Pre-Sprint-5c-Briefe. Posts ohne beides
    werden ausgelassen — sie sind ueber zwei Briefs nicht korrelierbar.

    Insertion-Order bleibt erhalten (Python-Dict-Garantie seit 3.7), damit
    die Beispiel-Auswahl in ``_compute_top_post_diff`` deterministisch wird:
    erste 3 Carried/New aus der natuerlichen Top-Post-Sortierung des
    aktuellen Briefs.
    """
    out: dict[str, str] = {}
    for pa in (agg.per_platform or []):
        for channel in (pa.de_channel, pa.us_channel, pa.uk_channel):
            if channel is None:
                continue
            for post in (channel.ranked_posts or []):
                identity = post.asset_id or post.post_url
                if not identity:
                    continue
                if identity in out:
                    continue
                out[identity] = post.caption_excerpt or ""
    return out


def _compute_top_post_diff(
    current_agg: PairAggregation,
    previous_agg: PairAggregation,
    *,
    max_examples: int = 3,
) -> dict:
    """Set-Diff der Top-Post-Identitaeten zwischen aktuellem und vorherigem
    Brief. Beispiele werden aus dem CURRENT-Map gezogen (insertion-ordered),
    weil das die natuerliche Top-Post-Reihenfolge dieses Briefs spiegelt
    — wir wollen die wichtigsten Carried/New oben, nicht alphabetisch."""
    current = _extract_ranked_post_identities(current_agg)
    previous = _extract_ranked_post_identities(previous_agg)
    current_ids = set(current.keys())
    previous_ids = set(previous.keys())
    carried = current_ids & previous_ids
    new = current_ids - previous_ids
    dropped = previous_ids - current_ids
    examples_carried: list[str] = []
    examples_new: list[str] = []
    for ident in current:
        if ident in carried and len(examples_carried) < max_examples:
            label = _excerpt(current[ident], max_len=60)
            if label:
                examples_carried.append(label)
        elif ident in new and len(examples_new) < max_examples:
            label = _excerpt(current[ident], max_len=60)
            if label:
                examples_new.append(label)
    return {
        "carried_count": len(carried),
        "new_count": len(new),
        "dropped_count": len(dropped),
        "examples_carried": examples_carried,
        "examples_new": examples_new,
    }


def _format_example_suffix(samples: list[str]) -> str:
    if not samples:
        return ""
    quoted = ", ".join(f"„{s}\"" for s in samples)
    return f" (z.B. {quoted})"


def _format_previous_context_block(
    *,
    prev_iso_year: int,
    prev_iso_week: int,
    prev_headline: str,
    diff: dict,
) -> str:
    """Rendert den Anti-Repetition-Block fuer den User-Prompt.
    Wolf-Ping-#1-Final-Wortlaut (Sprint 17.05.2026): "benenne die zeitliche
    Entwicklung" statt "finde", erweiterte Beispielliste (Release,
    Kampagnen-Phasenwechsel, Mechanik-Shift, Plattform-Asymmetrie).

    Wenn weder Carried noch New Posts existieren, wird der gesamte
    "Top-Post-Bewegung"-Mid-Block weggelassen — der Diff hat nichts
    Bewegungs-Aussagefaehiges zu zeigen, die Headline-Anweisung bleibt
    trotzdem stehen, weil die Vorgaenger-Headline allein schon ein
    Frame-Anker ist."""
    lines = [
        f"## Bezug zur Vorwoche (KW {prev_iso_week}/{prev_iso_year})",
        "",
        "Vorgänger-Headline:",
        f"> {prev_headline}",
        "",
    ]
    has_movement = diff["carried_count"] > 0 or diff["new_count"] > 0
    if has_movement:
        lines.extend([
            "Top-Post-Bewegung gegenüber dem Vorgänger (Identität via Asset-ID,",
            "Beispiele in Klammern):",
            "",
        ])
        if diff["carried_count"] > 0:
            suffix = _format_example_suffix(diff["examples_carried"])
            lines.append(f"- {diff['carried_count']} Posts übernommen{suffix}")
        if diff["new_count"] > 0:
            suffix = _format_example_suffix(diff["examples_new"])
            lines.append(f"- {diff['new_count']} neu{suffix}")
        if diff["dropped_count"] > 0:
            lines.append(
                f"- {diff['dropped_count']} aus dem Vorgänger nicht mehr unter den Top-Performern"
            )
        lines.append("")
    lines.extend([
        "Für Headline und TL;DR:",
        "",
        "Wiederhole nicht das narrative Frame des Vorgängers. Wenn dieselben",
        "Posts weiter oben stehen, benenne die zeitliche Entwicklung — Annäherung",
        "an einen Release, Phasenwechsel der Kampagne, Mechanik-Shift im Content",
        "(Reichweite → Engagement, statisch → bewegt), neue Plattform-Asymmetrie.",
        "„Dieselbe Welle trägt weiter\" ist als Beobachtung okay; dieselbe",
        "Verpackung wie letzte Woche nicht. Die Detail-Sektionen dürfen sich",
        "wiederholen, wo die Daten es erzwingen — nur Headline und TL;DR brauchen",
        "narrative Bewegung.",
    ])
    return "\n".join(lines)


def _load_previous_brief(
    session: Session,
    pair_key: str,
    iso_year: int,
    iso_week: int,
) -> Optional[InsightReportRow]:
    """Anti-Repetition-Sprint (17.05.2026): laedt den naechstaelteren Brief
    desselben Pairs. Strikt ``< (iso_year, iso_week)`` — bei einem
    ``replace=True``-Aufruf auf der gleichen KW findet die Query NICHT
    die zu ueberschreibende Row selbst, sondern den echten Vorgaenger.
    Returns None, wenn kein Vorgaenger existiert (erster Brief des Pairs).
    """
    stmt = (
        select(InsightReportRow)
        .where(InsightReportRow.pair_key == pair_key)
        .where(
            (InsightReportRow.iso_year < iso_year)
            | (
                (InsightReportRow.iso_year == iso_year)
                & (InsightReportRow.iso_week < iso_week)
            )
        )
        .order_by(
            InsightReportRow.iso_year.desc(),
            InsightReportRow.iso_week.desc(),
        )
    )
    return session.exec(stmt).first()


def _build_user_prompt(
    agg: PairAggregation,
    *,
    previous_context: Optional[str] = None,
) -> str:
    """Sprint 6 — strukturierter Multi-Plattform-Datenblock plus JSON-Anhang.

    Aufbau:

      1. Framing (Pair, KW, Modus-Hinweis "ganz genau", Verweis auf Voice/
         Anti-Pattern aus dem System-Prompt).
      2. Pro Plattform (in der Reihenfolge ``per_platform``) ein
         ``## TikTok`` / ``## Instagram`` / ``## YouTube``-Block mit den
         vorhandenen DE/US-Channel-Sektionen. **Komplett leere Plattformen
         (weder DE noch US, kein Match) werden ausgelassen** — das spart
         Tokens und vermeidet "Keine Daten"-Filler im Prompt.
      3. Top-5 Ranked Posts pro Channel (statt Top-10 wie in Sprint 1-5)
         mit ``[*Filmtitel*]``-Marker, wenn ``title_local`` gesetzt ist.
         Token-Budget bleibt bei Multi-Plattform-Pairs unter 12k input.
      4. JSON-Datenanhang: vollständige ``PairAggregation`` als
         strukturierter Backup-Quell für die Detail-Sektionen. Headline/
         TLDR scannen den Markdown-Teil oben, Detail-Sektionen holen sich
         ``historical_top_posts`` etc. aus dem JSON.

    Sprint 1 hat hier nur den JSON-Dump abgelegt; Sprint 4 hat
    ``per_platform`` ergänzt, aber der LLM musste den Block in der
    JSON-Struktur "finden". Sprint 6 hebt das vor — Multi-Plattform-
    Asymmetrien sind so direkt scannbar und das Few-Shot kann Plattform-
    Headern als Anker referenzieren.
    """
    framing = (
        f"Generiere den ausführlichen Wochenreport für {agg.pair_label}, "
        f"KW {agg.iso_week}/{agg.iso_year}, Datenfenster {agg.window_days} Tage "
        f"({agg.window_start.date().isoformat()} bis {agg.window_end.date().isoformat()}).\n\n"
        "Modus: 'ganz genau' — gib alle Sektionen vollständig aus, ca. 1500-2000 "
        "Wörter Gesamtoutput. Halte dich an den Berichtston und die Verbotslisten aus "
        "dem System-Prompt. Plattform-Vergleich ist erlaubt, wenn er sichtbar "
        "trägt — siehe Multi-Plattform-Klausel im System-Prompt. Filmtitel "
        "(in den Top-Posts in eckigen Klammern + Sternchen markiert) darfst "
        "du in Headline/TLDR mit Sternchen-Markup nutzen, wenn vorhanden — "
        "siehe Filmtitel-Klausel.\n\n"
        # Ton-Pass: sachlich-berichtender Reminder direkt im User-Prompt,
        # direkt vor den Daten — greift erfahrungsgemäß stärker als die
        # BERICHTSTON-Sektion 1500 Tokens weiter oben.
        "Erinnerung zum Ton: berichte sachlich und neutral, in ganzen Sätzen. "
        "Schreibe Zahlen aus (zum Beispiel 33.000, nicht 33k) und vermeide "
        "Szene-Jargon. Keine Wertungsformeln, keine Doppel-Beziffung in einem "
        "Atemzug, keine Compliance-Listen.\n\n"
        "Daten pro Plattform folgen. Komplett leere Plattformen sind ausgelassen.\n"
    )

    sections: list[str] = [framing]

    # Anti-Repetition (Sprint 17.05.2026): Vorgaenger-Kontext direkt nach
    # dem Framing, vor den Plattform-Datenbloecken. Die Headline-Generierung
    # scannt den Markdown-Teil oben (siehe Framing-Block), also greift der
    # Anti-Repetition-Anker upfront, bevor Anthropic die Daten liest. Bei
    # erstem Brief eines Pairs oder bei fehlender Vorgaenger-Headline ist
    # ``previous_context`` ``None`` und der Block wird ausgelassen.
    if previous_context:
        sections.append(previous_context)

    per_platform = agg.per_platform or []
    for platform_agg in per_platform:
        platform = platform_agg.platform
        de = platform_agg.de_channel
        us = platform_agg.us_channel
        uk = platform_agg.uk_channel
        cross_matches = platform_agg.cross_market_matches or []

        de_has_data = bool(de and de.posts_count)
        us_has_data = bool(us and us.posts_count)
        # Sprint UK-B1: UK als 3. Markt-Sektion. ``_format_channel_section``
        # nimmt das Markt-Kürzel als String-Parameter, daher kein Funktions-
        # Edit nötig — der Markdown-Header "### UK: @<handle>" entsteht
        # automatisch. Cross-Market mit UK ist B2-Scope, daher hier bewusst
        # keine UK-Cross-Matches.
        uk_has_data = bool(uk and uk.posts_count)
        if not de_has_data and not us_has_data and not uk_has_data and not cross_matches:
            # Plattform komplett leer (z. B. YT-DE bei Disney/Prime/Paramount,
            # ohne dass irgendeine Seite Posts oder Matches hätte) — auslassen.
            continue

        label = _PLATFORM_HEADER_LABEL.get(platform, platform.title())
        block = [f"## {label}"]
        if de_has_data:
            block.append(_format_channel_section("DE", de, platform))
        if us_has_data:
            block.append(_format_channel_section("US", us, platform))
        if uk_has_data:
            block.append(_format_channel_section("UK", uk, platform))
        cross_text = _format_cross_market_block(cross_matches)
        if cross_text:
            block.append(cross_text)
        sections.append("\n".join(block))

    if agg.notes:
        sections.append("## Notes\n" + "\n".join(f"- {n}" for n in agg.notes))

    # JSON-Anhang behält die volle Struktur — Detail-Sektionen
    # (``vergleichbare_posts``, ``ganz_konkret``) referenzieren
    # ``historical_top_posts``, ``title_coverage`` und weitere Aggregat-
    # Felder, die im Markdown-Overview bewusst fehlen, um den
    # Headline/TLDR-Scan kompakt zu halten.
    payload = agg.model_dump(mode="json")
    sections.append(
        "## Vollständiger Datenanhang (JSON)\n"
        "Detail-Sektionen (vergleichbare_posts, ganz_konkret, fuer_cutter) "
        "stützen sich auf diesen Block, insbesondere die "
        "``historical_top_posts``-Listen pro Channel.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )

    return "\n\n".join(sections)


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


def _try_parse_llm_json(
    raw_text: str,
) -> tuple[Optional[Any], Optional[json.JSONDecodeError], str]:
    """Sprint M2 lenient JSON parsing — single source of truth for the
    LLM-response parse step.

    Returns ``(parsed, error, parse_path)``:
    - ``parsed`` is the decoded Python object on success, else ``None``.
    - ``error`` is the *last* JSONDecodeError seen, retained so the caller
      can surface ``.pos`` in the final diagnostic log on total failure.
    - ``parse_path`` is ``"strict"`` (codefence-strip + json.loads worked),
      ``"lenient"`` (substring between first ``{`` and last ``}`` was
      required to parse), or ``""`` when nothing parsed.

    Variants tried in order:
    1. Strict: ``_strip_codefence`` then ``json.loads`` — covers (a)
       Markdown-Fences from prior behaviour.
    2. Lenient: extract the substring from the first ``{`` to the last
       ``}`` and re-parse — covers (b) preamble/postamble around an
       otherwise-valid object. The double extraction is cheap; we still
       fall through to the caller's re-call loop for (d)-style
       mid-document syntax errors that no string surgery can repair.
    """
    cleaned = _strip_codefence(raw_text)
    try:
        return json.loads(cleaned), None, "strict"
    except json.JSONDecodeError as strict_exc:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last > first:
            substring = cleaned[first : last + 1]
            if substring != cleaned:
                try:
                    return json.loads(substring), None, "lenient"
                except json.JSONDecodeError:
                    # Fall through with the strict error — its ``.pos`` is
                    # measured against ``cleaned`` (what we log), which is
                    # the most useful diagnostic for downstream analysis.
                    pass
        return None, strict_exc, ""


def _build_citation_allow_set(agg: PairAggregation) -> set[str]:
    """Sprint 28.05.2026 (Evidenz-Block) — sammelt alle zitier-faehigen
    IDs aus der ``PairAggregation`` zu einem Set, gegen das der
    Citation-Validator die ``cited_post_ids``-Felder der LLM-Antwort
    prueft.

    Erlaubte ID-Quellen (entspricht der EVIDENZ-PFLICHT im System-
    Prompt):
    - ``post_url`` aus ``top_posts`` / ``ranked_posts`` /
      ``historical_top_posts`` jedes ``ChannelStats`` (DE/US/UK,
      legacy top-level + per_platform).
    - ``asset_id`` aus ``ranked_posts`` (Sprint 5c).
    - ``match_key`` aus ``cross_market_matches`` /
      ``de_uk_matches`` / ``us_uk_matches`` (B2).

    Equality-Matching ohne URL-Normalisierung — das LLM sieht im
    JSON-Anhang exakt die Strings, die wir hier ins Allow-Set
    aufnehmen (gleiche ``model_dump``-Quelle wie der Prompt-Bau in
    ``_build_user_prompt``). Wenn Phase-1-Telemetrie zeigt, dass die
    Equality-Annahme nicht traegt (z. B. wegen Trailing-Slashes oder
    Schema-Drift), justieren wir das Matching hier — der Validator
    bleibt im Soft-Mode unkritisch.
    """
    allow: set[str] = set()

    def _collect_channel(channel: Optional[ChannelStats]) -> None:
        if channel is None:
            return
        for tp in channel.top_posts:
            if tp.post_url:
                allow.add(tp.post_url)
        for tp in channel.historical_top_posts:
            if tp.post_url:
                allow.add(tp.post_url)
        for rp in channel.ranked_posts:
            if rp.post_url:
                allow.add(rp.post_url)
            if rp.asset_id:
                allow.add(rp.asset_id)

    def _collect_matches(matches: list[CrossMarketMatch]) -> None:
        for m in matches:
            if m.match_key:
                allow.add(m.match_key)
            if m.de_post_url:
                allow.add(m.de_post_url)
            if m.us_post_url:
                allow.add(m.us_post_url)

    # Legacy top-level Channels — vor Sprint 4 die einzige Quelle, heute
    # Mirror der per_platform[0]. Beide einsammeln ist idempotent (Set).
    _collect_channel(agg.de_channel)
    _collect_channel(agg.us_channel)
    _collect_channel(agg.uk_channel)
    _collect_matches(agg.cross_market_matches)
    _collect_matches(agg.de_uk_matches)
    _collect_matches(agg.us_uk_matches)

    for platform_agg in agg.per_platform or []:
        _collect_channel(platform_agg.de_channel)
        _collect_channel(platform_agg.us_channel)
        _collect_channel(platform_agg.uk_channel)
        _collect_matches(platform_agg.cross_market_matches)
        _collect_matches(platform_agg.de_uk_matches)
        _collect_matches(platform_agg.us_uk_matches)

    return allow


def _collect_cited_ids(report: LLMReport) -> list[tuple[str, list[str]]]:
    """Liefert eine Liste von ``(section_path, cited_ids)``-Paaren ueber
    alle Narrativ-Sektionen mit ``cited_post_ids``-Feld. ``section_path``
    ist der Punkt-Notations-Pfad ins JSON (z.B.
    ``"trends[0].cited_post_ids"``) — landet so im Log und macht die
    Phase-1-Telemetrie auswertbar.

    Optional-Sektionen (``aktuell_im_fokus``, ``ganz_konkret``) werden
    ausgelassen, wenn sie ``None`` sind — sie sind in Backwards-Compat-
    Modus seit Sprint-Trailerhaus-Prompt-v1 nullable.
    """
    sections: list[tuple[str, list[str]]] = []
    for i, trend in enumerate(report.trends or []):
        sections.append((f"trends[{i}].cited_post_ids", trend.cited_post_ids))
    for i, action in enumerate(report.actions or []):
        sections.append((f"actions[{i}].cited_post_ids", action.cited_post_ids))
    for i, fokus in enumerate(report.aktuell_im_fokus or []):
        sections.append(
            (f"aktuell_im_fokus[{i}].cited_post_ids", fokus.cited_post_ids)
        )
    for i, schnitt in enumerate(report.ganz_konkret or []):
        sections.append(
            (f"ganz_konkret[{i}].cited_post_ids", schnitt.cited_post_ids)
        )
    sections.append(
        (
            "cross_market_insight.cited_post_ids",
            report.cross_market_insight.cited_post_ids,
        )
    )
    return sections


def _validate_citations(
    report: LLMReport,
    agg: PairAggregation,
    *,
    pair_key: str,
    iso_year: int,
    iso_week: int,
) -> bool:
    """Sprint 28.05.2026 (Stufenmodell B→A) — prueft die
    ``cited_post_ids``-Felder der LLM-Antwort gegen das Allow-Set aus
    ``PairAggregation``.

    Returns ``True`` wenn alle zitierten IDs belegbar sind, sonst
    ``False``. Phase 1 (Default,
    ``settings.insight_citation_strict_enforce=False``): Caller ignoriert
    das Bool und liefert den Brief trotzdem aus — der Log-Event
    ``insight-engine-citation-unverified`` liefert die Telemetrie fuer
    den Cutover-Entscheid. Phase 2 (Strikt, ENV-Flip auf True): Caller
    wirft die Antwort weg und triggert den bestehenden Retry-Loop.

    Strikt-Mode-Caller-Erwartung: ``False`` an der vordersten Stelle des
    ``parsed is not None``-Pfades behandeln (analog zu Schema-
    Validation-Fail), bevor ``llm_output`` gesetzt wird.

    Telemetrie-Detail: pro Sektion mit nicht-belegten IDs ein eigenes
    WARNING-Log mit ``found_ids`` / ``missing_ids`` — so kann Wolf
    auswerten, ob bestimmte Sektionen schlechter zitieren als andere,
    bevor der Strikt-Cutover scharf geschaltet wird.
    """
    allow_set = _build_citation_allow_set(agg)
    cited_sections = _collect_cited_ids(report)

    # Counter ueber alle Sektionen — landet als zusammenfassender
    # INFO-Log am Ende der Validation, sodass Cron-Lauf-Statistiken
    # nicht ueber WARNING-Records aggregieren muessen.
    total_cited = 0
    total_missing = 0
    total_empty_sections = 0
    unverified_sections: list[dict[str, Any]] = []

    for section_path, cited_ids in cited_sections:
        if not cited_ids:
            total_empty_sections += 1
            continue
        total_cited += len(cited_ids)
        missing = [cid for cid in cited_ids if cid not in allow_set]
        if missing:
            total_missing += len(missing)
            unverified_sections.append(
                {
                    "section": section_path,
                    "cited_count": len(cited_ids),
                    "missing_count": len(missing),
                    "missing_ids": missing[:5],  # cap fuer Log-Lesbarkeit
                }
            )

    all_belegt = total_missing == 0

    if unverified_sections:
        for entry in unverified_sections:
            logger.warning(
                "insight-engine-citation-unverified",
                extra={
                    "pair_key": pair_key,
                    "iso_year": iso_year,
                    "iso_week": iso_week,
                    "allow_set_size": len(allow_set),
                    **entry,
                },
            )

    logger.info(
        "insight-engine-citation-summary",
        extra={
            "pair_key": pair_key,
            "iso_year": iso_year,
            "iso_week": iso_week,
            "allow_set_size": len(allow_set),
            "cited_ids_total": total_cited,
            "missing_ids_total": total_missing,
            "sections_empty": total_empty_sections,
            "sections_total": len(cited_sections),
            "all_belegt": all_belegt,
        },
    )

    return all_belegt


def _describe_citation_failures(
    report: LLMReport, agg: PairAggregation
) -> Optional[str]:
    """Diagnose-Helfer (2026-06-22, additiv): liefert eine kompakte
    Beschreibung der gebrochenen Citation-Regeln fuer den
    ``citation_validation_error``-Pfad — welche Sektion welche IDs nicht
    belegen kann. Nur fuer die Diagnose-Telemetrie gedacht; aendert das
    Validierungs-Verhalten von ``_validate_citations`` NICHT (eigene,
    nebenwirkungsfreie Re-Berechnung gegen dasselbe Allow-Set). Wird im
    Strikt-Modus nur auf dem Failure-Pfad aufgerufen (selten), daher ist
    die Doppelberechnung unkritisch. ``None`` wenn nichts gebrochen ist."""
    allow_set = _build_citation_allow_set(agg)
    broken: list[str] = []
    for section_path, cited_ids in _collect_cited_ids(report):
        if not cited_ids:
            continue
        missing = [cid for cid in cited_ids if cid not in allow_set]
        if missing:
            broken.append(
                f"{section_path}: {len(missing)}/{len(cited_ids)} unbelegt "
                f"(z.B. {missing[:3]})"
            )
    if not broken:
        return None
    return "; ".join(broken)[:500]


def _estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    in_rate = settings.anthropic_opus_input_per_1k_usd or 0.0
    out_rate = settings.anthropic_opus_output_per_1k_usd or 0.0
    return round(
        (input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate,
        4,
    )


class _BriefLLMResult(NamedTuple):
    """Result of the shared tool-use LLM call+retry loop (C2). ``llm_output``
    is the schema-validated object (or None on parse/schema/truncation/strict-
    citation failure → caller persist-skips); tokens/cost reflect ALL attempts.

    Diagnose-Instrumentierung (2026-06-22, additiv): ``failure_kind`` /
    ``failure_detail`` klassifizieren den terminalen Fehler, der zu
    ``llm_output is None`` fuehrte, statt ihn im Cron-Sammelfehler
    ``no_llm_output`` zu verlieren. ``failure_kind`` ist genau eines von
    ``json_parse_error`` / ``schema_validation_error`` /
    ``citation_validation_error`` / ``truncation_error`` (oder ``None`` im
    Erfolgsfall). ``failure_detail`` traegt den konkreten Grund (Pydantic-
    Fehlertext, JSONDecodeError + Position, output_token_count + max_tokens
    bei Truncation, bzw. die gebrochenen Citation-Sektionen). Rein additiv —
    aendert kein Verhalten, Default ``None``."""
    llm_output: Optional[Any]
    raw_text: Optional[str]
    input_tokens: int
    output_tokens: int
    cost: Optional[float]
    anthropic_calls: int
    failure_kind: Optional[str] = None
    failure_detail: Optional[str] = None


def _run_brief_llm(
    *,
    system_prompt: str,
    user_prompt: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    validate: Callable[[Any], Any],
    model: str,
    max_tokens: int,
    log_subject: str,
    call_extra: dict,
    record_meta: dict,
    operation: str,
    citation_validate: Optional[Callable[[Any], bool]] = None,
    citation_detail: Optional[Callable[[Any], Optional[str]]] = None,
    strict_citations: bool = False,
    unwrap_expected_field: str = "headline",
) -> _BriefLLMResult:
    """Shared brief generation kernel (C2 — extracted verbatim from the pair
    path so the pair brief and the title brief share ONE call+retry loop, not
    two copies).

    Owns: the forced tool-use Anthropic call, content extraction, the
    JSON-parse retry loop (``MAX_RECALLS``), the truncation guard (#224), the
    single-key unwrap net, schema validation via ``validate``, the
    soft/strict citation check via ``citation_validate``, and per-call cost
    accounting. Returns ``llm_output=None`` on any terminal failure so the
    caller skips persistence. Behaviour is identical to the pre-extraction
    pair path; ``log_subject`` is logged under the ``pair`` key and
    ``record_meta``/``operation`` drive ``record_anthropic_call``.
    """

    def _call_and_extract(attempt_index: int) -> tuple[Any, str]:
        attempt_extra = {**call_extra, "attempt": attempt_index}
        logger.info("brief_anthropic_call_start", extra=attempt_extra)
        started = time.monotonic()
        try:
            msg = messages_create_strict_json(
                model=model,
                system=system_prompt,
                user_message=user_prompt,
                tool_name=tool_name,
                tool_description=tool_description,
                input_schema=input_schema,
                max_tokens=max_tokens,
            )
        except Exception as call_exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                "brief_anthropic_call_done",
                extra={
                    **attempt_extra,
                    "duration_ms": duration_ms,
                    "outcome": "error",
                    "error_type": type(call_exc).__name__,
                },
            )
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "brief_anthropic_call_done",
            extra={
                **attempt_extra,
                "duration_ms": duration_ms,
                "outcome": "success",
                "stop_reason": getattr(msg, "stop_reason", None),
            },
        )
        text = ""
        try:
            tool_input: Optional[Any] = None
            text_fallback = ""
            for block in msg.content or []:
                block_type = getattr(block, "type", None)
                if block_type == "tool_use" and tool_input is None:
                    tool_input = getattr(block, "input", None)
                elif block_type == "text":
                    text_fallback += getattr(block, "text", "")
            if tool_input is not None:
                text = json.dumps(tool_input, ensure_ascii=False, default=str)
            else:
                text = text_fallback
        except Exception as extract_exc:  # pragma: no cover — defensive
            logger.warning("insight-engine-content-extract-failed: %s", extract_exc)
        return msg, text

    MAX_RECALLS = 2
    call_attempts: list[tuple[Any, str]] = []

    parsed: Optional[Any] = None
    parse_error: Optional[json.JSONDecodeError] = None
    parse_path: str = ""
    llm_output: Optional[Any] = None
    raw_for_response: Optional[str] = None
    schema_validation_failed = False
    citation_strict_failed = False
    truncated_failed = False
    raw_text = ""
    # Diagnose-Instrumentierung (2026-06-22, additiv): konkreten Grund am
    # Failure-Punkt einfangen, wo die Werte lokal vorliegen — die spaetere
    # Klassifikation nach der Schleife liest sie aus.
    schema_error_detail: Optional[str] = None
    citation_failure_detail: Optional[str] = None

    for attempt_n in range(MAX_RECALLS + 1):
        if attempt_n > 0:
            reason = (
                "truncated-max-tokens"
                if truncated_failed
                else (
                    "citation-strict-unverified"
                    if citation_strict_failed and parsed is not None
                    else (
                        "schema-validation"
                        if schema_validation_failed
                        else "json-parse"
                    )
                )
            )
            logger.warning(
                "insight-engine-json-parse-retry",
                extra={
                    "pair": log_subject,
                    "attempt": attempt_n,
                    "max_attempts": MAX_RECALLS,
                    "reason": reason,
                    "error_type": type(parse_error).__name__ if parse_error else "Unknown",
                    "error_message": str(parse_error)[:200] if parse_error else "",
                },
            )
        try:
            message, raw_text = _call_and_extract(attempt_index=attempt_n)
        except Exception as call_exc:
            if attempt_n == 0:
                raise
            logger.error(
                "insight-engine-json-parse-recall-aborted",
                extra={
                    "pair": log_subject,
                    "attempt": attempt_n,
                    "error_type": type(call_exc).__name__,
                    "error_message": str(call_exc)[:200],
                },
            )
            break
        call_attempts.append((message, raw_text))

        if getattr(message, "stop_reason", None) == "max_tokens":
            truncated_failed = True
            parsed = None
            llm_output = None
            raw_for_response = raw_text
            logger.warning(
                "insight-engine-brief-truncated",
                extra={
                    "pair": log_subject,
                    "attempt": attempt_n,
                    "max_attempts": MAX_RECALLS,
                    "stop_reason": "max_tokens",
                    "max_tokens": max_tokens,
                },
            )
            continue
        truncated_failed = False

        parsed, parse_error, parse_path = _try_parse_llm_json(raw_text)
        citation_strict_failed = False
        # Wie bei citation_strict_failed: Flag pro Versuch zuruecksetzen,
        # damit die Terminal-Klassifikation nach der Schleife den Fehlermodus
        # des LETZTEN Versuchs meldet (ein frueher Schema-Fehler darf einen
        # spaeteren Parse-Fehler nicht als schema_validation_error tarnen).
        schema_validation_failed = False

        if parsed is None:
            continue

        candidate = _unwrap_single_key(parsed, expected_field=unwrap_expected_field)
        if candidate is not parsed:
            logger.warning(
                "insight-engine-llm-output-unwrapped",
                extra={"pair": log_subject, "wrapper_key": next(iter(parsed))},
            )

        try:
            llm_output = validate(candidate)
        except ValueError as exc:
            cleaned_for_log = _strip_codefence(raw_text)
            schema_error_detail = str(exc)[:500]
            logger.error(
                "insight-engine-schema-validation-failed",
                extra={
                    "pair": log_subject,
                    "attempt": attempt_n,
                    "max_attempts": MAX_RECALLS,
                    "error_message": schema_error_detail,
                    "raw_response_length": len(cleaned_for_log),
                    "raw_response_first_500": cleaned_for_log[:500],
                },
            )
            raw_for_response = raw_text
            schema_validation_failed = True
            llm_output = None
            # Cron-Run 16421771 (20.07.2026, lionsgate): Schema-Fehler sind
            # genauso nicht-deterministisch wie Parse-Fehler (derselbe Prompt
            # validiert im naechsten Anlauf oft sauber) — deshalb innerhalb
            # von MAX_RECALLS neu anfragen statt sofort terminal aufzugeben.
            continue

        citation_ok = citation_validate(llm_output) if citation_validate else True
        if strict_citations and not citation_ok:
            citation_strict_failed = True
            # Diagnose (additiv): welche Citation-Regel brach? Nur auf dem
            # Failure-Pfad, defensiv — die Telemetrie darf den Retry nie
            # killen.
            if citation_detail is not None:
                try:
                    citation_failure_detail = citation_detail(llm_output)
                except Exception as detail_exc:  # pragma: no cover — defensive
                    logger.warning(
                        "insight-engine-citation-detail-failed: %s", detail_exc
                    )
            raw_for_response = raw_text
            llm_output = None
            continue

        break

    # Diagnose-Instrumentierung (2026-06-22, additiv): den terminalen
    # Fehlergrund klassifizieren, damit der Cron ihn statt des pauschalen
    # ``no_llm_output`` ausweisen kann. Bleibt ``None`` im Erfolgsfall.
    failure_kind: Optional[str] = None
    failure_detail: Optional[str] = None

    if llm_output is not None:
        if parse_path == "lenient" or len(call_attempts) > 1:
            logger.info(
                "insight-engine-json-parse-recovered",
                extra={
                    "pair": log_subject,
                    "parse_path": parse_path,
                    "anthropic_calls": len(call_attempts),
                    "recall_count": len(call_attempts) - 1,
                },
            )
    elif schema_validation_failed:
        failure_kind = "schema_validation_error"
        failure_detail = schema_error_detail
    elif citation_strict_failed:
        failure_kind = "citation_validation_error"
        failure_detail = citation_failure_detail
        logger.error(
            "insight-engine-citation-strict-exhausted",
            extra={
                "pair": log_subject,
                "anthropic_calls": len(call_attempts),
                "recall_count": len(call_attempts) - 1,
            },
        )
    elif truncated_failed:
        # output_token_count des abgeschnittenen (letzten) Versuchs gegen
        # das max_tokens-Limit halten — beantwortet direkt, ob das Limit zu
        # niedrig oder das JSON zu gross ist (Wolf-Ping 2026-06-22).
        last_usage = (
            getattr(call_attempts[-1][0], "usage", None) if call_attempts else None
        )
        truncated_output_tokens = (
            int(getattr(last_usage, "output_tokens", 0) or 0) if last_usage else 0
        )
        failure_kind = "truncation_error"
        failure_detail = (
            f"stop_reason=max_tokens output_token_count={truncated_output_tokens} "
            f"max_tokens={max_tokens}"
        )
        logger.error(
            "insight-engine-brief-truncated-exhausted",
            extra={
                "pair": log_subject,
                "anthropic_calls": len(call_attempts),
                "recall_count": len(call_attempts) - 1,
                "outcome": "truncated",
                "output_token_count": truncated_output_tokens,
                "max_tokens": max_tokens,
            },
        )
    else:
        cleaned = _strip_codefence(raw_text)
        pos = parse_error.pos if parse_error and parse_error.pos is not None else 0
        failure_kind = "json_parse_error"
        failure_detail = (
            f"{parse_error} (char_position={pos}, raw_length={len(cleaned)})"
            if parse_error
            else f"JSON-Parse fehlgeschlagen ohne JSONDecodeError (raw_length={len(cleaned)})"
        )
        logger.error(
            "insight-engine-json-parse-failed",
            extra={
                "pair": log_subject,
                "error_message": str(parse_error) if parse_error else "",
                "char_position": pos,
                "raw_response_length": len(cleaned),
                "raw_response_first_500": cleaned[:500],
                "raw_response_around_error": cleaned[max(0, pos - 200): pos + 200],
                "anthropic_calls": len(call_attempts),
                "recall_count": len(call_attempts) - 1,
            },
        )
        raw_for_response = raw_text

    input_tokens_total = 0
    output_tokens_total = 0
    for msg_attempt, _ in call_attempts:
        usage = getattr(msg_attempt, "usage", None)
        if usage is None:
            continue
        in_t = int(getattr(usage, "input_tokens", 0) or 0)
        out_t = int(getattr(usage, "output_tokens", 0) or 0)
        if not (in_t or out_t):
            continue
        input_tokens_total += in_t
        output_tokens_total += out_t
        record_anthropic_call(
            usage,
            model=model,
            operation=operation,
            meta=record_meta,
        )

    cost = (
        _estimate_cost_usd(input_tokens_total, output_tokens_total)
        if (input_tokens_total or output_tokens_total)
        else None
    )
    return _BriefLLMResult(
        llm_output=llm_output,
        raw_text=raw_for_response,
        input_tokens=input_tokens_total,
        output_tokens=output_tokens_total,
        cost=cost,
        anthropic_calls=len(call_attempts),
        failure_kind=failure_kind,
        failure_detail=failure_detail,
    )


def _resolve_title_id_for_post_url(session: Session, post_url: Optional[str]) -> Optional[str]:
    """Deterministic post_url -> Post -> Asset.title_id resolution (V3 Sprint 1).

    ``post_url`` is a unique key (``Post.post_url``), so this never goes
    through the title NAME (which would reintroduce the MK/MKII ambiguity
    fixed in #230/#231). Returns the stringified title_id only when the post's
    assets carry EXACTLY ONE distinct non-NULL title_id; otherwise None
    (no post, non-film, matcher not (yet) assigned, or >1 distinct -> no guess).
    """
    if not post_url:
        return None
    post = session.exec(select(Post).where(Post.post_url == post_url)).first()
    if post is None:
        return None
    title_ids = {
        tid for tid in session.exec(
            select(Asset.title_id).where(
                Asset.post_id == post.id, Asset.title_id.is_not(None)
            )
        ).all()
    }
    if len(title_ids) != 1:
        return None
    return str(next(iter(title_ids)))


def _enrich_fokus_title_ids(session: Session, llm_output) -> None:
    """In-place: set ``title_id`` on each ``aktuell_im_fokus`` item via the
    deterministic chain. No-op when the field is absent/empty. Existing
    non-None values are preserved (idempotent)."""
    if llm_output is None:
        return
    for item in getattr(llm_output, "aktuell_im_fokus", None) or []:
        if getattr(item, "title_id", None):
            continue
        item.title_id = _resolve_title_id_for_post_url(session, getattr(item, "post_url", None))


def _market_has_data(channel: Optional[ChannelStats]) -> bool:
    """Ein Markt zaehlt nur als Datenquelle, wenn der Channel aufgeloest wurde
    UND in der Woche tatsaechlich Posts hatte — ein None- oder Null-Posts-
    Channel ist keine Vergleichsbasis."""
    return channel is not None and channel.channel_found and channel.posts_count > 0


def _has_cross_market_lage(agg: PairAggregation) -> bool:
    """Signal fuer den ``has_cross_market``-Validation-Context (Sprint
    15.06.2026): ``transfer_opportunity`` ist nur Pflicht, wenn eine ECHTE
    Cross-Market-Lage besteht — >= 2 Maerkte mit Posts UND mindestens ein
    Cross-Market-Match. Zwei Maerkte mit unabhaengigen Posts (z.B. lionsgate
    KW24: 15 US / 27 UK Posts, aber 0 Matches) sind keine vergleichbare Lage,
    da gibt es legitim nichts zu transferieren."""
    markets_with_posts = sum(
        _market_has_data(c)
        for c in (agg.de_channel, agg.us_channel, agg.uk_channel)
    )
    has_matches = (
        len(agg.cross_market_matches)
        + len(agg.de_uk_matches)
        + len(agg.us_uk_matches)
    ) > 0
    return markets_with_posts >= 2 and has_matches


def generate_weekly_report(
    session: Session,
    pair_key: str,
    *,
    window_days: int = 30,
    dry_run: bool = False,
    model: str = OPUS_MODEL_ALIAS,
    # Sprint-Trailerhaus-Prompt-v1: bumped 8k → 12k for the ``ganz genau``
    # mode (~1500-2000 words across nine sections). 2026-06: bumped 12k → 20k
    # after primevideo KW23 showed tail-truncation (fuer_cutter … last four
    # Optional sections dropping to null) — 12k was grenzwertig against the
    # full JSON brief; 20k gives comfortable headroom.
    max_tokens: int = 20000,
    now: Optional[datetime] = None,
    previous_context: Optional[str] = None,
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

    user_prompt = _build_user_prompt(agg, previous_context=previous_context)
    result = _run_brief_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tool_name=_BRIEF_TOOL_NAME,
        tool_description=_BRIEF_TOOL_DESCRIPTION,
        input_schema=_BRIEF_TOOL_INPUT_SCHEMA,
        # Sprint lionsgate-cross-market-conditional (10.06.2026): die
        # ``de_vs_us``-Pflicht ist datengetrieben (Validator auf
        # ``LLMReport``). Signal ist Config-Level: ``agg.de_channel`` ist
        # genau dann ``None``, wenn das Pair keinen DE-Channel definiert
        # (heute nur lionsgate) — NICHT, wenn ein Voll-Pair eine duenne
        # DE-Woche hat. Der Kernel ``_run_brief_llm`` bleibt generisch
        # (``validate`` ist weiter ``Callable[[Any], Any]``), deshalb
        # ``partial`` statt eines Context-Parameters durch den Kernel.
        validate=functools.partial(
            LLMReport.model_validate,
            context={
                "has_de_data": agg.de_channel is not None,
                "has_cross_market": _has_cross_market_lage(agg),
            },
        ),
        model=model,
        max_tokens=max_tokens,
        log_subject=pair_key,
        call_extra={
            "pair": pair_key,
            "window_days": window_days,
            "model": model,
            "prompt_chars": len(user_prompt),
        },
        record_meta={
            "pair_key": agg.pair_key,
            "iso_week": agg.iso_week,
            "iso_year": agg.iso_year,
        },
        operation="weekly_brief",
        # Soft-Modus (Default): _validate_citations laeuft immer (Telemetrie),
        # der Strikt-Pfad greift nur bei insight_citation_strict_enforce.
        citation_validate=lambda out: _validate_citations(
            out, agg, pair_key=pair_key, iso_year=agg.iso_year, iso_week=agg.iso_week
        ),
        # Diagnose (additiv): liefert auf dem Strikt-Failure-Pfad die
        # gebrochenen Citation-Sektionen fuer den Cron-Diagnose-Block.
        citation_detail=lambda out: _describe_citation_failures(out, agg),
        strict_citations=bool(settings.insight_citation_strict_enforce),
    )

    # V3 Sprint 1 — Post-LLM-Enrichment: title_id pro Fokus-Item über die
    # deterministische Kette post_url -> Post -> Asset.title_id setzen
    # (niemals über den Namen). Macht die Filmnamen im Frontend (Sprint 2)
    # auf eine film-zentrierte Ansicht verlinkbar.
    _enrich_fokus_title_ids(session, result.llm_output)

    # Diagnose-Instrumentierung (2026-06-22, additiv): bei terminalem
    # Failure die Klassifikation aus dem Kernel an den Cron durchreichen,
    # damit der Sammelfehler ``no_llm_output`` aufgeschluesselt wird. Nur
    # gesetzt, wenn wirklich kein Brief entstand — Erfolgsfall bleibt None.
    failure_diagnostic: Optional[dict] = None
    if result.llm_output is None and result.failure_kind is not None:
        failure_diagnostic = {
            "kind": result.failure_kind,
            "detail": result.failure_detail,
        }

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
        llm_output=result.llm_output,
        aggregation=agg,
        cost_usd_estimate=result.cost,
        input_tokens=result.input_tokens or None,
        output_tokens=result.output_tokens or None,
        raw_llm_text=result.raw_text,
        failure_diagnostic=failure_diagnostic,
    )


def _summarize_eval_variant(result: "_BriefLLMResult") -> dict:
    output = result.llm_output
    return {
        "status": "ok" if output is not None else "generation_failed",
        "headline": output.headline if output is not None else None,
        "tldr": output.tldr if output is not None else None,
        "failure_kind": result.failure_kind,
        "failure_detail": result.failure_detail,
        "raw_llm_text": result.raw_text if output is None else None,
        "input_tokens": result.input_tokens or None,
        "output_tokens": result.output_tokens or None,
        "cost_usd": result.cost,
    }


def run_prompt_eval(
    session: Session,
    pair_key: str,
    *,
    variant_b_system_prompt: str,
    window_days: int = 30,
    model: str = OPUS_MODEL_ALIAS,
    max_tokens: int = 20000,
    now: Optional[datetime] = None,
) -> dict:
    """Platin 3 — side-by-side eval of two brief system-prompt variants on
    the SAME real, already-collected aggregation for one pair/week.

    Runs ``aggregate_pair``/``_build_user_prompt`` exactly ONCE (free,
    DB-only) so both variants see identical input data, then calls Opus
    TWICE through the shared ``_run_brief_llm`` kernel: once with the
    current production ``SYSTEM_PROMPT`` ("a", the baseline) and once with
    ``variant_b_system_prompt`` ("b", the candidate). Neither result is
    persisted to the ``InsightReport`` cache — this is a read-only
    experimentation tool, safe to re-run repeatedly without the
    cache-poisoning risk that ``/insights/regenerate`` guards against.

    Cost accounting: both calls flow through the normal
    ``record_anthropic_call`` path (counts toward the real monthly Anthropic
    budget, as it is real spend) but tagged ``operation="prompt_eval"``
    instead of ``"weekly_brief"`` so it shows up as its own bucket in
    ``/admin/cost-summary?group_by=operation``. ~2x a normal brief per call
    (~$0.30 at current Opus pricing) — an operator-triggered, on-demand
    tool, never part of the automated weekly cron.

    Citations run in soft mode only (``strict_citations=False`` always,
    regardless of ``settings.insight_citation_strict_enforce``) — an eval
    run wants to SEE both outputs even if one has an unverified citation,
    not have that variant silently retried/discarded.
    """
    if not is_anthropic_configured():
        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt — Prompt-Eval kann nicht laufen."
        )

    agg = aggregate_pair(session, pair_key, window_days=window_days, now=now)
    user_prompt = _build_user_prompt(agg, previous_context=None)

    def _run(variant_label: str, system_prompt: str) -> _BriefLLMResult:
        return _run_brief_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_name=_BRIEF_TOOL_NAME,
            tool_description=_BRIEF_TOOL_DESCRIPTION,
            input_schema=_BRIEF_TOOL_INPUT_SCHEMA,
            validate=functools.partial(
                LLMReport.model_validate,
                context={
                    "has_de_data": agg.de_channel is not None,
                    "has_cross_market": _has_cross_market_lage(agg),
                },
            ),
            model=model,
            max_tokens=max_tokens,
            log_subject=f"{pair_key}:eval:{variant_label}",
            call_extra={
                "pair": pair_key,
                "eval_variant": variant_label,
                "model": model,
                "prompt_chars": len(user_prompt),
            },
            record_meta={
                "pair_key": agg.pair_key,
                "iso_week": agg.iso_week,
                "iso_year": agg.iso_year,
                "eval_variant": variant_label,
            },
            operation="prompt_eval",
            citation_validate=lambda out: _validate_citations(
                out, agg, pair_key=pair_key, iso_year=agg.iso_year, iso_week=agg.iso_week
            ),
            citation_detail=lambda out: _describe_citation_failures(out, agg),
            strict_citations=False,
        )

    result_a = _run("a", SYSTEM_PROMPT)
    result_b = _run("b", variant_b_system_prompt)

    return {
        "pair_key": agg.pair_key,
        "iso_year": agg.iso_year,
        "iso_week": agg.iso_week,
        "variant_a": _summarize_eval_variant(result_a),
        "variant_b": _summarize_eval_variant(result_b),
    }


def _hydrate_from_persisted(row: InsightReportRow, *, window_days: int) -> InsightReport:
    """Rebuild a Pydantic ``InsightReport`` from a stored ``insight_report``
    row. Used by the cache hit path of ``generate_and_persist_report`` —
    the JSONB-serialised aggregation/llm_output blobs round-trip through
    ``model_validate`` so consumers see the same shape they would from a
    fresh generate call. ``cost_usd_estimate`` is reconstructed from the
    ``cost_usd_cents`` integer that the persistence layer stores.
    """
    aggregation = PairAggregation.model_validate(row.aggregation)
    # Sprint lionsgate-cross-market-conditional (10.06.2026): Context auch
    # hier — der ``de_vs_us``-Validator auf ``LLMReport`` hat als
    # defensiven Default "fehlender Context = Pflicht an". Ohne diese
    # Zeile wuerde ein korrekt persistierter lionsgate-Brief
    # (``de_vs_us=None``) bei JEDEM Cache-Hit kippen. Signal identisch
    # zur Generierungs-Call-Site: Config-Level ``de_channel is not None``.
    llm_output = (
        LLMReport.model_validate(
            row.llm_output,
            context={
                "has_de_data": aggregation.de_channel is not None,
                "has_cross_market": _has_cross_market_lage(aggregation),
            },
        )
        if row.llm_output
        else None
    )
    cost_usd_estimate: Optional[float] = (
        round(row.cost_usd_cents / 100.0, 4) if row.cost_usd_cents is not None else None
    )
    return InsightReport(
        pair_key=row.pair_key,
        pair_label=aggregation.pair_label,
        iso_week=row.iso_week,
        iso_year=row.iso_year,
        window_days=window_days,
        coverage_pct=aggregation.title_coverage.overall_coverage_pct,
        generated_at=row.generated_at,
        model=row.model,
        dry_run=False,
        llm_output=llm_output,
        aggregation=aggregation,
        cost_usd_estimate=cost_usd_estimate,
    )


def _persist_report(session: Session, report: InsightReport) -> None:
    """Upsert one ``insight_report`` row keyed by (pair_key, iso_year, iso_week).

    Last-Write-Wins semantics — a ``force=true`` regeneration overwrites
    the previously persisted brief. We delete-then-insert because SQLite
    (used by the test suite) doesn't have a portable composite-key UPSERT
    that matches Postgres ``ON CONFLICT (pair_key, iso_year, iso_week)
    DO UPDATE``; the delete + insert in the same session/transaction is
    equivalent for our concurrency model (single FastAPI worker per
    request, no parallel writes for the same composite key).

    Persisting requires an ``llm_output`` — dry-run reports never reach
    this function (the caller guards on ``dry_run``).
    """
    if report.llm_output is None:
        # Defensive — the GET endpoint guards on dry_run before calling
        # this, but a JSON-parse-failure path could theoretically slip
        # through. Skip persistence rather than write an empty row.
        logger.warning(
            "insight-report-persist-skipped: pair=%s week=%d/%d (no llm_output)",
            report.pair_key, report.iso_year, report.iso_week,
        )
        return

    cost_cents: Optional[int] = (
        int(round(report.cost_usd_estimate * 100)) if report.cost_usd_estimate else None
    )

    existing = session.get(
        InsightReportRow,
        (report.pair_key, report.iso_year, report.iso_week),
    )
    if existing is not None:
        session.delete(existing)
        session.flush()

    row = InsightReportRow(
        pair_key=report.pair_key,
        iso_year=report.iso_year,
        iso_week=report.iso_week,
        aggregation=report.aggregation.model_dump(mode="json"),
        llm_output=report.llm_output.model_dump(mode="json"),
        generated_at=report.generated_at,
        model=report.model,
        cost_usd_cents=cost_cents,
        input_tokens=report.input_tokens,
        output_tokens=report.output_tokens,
    )
    session.add(row)
    session.commit()


def _acquire_brief_lock(
    session: Session,
    *,
    pair_key: str,
    iso_year: int,
    iso_week: int,
) -> bool:
    """Postgres advisory lock keyed by ``(pair_key, iso_year, iso_week)``.

    Returns ``True`` if the dialect is Postgres and the lock was issued
    (the caller now holds the lock until the transaction commits),
    ``False`` for SQLite test paths where no advisory-lock mechanism
    exists. The caller short-circuits the lock-protected re-check on the
    ``False`` path and falls back to the plain pre-check semantics.

    ``pg_advisory_xact_lock`` is the right choice over ``pg_try_*``
    because we WANT the second concurrent worker to block until the
    first has committed: that's how the re-check inside the lock sees
    the persisted row and short-circuits the second LLM call. The
    ``SET LOCAL lock_timeout`` cap prevents a stuck worker from
    blocking the API forever — 300s covers worst-case Opus latency
    (60-90s) plus a comfortable cushion. The lock is automatically
    released when the transaction commits or rolls back, so the only
    cleanup we need is the regular ``session.commit()`` later in the
    code path.

    Lock-key construction: a 31-bit signed integer derived from
    ``hash((pair_key, iso_year, iso_week))``. The exact namespace of
    advisory locks is global per database, so a hash collision would
    serialise two unrelated pairs by accident — that's harmless (only
    affects throughput, not correctness) and the 2**31 keyspace is
    wide enough that collisions are astronomically rare in our
    ~10-pair × ~52-week working set.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return False
    lock_int = abs(hash((pair_key, iso_year, iso_week))) % (2**31)
    logger.info(
        "brief_lock_attempt",
        extra={
            "pair": pair_key,
            "iso_week": iso_week,
            "iso_year": iso_year,
            "lock_key": lock_int,
        },
    )
    session.exec(sa.text("SET LOCAL lock_timeout = '300s'"))
    attempt_started = time.perf_counter()
    try:
        session.exec(sa.text("SELECT pg_advisory_xact_lock(:k)"), params={"k": lock_int})
    except Exception as exc:  # noqa: BLE001 — log + re-raise so callers see the failure
        wait_ms = int((time.perf_counter() - attempt_started) * 1000)
        if "lock_timeout" in str(exc).lower() or "canceling statement" in str(exc).lower():
            logger.warning(
                "brief_lock_timeout",
                extra={
                    "pair": pair_key,
                    "iso_week": iso_week,
                    "iso_year": iso_year,
                    "lock_key": lock_int,
                    "timeout_s": 300,
                    "wait_ms": wait_ms,
                },
            )
        raise
    wait_ms = int((time.perf_counter() - attempt_started) * 1000)
    logger.info(
        "brief_lock_acquired",
        extra={
            "pair": pair_key,
            "iso_week": iso_week,
            "iso_year": iso_year,
            "lock_key": lock_int,
            "wait_ms": wait_ms,
        },
    )
    return True


def generate_and_persist_report(
    session: Session,
    pair_key: str,
    *,
    window_days: int = 30,
    force: bool = False,
    replace: bool = False,
    model: str = OPUS_MODEL_ALIAS,
    max_tokens: int = 20000,
    now: Optional[datetime] = None,
) -> InsightReport:
    """Cache-aware variant of ``generate_weekly_report`` for the
    Sprint-1 persistence path.

    Behaviour:
    - Run ``aggregate_pair`` first (cheap, DB-only) so the ISO week
      lookup uses the same ``iso_year``/``iso_week`` the report would
      eventually carry. Computing them separately from
      ``datetime.now().isocalendar()`` would risk a mismatch around
      week-boundary calls.
    - Acquire a Postgres advisory lock keyed by the persistence PK.
      Concurrent requests for the same ``(pair, year, week)`` block
      here until the holder commits, then the re-check inside the
      lock returns the persisted row and short-circuits the second
      LLM call. SQLite test paths skip the lock and rely on the
      plain pre-check.
    - **Double-check locking** (Sprint 3c): after the lock acquires,
      re-read the row IRRESPECTIVE of ``force``. If a row exists for
      this ``(pair_key, iso_year, iso_week)`` PK, return it without
      an LLM call. ``force=true`` semantics: "ignore the optional
      pre-lock first-read", **not** "ignore the composite PK". A
      caller who actually wants to overwrite a persisted brief
      passes ``replace=True`` (Sprint Force-Regenerate 2026-05-17,
      PR #150), which is the explicit UPSERT pathway.
    - ``replace=True`` bypasses the composite-PK existence check
      entirely. ``_persist_report`` then handles the actual UPSERT
      via delete-then-insert (Postgres ``ON CONFLICT (pair_key,
      iso_year, iso_week) DO UPDATE`` semantics). Sprint-3c's
      anti-retry-echo race for ``force=True`` callers stays closed:
      ``replace`` is the opt-in destructive path, not the default.
    - Otherwise call Opus, build the report, persist it (Last-Write-Wins
      on the composite PK), return the fresh report.

    Anti-retry-echo (2026-05-12 sprint chain): Sprint 3b smoke-test
    verified hypothesis D — the lock blocks correctly but the
    force-path's age-based short-circuit failed to return C1's
    fresh brief to C2 once Opus latency exceeded the 120s window.
    Dropping the age gate and always honouring the composite PK
    fixes the leak without changing the rest of the lock contract.
    The ``replace`` parameter sidesteps the protection for callers
    that *want* a destructive overwrite (admin manual regenerate);
    parallel ``replace=True`` calls can race and produce two LLM
    calls within the same lock-window. Operator-triggered only,
    $0.40 worst-case double-spend on rapid re-clicks.
    """
    pipeline_started = time.perf_counter()
    logger.info(
        "brief_pipeline_start",
        extra={
            "pair": pair_key,
            "force": force,
            "replace": replace,
            "window_days": window_days,
        },
    )

    agg = aggregate_pair(session, pair_key, window_days=window_days, now=now)

    has_lock = _acquire_brief_lock(
        session,
        pair_key=pair_key,
        iso_year=agg.iso_year,
        iso_week=agg.iso_week,
    )

    # Re-check inside the lock (Postgres) or single-check fallback (SQLite).
    # When ``has_lock`` is True, a concurrent worker has already committed
    # by the time we get here, so the SELECT inside this transaction
    # observes the latest committed snapshot for our PK.
    existing = session.get(
        InsightReportRow,
        (pair_key, agg.iso_year, agg.iso_week),
    )
    logger.info(
        "brief_precheck_done",
        extra={
            "pair": pair_key,
            "iso_week": agg.iso_week,
            "iso_year": agg.iso_year,
            "exists": existing is not None,
            "force": force,
            "replace": replace,
            "lock_path": has_lock,
        },
    )
    if existing is not None and not replace:
        # Double-check locking: same PK after lock → return existing,
        # regardless of force. force=true bypasses the optional first-
        # read optimisation, not the composite PK. ``replace=True`` is
        # the explicit overwrite path (PR #150).
        outcome = "cache_hit" if not force else "lock_dedup"
        logger.info(
            "brief_pipeline_done",
            extra={
                "pair": pair_key,
                "iso_week": agg.iso_week,
                "total_ms": int((time.perf_counter() - pipeline_started) * 1000),
                "outcome": outcome,
            },
        )
        return _hydrate_from_persisted(existing, window_days=window_days)

    # Cache miss, force on a stale row, OR replace=True with existing row.
    # The advisory lock (when active) guarantees that no other worker is
    # in this branch for the same ``(pair, year, week)`` right now.
    #
    # Anti-Repetition (Sprint 17.05.2026): vor dem LLM-Call den naechst-
    # aelteren Brief laden und einen Previous-Context-Block fuer den Prompt
    # bauen. Bei strict ``< (iso_year, iso_week)`` faengt der Lookup auch
    # ``replace=True``-Aufrufe sauber ab — der Vorgaenger ist die KW-1,
    # nicht der zu ueberschreibende Brief selbst. Bei fehlender/leerer
    # Vorgaenger-Headline wird der ganze Block ausgelassen (Wolf-Ping-#1-
    # Default: ohne Headline-Anker hilft der Diff allein nicht beim
    # Narrative-Wechsel). Erstgang fuer ein Pair → previous=None → Block
    # ausgelassen, Brief verhaelt sich wie vor dem Sprint.
    previous_context_block: Optional[str] = None
    previous_context_info: dict = {"has_previous_context": False}
    previous = _load_previous_brief(session, pair_key, agg.iso_year, agg.iso_week)
    if previous is not None:
        try:
            previous_headline_raw = (previous.llm_output or {}).get("headline")
            if not previous_headline_raw or not str(previous_headline_raw).strip():
                raise ValueError("empty headline")
            previous_agg = PairAggregation.model_validate(previous.aggregation)
            diff = _compute_top_post_diff(agg, previous_agg)
            previous_context_block = _format_previous_context_block(
                prev_iso_year=previous.iso_year,
                prev_iso_week=previous.iso_week,
                prev_headline=str(previous_headline_raw).strip(),
                diff=diff,
            )
            previous_context_info = {
                "has_previous_context": True,
                "previous_iso_year": previous.iso_year,
                "previous_iso_week": previous.iso_week,
                "diff_carried_count": diff["carried_count"],
                "diff_new_count": diff["new_count"],
                "diff_dropped_count": diff["dropped_count"],
            }
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            logger.warning(
                "previous_context_skipped",
                extra={
                    "reason": "headline_unavailable",
                    "pair_key": pair_key,
                    "previous_iso_year": previous.iso_year,
                    "previous_iso_week": previous.iso_week,
                    "error_class": type(exc).__name__,
                    "error_message": str(exc)[:200],
                },
            )

    logger.info(
        "brief_llm_call_start",
        extra={"pair": pair_key, "iso_week": agg.iso_week, "model": model},
    )
    llm_started = time.perf_counter()
    report = generate_weekly_report(
        session,
        pair_key,
        window_days=window_days,
        dry_run=False,
        model=model,
        max_tokens=max_tokens,
        now=now,
        previous_context=previous_context_block,
    )
    llm_duration_ms = int((time.perf_counter() - llm_started) * 1000)
    logger.info(
        "brief_llm_call_done",
        extra={
            "pair": pair_key,
            "iso_week": agg.iso_week,
            "tokens_in": report.input_tokens or 0,
            "tokens_out": report.output_tokens or 0,
            "duration_ms": llm_duration_ms,
        },
    )
    # ``record_anthropic_call`` happened inside ``generate_weekly_report``
    # (opens its own session). Surface the cost figures here so a single
    # log stream covers the whole pipeline without grepping cost_log.
    cost_millicents = (
        int(round(report.cost_usd_estimate * 100_000))
        if report.cost_usd_estimate
        else 0
    )
    logger.info(
        "brief_cost_logged",
        extra={
            "pair": pair_key,
            "iso_week": agg.iso_week,
            "cost_millicents": cost_millicents,
        },
    )

    _persist_report(session, report)
    logger.info(
        "brief_report_persisted",
        extra={
            "pair": pair_key,
            "iso_week": agg.iso_week,
            "iso_year": agg.iso_year,
            "lock_path": has_lock,
        },
    )

    # Outcome flag distinguishes a first-time generation from a
    # replace=True overwrite — both run Opus, but the post-Sprint-3c
    # contract treats them differently in the audit trail. PR #150.
    outcome = "replace_overwrite" if (replace and existing is not None) else "fresh_generation"
    logger.info(
        "brief_pipeline_done",
        extra={
            "pair": pair_key,
            "iso_week": agg.iso_week,
            "total_ms": int((time.perf_counter() - pipeline_started) * 1000),
            "outcome": outcome,
            **previous_context_info,
        },
    )
    return report


__all__ = [
    "PAIRS",
    "OPUS_MODEL_ALIAS",
    "SYSTEM_PROMPT",
    "BRIEF_VOICE",
    "aggregate_pair",
    "last_completed_iso_week_anchor",
    "generate_weekly_report",
    "generate_and_persist_report",
]
