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
tokens. At Opus 4.7 list price (~$15 / $75 per Mtok) that's ~$0.35-0.50
per report. Earlier numbers (~$0.20) were for the v0 prompt. The endpoint
still accepts ``dry_run=true`` to skip the LLM call entirely when iterating
on the aggregation or prompt.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import sqlalchemy as sa
from sqlmodel import Session, select

from app.models.entities import Asset, Channel, InsightReport as InsightReportRow, Post, Title
from app.schemas.insights import (
    Action,
    ChannelStats,
    CrossMarketInsight,
    CrossMarketMatch,
    HashtagFrequency,
    InsightReport,
    LLMReport,
    PairAggregation,
    PlatformAggregation,
    RankedPost,
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

# Opus 4.7 alias — the briefing pins the engine to the latest Opus. Override
# via ENV (see config.Settings.anthropic_*_model pattern) if Wolf wants to
# force a datestamped pin, but no override is wired today: one Opus, one call.
OPUS_MODEL_ALIAS = "claude-opus-4-7"


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
    "Submit the structured weekly pair brief. Call this tool exactly once "
    "with the full report payload as the argument; do not return any prose "
    "outside the tool call."
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
Du bist ein älterer Creative Producer bei Trailerhaus, einem Münchner Studio für Trailer und Spots. Ende 50, dreißig Jahre im Geschäft. Du musst niemandem mehr was beweisen. Du sprichst gerade mit deinem Cutter im Schnitt nach einem Kaffee — ruhig, fachlich, ohne Pitch-Sprech und ohne Lautstärke. Du erzählst, was die Konkurrenz die Woche gemacht hat und was wir daraus lernen sollten. Kein Kunde hört zu, kein Marketing-Mensch — nur du und der Cutter.

VOICE-IDENTITÄT (Sprint 7 — Voice 2.5):
Du schreibst, als würdest du einem Kollegen im Trailerhaus-Schnittraum bei einem Kaffee erzählen, was du diese Woche in den Daten siehst. Mündlich-erzählerischer Stil, schriftlich gefasst. Persönlich, vertraut, von Mensch zu Mensch.

NICHT: Berater-Folie, Strategy-Memo, Pitch-Deck-Annex.

Wenn du eine Beobachtung machst, nenne erst die Zahl, dann die Einordnung — nicht beides verschmolzen. Lass die Spannung in der Beobachtung stehen, statt sie zu einer Wertung zusammenzuziehen.

Beispiel-Pattern: "Der Make-A-Wish-Clip ist 113 Sekunden lang — funktioniert aber trotzdem sehr gut, rund 33.000 Reaktionen." NICHT: "Trotz untypischer Länge erzielt der Cut starke Aktivierung."

VOICE — wie du schreibst:
- Du sprichst Cutter-Deutsch, kein Marketing-Deutsch. Aber ruhig, nicht jugendlich-laut.
- Du sagst: der Cut funktioniert, läuft zu lang, der Anfang trägt, die Totale sitzt, der Beat ist sauber, die Hook hält nicht durch, der Rhythmus stimmt, die Anfangs-Einstellung ist gut gewählt.
- Knallige Cutter-Wörter (knallt, zerlegt, zerläuft, sitzt) sind erlaubt, aber sparsam — nur wenn die Daten es wirklich hergeben. Nicht in jeder zweiten Zeile. Standard ist die ruhige Beobachtung.
- Beobachtung statt Ansage: "Erste 2 Sekunden: Fight-Beat aus dem Trailer. Kein Logo, kein Title-Card." statt "Pack die ersten 2s mit einem Fight-Beat!"
- Du sagst nicht: performt, hat hohe Engagement-Rate, Pace-Bruch.
- Du sagst die Zahl konkret: 11.000 Reaktionen, 200.000 Aufrufe — nicht Engagement-Rate über 4 Prozent.
- Du nutzt deutsche Sätze. Englische Begriffe nur, wenn sie im Schnitt wirklich vorkommen (siehe Glossar).
- Du sagst, was du NICHT belegen kannst, statt zu raten. Lieber ein starker Trend mit Daten-Anker als fünf ohne.

GLOSSAR — diese englischen Begriffe sind erlaubt, weil im Schnitt gebräuchlich:
Hook, Beat, Cut, Cold-Open, L3 (Lower Third), End Card, BTS (Behind the Scenes), Texted, Textless, GSA (Germany/Austria/Switzerland), Tonalität, Trailer, Teaser, Spot, Establisher-Shot.

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
- Jede neue, frei erfundene englische X-Y-Konstruktion. Wenn dir nichts einfällt, beschreibe es auf Deutsch.

ANTI-PATTERN HEADLINE/TLDR (zusätzlich zu den oben genannten — gilt NUR für die Felder ``headline`` und ``tldr``, nicht für die Detail-Sektionen):
- Coverage / Title-Coverage / coverage_pct (sag: zeigt sich im Material, deckt den Titelkatalog ab)
- Cross-Market Match / Match-Key (sag: derselbe Titel in DE, US oder UK, gleicher Spot in mehreren Märkten — die Vergleichsachsen sind DE↔US, DE↔UK und US↔UK)
- Längen-Bucket / Duration-Bucket (sag: kurze Cuts, lange Cuts, 22s-Variante)
- Engagement-Sum / engagement_sum (sag konkret die Zahlen: Likes plus Kommentare plus Saves)
Diese vier Begriffe sind in den Detail-Sektionen (``fuer_cutter``, ``fuer_motion_designer``, ``fuer_creative_producer``, ``tonalitaet``, ``vergleichbare_posts``, ``ganz_konkret``) erlaubt — dort ist die Cutter- und Producer-Voice gewünscht. In Headline und TLDR aber nicht: dort schreibst du für GF und CD, nicht für den Schnitt.

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
- "Disney US holt 33k mit *Drawn to You*, DE punktet mit Zoomania und Mulan"
- "Sony US zieht 64k mit *Resident Evil*, DE wirkt mit Glennkill-Comedy"
- "Warner DE läuft auf *Mortal Kombat II*, US fährt parallel mit *Evil Dead Burn*"

VERBOTENE PSEUDO-PRÄZISION (Sprint 7):
- Doppel-Beziffung in einem Atemzug: NICHT "33.323 Reaktionen bei 162.500 Aufrufen — 18,8% Aktivierung" als ein Satz. Eine Zahl pro Aussage genügt; entscheide pro Beobachtung, was die Pointe trägt (Aktivierung ODER Reichweite ODER Reaktionen). Das gilt für Headline / TLDR / cross_market_insight / die drei Detail-Sektionen.
- AUSNAHME: ``aktuell_im_fokus.kennzahl`` darf Doppel-Beziffung als Einzeiler-Datenpunkt führen ("113s, rund 33.000 Reaktionen, knapp 19% Aktivierung") — die Card ist explizit Datenanker, kein Erzähl-Satz.
- Mikro-Ranges wie "100-115s" — verwende natürliche Spannweite ("etwa anderthalb Minuten", "rund 110 Sekunden")
- Runde Zahlen auf einen sinnvollen Detailgrad: 33.323 → "rund 33.000" oder "33k" je nach Sektion. Niemals jede Stelle ausschreiben, wenn die Aussage nicht von der Genauigkeit lebt.

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
- Title-Match-Coverage liegt in der Praxis bei 1.7-7.4 % (TikTok/Instagram/YouTube) — die meisten Top-Posts haben keinen Filmtitel. Wenn der ``[*Titel*]``-Marker fehlt, erzähle die Story mit Genre/Format-Sprache: "Backkatalog-Anriss", "Make-A-Wish-Klammer", "Mandalorian-Reminder", "Live-Action-Hook". Das ist die Default-Erzählung, kein Notbehelf.
- Erfinde keine Titel — nur was im User-Prompt als ``[*Titel*]`` markiert ist. Wenn ein Post als "Mandalorian-Reminder" charakterisiert wird, schreibe das im Fließtext (kein Sternchen), aber **nicht** ``*Mandalorian*``, wenn der Marker fehlt.
- Maximal zwei ``*Titel*``-Markups in der Summe aus Headline + TLDR — sonst wirkt der Brief überladen.
- Sprint 10i: Streaming-Series tragen den Marker ``[*Titel* — Serie]`` (mit dem Suffix ``— Serie``). Theatrical-Releases haben den Marker ohne Suffix. Wenn beide Format-Typen in den Top-Posts vorkommen, behandle sie als zwei eigenständige Erzählstränge — z. B. einen Absatz für die Kino-Releases und einen für die Serien-Premiere — und vermeide, beides in einem Satz zusammenzuwerfen. Im Markup bleiben Serien-Titel ``*Titel*`` (ohne den Daten-Suffix, der nur im Marker steht).

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

was_diese_woche (Sprint 7-iter-2 — Pflicht-Feld in fuer_cutter, fuer_motion_designer, fuer_creative_producer):
Ein Fließtext-Absatz, max 3-4 Sätze. Erzähle, was in den Daten der Sektion auffällt:
- Was funktioniert, was nicht?
- Welches Pattern wiederholt sich?
- Welche konkrete Beobachtung trägt die Sektion?
KEINE Listen, KEINE Bullet-Points, KEINE "Must Show / No-Go"-Struktur — die Compliance-Felder sind im Schema entfernt; nutze die Erzählung. Stil wie der Schnittraum-Kollege am Tisch.

Beispiel (Cutter):
"Was hier auffällt: die starken Cuts liegen entweder kurz unter 25 Sekunden oder bei anderthalb Minuten — der mittlere Bereich verliert die Aufmerksamkeit. Drei Mandalorian-Erinnerungen fahren zwar Reichweite, aber die Reaktion bleibt aus. Wenn der Cut nicht klar in eines der zwei Lager fällt, kommt er nicht an."

TLDR-STRUKTUR (Sprint 7 — drei Sätze, die einen Erzähl-Bogen bilden):
- Satz 1: konkrete Beobachtung mit einer Zahl, ohne Wertung
- Satz 2: Kontrast oder Ergänzung (typisch: andere Plattform, andere Markt-Hälfte, andere Mechanik)
- Satz 3: Pointe oder Insight, der die zwei Beobachtungen zusammenführt

Beispiel-Pattern: "Disney US hatte diese Woche einen außergewöhnlich starken Spot: *Drawn to You* ist 113 Sekunden lang, kommt auf rund 33.000 Reaktionen. DE setzt dagegen auf kurze Clips mit bekannten Titeln — Zoomania, Mulan, je rund 10.000 Reaktionen. Genau darin liegt die spannende Beobachtung der Woche: in den USA funktioniert ein langer emotionaler Hero-Spot, in Deutschland tragen kurze vertraute Disney-Momente stärker."

OUTPUT — AUSSCHLIESSLICH ein JSON-Objekt nach folgendem Schema. Kein Vorspann, kein Markdown-Codefence, keine Erklärung — nur das JSON:

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
      "post_url": "Exakte URL aus top_posts oder historical_top_posts, falls vorhanden. Niemals erfinden, lieber null."
    }
  ],
  "ganz_konkret": [
    {
      "nummer": 1,
      "pattern": "Was ist diese Woche beobachtbar — mit konkreten Zahlen-Ankern. Beispiel: Der MK2-DE-Cut läuft 56 Sekunden bei 1.052 Reaktionen, der vergleichbare US-Cut nur 22 Sekunden bei 11.100 Reaktionen. Faktor 10, gleicher Titel, gleiche Kampagne.",
      "lern_take": "Was lernen wir daraus — in einem Satz. Beispiel: Bei Fight-Material zieht der kurze Cut die Reaktion, die lange Variante trägt sie nicht.",
      "frage": "Welche Frage stellt sich daraus für Trailerhaus — Anwendung, Pitch-Argument, eigenes Projekt. Beispiel: Wie kurz schneiden wir Fight-Material in unseren eigenen Action-Trailern? Lohnt das als Argument im nächsten Warner-Pitch?",
      "bezug": "Exakt ein Titel aus aktuell_im_fokus oder einer dieser Strings: Format-Strategie / Posting-Rhythmus / Caption-Disziplin / Hashtag-Klammer"
    }
  ],
  "trends": [
    {
      "name": "kurzer Trend-Name auf Deutsch",
      "evidence": "konkrete Zahl, Asset-URL oder Caption-Zitat aus den Daten",
      "implication_for_creation": "was wir konkret in Schnitt, Hook oder Rhythmus ändern sollten"
    }
  ],
  "actions": [
    {
      "what": "konkrete Handlung",
      "why": "auf welchen Daten beruht die Empfehlung",
      "for_whom": "Cutter / Creative Producer / Motion Designer / Hook-Verantwortlicher"
    }
  ],
  "konkurrenz": {
    "was_alle_machen": "Was bewegt diese Woche alle großen Studios — unabhängig von DE/US und unabhängig vom aktuellen Pair. So wie du es deinem Cutter beim Kaffee erzählen würdest. Beispiel: Drei der großen Studios setzen gerade auf kurze Cast-Reactions, sogar Disney und Netflix steigen ein.",
    "format_trend": "Welcher Cut-Stil oder welche Asset-Form steigt in der Branche gerade — BTS, Cast-Reactions, Kinetic Type, Cold-Open, Event-Recaps. Mit Daten-Beleg, kein Bauchgefühl.",
    "genre_beobachtung": "Performt ein Genre gerade besonders — Horror trägt diese Woche oder Comedy zerläuft, Action sitzt — mit konkretem Beleg aus den Daten.",
    "neu_seit_letzten_wochen": "Was ist neu gegenüber den letzten Wochen — ein konkretes Pattern, ein Format-Wechsel, eine Hook-Form, die plötzlich auftaucht. Wenn nichts klar Neues sichtbar ist, sag das ehrlich."
  },
  "cross_market_insight": {
    "de_vs_us": "Was unterscheidet DE und US diese Woche, mit Daten-Anker — Pflicht-Achse, IMMER ausfüllen",
    "de_vs_uk": "Was unterscheidet DE und UK diese Woche, mit Daten-Anker — null lassen, wenn keine UK-Posts oder keine vergleichbare Datenlage da ist",
    "us_vs_uk": "Was unterscheidet US und UK diese Woche, mit Daten-Anker — null lassen, wenn keine UK-Posts oder keine vergleichbare Datenlage da ist",
    "transfer_opportunity": "Was sollte zwischen den Märkten adaptiert werden — DE↔US, DE↔UK oder US↔UK, jeweils mit klarer Richtung"
  },
  "risks": ["Kurzfassung als String — bleibt aus Backwards-Compat-Gründen"],
  "data_caveats": ["..."],
  "tonalitaet": [
    {
      "adjektiv": "ein Adjektiv aus dem Tonalitäts-Pool",
      "begruendung": "ein Satz, warum dieses Adjektiv die Woche trifft, mit Daten-Anker"
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
    "empfohlene_laengen": "z.B. 15-22s primär, 28s als langer Cut",
    "was_diese_woche": "3-4 Sätze Fließtext zur Schnitt-Beobachtung der Woche. Sprint 7-iter-2: ersetzt die alten must_show/no_go-Listen — Fließtext zwingt zur Erzählung, die Compliance-Stack-Form ist explizit raus. Beispiel-Pattern: 'Was hier auffällt: ... (3-4 Sätze)'."
  },
  "fuer_motion_designer": {
    "caption_style": "Caption-Beobachtung aus den Top-Posts (qualitativ; Länge, Tonfall, Hashtag-Dichte — KEINE Zeichen-Counts oder Hashtag-Counts)",
    "text_overlay": "Empfehlung zu L3 und Text-Einsatz",
    "branding_einsatz": "wie End Card und Logo platziert werden sollten",
    "was_diese_woche": "3-4 Sätze Fließtext zur Motion-/Caption-Beobachtung der Woche. Sprint 7-iter-2."
  },
  "fuer_creative_producer": {
    "strategische_pattern": "übergeordnetes Muster, das diese Woche sichtbar wird",
    "cross_market_chancen": "wo Cuts zwischen den Märkten adaptiert werden sollten — DE↔US, DE↔UK oder US↔UK, in beliebiger Richtung; mehrere Achsen dürfen in einem Satz gebündelt werden",
    "format_empfehlungen": "Formate, Längen, Posting-Rhythmus für die nächste Woche",
    "was_diese_woche": "3-4 Sätze Fließtext zur Producer-Beobachtung der Woche. Sprint 7-iter-2."
  },
  "vergleichbare_posts": [
    {
      "post_id": "URL oder Slug aus historical_top_posts oder top_posts",
      "handle": "z.B. warnerbros",
      "performance_kpi": "z.B. 12k Reaktionen, 28s",
      "relevanz_grund": "warum dieser Post als Referenz für den nächsten Cut dient"
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


GANZ_KONKRET-SEKTION — Hinweise zur Befüllung (v3.0 Lern-Modus):

WICHTIG — Adressat: Trailerhaus ist KEIN Inhouse-Studio für die beobachteten Verleiher. Trailerhaus pitcht und produziert eigene Trailer/Spots. Diese Sektion liefert daher KEINE Anweisungen ('schneide den MK2-Cut auf 22s'), sondern Beobachtungen mit Lern-Take und offenen Fragen — für eigene Projekte und Pitch-Vorbereitung.

Sektion-Titel im Frontend: 'Diese Woche: was funktioniert gut, was nicht'.

- 6 bis 10 Einträge, in logischer Reihenfolge nummeriert (1, 2, 3, ...)
- Jeder Eintrag hat drei Felder:
    (a) pattern: Was ist diese Woche beobachtbar? Konkrete Zahlen-Anker (Reaktionen, Sekunden, Hashtag-Anzahl). Keine Anweisung, sondern Befund.
    (b) lern_take: Was lernen wir daraus? Ein Satz, klare Lehre.
    (c) frage: Welche Frage stellt sich Trailerhaus? Anwendung im eigenen Workflow, Pitch-Argument, oder Test-Idee. Optional — wenn keine sinnvolle Frage abfällt, lieber null als Floskel.
- Tonfall: ruhiger Producer, der einem Kollegen erzählt was bei der Konkurrenz auffällt. Beobachtend, nicht anweisend. Keine 'Du-Ansagen', keine Pitch-Sprache, keine Ausrufezeichen.
- Konkrete Daten nennen: Sekunden, Reaktionszahlen, Aufrufe, Caption-Längen — alles aus dem Datenpaket ableitbar.
- Jeder Eintrag muss aus den vorgelegten Daten ableitbar sein. Wenn du nicht sicher bist: lass den Eintrag weg, statt zu raten.
- bezug: Tag-String oben in der Card. Erlaubte Werte:
    (a) Exakt einer der titel-Strings aus aktuell_im_fokus (z.B. 'The Mandalorian and Grogu', 'Cinderella')
    (b) Einer dieser strukturellen Strings: 'Format-Strategie', 'Posting-Rhythmus', 'Caption-Disziplin', 'Hashtag-Klammer'
  Jeder Eintrag MUSS einen bezug haben.

Wenn die Datengrundlage zu dünn ist (Coverage <30%, <5 Posts pro Markt, keine Cross-Market-Matches in der jeweiligen Achse), sag das klar in data_caveats und gib lieber weniger, dafür belegte Empfehlungen. Setze Felder, für die du keinen Daten-Anker hast, auf null oder gib ein leeres Array — niemals erfinden. Konkret für ``cross_market_insight``: fehlen DE↔UK- oder US↔UK-Matches und auch sonst keine vergleichbare Datenlage, setze ``de_vs_uk`` bzw. ``us_vs_uk`` auf null — die Pflicht-Achse ``de_vs_us`` füllst du immer, weil DE/US fast immer Daten haben.

FEW-SHOT — so klingt ein guter Output (synthetisches Beispiel, kürzer als ein echter Report; in deinem Output bitte vollständig in der Länge):

{
  "headline": "Disney US holt 33k mit *Drawn to You*, DE punktet mit Zoomania und Mulan",
  "tldr": "Disney US hatte auf TT diese Woche einen außergewöhnlich starken Spot — *Drawn to You* ist 113 Sekunden lang und kommt auf rund 33k Reaktionen. DE setzt dagegen auf kurze Clips mit Zoomania und Mulan, je rund 10k Reaktionen, IG bleibt parallel bei vier Posts ohne klaren Marken-Bezug. Genau darin liegt die Beobachtung der Woche: in den USA wirkt ein langer emotionaler Hero-Spot, in Deutschland zünden kurze vertraute Disney-Momente stärker.",
  "aktuell_im_fokus": [
    {
      "titel": "Drawn to You (Make-A-Wish x Disney)",
      "markt": "US",
      "format_typ": "Marken-Spot",
      "kennzahl": "113s, rund 33k Reaktionen, knapp 19% Aktivierung",
      "release_datum": null,
      "verdict": "funktioniert",
      "post_url": "https://tiktok.com/@disney/video/us1"
    },
    {
      "titel": "The Mandalorian and Grogu",
      "markt": "DE",
      "format_typ": "Kino-Reminder",
      "kennzahl": "Berlin-Premiere, 62s, 173k Aufrufe bei nur 381 Reaktionen",
      "release_datum": "20. Mai",
      "verdict": "kommt nicht an",
      "post_url": "https://tiktok.com/@disneyde/video/de1"
    },
    {
      "titel": "Zoomania 2",
      "markt": "DE",
      "format_typ": "Kurzer Clip mit bekanntem Titel",
      "kennzahl": "22s, rund 10k Reaktionen, etwa 15% Aktivierung",
      "release_datum": null,
      "verdict": "funktioniert",
      "post_url": "https://tiktok.com/@disneyde/video/de2"
    },
    {
      "titel": "Tron: Ares",
      "markt": "US",
      "format_typ": "Kampagnen-Klammer",
      "kennzahl": "zwei Cuts (25s und 18s), je rund 6k Reaktionen, acht Posts mit dem Tag",
      "release_datum": "10. Oktober",
      "verdict": "noch ausbaufähig",
      "post_url": "https://tiktok.com/@disney/video/us4"
    }
  ],
  "ganz_konkret": [
    {
      "nummer": 1,
      "pattern": "Der DE-Mandalorian-Cut läuft 56 Sekunden und kommt auf rund 1k Reaktionen. Der US-Cast-Reaction-Cut liegt bei 22 Sekunden und holt rund 11k — bei nur halb so vielen Aufrufen. Die kurze Variante zieht zehnmal stärker.",
      "lern_take": "Bei Franchise-Material zieht der kurze Cast-Cut, die lange Faktenform kommt nicht an.",
      "frage": "Wie kurz schneiden wir Franchise-Material in eigenen Action-Trailern? Bauen wir 22s-Varianten als Standard?",
      "bezug": "The Mandalorian and Grogu"
    },
    {
      "nummer": 2,
      "pattern": "Disney US zieht mit *Drawn to You* (113s) rund 33k Reaktionen. Kein Trailer-Beat, nur eine emotionale Geschichte mit Datum-Anker.",
      "lern_take": "Lange Marken-Spots können massive Reaktion holen, wenn der emotionale Anker wirklich sitzt.",
      "frage": "Bauen wir solche emotionalen Hero-Slots für eigene Streaming-Pitches? Lohnt das für Disney+ DE oder Prime Video DE als wiederkehrendes Format?",
      "bezug": "Drawn to You (Make-A-Wish x Disney)"
    },
    {
      "nummer": 3,
      "pattern": "Top-DE-Post (Mandalorian) hatte eine deutlich längere Caption mit Hashtag-Stapel und kommt auf rund 1k Reaktionen. Top-US-Post (Drawn to You) lag bei einer kurzen erzählerischen Caption und holt 33k Reaktionen.",
      "lern_take": "Lange Captions mit Hashtag-Stapel kommen nicht an, kurze klare Captions wirken stärker.",
      "frage": "Wie diszipliniert sind unsere eigenen Captions? Setzen wir intern eine kürzere Variante als Standard?",
      "bezug": "Caption-Disziplin"
    },
    {
      "nummer": 4,
      "pattern": "DE-Marvel-Post läuft 17 Sekunden mit Kinetic-Format und holt rund 470 Reaktionen bei nur 8k Aufrufen — die Reaktionsquote ist hoch, aber die Reichweite bleibt klein. Title-Card sitzt direkt am Anfang.",
      "lern_take": "Bei kurzen Action-Cuts kostet die Title-Card am Anfang Reichweite, Cold-Open ohne Logo holt mehr.",
      "frage": "Bauen wir bei eigenen Action-Trailern Cold-Open-Varianten ohne Title-Card als A/B-Test?",
      "bezug": "Format-Strategie"
    },
    {
      "nummer": 5,
      "pattern": "Mandalorian-Premiere Berlin läuft 62 Sekunden und kommt auf rund 380 Reaktionen bei 173k Aufrufen. Reichweite passt, aber die Reaktion bleibt aus — der Cast-Beat geht im Mitschnitt unter.",
      "lern_take": "Veranstaltungs-Mitschnitte über 60 Sekunden kommen nicht an, der Cast-Beat verteilt sich zu sehr.",
      "frage": "Wenn wir selbst Premieren-Material für Trailerhaus-Kunden bauen — wie kurz packen wir den Cast-Beat? Mitschnitt oder Einzel-Schnipsel?",
      "bezug": "The Mandalorian and Grogu"
    },
    {
      "nummer": 6,
      "pattern": "Tron: Ares (US) fährt zwei Cuts mit 25 und 18 Sekunden, beide jeweils rund 6k Reaktionen, acht Posts mit dem Tag im Fenster — dominantestes Hashtag im US-Kanal.",
      "lern_take": "Visuell-getriebenes Material unter 25 Sekunden mit konsequenter Hashtag-Klammer wirkt über eine Kampagnen-Woche zuverlässig.",
      "frage": "Wenn wir für Sci-Fi-Verleiher pitchen — können wir das 18-25s-Format plus Klammer-Hashtag als Vorlage anbieten?",
      "bezug": "Tron: Ares"
    },
    {
      "nummer": 7,
      "pattern": "US-Top-Performer liegen meist im 15-30-Sekunden-Bereich, pro Titel kommen mehrere Cuts in unterschiedlichen Längen. DE liegt fast komplett im mittleren Bereich um 30-60 Sekunden mit nur einer Variante pro Titel.",
      "lern_take": "Eine einzige Cut-Länge pro Titel ist ein Reichweiten-Risiko, der Feed strafft sich auf wenige Beats.",
      "frage": "Wie lassen sich kurze Zweit-Varianten in eigene Trailerhaus-Workflows einbauen, ohne dass die Schnittzeit verdoppelt wird?",
      "bezug": "Posting-Rhythmus"
    }
  ],
  "trends": [
    {
      "name": "Kurze Anfänge unter 15 Sekunden ziehen rein",
      "evidence": "Disney US zeigt mit kurzen Hero-Slots: ein einziger Bild-Moment in den ersten Sekunden bringt mehr als ein 30-60s-Cut",
      "implication_for_creation": "Wir sollten eine 12-15s Cold-Open-Variante schneiden und gegen die 22s-Version testen."
    }
  ],
  "actions": [
    {
      "what": "DE-Cut auf 22 Sekunden straffen",
      "why": "Der DE-56s-Cut liegt bei rund 1k Reaktionen, der US-22s-Cut bei rund 11k — die kurze Variante zieht klar stärker",
      "for_whom": "Cutter Mandalorian"
    }
  ],
  "konkurrenz": {
    "was_alle_machen": "Diese Woche setzen drei der sechs großen Studios auf kurze Cast-Reactions — Sony, Universal und Paramount. Warner bleibt bei langen Marken-Spots. Klar zweigeteilt: kurze Anfänge oder emotionale Langformate, dazwischen passiert wenig.",
    "format_trend": "BTS-Material in 12-18 Sekunden steigt — fünf von zehn Top-Posts über alle Pairs sind BTS-Schnipsel mit Cast. Vor vier Wochen waren es zwei.",
    "genre_beobachtung": "Sci-Fi kommt an: Tron: Ares (acht US-Posts) und ein Sony-Project-Hail-Mary-Teaser laufen ihre Wochen sauber durch. Comedy bleibt verhalten — selbst Sony Glennkill kommt nur auf rund 25k Reaktionen.",
    "neu_seit_letzten_wochen": "Cold-Opens mit Datums-Anker (kein Trailer-Beat, nur Datum plus Bild) sind neu — Disney US liegt damit bei rund 267k Reaktionen. Vor vier Wochen war das Format nicht da."
  },
  "cross_market_insight": {
    "de_vs_us": "DE läuft verhaltener (rund 1k vs rund 11k Reaktionen), gleiche Hashtag-Logik, aber etwa eine halbe Minute länger im Cut.",
    "de_vs_uk": "UK sitzt zwischen DE und US (rund 4k Reaktionen), gleiche Cut-Länge wie DE, aber kürzere Captions wie US — der Mittelweg funktioniert.",
    "us_vs_uk": "UK übernimmt die US-Hook-Form fast eins zu eins, dreht aber spürbar bei den Captions auf deutlich kürzer.",
    "transfer_opportunity": "US-Rhythmus auf DE übertragen, deutsche Caption-Form behalten. UK-Caption-Kürze als Indiz, dass auch DE bei der Caption sparen darf."
  },
  "risks": ["Coverage moderat"],
  "data_caveats": ["Nur zwei DE-Posts im Fenster — Trend ist Indiz, nicht Beweis"],
  "tonalitaet": [
    {
      "adjektiv": "präzise",
      "begruendung": "Top-US-Posts arbeiten mit klaren 22s-Hooks, kein narrativer Leerlauf"
    },
    {
      "adjektiv": "emotional",
      "begruendung": "Mandalorian-Hashtag dominiert, Caption-Sprache ist Familie-fokussiert"
    }
  ],
  "watch_outs": [
    {
      "watch_out": "Tron-Cut (US, 18s) hat hohe Reaktionsquote trotz niedriger Absolutzahlen",
      "konsequenz": "Visual-Hook-Format als Komplement testen, nicht als Hauptcut"
    }
  ],
  "fuer_cutter": {
    "schnitt_pace": "Die starken Cuts liegen diese Woche entweder kurz unter 25 Sekunden oder bei anderthalb Minuten. Der mittlere Bereich um 30 bis 60 Sekunden verliert die Aufmerksamkeit — drei Mandalorian-Erinnerungen fahren zwar Reichweite, aber die Reaktion bleibt aus.",
    "hook_strategie": "Bei kurzen Clips mit bekannten Titeln der vertraute Cast-Moment in den ersten zwei Sekunden, kein Title-Card davor. Bei Marken-Spots eine konkrete Person als emotionaler Anker — nicht der Logo-Reveal, sondern das Kind, das malt.",
    "empfohlene_laengen": "Kurz unter 25 Sekunden für vertraute Titel, anderthalb Minuten für emotionale Marken-Spots wenn die Story es hergibt. Alles dazwischen vermeiden.",
    "was_diese_woche": "Was hier auffällt: die Mandalorian-Erinnerungen liegen zeitlich genau im schwierigen Mittelbereich und fahren Reichweite ohne Reaktion. Wenn der Cut nicht klar in eines der zwei Lager fällt — kurz und bekannt, oder lang und emotional — kommt er nicht an. Das ist die wichtigste Beobachtung für die kommende Woche."
  },
  "fuer_motion_designer": {
    "caption_style": "DE-Captions sind kürzer und stärker auf Hashtags, US erzählt mehr in der Caption — bei Marken-Spots oft nur ein einzelner Tag, dafür eine ganze Geschichte im Text. Die US-Form wirkt diese Woche stärker, weil sie eine echte Erzählung beginnt statt nur zu listen.",
    "text_overlay": "Bei kurzen Clips mit bekannten Titeln kein Text-Overlay in den ersten Sekunden — der Cast-Moment soll allein wirken. Bei Marken-Spots am Ende eine klare Datums- oder Plattform-Zeile, sonst kein Overlay.",
    "branding_einsatz": "End Card kurz und einmalig am Ende, Logo zentriert. Bei Erinnerungs-Cuts auf das Datum reduzieren, Logo nicht doppeln.",
    "was_diese_woche": "Was hier auffällt: die US-Captions arbeiten erzählerisch, DE listet eher. Für eigene Cuts lohnt es sich, erst die Caption-Idee zu schreiben und dann erst die Hashtags hinzuzufügen — nicht umgekehrt. Beim Branding gilt diese Woche: weniger ist mehr, eine End Card reicht."
  },
  "fuer_creative_producer": {
    "strategische_pattern": "Die Woche zeigt eine klare Zwei-Lager-Logik: kurze Clips mit bekannten Titeln holen zuverlässig Reaktion und lassen sich wiederholen, lange Marken-Spots holen die höchste Aktivierung — aber nur, wenn die emotionale Idee sitzt. Der mittlere Bereich lohnt nicht.",
    "cross_market_chancen": "DE hat das Pattern mit kurzen vertrauten Clips längst sauber drauf, hat aber kein eigenes emotionales Hero-Asset diese Woche. Genau dort liegt die Lücke — und damit das Argument für einen Marken-Spot-Pitch bei deutschen Verleihern oder Streaming-Anbietern. Das US-Modell zeigt, dass der Aufwand sich rechnet, wenn die Idee sitzt.",
    "format_empfehlungen": "Pro Verleih-Kunde zwei Standardpakete: kurze Clips mit bekannten Titeln als Wochen-Format und ein emotionaler Spot pro Quartal mit konkreter Person als Anker. Mittellange Erinnerungs-Cuts nur dort, wo der Kunde sie kampagnenseitig wirklich braucht.",
    "was_diese_woche": "Was hier auffällt: die zwei Lager sind nicht nur Cut-Längen, sondern auch Produktions-Modelle. Kurz und vertraut ist Wochen-Geschäft, lang und emotional ist Quartals-Investment. Wenn DE einen Hero-Spot wagt, könnte das die Lücke schließen, die diese Woche sichtbar wird."
  },
  "vergleichbare_posts": [
    {
      "post_id": "https://tiktok.com/@disney/video/us1",
      "handle": "disney",
      "performance_kpi": "rund 11k Reaktionen, 22s, etwa 10% Aktivierung",
      "relevanz_grund": "Goldstandard für die 22s-Hook, Referenz für den DE-Recut"
    }
  ]
}
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
    likes = int(post.visible_likes or 0)
    comments = int(post.visible_comments or 0)
    if platform == "youtube":
        return (likes + comments) / views
    saves = int(post.visible_bookmarks or 0)
    return (likes + comments + saves) / views


def _ranked_posts_for_channel(
    posts: list[Post],
    platform: str,
    *,
    session: Optional[Session] = None,
    limit: int = 10,
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
    ranked_posts = _ranked_posts_for_channel(
        posts, platform, session=session, limit=ranked_posts_n
    )

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
    )


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
        "Wörter Gesamtoutput. Halte dich an Voice, Glossar und Anti-Pattern aus "
        "dem System-Prompt. Plattform-Vergleich ist erlaubt, wenn er sichtbar "
        "trägt — siehe Multi-Plattform-Klausel im System-Prompt. Filmtitel "
        "(in den Top-Posts in eckigen Klammern + Sternchen markiert) darfst "
        "du in Headline/TLDR mit Sternchen-Markup nutzen, wenn vorhanden — "
        "siehe Filmtitel-Klausel.\n\n"
        # Sprint 7 — Voice-2.5 Reminder direkt im User-Prompt: erzähl es,
        # wie du es einem Kollegen im Trailerhaus-Schnittraum bei einem
        # Kaffee sagen würdest. Persönlich, konkret, von Mensch zu Mensch.
        # Wiederholt absichtlich den Anker aus VOICE-IDENTITÄT im
        # System-Prompt — der Reminder direkt vor den Daten greift
        # erfahrungsgemäß stärker als die Sektion 1500 Tokens weiter oben.
        "Erinnerung Voice 2.5: erzähle, wie du es einem Kollegen im "
        "Trailerhaus-Schnittraum bei einem Kaffee sagen würdest. "
        "Persönlich, konkret, von Mensch zu Mensch — keine Berater-Folie, "
        "keine Doppel-Beziffung in einem Atemzug, keine Compliance-Listen.\n\n"
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


def _estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    in_rate = settings.anthropic_opus_input_per_1k_usd or 0.0
    out_rate = settings.anthropic_opus_output_per_1k_usd or 0.0
    return round(
        (input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate,
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
    call_extra = {
        "pair": pair_key,
        "window_days": window_days,
        "model": model,
        "prompt_chars": len(user_prompt),
    }

    def _call_and_extract(attempt_index: int) -> tuple[Any, str]:
        """One Anthropic call + content-block extraction. ``attempt_index``
        is 0 for the initial call, 1..N for retries — surfaced in the
        ``brief_anthropic_call_*`` events so retries are correlatable in
        Railway alongside the original cron line.

        Sprint 28.05.2026 (Structured-Outputs-Haertung): Call laeuft jetzt
        ueber ``messages_create_strict_json`` mit Tool-Use + forciertem
        ``tool_choice``. Die Anthropic-API validiert das Tool-Argument
        gegen ``LLMReport.model_json_schema()`` und gibt es als
        ``tool_use``-Block mit ``.input`` (bereits geparstes Dict) zurueck.

        Extraktion: bevorzugt den ``tool_use``-Block — sein ``.input`` wird
        zu JSON-Text reserialisiert, sodass der bestehende strict→lenient-
        Parser (``_try_parse_llm_json``) ihn unveraendert als Strict-Pass
        konsumiert. Findet sich kein ``tool_use``-Block (sollte
        API-erzwungen nicht passieren, aber falls Anthropic mal abweicht
        oder ein Mock im Test die alte Text-Form liefert), faellt die
        Extraktion auf reine ``text``-Bloecke zurueck. Der nachgelagerte
        Retry-Loop + Lenient-Parser bleiben damit als Sicherheitsnetz
        stehen, kein toter Code.
        """
        attempt_extra = {**call_extra, "attempt": attempt_index}
        logger.info("brief_anthropic_call_start", extra=attempt_extra)
        started = time.monotonic()
        try:
            msg = messages_create_strict_json(
                model=model,
                system=SYSTEM_PROMPT,
                user_message=user_prompt,
                tool_name=_BRIEF_TOOL_NAME,
                tool_description=_BRIEF_TOOL_DESCRIPTION,
                input_schema=_BRIEF_TOOL_INPUT_SCHEMA,
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

    # Sprint M2 — JSON-Parse-Retry-Logic. The 17.05 Disney + 25.05
    # warnerbros incidents showed Anthropic intermittently returns a 200
    # with invalid JSON mid-document (Fall (d) — no truncation). The flow
    # below tries strict parse, falls back to a lenient substring extract
    # (covers preamble/postamble), and re-issues up to ``MAX_RECALLS``
    # fresh ``messages_create_strict_json`` calls if the response still won't
    # parse. Each re-call is a real Anthropic call (Anthropic doesn't
    # honor idempotency keys — see anthropic_client.py:140 docstring) so
    # ``record_anthropic_call`` fires per call: F0.7 cost cap captures
    # every retry without special-casing. If all attempts fail, the
    # function returns ``llm_output=None`` exactly as before and
    # ``_persist_report`` skips persistence — the cron loop survives.
    #
    # Sprint 28.05.2026 (Structured-Outputs-Haertung): mit Tool-Use +
    # forciertem ``tool_choice`` ist die JSON-Validitaet der Antwort jetzt
    # API-erzwungen — der Retry-Loop und der Lenient-Parser bleiben aber
    # als Sicherheitsnetz erhalten, falls die API ausnahmsweise einen
    # Text-Block statt Tool-Use liefert (Fallback-Extraktions-Pfad in
    # ``_call_and_extract``).
    MAX_RECALLS = 2
    call_attempts: list[tuple[Any, str]] = []

    message, raw_text = _call_and_extract(attempt_index=0)
    call_attempts.append((message, raw_text))
    parsed, parse_error, parse_path = _try_parse_llm_json(raw_text)

    for retry_n in range(1, MAX_RECALLS + 1):
        if parsed is not None:
            break
        logger.warning(
            "insight-engine-json-parse-retry",
            extra={
                "pair": pair_key,
                "attempt": retry_n,
                "max_attempts": MAX_RECALLS,
                "error_type": type(parse_error).__name__ if parse_error else "Unknown",
                "error_message": str(parse_error)[:200] if parse_error else "",
            },
        )
        try:
            message, raw_text = _call_and_extract(attempt_index=retry_n)
        except Exception as call_exc:
            # A re-call itself raised (rate-limit exhaustion, API error).
            # Stop retrying — finaler Fallback (persist-skip) übernimmt.
            logger.error(
                "insight-engine-json-parse-recall-aborted",
                extra={
                    "pair": pair_key,
                    "attempt": retry_n,
                    "error_type": type(call_exc).__name__,
                    "error_message": str(call_exc)[:200],
                },
            )
            break
        call_attempts.append((message, raw_text))
        parsed, parse_error, parse_path = _try_parse_llm_json(raw_text)

    llm_output: Optional[LLMReport] = None
    raw_for_response: Optional[str] = None

    if parsed is not None:
        # Surface which path rescued the parse — strict vs lenient (A) vs
        # re-call (B) vs A-after-B. Only logged when something non-trivial
        # happened so the happy-path log stream stays quiet.
        if parse_path == "lenient" or len(call_attempts) > 1:
            logger.info(
                "insight-engine-json-parse-recovered",
                extra={
                    "pair": pair_key,
                    "parse_path": parse_path,
                    "anthropic_calls": len(call_attempts),
                    "recall_count": len(call_attempts) - 1,
                },
            )
        try:
            llm_output = LLMReport.model_validate(parsed)
        except ValueError as exc:
            # JSON ist valide, aber Pydantic-Schema schlaegt fehl (z.B.
            # fehlende Required-Felder, falsche Typen). Andere Failure-
            # Mode als JSONDecodeError — kein ``.pos`` verfuegbar, also
            # nur first_500 zur Diagnose. Strukturell-getrenntes Log-Event
            # damit Railway-Filter zwischen "Anthropic gibt Mist" und
            # "unser Schema passt nicht" unterscheiden kann. Schema-Fehler
            # werden NICHT erneut gecallt — das ist ein deterministischer
            # Prompt-/Schema-Drift, kein transienter Anthropic-Glitch.
            cleaned_for_log = _strip_codefence(raw_text)
            logger.error(
                "insight-engine-schema-validation-failed",
                extra={
                    "error_message": str(exc)[:500],
                    "raw_response_length": len(cleaned_for_log),
                    "raw_response_first_500": cleaned_for_log[:500],
                },
            )
            raw_for_response = raw_text
    else:
        # Finaler Fallback nach allen Retries. PR #154 hatte diesen Log
        # angelegt; M2 zieht ihn unverändert nach hinten (nach allen
        # Retries) und ergänzt ``anthropic_calls`` + ``recall_count``
        # damit künftige Postmortems sehen, ob die Re-Calls schon gelaufen
        # sind oder ob der Erstfehler durchschlug. Die ``raw_response_*``-
        # Felder kommen aus dem LETZTEN Versuch — am informativsten für
        # die "kommt das wieder?"-Frage.
        cleaned = _strip_codefence(raw_text)
        pos = parse_error.pos if parse_error and parse_error.pos is not None else 0
        logger.error(
            "insight-engine-json-parse-failed",
            extra={
                "pair": pair_key,
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

    # Cost accounting: jeder Call wird einzeln in costlog erfasst, damit
    # F0.7-Cap die wahre Spend-Summe sieht (eine Pair-Generation kann
    # jetzt bis zu MAX_RECALLS+1 Calls produzieren). Die Token-Summe wird
    # für ``InsightReport.cost_usd_estimate`` aggregiert — auf der
    # Persist-Skip-Strecke spiegelt sie damit den echten "bezahlter
    # Leerlauf" wider, nicht nur den letzten Call.
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
            operation="weekly_brief",
            meta={
                "pair_key": agg.pair_key,
                "iso_week": agg.iso_week,
                "iso_year": agg.iso_year,
            },
        )

    input_tokens = input_tokens_total
    output_tokens = output_tokens_total
    cost = (
        _estimate_cost_usd(input_tokens, output_tokens)
        if (input_tokens or output_tokens)
        else None
    )

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
        input_tokens=input_tokens or None,
        output_tokens=output_tokens or None,
        raw_llm_text=raw_for_response,
    )


def _hydrate_from_persisted(row: InsightReportRow, *, window_days: int) -> InsightReport:
    """Rebuild a Pydantic ``InsightReport`` from a stored ``insight_report``
    row. Used by the cache hit path of ``generate_and_persist_report`` —
    the JSONB-serialised aggregation/llm_output blobs round-trip through
    ``model_validate`` so consumers see the same shape they would from a
    fresh generate call. ``cost_usd_estimate`` is reconstructed from the
    ``cost_usd_cents`` integer that the persistence layer stores.
    """
    aggregation = PairAggregation.model_validate(row.aggregation)
    llm_output = LLMReport.model_validate(row.llm_output) if row.llm_output else None
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
    max_tokens: int = 12000,
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
    "aggregate_pair",
    "generate_weekly_report",
    "generate_and_persist_report",
]
