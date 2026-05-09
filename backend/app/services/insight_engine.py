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
                # Production-confirmed handle (channels_perplexity_2026_05_03.csv).
                {"handle": "warnerbros", "market": "US"},
                # DE handle per Wolf brief; aliasing handled by the case-insensitive
                # lookup. If the actual stored handle differs, ``aggregate_pair``
                # records that in ``notes`` rather than failing.
                {"handle": "warnerbrosdeutschland", "market": "DE"},
            ],
            "instagram": [
                {"handle": "warnerbros", "market": "US"},
                {"handle": "warnerbrosde", "market": "DE"},
            ],
            "youtube": [
                {"handle": "WarnerBrosPictures", "market": "US"},
                {"handle": "WarnerBrosDE", "market": "DE"},
            ],
        },
        # Backwards-Compat mirror — TikTok = first platform.
        "platform": "tiktok",
        "channels": [
            {"handle": "warnerbros", "market": "US"},
            {"handle": "warnerbrosdeutschland", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "sonypictures": {
        "label": "sonypictures DE+US",
        "platforms": {
            "tiktok": [
                {"handle": "sonypictures", "market": "US"},
                {"handle": "sonypicturesgermany", "market": "DE"},
            ],
            "instagram": [
                {"handle": "sonypictures", "market": "US"},
                {"handle": "sonypicturesde", "market": "DE"},
            ],
            "youtube": [
                {"handle": "SonyPicturesEntertainment", "market": "US"},
                {"handle": "SonyPicturesGermany", "market": "DE"},
            ],
        },
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
        "platforms": {
            "tiktok": [
                {"handle": "primevideo", "market": "US"},
                {"handle": "primevideode", "market": "DE"},
            ],
            "instagram": [
                {"handle": "primevideo", "market": "US"},
                {"handle": "primevideode", "market": "DE"},
            ],
            # No DE-side YouTube channel for Prime — single-channel platform
            # entry. ``_aggregate_platform`` handles the missing-market case
            # by leaving ``de_channel`` None.
            "youtube": [
                {"handle": "PrimeVideo", "market": "US"},
            ],
        },
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
        "platforms": {
            "tiktok": [
                # Wolf-spec handle. The whitelist-expansion migration registers
                # ``disneystudios`` and ``disneyanimation`` for US Disney; if
                # ``disney`` is not the production handle for the US side,
                # ``aggregate_pair`` will surface that in ``notes``.
                {"handle": "disney", "market": "US"},
                {"handle": "disneyde", "market": "DE"},
            ],
            "instagram": [
                {"handle": "disney", "market": "US"},
                # IG-DE handle differs from TikTok (``disneyde``) — Disney runs
                # ``disneydeutschland`` on Instagram.
                {"handle": "disneydeutschland", "market": "DE"},
            ],
            # No DE-side YouTube channel for Disney's main studios feed.
            "youtube": [
                {"handle": "WaltDisneyStudios", "market": "US"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "disney", "market": "US"},
            {"handle": "disneyde", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "netflix": {
        "label": "netflix DE+US",
        "platforms": {
            "tiktok": [
                {"handle": "netflix", "market": "US"},
                {"handle": "netflixde", "market": "DE"},
            ],
            "instagram": [
                {"handle": "netflix", "market": "US"},
                {"handle": "netflixde", "market": "DE"},
            ],
            "youtube": [
                {"handle": "Netflix", "market": "US"},
                {"handle": "NetflixDE", "market": "DE"},
            ],
        },
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
        "platforms": {
            "tiktok": [
                # US handle is ``paramountpics``, not ``paramountpictures``
                # (per migration e5d8f1a36b40 + Wolf brief).
                {"handle": "paramountpics", "market": "US"},
                {"handle": "paramountpicturesgermany", "market": "DE"},
            ],
            "instagram": [
                {"handle": "paramountpics", "market": "US"},
                # IG-DE uses underscores: ``paramount_pictures_germany``.
                {"handle": "paramount_pictures_germany", "market": "DE"},
            ],
            # No DE-side YouTube channel for Paramount Pictures.
            "youtube": [
                {"handle": "ParamountPictures", "market": "US"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "paramountpics", "market": "US"},
            {"handle": "paramountpicturesgermany", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    },
    "universalpictures": {
        "label": "universalpictures DE+US",
        # Universal stays disabled — no platforms-dict yet. When the pair
        # activates, fill in TT/IG/YT entries analogously to the others.
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
- Cross-Market Match / Match-Key (sag: derselbe Titel in DE und US, gleicher Spot in beiden Märkten)
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
- "Discovery-Clip" (statt früher "Discovery-Schnipsel")
- "Kino-Reminder" (Trailer-Erinnerung mit Datum)
- "Ankündigungs-Post" (Format-Bruch, einzelner Marken-Statement)
- weitere möglich, aber NICHT "Format-Block" / "Format-Spur" / "Backkatalog-Anriss" — siehe VERBOTENE BERATER-VOKABEL.

kennzahl (Format-Empfehlung):
- Einzeiler im Stil "X Sekunden, Y Reaktionen, Z% Aktivierung" — Doppel-Beziffung hier explizit erlaubt (Card ist der rohe Datenpunkt).

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
    "de_vs_us": "Was unterscheidet die Märkte diese Woche, mit Daten-Anker",
    "transfer_opportunity": "Was sollte aus US für DE adaptiert werden oder umgekehrt"
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
    "schnitt_pace": "Beobachtung zum Rhythmus, abgeleitet aus Top-Posts und Längen-Buckets — in Cutter-Sprache (kurze Cuts funktionieren, lange laufen zu lang, etc.)",
    "hook_strategie": "welche Hook-Form trägt diese Woche (Cold-Open, Title-First, BTS, Cast-Reaction, ...)",
    "empfohlene_laengen": "z.B. 15-22s primär, 28s als langer Cut",
    "must_show": ["Element, das im Cut sein muss, mit Begründung aus den Daten"],
    "no_go": ["Element, das NICHT trägt — Begründung aus den Daten"]
  },
  "fuer_motion_designer": {
    "caption_style": "Caption-Beobachtung aus den Top-Posts (Länge, Tonfall, Hashtag-Dichte)",
    "text_overlay": "Empfehlung zu L3 und Text-Einsatz",
    "branding_einsatz": "wie End Card und Logo platziert werden sollten"
  },
  "fuer_creative_producer": {
    "strategische_pattern": "übergeordnetes Muster, das diese Woche sichtbar wird",
    "cross_market_chancen": "wo DE-Cuts US-Patterns adaptieren sollten oder umgekehrt",
    "format_empfehlungen": "Formate, Längen, Posting-Rhythmus für die nächste Woche"
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

Wenn die Datengrundlage zu dünn ist (Coverage <30%, <5 Posts pro Markt, keine Cross-Market-Matches), sag das klar in data_caveats und gib lieber weniger, dafür belegte Empfehlungen. Setze Felder, für die du keinen Daten-Anker hast, auf null oder gib ein leeres Array — niemals erfinden.

FEW-SHOT — so klingt ein guter Output (synthetisches Beispiel, kürzer als ein echter Report; in deinem Output bitte vollständig in der Länge):

{
  "headline": "Disney US trägt mit *Drawn to You*, DE setzt auf kurze Clips mit bekannten Titeln",
  "tldr": "Disney US hatte auf TT diese Woche einen außergewöhnlich starken Spot — *Drawn to You* ist 113 Sekunden lang und kommt auf rund 33k Reaktionen. DE setzt dagegen auf kurze Clips mit Zoomania und Mulan, je rund 10k Reaktionen, IG bleibt parallel bei vier Posts ohne klaren Marken-Bezug. Genau darin liegt die Beobachtung der Woche: in den USA funktioniert ein langer emotionaler Hero-Spot, in Deutschland tragen kurze vertraute Disney-Momente stärker.",
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
      "kennzahl": "Berlin-Premiere, 62s, 173k Aufrufen aber nur 381 Reaktionen",
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
      "format_typ": "Discovery-Clip",
      "kennzahl": "zwei Cuts (25s und 18s), je etwa 6k Reaktionen, acht Posts mit dem Tag",
      "release_datum": "10. Oktober",
      "verdict": "noch ausbaufähig",
      "post_url": "https://tiktok.com/@disney/video/us4"
    }
  ],
  "ganz_konkret": [
    {
      "nummer": 1,
      "pattern": "Der DE-Mandalorian-Cut läuft 56 Sekunden und kommt auf rund 1k Reaktionen. Der US-Cast-Reaction-Cut liegt bei 22 Sekunden und holt rund 11k — bei nur halb so vielen Aufrufen. Die kurze Variante kommt zehnmal stärker an.",
      "lern_take": "Bei Franchise-Material zieht der kurze Cast-Cut, die lange Faktenform kommt im Feed nicht durch.",
      "frage": "Wie kurz schneiden wir Franchise-Material in eigenen Action-Trailern? Bauen wir 22s-Varianten als Standard?",
      "bezug": "The Mandalorian and Grogu"
    },
    {
      "nummer": 2,
      "pattern": "Disney US zieht mit *Drawn to You* (113s) rund 33k Reaktionen. Kein Trailer-Beat, nur eine emotionale Geschichte mit Datum-Anker.",
      "lern_take": "Lange Marken-Spots können massive Reaktion holen, wenn der emotionale Anker wirklich trägt.",
      "frage": "Bauen wir solche emotionalen Hero-Slots für eigene Streaming-Pitches? Lohnt das für Disney+ DE oder Prime Video DE als wiederkehrendes Format?",
      "bezug": "Drawn to You (Make-A-Wish x Disney)"
    },
    {
      "nummer": 3,
      "pattern": "Top-DE-Post (Mandalorian) hatte 134 Zeichen Caption plus vier Hashtags und rund 1k Reaktionen. Top-US-Post (Drawn to You) lag bei 65 Zeichen mit einem Hashtag und 33k Reaktionen — kürzere Caption, deutlich höhere Reaktion.",
      "lern_take": "Lange Captions mit Hashtag-Stapel kommen im Feed nicht durch, kurze klare Captions tragen.",
      "frage": "Wie diszipliniert sind unsere eigenen Captions? Setzen wir intern eine 90-Zeichen-Regel als Standard?",
      "bezug": "Caption-Disziplin"
    },
    {
      "nummer": 4,
      "pattern": "DE-Marvel-Post läuft 17 Sekunden mit Kinetic-Format und holt rund 470 Reaktionen bei nur 8k Aufrufen — die Reaktionsquote ist hoch, aber die Reichweite hält nicht. Title-Card sitzt direkt am Anfang.",
      "lern_take": "Bei kurzen Action-Cuts kostet die Title-Card am Anfang Reichweite, Cold-Open ohne Logo holt mehr.",
      "frage": "Bauen wir bei eigenen Action-Trailern Cold-Open-Varianten ohne Title-Card als A/B-Test?",
      "bezug": "Format-Strategie"
    },
    {
      "nummer": 5,
      "pattern": "Mandalorian-Premiere Berlin läuft 62 Sekunden und holt rund 380 Reaktionen bei 173k Aufrufen. Reichweite passt, aber die Reaktion bleibt aus — der Cast-Beat geht im Sammel-Cut verloren.",
      "lern_take": "Veranstaltungs-Mitschnitte über 60 Sekunden kommen im Feed nicht durch, der Cast-Beat verteilt sich zu sehr.",
      "frage": "Wenn wir selbst Premieren-Material für Trailerhaus-Kunden bauen — wie kurz packen wir den Cast-Beat? Sammel-Cut oder Einzel-Schnipsel?",
      "bezug": "The Mandalorian and Grogu"
    },
    {
      "nummer": 6,
      "pattern": "Tron: Ares (US) fährt zwei Cuts mit 25 und 18 Sekunden, beide jeweils rund 6k Reaktionen, acht Posts mit dem Tag im Fenster — dominantestes Hashtag im US-Kanal.",
      "lern_take": "Visuell-getriebenes Material unter 25 Sekunden mit konsequenter Hashtag-Klammer lässt sich über eine Kampagnen-Woche tragen.",
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
      "why": "Der DE-56s-Cut liegt bei rund 1k Reaktionen, der US-22s-Cut bei rund 11k — die kurze Variante kommt klar stärker an",
      "for_whom": "Cutter Mandalorian"
    }
  ],
  "konkurrenz": {
    "was_alle_machen": "Diese Woche setzen drei der sechs großen Studios auf kurze Cast-Reactions — Sony, Universal und Paramount. Warner bleibt bei langen Marken-Spots. Klar zweigeteilt: kurze Anfänge oder emotionale Langformate, dazwischen passiert wenig.",
    "format_trend": "BTS-Material in 12-18 Sekunden steigt — fünf von zehn Top-Posts über alle Pairs sind BTS-Schnipsel mit Cast. Vor vier Wochen waren es zwei.",
    "genre_beobachtung": "Sci-Fi kommt an: Tron: Ares (acht US-Posts) und ein Sony-Project-Hail-Mary-Teaser tragen ihre Wochen. Comedy bleibt verhalten — selbst Sony Glennkill kommt nur auf rund 25k Reaktionen.",
    "neu_seit_letzten_wochen": "Cold-Opens mit Datums-Anker (kein Trailer-Beat, nur Datum plus Bild) sind neu — Disney US liegt damit bei rund 267k Reaktionen. Vor vier Wochen war das Format nicht da."
  },
  "cross_market_insight": {
    "de_vs_us": "DE läuft verhaltener (rund 1k vs rund 11k Reaktionen), gleiche Hashtag-Logik, aber etwa eine halbe Minute länger im Cut.",
    "transfer_opportunity": "US-Rhythmus auf DE übertragen, deutsche Caption-Form behalten."
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
    "schnitt_pace": "Was diese Woche auffällt: Top-Performer liegen meist bei 15 bis 30 Sekunden; alles über einer Minute kommt im Feed nicht durch. Der mittlere Bereich um 30 bis 60 Sekunden ist diese Woche schwierig — die Berlin-Premiere bringt zwar Reichweite, aber die Leute reagieren kaum.",
    "hook_strategie": "Cold-Open mit Cast-Beat in den ersten zwei Sekunden, kein Title-Card davor",
    "empfohlene_laengen": "22 Sekunden primär, 12 Sekunden als kurze Variante zum Reinzeigen",
    "must_show": ["Hauptkonflikt im ersten Beat", "Logo-Reveal als End Card maximal eine Sekunde"],
    "no_go": ["Cuts über 28 Sekunden ohne klaren Bruch", "Captions über 120 Zeichen"]
  },
  "fuer_motion_designer": {
    "caption_style": "Caption-Stil weicht zwischen DE und US deutlich ab: TT-DE arbeitet kürzer und hashtag-lastig, TT-US erzählt bei Marken-Spots länger mit echter Geschichte. Auf IG ähnliches Bild — kurz bei Routine-Posts, narrativ bei Format-Brüchen.",
    "text_overlay": "L3 mit Cast-Name plus Datum am Ende, kein narrativer Text-Einsatz davor",
    "branding_einsatz": "End Card eine Sekunde, Logo zentriert, kein Lower-Third-Branding"
  },
  "fuer_creative_producer": {
    "strategische_pattern": "Die Woche zeigt zwei tragende Spuren: kurze Clips mit bekannten Titeln holen günstig Reaktion und lassen sich wiederholen, lange Marken-Spots holen die höchste Aktivierung — aber nur, wenn die Erzählung wirklich trägt.",
    "cross_market_chancen": "US arbeitet mit Wochen-Klammern (ein Thema, ein Tag, mehrere Posts), DE mit Marken-Klammer auf jedem Post. Das US-Modell könnte für DE-Verleiher das stärkere Argument sein.",
    "format_empfehlungen": "Pro Woche zwei Cuts: 22 Sekunden als Hauptcut, 12 Sekunden als kurze Variante"
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
    platform: str = "tiktok",
    top_posts_n: int = 3,
    top_hashtags_n: int = 5,
    ranked_posts_n: int = 10,
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
        avg_activation_rate=round(avg_activation_rate, 4),
        historical_top_posts=historical_top_posts,
        ranked_posts=ranked_posts,
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
    de_spec = next((c for c in channel_specs if c["market"] == "DE"), None)
    us_spec = next((c for c in channel_specs if c["market"] == "US"), None)
    de_channel = _find_channel(session, de_spec["handle"], platform) if de_spec else None
    us_channel = _find_channel(session, us_spec["handle"], platform) if us_spec else None

    notes: list[str] = []
    if de_spec and de_channel is None:
        notes.append(
            f"DE-Channel @{de_spec['handle']} ({label}) wurde nicht in der DB gefunden — "
            "Onboarding/Whitelist-Eintrag prüfen."
        )
    if us_spec and us_channel is None:
        notes.append(
            f"US-Channel @{us_spec['handle']} ({label}) wurde nicht in der DB gefunden — "
            "Onboarding/Whitelist-Eintrag prüfen."
        )

    de_stats = (
        _channel_stats(
            session, de_channel,
            de_spec["handle"] if de_spec else "",
            "DE", window_start, window_end, platform=platform,
        ) if de_spec else None
    )
    us_stats = (
        _channel_stats(
            session, us_channel,
            us_spec["handle"] if us_spec else "",
            "US", window_start, window_end, platform=platform,
        ) if us_spec else None
    )
    matches = _cross_market_matches(session, de_channel, us_channel, window_start, window_end)
    coverage = _title_coverage(de_stats, us_stats, session, de_channel, us_channel, window_start, window_end)

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
    if (de_channel is not None and us_channel is not None) and not matches:
        notes.append(
            f"Keine de_us_match_key-Treffer im {label}-Fenster — Cross-Market-Insight basiert "
            "auf indirekten Signalen."
        )

    return PlatformAggregation(
        platform=platform,
        de_channel=de_stats,
        us_channel=us_stats,
        cross_market_matches=matches,
        title_coverage=coverage,
        notes=notes,
    )


def _platforms_dict_for(pair_def: dict) -> dict[str, list[dict]]:
    """Return the ``platforms`` dict for a pair, falling back to a synthetic
    single-platform entry built from the legacy ``platform``/``channels``
    fields. Lets disabled pairs (universalpictures) and any future pair
    that hasn't been migrated to the new structure still aggregate."""
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
        cross_market_matches=first.cross_market_matches if first else [],
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
        overall_coverage_pct=0.0,
    )


# ---------- LLM call --------------------------------------------------------


def _format_ranked_post_line(idx: int, p: RankedPost) -> str:
    """Sprint 6 — kompakte Top-Posts-Zeile pro Plattform mit
    ``[*Filmtitel*]``-Marker, wenn ``title_local`` gesetzt ist.

    Format: ``  i. Xk views, Yk likes, Z.Z% akt., {duration}s [*Titel*]``
    Caption-Auszug folgt eingerückt darunter (max 80 Zeichen)."""
    views = int(p.views or 0)
    likes = int(p.likes or 0)
    akt_pct = (p.activation_rate or 0.0) * 100
    duration = f", {p.duration_seconds}s" if p.duration_seconds else ""
    title_marker = f" [*{p.title_local}*]" if p.title_local else ""
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


def _build_user_prompt(agg: PairAggregation) -> str:
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
        "Daten pro Plattform folgen. Komplett leere Plattformen sind ausgelassen.\n"
    )

    sections: list[str] = [framing]

    per_platform = agg.per_platform or []
    for platform_agg in per_platform:
        platform = platform_agg.platform
        de = platform_agg.de_channel
        us = platform_agg.us_channel
        cross_matches = platform_agg.cross_market_matches or []

        de_has_data = bool(de and de.posts_count)
        us_has_data = bool(us and us.posts_count)
        if not de_has_data and not us_has_data and not cross_matches:
            # Plattform komplett leer (z. B. YT-DE bei Disney/Prime/Paramount,
            # ohne dass irgendeine Seite Posts oder Matches hätte) — auslassen.
            continue

        label = _PLATFORM_HEADER_LABEL.get(platform, platform.title())
        block = [f"## {label}"]
        if de_has_data:
            block.append(_format_channel_section("DE", de, platform))
        if us_has_data:
            block.append(_format_channel_section("US", us, platform))
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


def generate_and_persist_report(
    session: Session,
    pair_key: str,
    *,
    window_days: int = 30,
    force: bool = False,
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
    - If ``force=False`` and a row exists for this (pair_key, iso_year,
      iso_week), hydrate and return it without an LLM call.
    - Otherwise call Opus, build the report, persist it (Last-Write-Wins
      on the composite PK), return the fresh report.

    ``dry_run`` is intentionally not a parameter — the dry-run path stays
    on the original ``generate_weekly_report`` and bypasses persistence
    entirely. Callers (the GET endpoint) branch on ``dry_run`` before
    invoking either function.
    """
    agg = aggregate_pair(session, pair_key, window_days=window_days, now=now)

    if not force:
        existing = session.get(
            InsightReportRow,
            (pair_key, agg.iso_year, agg.iso_week),
        )
        if existing is not None:
            return _hydrate_from_persisted(existing, window_days=window_days)

    # Cache miss (or force) → run the LLM. We re-call ``generate_weekly_report``
    # which re-runs ``aggregate_pair`` internally. The duplicate aggregation
    # is cheap (DB-only, fast), and keeping a single LLM-call code path
    # simplifies maintenance over wiring a precomputed-aggregation kwarg
    # through the call site. If aggregation cost ever becomes hot, this
    # is a one-line refactor.
    report = generate_weekly_report(
        session,
        pair_key,
        window_days=window_days,
        dry_run=False,
        model=model,
        max_tokens=max_tokens,
        now=now,
    )

    _persist_report(session, report)

    return report


__all__ = [
    "PAIRS",
    "OPUS_MODEL_ALIAS",
    "SYSTEM_PROMPT",
    "aggregate_pair",
    "generate_weekly_report",
    "generate_and_persist_report",
]
