"""Pydantic schemas for the weekly insight report (Sprint 1 — Insight-Engine MVP).

The ``InsightReport`` is what ``GET /api/insights/weekly`` returns. It is a
union of three concerns:

1. ``aggregation`` — the deterministic, audit-friendly view of the raw data
   the LLM was given. Wolf reads this when a report feels off, to decide
   whether the issue is a prompt issue or a data issue.
2. ``llm_output`` — the strategist-facing narrative produced by Opus 4.7.
   Set to ``None`` when the endpoint is called with ``dry_run=true``, so
   the prompt + data can be QA'd without spending tokens.
3. ``coverage_pct`` / ``model`` / ``cost_usd_estimate`` / ``generated_at`` —
   meta the Frontend uses for the caveat banner above the report.

Sprint-2 work (cron-cached reports, daily pulse, more pairs) reuses these
schemas without changes; the only thing that grows is the lookup map in
``services/insight_engine.PAIRS``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TopPost(BaseModel):
    post_url: str
    caption_excerpt: str
    duration_seconds: Optional[int]
    engagement_sum: int
    likes: Optional[int]
    comments: Optional[int]
    shares: Optional[int]
    saves: Optional[int]
    views: Optional[int]
    asset_type: Optional[str] = None
    title: Optional[str] = None
    published_at: Optional[datetime] = None


class HashtagFrequency(BaseModel):
    tag: str
    count: int


class ChannelStats(BaseModel):
    handle: str
    market: str
    channel_id: Optional[str]
    channel_found: bool
    posts_count: int
    assets_count: int
    coverage_pct: float
    top_hashtags: list[HashtagFrequency]
    avg_caption_length: float
    avg_duration_seconds: Optional[float]
    duration_buckets: dict[str, int]
    top_posts: list[TopPost]
    avg_engagement: float
    # Sprint-Trailerhaus-Prompt-v1: top historical posts from BEFORE the
    # current window. The LLM uses these as ground truth for the
    # ``vergleichbare_posts`` section. Default empty so existing fixtures
    # and old reports remain valid.
    historical_top_posts: list[TopPost] = []


class CrossMarketMatch(BaseModel):
    match_key: str
    title: Optional[str]
    de_engagement: int
    us_engagement: int
    de_duration_seconds: Optional[int]
    us_duration_seconds: Optional[int]
    de_post_url: Optional[str]
    us_post_url: Optional[str]
    de_caption_excerpt: Optional[str]
    us_caption_excerpt: Optional[str]


class TitleCoverage(BaseModel):
    titles_in_both_markets: list[str]
    de_only_titles: list[str]
    us_only_titles: list[str]
    de_assets_with_title: int
    de_assets_total: int
    us_assets_with_title: int
    us_assets_total: int
    overall_coverage_pct: float


class PairAggregation(BaseModel):
    pair_key: str
    pair_label: str
    platform: str
    window_days: int
    window_start: datetime
    window_end: datetime
    iso_week: int
    iso_year: int
    de_channel: Optional[ChannelStats]
    us_channel: Optional[ChannelStats]
    cross_market_matches: list[CrossMarketMatch]
    title_coverage: TitleCoverage
    notes: list[str]


class Trend(BaseModel):
    name: str
    evidence: str
    implication_for_creation: str


class Action(BaseModel):
    what: str
    why: str
    for_whom: str


class Konkurrenz(BaseModel):
    """Branchen-Sicht: was machen die anderen großen Studios und Plattformen
    diese Woche, unabhängig vom DE/US-Vergleich des aktuellen Pairs.
    Sprint-Trailerhaus-Prompt-v2: ergänzt das DE/US-Bild um eine
    plattform- und genre-übergreifende Beobachtung."""
    was_alle_machen: Optional[str] = None
    format_trend: Optional[str] = None
    genre_beobachtung: Optional[str] = None
    neu_seit_letzten_wochen: Optional[str] = None


class SchnittAufgabe(BaseModel):
    """Beobachtung mit Lern-Take und offener Frage fuer Trailerhaus.
    Sprint-Trailerhaus-Prompt-v3.0 (Lern-Modus statt Anweisungs-Modus):
    
    Trailerhaus arbeitet nicht inhouse fuer die beobachteten Studios,
    sondern lernt aus deren Posts fuer eigene Projekte und Pitches.
    Daher keine Anweisungen mehr, sondern Beobachtung + Lehre + offene
    Frage.
    
    Felder (v3.0):
    - pattern: Was ist beobachtbar? (Daten-Anker, konkrete Zahlen)
    - lern_take: Was lernen wir daraus? (Ein-Satz-Take)
    - frage: Welche Frage stellt sich Trailerhaus? (Anwendung, Pitch,
      eigenes Projekt — optional, lieber null als Floskel)
    
    bezug (v2.3): Tag-String oben in der Card. Verweist entweder auf
    einen Titel aus aktuell_im_fokus oder auf einen der erlaubten
    strukturellen Werte (Format-Strategie, Posting-Rhythmus,
    Caption-Disziplin, Hashtag-Klammer)."""
    nummer: int
    pattern: str
    lern_take: str
    frage: Optional[str] = None
    bezug: Optional[str] = None


class TitelImFokus(BaseModel):
    """Ein Titel, eine Kampagne oder ein Format-Block, der diese Woche
    sichtbar im Material auftaucht. Sektion 'Worum geht's diese Woche'
    gibt einem Cutter in 10 Sekunden Ueberblick, welche konkreten Titel
    in den Aufgaben weiter unten gemeint sind.
    Sprint-Trailerhaus-Prompt-v2.2.
    
    post_url (v2.4): URL des Referenz-Posts, falls vorhanden. Macht den
    Titel im Frontend klickbar — Cutter kann den Spot direkt ansehen.
    Nur exakte URLs aus dem Input verwenden, niemals erfinden."""
    titel: str
    markt: str
    format_typ: str
    kennzahl: str
    release_datum: Optional[str] = None
    verdict: Optional[str] = None
    post_url: Optional[str] = None




class CrossMarketInsight(BaseModel):
    de_vs_us: str
    transfer_opportunity: str


class Tonalitaet(BaseModel):
    """A single Tonalitäts-Adjektiv with a one-sentence Trailerhaus-Begründung
    rooted in the data. The pool is fixed in the system prompt; the LLM picks
    3-5 adjectives that match the week's evidence."""
    adjektiv: str
    begruendung: str


class WatchOut(BaseModel):
    """Replacement for the unstructured ``risks`` list. ``watch_out`` is the
    observation, ``konsequenz`` is what it changes for the cut. ``risks``
    stays on ``LLMReport`` as a string-list alias for backwards-compat."""
    watch_out: str
    konsequenz: str


class FuerCutter(BaseModel):
    schnitt_pace: Optional[str] = None
    hook_strategie: Optional[str] = None
    empfohlene_laengen: Optional[str] = None
    must_show: list[str] = []
    no_go: list[str] = []


class FuerMotionDesigner(BaseModel):
    caption_style: Optional[str] = None
    text_overlay: Optional[str] = None
    branding_einsatz: Optional[str] = None


class FuerCreativeProducer(BaseModel):
    strategische_pattern: Optional[str] = None
    cross_market_chancen: Optional[str] = None
    format_empfehlungen: Optional[str] = None


class VergleichbarerPost(BaseModel):
    """A historical post the LLM picked as a reference for the cutter.
    All fields optional so the model can degrade gracefully when the data
    package is thin (e.g. missing post_id on older rows)."""
    post_id: Optional[str] = None
    handle: Optional[str] = None
    performance_kpi: Optional[str] = None
    relevanz_grund: Optional[str] = None


class LLMReport(BaseModel):
    """Strategist-facing narrative produced by Opus 4.7.

    Sprint-Trailerhaus-Prompt-v1 extends the schema additively:
    - Original Sprint-1 fields (headline … data_caveats) remain required so
      existing reports still validate.
    - New role-oriented sections (tonalitaet, watch_outs, fuer_cutter,
      fuer_motion_designer, fuer_creative_producer, vergleichbare_posts)
      are Optional so older saved reports and a defensive parse-fallback
      both still load.
    - ``watch_outs`` is the structured replacement for ``risks``; keep both
      until the Frontend has migrated.
    """
    headline: str
    tldr: str
    trends: list[Trend]
    actions: list[Action]
    cross_market_insight: CrossMarketInsight
    risks: list[str]
    data_caveats: list[str]
    # --- New (Trailerhaus-Prompt-v1, all optional for backwards-compat) ---
    aktuell_im_fokus: Optional[list[TitelImFokus]] = None
    ganz_konkret: Optional[list[SchnittAufgabe]] = None
    konkurrenz: Optional[Konkurrenz] = None
    tonalitaet: Optional[list[Tonalitaet]] = None
    watch_outs: Optional[list[WatchOut]] = None
    fuer_cutter: Optional[FuerCutter] = None
    fuer_motion_designer: Optional[FuerMotionDesigner] = None
    fuer_creative_producer: Optional[FuerCreativeProducer] = None
    vergleichbare_posts: Optional[list[VergleichbarerPost]] = None


class InsightReport(BaseModel):
    pair_key: str
    pair_label: str
    iso_week: int
    iso_year: int
    window_days: int
    coverage_pct: float
    generated_at: datetime
    model: str
    dry_run: bool = False
    llm_output: Optional[LLMReport] = None
    aggregation: PairAggregation
    cost_usd_estimate: Optional[float] = None
    # Sprint 1 (Persistenz): token counters are surfaced so the
    # persistence layer can store them on the ``insight_report`` row.
    # Optional because the dry-run path and the parse-failure path
    # don't carry usage metadata. Frontend ignores them today.
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    raw_llm_text: Optional[str] = Field(
        default=None,
        description="Raw assistant text — populated only when JSON parsing fails, to surface the failure in the response without losing the LLM's reply.",
    )
