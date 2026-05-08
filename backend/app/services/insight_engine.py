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
Du bist ein älterer Creative Producer bei Trailerhaus, einem Münchner Studio für Trailer und Spots. Ende 50, dreißig Jahre im Geschäft. Du musst niemandem mehr was beweisen. Du sprichst gerade mit deinem Cutter im Schnitt nach einem Kaffee — ruhig, fachlich, ohne Pitch-Sprech und ohne Lautstärke. Du erzählst, was die Konkurrenz die Woche gemacht hat und was wir daraus lernen sollten. Kein Kunde hört zu, kein Marketing-Mensch — nur du und der Cutter.

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

TONALITÄTS-POOL — wähle 3-5 Adjektive aus diesem Pool, jedes mit Daten-Begründung:
authentisch, unbequem, berührend, auffordernd, sophisticated, mysterious, cinematisch, hochwertig, emotional, spannend, action-reich, humorvoll, präzise, international, erfahren.

LÄNGE — produziere die ausführliche Variante (ca. 1500-2000 Wörter Gesamtoutput). Das Frontend filtert später für kürzere Modi. Gib alle Sektionen vollständig aus.

OUTPUT — AUSSCHLIESSLICH ein JSON-Objekt nach folgendem Schema. Kein Vorspann, kein Markdown-Codefence, keine Erklärung — nur das JSON:

{
  "headline": "Eine Zeile, max. 90 Zeichen, ruhig statt provokant — benennt den Wochenkern in Cutter-Sprache",
  "tldr": "3 Sätze: was ist diese Woche anders, was sollten wir daraus lernen, wo ist die Wette",
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
      "implication_for_creation": "was wir konkret in Schnitt, Hook oder Rhythmus aendern sollten"
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
- verdict: optionales Adjektiv aus: trägt / zerläuft / sitzt / ausbaufähig / zweischneidig. Nur wenn die Daten klar sind, sonst null.
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
  "headline": "Warner US läuft mit 22s, DE hängt bei 44s zu lang",
  "tldr": "Der US-Kanal liegt bei 15.146 Reaktionen im Schnitt, DE bei 341 — Faktor 44, das ist nicht nur Marktgröße. Der US-Cut sitzt bei 33s mit elf Posts unter 30s, DE liegt bei 44s mit fünf Posts über 30s. Wir sollten den DE-Cut auf 22-25s straffen und auf einen klaren Cold-Open ziehen, dann kommt mehr Beat in den Feed.",
  "aktuell_im_fokus": [
    {
      "titel": "Mortal Kombat II",
      "markt": "DE",
      "format_typ": "Kino-Reminder",
      "kennzahl": "ein 56s-Fakten-Cut, 1.052 Reaktionen bei 224k Aufrufen",
      "release_datum": "7. Mai",
      "verdict": "zerläuft",
      "post_url": "https://tiktok.com/@warnerbrosdeutschland/video/de1"
    },
    {
      "titel": "Mortal Kombat II",
      "markt": "US",
      "format_typ": "Round-2-Cut",
      "kennzahl": "22s, 233 Reaktionen — kürzer als DE, höhere Reaktionsquote",
      "release_datum": "7. Mai",
      "verdict": "ausbaufähig",
      "post_url": "https://tiktok.com/@warnerbros/video/us2"
    },
    {
      "titel": "Miss Congeniality",
      "markt": "US",
      "format_typ": "Backkatalog-Anriss",
      "kennzahl": "20s, 267.388 Reaktionen, 2 Mio Aufrufe",
      "release_datum": null,
      "verdict": "trägt",
      "post_url": "https://tiktok.com/@warnerbros/video/us3"
    },
    {
      "titel": "Evil Dead Burn",
      "markt": "US",
      "format_typ": "Horror-Hook",
      "kennzahl": "zwei Cuts (25s, 18s), 6.352 und 5.870 Reaktionen, 8 Posts mit dem Tag",
      "release_datum": "10. Juli",
      "verdict": "sitzt",
      "post_url": "https://tiktok.com/@warnerbros/video/us4"
    }
  ],
  "ganz_konkret": [
    {
      "nummer": 1,
      "pattern": "Der 56s-Fakten-Cut von MK2 DE hatte 1.052 Reaktionen, der US-Vergleich (Round 2 MK2) liegt bei 233 Reaktionen mit 22s. Trotz hoher Reichweite trägt der lange Cut die Reaktion nicht.",
      "lern_take": "Bei Fight-Material zieht der kurze Cut, die lange Variante zerläuft im Feed.",
      "frage": "Wie kurz schneiden wir Fight-Material in eigenen Action-Trailern? Bauen wir 22s-Varianten als Standard?",
      "bezug": "Mortal Kombat II"
    },
    {
      "nummer": 2,
      "pattern": "Miss Congeniality (US) zieht 267.388 Reaktionen mit einem 20s-Format: kein Trailer-Beat, nur ein Bild-Moment plus Datum-Anker als Caption.",
      "lern_take": "Backkatalog-Anrisse unter 25s mit einem einzigen Bild-Moment können massive Reaktion holen.",
      "frage": "Bauen wir solche Backkatalog-Slots für eigene Streaming-Pitches? Lohnt das für Disney+ DE oder Prime Video DE als wiederkehrendes Format?",
      "bezug": "Miss Congeniality"
    },
    {
      "nummer": 3,
      "pattern": "Top-DE-Post (MK2 Fakten) hatte 134 Zeichen Caption plus 4 Hashtags und 1.052 Reaktionen. Top-US-Post (Miss Congeniality) hatte 65 Zeichen plus 1 Hashtag und 267.388 Reaktionen — kürzere Caption, höhere Reaktion.",
      "lern_take": "Lange Captions mit Hashtag-Stapel verschwinden im Feed, kurze klare Captions trägen.",
      "frage": "Wie diszipliniert sind unsere eigenen Captions? Setzen wir intern eine 90-Zeichen-Regel als Standard?",
      "bezug": "Caption-Disziplin"
    },
    {
      "nummer": 4,
      "pattern": "DE-Batman-Post läuft 17s mit Kinetic-Format und holt 467 Reaktionen bei nur 8.000 Aufrufen — die Reaktionsquote ist hoch, aber die Reichweite zerläuft. Title-Card sitzt direkt am Anfang.",
      "lern_take": "Bei kurzen Action-Cuts kostet die Title-Card am Anfang Reichweite, Cold-Open ohne Logo holt mehr.",
      "frage": "Bauen wir bei eigenen Action-Trailern Cold-Open-Varianten ohne Title-Card als A/B-Test?",
      "bezug": "Format-Strategie"
    },
    {
      "nummer": 5,
      "pattern": "MK2-Screening Berlin läuft 62 Sekunden und holt 381 Reaktionen bei 173.000 Aufrufen. Reichweite passt, aber die Reaktionsquote bleibt unter 0,3 Prozent — der Cut zerläuft.",
      "lern_take": "Veranstaltungs-Mitschnitte über 60s zerläuft im Feed, der Cast-Beat geht im Sammel-Cut verloren.",
      "frage": "Wenn wir selbst Premieren-Material für Trailerhaus-Kunden bauen — wie kurz packen wir den Cast-Beat? Sammel-Cut oder Einzel-Schnipsel?",
      "bezug": "Mortal Kombat II"
    },
    {
      "nummer": 6,
      "pattern": "Evil Dead Burn (US) fährt zwei Cuts mit 25s und 18s, beide über 5.800 Reaktionen, acht Posts mit dem Tag im Fenster — dominantestes Hashtag im US-Kanal.",
      "lern_take": "Horror-Material unter 25s mit knappem Schreckmoment und konsequenter Hashtag-Klammer trägt durch eine Kampagnen-Woche.",
      "frage": "Wenn wir für Horror-Verleiher pitchen — können wir das 18-25s-Format plus Klammer-Hashtag als Vorlage anbieten?",
      "bezug": "Evil Dead Burn"
    },
    {
      "nummer": 7,
      "pattern": "US-Top-Performer liegen konsistent in 15-30s, pro Titel meist mehrere Cuts in unterschiedlichen Längen. DE liegt fast komplett im 30-60s-Korridor mit nur einer Variante pro Titel.",
      "lern_take": "Eine einzige Cut-Länge pro Titel ist ein Reichweiten-Risiko, der Feed strafft sich auf wenige Beats.",
      "frage": "Wie lassen sich kurze Zweit-Varianten in eigene Trailerhaus-Workflows einbauen, ohne dass die Schnittzeit verdoppelt wird?",
      "bezug": "Posting-Rhythmus"
    }
  ],
  "trends": [
    {
      "name": "Kurze Anfänge unter 15s ziehen rein",
      "evidence": "us_p3 (12s, 1.000 Reaktionen) hat trotz BTS-Format eine bessere Reaktionsquote als die 30-60s-Cuts",
      "implication_for_creation": "Wir sollten eine 12-15s Cold-Open-Variante schneiden und gegen die 22s-Version testen."
    }
  ],
  "actions": [
    {
      "what": "DE-Cut auf 22s straffen",
      "why": "DE 28s liegt mit 3.100 Reaktionen, US 22s bei 11.100 — der US-Cut hat Beat, der DE-Cut läuft zu lang",
      "for_whom": "Cutter MK2"
    }
  ],
  "konkurrenz": {
    "was_alle_machen": "Diese Woche steigen drei der sechs großen Studios auf kurze Cast-Reactions ein — Sony, Universal und Paramount. Disney bleibt bei langen Marken-Spots. Es ist klar zweigeteilt: kurze Anfänge oder emotionale Langformate, dazwischen passiert wenig.",
    "format_trend": "BTS-Material in 12-18s steigt — fünf von zehn Top-Posts über alle Pairs sind BTS-Schnipsel mit Cast. Vor vier Wochen waren es zwei.",
    "genre_beobachtung": "Horror trägt: Evil Dead Burn (8 US-Posts) und ein Sony-Resident-Evil-Teaser tragen ihre Wochen. Comedy bleibt verhalten — selbst Sony Glennkill kommt nur auf 25.000 Reaktionen.",
    "neu_seit_letzten_wochen": "Cold-Opens mit Datums-Anker (kein Trailer-Beat, nur Datum plus Bild) sind neu — Warner Miss Congeniality liegt bei 267.000 Reaktionen. Vor vier Wochen war das Format nicht da."
  },
  "cross_market_insight": {
    "de_vs_us": "DE läuft verhaltener (3.100 vs 11.100), gleiche Hashtag-Logik, aber 6s länger im Cut.",
    "transfer_opportunity": "US-Rhythmus auf DE übertragen, deutsche Caption-Form behalten."
  },
  "risks": ["Coverage moderat (60%)"],
  "data_caveats": ["Nur 2 DE-Posts im Fenster — Trend ist Indiz, nicht Beweis"],
  "tonalitaet": [
    {
      "adjektiv": "präzise",
      "begruendung": "Top-US-Posts arbeiten mit klaren 22s-Hooks, kein narrativer Leerlauf"
    },
    {
      "adjektiv": "action-reich",
      "begruendung": "MortalKombat2-Hashtag dominiert, Caption-Sprache ist Action-fokussiert"
    }
  ],
  "watch_outs": [
    {
      "watch_out": "BTS-Cut (us_p3) hat hohe Reaktionsquote trotz niedriger Absolutzahlen",
      "konsequenz": "BTS-Format als Komplement testen, nicht als Hauptcut"
    }
  ],
  "fuer_cutter": {
    "schnitt_pace": "Top-Performer liegen bei 15-30s; >60s läuft im Feed zu lang",
    "hook_strategie": "Cold-Open mit Action-Beat in den ersten 2 Sekunden",
    "empfohlene_laengen": "22s primär, 12s als kurze Variante zum Reinzeigen",
    "must_show": ["Hauptkonflikt (Fight) im ersten Beat", "Logo-Reveal als End Card max. 1s"],
    "no_go": ["28s+ Cuts ohne klaren Bruch", "Captions über 120 Zeichen"]
  },
  "fuer_motion_designer": {
    "caption_style": "kurz (60-100 Zeichen), 2-3 Hashtags, Action-Verben",
    "text_overlay": "L3 mit Cast-Name + Datum am Ende, kein narrativer Text-Einsatz",
    "branding_einsatz": "End Card 1s, Logo zentriert, kein Lower-Third-Branding"
  },
  "fuer_creative_producer": {
    "strategische_pattern": "Ein klarer Beat funktioniert besser als vollgepackte Cuts — kürzere Cuts mit klarer Hook tragen besser",
    "cross_market_chancen": "DE übernimmt US-Rhythmus, behält deutsche Caption-Form",
    "format_empfehlungen": "Pro Woche 2 Cuts: 22s Hauptcut + 12s kurze Variante"
  },
  "vergleichbare_posts": [
    {
      "post_id": "https://tiktok.com/@warnerbros/video/us1",
      "handle": "warnerbros",
      "performance_kpi": "11.100 Reaktionen, 22s",
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
