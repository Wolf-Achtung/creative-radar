"""Segment-Roundup-Generator (Master-Plan-Schritt-3, Pilot).

Erzeugt fuer ein Segment einen deskriptiven Wochen-Roundup: Top-Material
aller zugehoerigen Channels im Zeitfenster, aggregiert und durch eine
LLM-Synthese in Brief-Form gegossen.

Disjunktheit zur Pair-Pipeline (Brief-Vorgabe 25.05.):
- Eigenes Persistenz-Schema (``segment_roundup``-Tabelle, Migration
  c7d4e8f3a9b2).
- Eigene Pydantic-Schemas (``SegmentAggregation``, ``SegmentRoundupReport``,
  ``SegmentRoundupLLMReport`` in ``app.schemas.insights``).
- Eigener LLM-Task — deskriptiv, kein Markt-Vergleich, kein Cross-
  Segment-Insight. Seit Sprint roundup-inherit-brief-voice (2026-06-11)
  erbt der System-Prompt die Pair-Voice (``ROUNDUP_SYSTEM_PROMPT =
  BRIEF_VOICE + ROUNDUP_TASK``, Muster Titel-Brief).
- Filtert ueber ``Channel.segment`` (Pair-Pool-Channels haben
  ``segment = NULL`` → disjunkt).

Wiederverwendet aus ``insight_engine`` (read-only, kein Touch des
Pair-Pfads):
- ``_engagement_sum``, ``_extract_hashtags``, ``compute_activation_rate``,
  ``_duration_bucket``, ``_excerpt`` — generische Post-Helper.
- ``_ranked_posts_for_channel`` — Top-N-Sortier-Pipeline mit Asset/Title-
  Anreicherung.
- ``_try_parse_llm_json`` — robustes JSON-Parsing inkl. Codefence-Stripping
  und Lenient-Substring-Fallback (M2-Lehre).

Pilot-Defaults (Wolf-Festlegung 25.05.):
- Pilot-Segment: ``us_major`` (33 Channels — groesstes Segment).
- Zeitfenster: 14 Tage (bewusste Abweichung vom Pair-30d-Default).
- Top-N pro Channel: 5 Posts (analog Sprint-6-Konvention im Pair-Prompt).
- LLM-Modell: Opus 4.7 (gleicher Modus wie Pair-Brief).
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import sqlalchemy as sa
from sqlmodel import Session, select

from app.models.entities import (
    Asset,
    Channel,
    ChannelSegment,
    Post,
    SegmentRoundup as SegmentRoundupRow,
    Title,
)
from app.schemas.insights import (
    ChannelRoundupStats,
    HashtagFrequency,
    RankedPost,
    SegmentAggregation,
    SegmentRoundupLLMReport,
    SegmentRoundupReport,
)
from app.services.anthropic_client import (
    AnthropicAuthError,
    _unwrap_single_key,
    call_with_json_retry,
    is_anthropic_configured,
)
from app.services.cost_log import record_anthropic_call
from app.services.insight_engine import (
    BRIEF_VOICE,
    OPUS_MODEL_ALIAS,
    _engagement_sum,
    _estimate_cost_usd,
    _extract_hashtags,
    _ranked_posts_for_channel,
)

logger = logging.getLogger(__name__)


# Pilot-Defaults (Wolf 25.05.) — parametrisiert, kein hartkodiertes
# Verhalten. Wolf kann via API/Skript-Param ueberschreiben.
ROUNDUP_DEFAULT_WINDOW_DAYS = 14
# Schritt-3c (26.05.): Top-N 5 -> 8. Wolf-Ping 1 (b) — fuer aussagekraeftige
# Titel-Bloecke braucht das LLM mehr Material; mit 5 Posts pro Channel sind
# das haeufig dieselben Hashtag-Pushes ohne Filmtitel. Token-Wirkung
# vernachlaessigbar (~13-14k Input vs. ~10k vorher, F0.7-Cap weit weg).
ROUNDUP_DEFAULT_TOP_POSTS_N = 8
ROUNDUP_DEFAULT_MAX_TOKENS = 8000


def parse_cron_roundup_segments(raw: str) -> list[ChannelSegment]:
    """Parst die ``cron_roundup_segments``-Setting (CSV) in eine
    geordnete Liste von ``ChannelSegment``-Werten.

    Wolf-Festlegung Ping 1, 25.05.:
    - Tolerant fuer Whitespace, leere Tokens (z.B. trailing comma).
    - Unbekannter Einzelwert → Warning-Log + skip (Cron laeuft mit
      Rest weiter).
    - Komplett leerer oder durchgehend unparsebarer Gesamtwert →
      ERROR-Log und leere Liste zurueck. **NICHT** still in
      "keine Roundups" kippen — der Caller (Cron) muss die leere
      Liste als Stop-Signal interpretieren und ``skipped_reason=
      no_parseable_segments`` ins Summary schreiben.

    Erhaltung der Reihenfolge: dieselbe wie in der CSV — Wolf kann
    damit Prioritaet steuern (wichtigstes Segment zuerst), falls
    F0.7-Cap im Cron mitten im Roundup-Block zuschlaegt.
    """
    raw = (raw or "").strip()
    if not raw:
        logger.error(
            "cron_roundup_segments_empty",
            extra={"reason": "config value is empty or whitespace-only"},
        )
        return []

    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        # raw war z.B. "," oder ",,, " — nur Trenner ohne Werte.
        logger.error(
            "cron_roundup_segments_empty",
            extra={"reason": "no non-empty tokens after split", "raw": raw[:200]},
        )
        return []

    parsed: list[ChannelSegment] = []
    unknowns: list[str] = []
    for token in tokens:
        try:
            parsed.append(ChannelSegment(token))
        except ValueError:
            unknowns.append(token)
            logger.warning(
                "cron_roundup_segments_unknown_value",
                extra={"token": token, "allowed": [s.value for s in ChannelSegment]},
            )

    if not parsed:
        # Alle Tokens waren unparsebar — gleicher Failure-Mode wie
        # leere CSV. Wolf-Vorgabe: nicht still in "keine Roundups"
        # kippen.
        logger.error(
            "cron_roundup_segments_empty",
            extra={
                "reason": "all tokens unknown",
                "unknown_tokens": unknowns,
                "allowed": [s.value for s in ChannelSegment],
            },
        )
    return parsed


# Sprint roundup-inherit-brief-voice (2026-06-11): Der Roundup erbt die
# komplette Pair-Voice (Persona, BERICHTSTON, Anti-Pattern-Listen,
# Berater-Vokabel-Verbote) nach dem Titel-Brief-Muster
# (``TITLE_SYSTEM_PROMPT = BRIEF_VOICE + TITLE_BRIEF_TASK``). Vorher war
# ROUNDUP_SYSTEM_PROMPT ein eigener Prompt ohne die Verbotslisten — Quelle
# der Marketing-Sprech-Ausreisser ("trommelt fuer", "ballert durch",
# "Theatrical-Drops"). BRIEF_VOICE selbst bleibt unangetastet.
#
# Die pair-spezifischen Voice-Stellen werden im Task als EXPLIZITE
# Gegen-Anweisungen neutralisiert (P0-1-Carve-outs: Zielstil-/Headline-
# Beispiele, format_typ-/kennzahl-Feld-Mappings, Plattform-Header-Mechanik,
# Tonalitaets-Pool, Laenge). Anders als der Titel-Brief laeuft der Roundup
# ueber freies Text-JSON (``call_with_json_retry``) statt forced Tool-Use —
# das weichere Schema-Enforcement vertraegt keine stillen Auslassungen,
# dangling Anweisungen ("1500-2000 Woerter", "waehle 3-5 Adjektive")
# wuerden Fuell-Text oder Sektions-Halluzination provozieren.
ROUNDUP_TASK = """

SEGMENT-ROUNDUP — AUFGABE:
Du schreibst einen wöchentlichen Roundup für EIN Segment des deutschen/
internationalen Film-Marketing-Markts (z. B. US Major, US Independent,
DE Verleih, DE Independent). Achse ist das Segment, nicht ein Studio-Pair.

WICHTIG: Der Berichtston und die Verbotslisten oben gelten unverändert.
Aber dieser Brief ist KEIN Pair-Brief — folgende Stellen oben gehören dem
Pair-Brief und gelten hier in angepasster Form:
- Das VORHER/NACHHER-Zielstil-Beispiel und die Beispiel-Headlines unter
  HEADLINE-FORM erzählen Markt-Vergleiche (UK/DE/US). Übernimm daraus NUR
  den Ton und die Verb-Wahl, NICHT die Vergleichsstruktur — der Roundup
  beschreibt EIN Segment.
- "Subjekt klar (Studio + Markt)" in HEADLINE-FORM heißt hier:
  Channel/Verleiher + Segment. Das maßgebliche Headline-Beispiel für
  diesen Brief steht unten unter WAS DIESER BRIEF IST.
- Die format_typ-Ausnahme der Klassifikations-Substantive (oben:
  "ausschließlich in ``aktuell_im_fokus.format_typ``") gilt hier für
  ``titles[].format_typ``.
- Die kennzahl-Ausnahme der Pseudo-Präzisions-Regeln (oben:
  "``aktuell_im_fokus.kennzahl``") gilt hier für ``titles[].kennzahl``:
  Doppel-Beziffung ist dort als Einzeiler-Datenpunkt ausdrücklich ERLAUBT
  und erwünscht. Die Sekunden-Range-Ausnahme für ``fuer_cutter`` läuft ins
  Leere — dieses Feld existiert hier nicht.
- PLATTFORM-VERGLEICH oben verlangt ``## Plattform``-Header im User-Prompt;
  die gibt es hier nicht. Im Roundup steht die Plattform als Suffix pro
  Channel ("@handle (platform)") — du darfst eine Plattform erwähnen, wenn
  sie dort vorkommt, und keine erfinden, die dort fehlt. Der Hinweis zur
  YouTube-Aktivierungs-Methodik gilt unverändert.
- TONALITÄTS-POOL: dieser Brief hat KEIN ``tonalitaet``-Feld. Wähle KEINE
  Adjektive aus dem Pool aus und erfinde keine Tonalitäts-Sektion.
- LÄNGE: die "1500-2000 Wörter / alle Sektionen"-Vorgabe oben gilt hier
  NICHT. Für diesen Brief gilt EHRLICHKEIT VOR FÜLLE (unten) — der Brief
  ist so kurz, wie die Substanz es verlangt.
- Generell: Alle oben erwähnten Output-Felder, die im JSON-Schema am Ende
  dieses Prompts nicht vorkommen — namentlich ``fuer_cutter``,
  ``fuer_motion_designer``, ``fuer_creative_producer``,
  ``cross_market_insight`` (samt ``de_vs_us``, ``de_vs_uk``, ``us_vs_uk``,
  ``transfer_opportunity``), ``aktuell_im_fokus``, ``must_show``,
  ``no_go``, ``tonalitaet`` (oben schon einzeln abgefangen),
  ``begruendung``, ``vergleichbare_posts``, ``ganz_konkret`` — gehören dem
  Pair-Brief und existieren in diesem Roundup nicht. Erzeuge sie nicht.
  Maßgeblich für die Output-Struktur ist ausschließlich das JSON-Schema am
  Ende dieses Prompts.

WAS DIESER BRIEF IST
- Konkret und namentlich: nenne Filme/Serien, Verleiher/Channels, echte Zahlen
  aus den Daten (Views, Likes, Aktivierung, Sekunden) — keine Aktivitäts-
  Aufzählung in Abstrakta.
- Headline mit einem klaren Hauptgedanken in aktiver, sachlicher Sprache.
  Das maßgebliche Beispiel für diesen Brief (statt der Pair-Beispiele oben):
  Gute Form: "US-Independents veröffentlichen diese Woche vor allem Material
  von Festivals — A24 erreicht mit Eddington rund 40.000 Reaktionen."
  Schlechte Form: "Aktivitäts-Schwerpunkt liegt bei Trailer-Posts."
- Lass die Zahlen sprechen: die kennzahl in jedem Titel-Block macht
  sichtbar, wie ein Post gelaufen ist. Vergib KEIN explizites Urteil
  ("funktioniert" / "ausbaufähig" / "kommt nicht an") — dafür gibt es
  keinen definierten Maßstab. Der Leser zieht seinen Schluss aus der
  Kennzahl selbst.

WAS DIESER BRIEF NICHT IST
- KEIN Markt-Vergleich (DE↔US↔UK) und KEIN Segment-übergreifender Blick —
  der Roundup beschreibt EIN Segment. Keine Cross-Segment-Aussagen, kein
  "wie in us_major". Die Markt-Vergleichs-Beispiele oben (Zielstil,
  Beispiel-Headlines) gehören dem Pair-Brief — übernimm ihre
  Gegenüberstellungs-Struktur nicht.
- KEIN Matching zwischen Channels und auch keine Bestenliste — Channels
  stehen pro Titel-Block durch ihre Posts da, das reicht.

EHRLICHKEIT VOR FÜLLE
Lieber 2 echte Titel-Blöcke als 6 mit aufgeblasener Substanz. Die Anzahl
folgt der Substanz im Material: typischerweise 5-7 bei aktiven Segmenten,
deutlich weniger bei ruhigen. Wenn ein Segment dünn ist (wenige Posts,
viele stumme Channels), bleibt der Brief knapp — das ist erwünscht,
``data_caveats`` macht die Lautstärke transparent.

KENNZAHLEN
Pro Titel-Block gib eine konkrete Kennzahl an, die du im Material findest —
Form analog Pair-Brief, z.B. "82s, 24.000 Views, 8% Aktivierung". Erfinde
nichts, zitiere wörtlich aus den Top-Post-Zeilen.

Hinweis zu Bild-Posts und Carousels: Instagram liefert für Foto-Posts
keine View-Zahl. Solche Post-Zeilen erscheinen mit Likes (und ggf.
Sekunden bei Video) **ohne** Views/Aktivierung — das ist korrekt, kein
schwacher Post. Übernimm die Like-Zahl als Kennzahl, statt einen Post
mit 0 Views als "kommt nicht an" zu lesen.

OUTPUT — AUSSCHLIESSLICH ein JSON-Objekt nach folgendem Schema. Kein
Vorspann, kein Markdown-Codefence, keine Erklärung — nur das JSON:

{
  "headline": "1 Satz, sachlich und aktiv: ein Hauptgedanke mit konkretem Verb (holt, zieht, läuft, kommt auf, punktet — siehe HEADLINE-FORM oben), kein Werturteil, keine Dramatisierung",
  "tldr": "2-3 Sätze: Hauptaussage zuerst, Beleg dahinter. Eine Zahl mit Einordnung, kein nacktes Datenpaar.",
  "titles": [
    {
      "titel": "Filmtitel / Franchise / Kampagne",
      "channel": "@handle des Channels, der gepostet hat",
      "format_typ": "Kino-Erinnerung / Material vom Set / Reaktionen der Darsteller / Festival-Material / Trailer-Veröffentlichung / …",
      "kennzahl": "Konkrete Zahl aus dem Material, z.B. '82s, 24.000 Views, 8% Aktivierung'",
      "release_datum": "optional, falls erkennbar (z.B. '22. Mai') — sonst null",
      "post_url": "Exakte URL aus einer Top-Post-Zeile, falls vorhanden — sonst null. Niemals erfinden."
    }
  ],
  "themes": ["Optional, 2-5: wiederkehrende Motive/Themen über mehrere Channels hinweg — null lassen, wenn nichts klar wiederkehrt"],
  "data_caveats": ["Lautstärke-Hinweise: wie viele Channels lieferten Posts, wo sind Lücken, was relativiert den Brief"]
}

Antworte ausschließlich mit dem JSON-Objekt, ohne Markdown-Codefences."""

ROUNDUP_SYSTEM_PROMPT = BRIEF_VOICE + ROUNDUP_TASK


def _select_channels_for_segment(session: Session, segment: ChannelSegment) -> list[Channel]:
    """Liefert alle aktiven Channels mit dem gegebenen Segment.

    Disjunktheits-Vertrag: Pair-Pool-Channels haben ``segment = NULL`` und
    werden hier nie gefunden — keine Doppelabdeckung mit dem Pair-Pfad.
    """
    rows = session.exec(
        select(Channel)
        .where(Channel.active == True)  # noqa: E712 — SQL-side bool compare
        .where(Channel.segment == segment)
        .order_by(Channel.platform, Channel.handle)
    ).all()
    return list(rows)


def _channel_roundup_stats(
    session: Session,
    channel: Channel,
    window_start: datetime,
    window_end: datetime,
    *,
    top_posts_n: int = ROUNDUP_DEFAULT_TOP_POSTS_N,
) -> ChannelRoundupStats:
    """Per-Channel-Aggregation fuer den Roundup. Wiederverwendet die
    generischen Post-Helper aus ``insight_engine`` (read-only Import),
    aber schlankere Output-Form: keine title_coverage, keine cross-market-
    Felder, keine historical_top_posts."""
    posts_stmt = (
        select(Post)
        .where(Post.channel_id == channel.id)
        .where(
            sa.or_(
                sa.and_(
                    Post.published_at.is_not(None),
                    Post.published_at >= window_start,
                    Post.published_at <= window_end,
                ),
                sa.and_(
                    Post.published_at.is_(None),
                    Post.detected_at >= window_start,
                    Post.detected_at <= window_end,
                ),
            )
        )
    )
    posts: list[Post] = list(session.exec(posts_stmt).all())

    if not posts:
        return ChannelRoundupStats(
            channel_id=str(channel.id),
            handle=channel.handle or channel.name,
            platform=channel.platform,
            market=str(channel.market) if channel.market else None,
            posts_count=0,
            avg_engagement=0.0,
            avg_caption_length=0.0,
            avg_duration_seconds=None,
            top_hashtags=[],
            top_posts=[],
        )

    # Hashtag- und Caption-Aggregation
    tag_counter: Counter[str] = Counter()
    caption_lens: list[int] = []
    durations: list[int] = []
    for p in posts:
        caption_lens.append(len(p.caption or ""))
        for tag in _extract_hashtags(p.caption, p.raw_payload):
            tag_counter[tag] += 1
        if p.duration_seconds is not None:
            durations.append(int(p.duration_seconds))

    avg_caption = sum(caption_lens) / len(caption_lens) if caption_lens else 0.0
    avg_duration = sum(durations) / len(durations) if durations else None
    avg_engagement = sum(_engagement_sum(p) for p in posts) / len(posts) if posts else 0.0

    # Top-N pro Channel (Wolf-Festlegung: 5). Wiederverwendung des
    # ``_ranked_posts_for_channel``-Helpers mit Asset+Title-Anreicherung —
    # gleiche Sortier- und Tiebreaker-Semantik wie der Pair-Brief.
    top_posts: list[RankedPost] = _ranked_posts_for_channel(
        posts,
        channel.platform,
        session=session,
        limit=top_posts_n,
    )

    return ChannelRoundupStats(
        channel_id=str(channel.id),
        handle=channel.handle or channel.name,
        platform=channel.platform,
        market=str(channel.market) if channel.market else None,
        posts_count=len(posts),
        avg_engagement=round(avg_engagement, 1),
        avg_caption_length=round(avg_caption, 1),
        avg_duration_seconds=round(avg_duration, 1) if avg_duration is not None else None,
        top_hashtags=[
            HashtagFrequency(tag=tag, count=count)
            for tag, count in tag_counter.most_common(5)
        ],
        top_posts=top_posts,
    )


def aggregate_segment(
    session: Session,
    segment: ChannelSegment,
    *,
    window_days: int = ROUNDUP_DEFAULT_WINDOW_DAYS,
    top_posts_n: int = ROUNDUP_DEFAULT_TOP_POSTS_N,
    now: Optional[datetime] = None,
) -> SegmentAggregation:
    """Baut die deterministische Segment-Aggregation fuer ein Zeitfenster.

    Nutzt ``isocalendar`` analog Pair-Pipeline. ``now`` injectable fuer
    Tests; Production-Caller liefern nichts.
    """
    now = now or datetime.now(timezone.utc)
    window_end = now
    window_start = now - timedelta(days=window_days)
    iso_year, iso_week, _ = now.isocalendar()

    channels = _select_channels_for_segment(session, segment)
    stats: list[ChannelRoundupStats] = [
        _channel_roundup_stats(
            session, c, window_start, window_end, top_posts_n=top_posts_n,
        )
        for c in channels
    ]
    channels_with_posts = sum(1 for s in stats if s.posts_count > 0)
    total_posts = sum(s.posts_count for s in stats)

    return SegmentAggregation(
        segment=segment.value,
        iso_year=iso_year,
        iso_week=iso_week,
        window_days=window_days,
        window_start=window_start,
        window_end=window_end,
        channels_evaluated=len(channels),
        channels_with_posts=channels_with_posts,
        total_posts=total_posts,
        channels=stats,
    )


def _format_post_line(idx: int, p: RankedPost) -> str:
    """Eine Post-Zeile fuer den LLM-Prompt.

    Schritt-3c (26.05.): an die Pair-Brief-Form (``_format_ranked_post_line``
    in ``insight_engine``) angeglichen — views, likes, activation-rate,
    duration werden mitgegeben, damit das LLM **echte Zahlen** in den
    ``titles[*].kennzahl`` zitieren kann.

    Daten-Hygiene-Sprint, Option A1 (Wolf 26.05.): Instagram liefert
    fuer Foto-Posts und Carousels keine View-Zahl — Apify mapped
    ``visible_views`` nur aus video-only-Feldern (``videoViewCount`` /
    ``videoPlayCount``), Bild-Posts landen mit ``views = None`` in der
    DB und mit ``views = 0`` im Prompt. Wenn das LLM ``0 Views, X Likes,
    0,0% Aktivierung`` zitiert, wirkt der Post schwach, ist es aber
    nicht. Der Fix laesst views/akt./Sekunden in genau diesem Fall weg
    (``views == 0 && likes > 0``) und gibt nur die Like-Zahl als
    Kennzahl-Datenanker mit. Der Hinweis im System-Prompt erklaert dem
    LLM den Mechanismus zusaetzlich.

    Format-Branch fuer Bild-Posts/Carousels:
        ``  i. {likes} likes [*Titel*]``
        ``     "{caption_excerpt}"``
        ``     URL: {post_url}``

    Format-Branch fuer Video-/Standard-Posts:
        ``  i. {views} views, {likes} likes, {pct}% akt., {dur}s [*Titel*]``
        ``     "{caption_excerpt}"``
        ``     URL: {post_url}``
    """
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
    if views == 0 and likes > 0:
        # Bild-Post / Carousel — Plattform liefert keine View-Zahl.
        # views, akt. und Sekunden bewusst weglassen, damit das LLM
        # die Like-Zahl als Datenanker nimmt.
        line = f"  {idx}. {likes:,} likes{title_marker}"
    else:
        line = (
            f"  {idx}. {views:,} views, {likes:,} likes, "
            f"{akt_pct:.1f}% akt.{duration}{title_marker}"
        )
    if p.caption_excerpt:
        excerpt = p.caption_excerpt.strip()
        if len(excerpt) > 100:
            excerpt = excerpt[:100].rstrip() + "…"
        line += f"\n     \"{excerpt}\""
    if p.post_url:
        # URL bewusst auf eigener Zeile — das LLM braucht sie wortwoertlich
        # fuer ``titles[*].post_url`` und ein Mittensatz-Match schlaegt
        # haeufiger fehl als ein klares ``URL:``-Praefix.
        line += f"\n     URL: {p.post_url}"
    return line


def _format_channel_block(stats: ChannelRoundupStats) -> str:
    """Pro Channel ein Markdown-Block fuer den LLM-Prompt. Keine
    JSON-Anhang am Promptende — Wolf-Festlegung 25.05.: schlanker als
    Pair-Brief, weil 33 Channels sonst Token-Explosion produzieren.

    Schritt-3c (26.05.): Header zeigt zusaetzlich avg activation in
    Prozent — analog Pair-Brief-Channel-Section. Caption-Laenge bleibt
    weg (kein Cutter-relevanter Datenpunkt im Roundup-Kontext).
    """
    if stats.posts_count == 0:
        return f"### @{stats.handle} ({stats.platform}, {stats.market or '–'})\n  *(keine Posts im Fenster)*"

    # avg_activation ist nicht in ChannelRoundupStats persistiert; ableiten
    # aus top_posts.activation_rate-Mittel als gute Naeherung. Bei wenigen
    # Top-Posts ist das exakt, bei vielen Posts ueberschaetzt es leicht —
    # ist als Prompt-Hinweis gut genug, der LLM zitiert die per-Post-Zahl
    # aus den Top-Post-Zeilen, nicht den Channel-Average.
    if stats.top_posts:
        avg_act = sum((p.activation_rate or 0.0) for p in stats.top_posts) / len(stats.top_posts)
    else:
        avg_act = 0.0
    header = (
        f"### @{stats.handle} ({stats.platform}, {stats.market or '–'}) — "
        f"{stats.posts_count} Posts, "
        f"avg engagement {stats.avg_engagement:.0f}, "
        f"avg activation {avg_act * 100:.1f}%"
    )
    if stats.top_hashtags:
        tags = ", ".join(f"#{h.tag} ({h.count})" for h in stats.top_hashtags[:5])
        header += f"\n  Top-Hashtags: {tags}"
    posts_block = "\n".join(_format_post_line(i + 1, p) for i, p in enumerate(stats.top_posts))
    return f"{header}\n{posts_block}"


def _build_user_prompt(agg: SegmentAggregation) -> str:
    """LLM-User-Prompt fuer den Roundup. Strukturell schlank: Segment-
    Header + Channel-Bloecke. Kein JSON-Anhang (Wolf 25.05.).
    """
    header = (
        f"# Segment-Roundup: {agg.segment} — KW {agg.iso_week}/{agg.iso_year}\n\n"
        f"Zeitfenster: {agg.window_days} Tage "
        f"({agg.window_start.date().isoformat()} bis {agg.window_end.date().isoformat()}).\n"
        f"Channels ausgewertet: {agg.channels_evaluated} "
        f"(davon mit Posts im Fenster: {agg.channels_with_posts}).\n"
        f"Posts gesamt: {agg.total_posts}.\n\n"
        f"## Channels mit Aktivitaet\n"
    )
    blocks = [_format_channel_block(s) for s in agg.channels if s.posts_count > 0]
    # Schritt-4 Dedupe-Fix (Wolf-Ping-1, 25.05.): Channel-Rows mit dem
    # gleichen Handle auf mehreren Plattformen erschienen im Pilot-Output
    # mehrfach als ``@disney`` ohne Plattform-Unterscheidung. Fix Option
    # (ii) — Platform-Suffix ``@handle (platform)`` macht jeden Eintrag
    # eindeutig, verliert keine Information und gibt dem LLM den
    # Plattform-Kontext fuer eine ggf. plattform-spezifische Caveat-
    # Formulierung. Beispiel: ``@disney (instagram), @disney (tiktok)``.
    silent_entries = [
        f"@{s.handle} ({s.platform})"
        for s in agg.channels if s.posts_count == 0
    ]
    silent_note = ""
    if silent_entries:
        silent_note = (
            f"\n\n## Channels ohne Posts im Fenster ({len(silent_entries)})\n"
            + ", ".join(silent_entries)
        )
    return header + "\n\n".join(blocks) + silent_note


def generate_segment_roundup(
    session: Session,
    segment: ChannelSegment,
    *,
    window_days: int = ROUNDUP_DEFAULT_WINDOW_DAYS,
    top_posts_n: int = ROUNDUP_DEFAULT_TOP_POSTS_N,
    model: str = OPUS_MODEL_ALIAS,
    max_tokens: int = ROUNDUP_DEFAULT_MAX_TOKENS,
    now: Optional[datetime] = None,
) -> SegmentRoundupReport:
    """Baut die Aggregation, ruft das LLM mit M2-Retry-Loop, gibt den
    fertigen Roundup-Report zurueck. Persistenz separat via
    ``generate_and_persist_roundup``.

    Schritt-4-Erweiterung (2026-05-25): nutzt ``call_with_json_retry``
    aus ``anthropic_client`` — bis zu 2 Re-Calls bei JSON-Parse-Fehler,
    analog Pair-Brief-M2. Jeder Anthropic-Call landet einzeln im
    costlog (``operation='segment_roundup'``), F0.7-Cap erfasst die
    wahre Spend-Summe inkl. Retries.

    Bei totalem Parse-Fehler nach allen Retries bleibt ``llm_output = None``
    und ``raw_llm_text`` surface — Caller (``_persist_roundup``) skippt
    dann die DB-Row.
    """
    agg = aggregate_segment(
        session, segment,
        window_days=window_days,
        top_posts_n=top_posts_n,
        now=now,
    )
    generated_at = datetime.now(timezone.utc)

    if not is_anthropic_configured():
        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt — Segment-Roundup kann nicht generieren."
        )

    user_prompt = _build_user_prompt(agg)
    logger.info(
        "roundup_anthropic_call_start",
        extra={
            "segment": segment.value,
            "iso_week": agg.iso_week,
            "channels_evaluated": agg.channels_evaluated,
            "channels_with_posts": agg.channels_with_posts,
            "total_posts": agg.total_posts,
            "prompt_chars": len(user_prompt),
        },
    )

    retry_result = call_with_json_retry(
        model=model,
        system=ROUNDUP_SYSTEM_PROMPT,
        user_message=user_prompt,
        max_tokens=max_tokens,
        max_recalls=2,
        log_prefix="roundup",
        log_extra={
            "segment": segment.value,
            "iso_year": agg.iso_year,
            "iso_week": agg.iso_week,
        },
    )

    # Letzter raw_text aus call_attempts fuer raw_for_response / Diagnose.
    last_raw_text = (
        retry_result.call_attempts[-1][1]
        if retry_result.call_attempts else ""
    )

    llm_output: Optional[SegmentRoundupLLMReport] = None
    raw_for_response: Optional[str] = None
    if retry_result.parsed is not None:
        # Defensive/consistency net mirroring the brief path: unwrap a
        # single stray top-level wrapper key before validating. The roundup
        # path uses the text completion (``call_with_json_retry`` →
        # ``messages_create_text``), NOT forced tool-use, so the
        # ``{"what": {...}}`` tool-wrapper bug does not occur here today —
        # this is purely consistency/future-proofing.
        candidate = _unwrap_single_key(retry_result.parsed, expected_field="headline")
        if candidate is not retry_result.parsed:
            logger.warning(
                "roundup-llm-output-unwrapped",
                extra={"segment": segment.value, "wrapper_key": next(iter(retry_result.parsed))},
            )
        try:
            llm_output = SegmentRoundupLLMReport.model_validate(candidate)
            logger.info(
                "roundup_llm_call_ok",
                extra={
                    "segment": segment.value,
                    "iso_week": agg.iso_week,
                    "parse_path": retry_result.parse_path,
                    "anthropic_calls": len(retry_result.call_attempts),
                },
            )
        except ValueError as exc:
            logger.error(
                "roundup-schema-validation-failed",
                extra={
                    "segment": segment.value,
                    "error_message": str(exc)[:500],
                    "raw_response_first_500": last_raw_text[:500],
                },
            )
            raw_for_response = last_raw_text
    else:
        pos = (
            retry_result.parse_error.pos
            if retry_result.parse_error and retry_result.parse_error.pos is not None
            else 0
        )
        logger.error(
            "roundup-json-parse-failed",
            extra={
                "segment": segment.value,
                "char_position": pos,
                "raw_response_length": len(last_raw_text),
                "raw_response_first_500": last_raw_text[:500],
                "raw_response_around_error": last_raw_text[max(0, pos - 200): pos + 200],
                "anthropic_calls": len(retry_result.call_attempts),
                "recall_count": len(retry_result.call_attempts) - 1,
            },
        )
        raw_for_response = last_raw_text

    # Cost-Erfassung: jeder Call wird einzeln in costlog erfasst
    # (F0.7-Cap erfasst Roundup-Spend automatisch ueber die anthropic_*-
    # Provider-Buckets). Token-Summe ueber alle Versuche, damit auch
    # bezahlte Re-Calls bei Total-Parse-Fail im Report sichtbar bleiben.
    input_tokens_total = 0
    output_tokens_total = 0
    for msg_attempt, _ in retry_result.call_attempts:
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
            operation="segment_roundup",
            meta={
                "segment": segment.value,
                "iso_year": agg.iso_year,
                "iso_week": agg.iso_week,
            },
        )
    input_tokens = input_tokens_total
    output_tokens = output_tokens_total
    cost = (
        _estimate_cost_usd(input_tokens, output_tokens)
        if (input_tokens or output_tokens)
        else None
    )

    return SegmentRoundupReport(
        segment=segment.value,
        iso_year=agg.iso_year,
        iso_week=agg.iso_week,
        window_days=window_days,
        generated_at=generated_at,
        model=model,
        aggregation=agg,
        llm_output=llm_output,
        cost_usd_estimate=cost,
        input_tokens=input_tokens or None,
        output_tokens=output_tokens or None,
        raw_llm_text=raw_for_response,
    )


def _persist_roundup(session: Session, report: SegmentRoundupReport) -> None:
    """Upsert eine ``segment_roundup``-Row keyed auf
    ``(segment, iso_year, iso_week)``. Last-Write-Wins (delete-then-insert)
    analog ``_persist_report`` der Pair-Pipeline.

    Skippt bei ``llm_output = None`` — analog Pair-Brief-Konvention, kein
    leerer Row.
    """
    if report.llm_output is None:
        logger.warning(
            "segment-roundup-persist-skipped: segment=%s week=%d/%d (no llm_output)",
            report.segment, report.iso_year, report.iso_week,
        )
        return

    cost_cents: Optional[int] = (
        int(round(report.cost_usd_estimate * 100)) if report.cost_usd_estimate else None
    )

    # PK ist (segment, iso_year, iso_week). Wir nutzen den enum-value direkt
    # fuer die PK-Lookup.
    existing = session.get(
        SegmentRoundupRow,
        (ChannelSegment(report.segment), report.iso_year, report.iso_week),
    )
    if existing is not None:
        session.delete(existing)
        session.flush()

    row = SegmentRoundupRow(
        segment=ChannelSegment(report.segment),
        iso_year=report.iso_year,
        iso_week=report.iso_week,
        window_days=report.window_days,
        channels_aggregation=report.aggregation.model_dump(mode="json"),
        llm_output=report.llm_output.model_dump(mode="json"),
        generated_at=report.generated_at,
        model=report.model,
        cost_usd_cents=cost_cents,
        input_tokens=report.input_tokens,
        output_tokens=report.output_tokens,
    )
    session.add(row)
    session.commit()


def generate_and_persist_roundup(
    session: Session,
    segment: ChannelSegment,
    *,
    window_days: int = ROUNDUP_DEFAULT_WINDOW_DAYS,
    top_posts_n: int = ROUNDUP_DEFAULT_TOP_POSTS_N,
    model: str = OPUS_MODEL_ALIAS,
    now: Optional[datetime] = None,
) -> SegmentRoundupReport:
    """End-to-End: aggregieren → LLM-Call → persistieren (idempotent
    Last-Write-Wins). Pilot-Auslosse-Pfad ruft das hier auf.
    """
    report = generate_segment_roundup(
        session, segment,
        window_days=window_days,
        top_posts_n=top_posts_n,
        model=model,
        now=now,
    )
    _persist_roundup(session, report)
    return report


__all__ = [
    "ROUNDUP_DEFAULT_WINDOW_DAYS",
    "ROUNDUP_DEFAULT_TOP_POSTS_N",
    "aggregate_segment",
    "generate_segment_roundup",
    "generate_and_persist_roundup",
    "parse_cron_roundup_segments",
]
