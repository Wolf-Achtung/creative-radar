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
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class RankedPost(BaseModel):
    """One post in the Ranking section (Sprint 2 — top-N per channel).

    Carries all five raw metrics plus the derived ``activation_rate`` so the
    Frontend can re-sort client-side without a backend round-trip. Defaults
    are zero/empty (not None) for the numeric fields so older persisted
    briefs that lack these fields still validate via ``model_validate`` —
    the Sprint-1 ``insight_report`` table stores the aggregation as JSON,
    and a brief generated before this sprint will simply have an empty
    ``ranked_posts`` list once Pydantic re-hydrates it.

    ``platform`` reflects ``post.platform`` (currently always ``tiktok``
    for the six Tier-A pairs; pre-wired for the multi-platform sprint so
    the Frontend pill renders correctly without a follow-up schema bump).

    ``saves``/``shares`` stay at 0 for YouTube — the API does not surface
    them. ``activation_rate`` follows the YT branch in
    ``compute_activation_rate`` for those rows.
    """
    post_url: Optional[str] = None
    caption_excerpt: str = ""
    platform: str = "tiktok"
    published_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None

    # Raw metrics — Frontend uses these for client-side re-sorting.
    views: int = 0
    likes: int = 0
    comments: int = 0
    saves: int = 0
    shares: int = 0

    # Derived aggregates.
    engagement_sum: int = 0
    activation_rate: float = 0.0

    # Sprint 5b — eager-loaded from the best matching Asset/Title row per
    # post. All four default to ``None`` so older persisted briefs (pre-
    # Sprint-5b) still validate via ``model_validate`` without migration.
    # Coverage in production is asymmetric: ``thumbnail_url`` is filled
    # for ~90-100% of posts, the title fields for <10% — the Frontend
    # treats the title as bonus context, not a required column.
    title_local: Optional[str] = None
    title_original: Optional[str] = None
    franchise: Optional[str] = None
    thumbnail_url: Optional[str] = None
    # Sprint 10i — Title.content_type ("Film" | "Series") so the LLM prompt
    # marker can flag Series posts. Default None for back-compat with
    # persisted briefs that pre-date Sprint 10i.
    content_type: Optional[str] = None

    # Sprint 5c — Asset-UUID des Sprint-5b-Eager-Loads, durchgereicht als
    # String. Frontend nutzt es für ``/api/thumbnails/{asset_id}`` (CDN-
    # Hotlink-Protection-Bypass via Referer-Header). Default ``None``
    # damit Briefe von vor Sprint 5c sauber parsen — der Frontend-
    # Fallback-Pfad nutzt dann direkt ``thumbnail_url``.
    asset_id: Optional[str] = None

    # Sprint 28.05.2026 (Punkt 4) — Breakout-Score gegen Channel-Baseline.
    # ``None`` wenn der Channel-Pool im 30-Tage-Fenster < 5 Posts hatte
    # (z-Score statistisch nicht definiert) oder std=0 (alle Posts
    # identisch). Default ``None`` damit persistierte Briefe von vor
    # diesem Sprint weiter via ``model_validate`` parsen — Frontend
    # graceful-degrades und blendet die Sortier-Option / "Breakouts"-
    # Sektion aus, wenn das Feld ueberall ``None`` ist.
    breakout_score: Optional["BreakoutScore"] = None


class HashtagFrequency(BaseModel):
    tag: str
    count: int


class BreakoutScore(BaseModel):
    """Sprint 28.05.2026 (Punkt 4) — relative Performance eines Posts
    gemessen gegen die Baseline desselben Channels im 30-Tage-Fenster.

    Der Score ist serverseitig berechnet (kein LLM-Pfad). Er existiert
    nur, wenn der Channel-Pool im Fenster ``sample_size >= 5`` hat —
    sonst ist der z-Score statistisch nicht aussagekraeftig und das
    Feld bleibt am Trager-RankedPost ``None``. Wenn der Score je im
    LLM-Brief auftaucht, gehoert er ueber das ``cited_post_ids``-
    Evidenz-Feld in den Brief, nicht als Freitext (siehe EVIDENZ-PFLICHT
    in ``insight_engine.SYSTEM_PROMPT``).

    Felder:
    - ``z_score`` — roher z-Score ``(eng - mean) / std``. Negative
      Werte = unterdurchschnittlich; > 2 = klarer Ausreisser.
    - ``multiplier`` — ``eng / mean``. Frontend rendert dies als
      "4,7x ueber Kanal-Schnitt". 1.0 = exakt Mittelwert.
    - ``weighted_score`` — ``z_score * decay_weight``. Sortier-
      Schluessel fuer das Breakouts-Ranking — bevorzugt Ausreisser, die
      ZUSAETZLICH jung sind (recency matters for "was trendet jetzt?").
    - ``decay_weight`` — exponentieller Recency-Faktor, ``0 < w <= 1``.
      Halbwertzeit 7 Tage: heute=1.0, nach 7d=0.5, nach 14d=0.25. Nutzt
      ``published_at`` mit Fallback auf ``detected_at`` (analog
      ``_channel_stats``-Window-Query).
    - ``baseline_mean`` / ``baseline_std`` / ``sample_size`` —
      Diagnose-Felder, damit die Frontend-Tooltips den Kontext zeigen
      koennen ("Schnitt 850 Reaktionen aus 23 Posts") und das Backend-
      Postmortem nachvollziehen kann, warum ein Score wie hoch ist.
    """
    z_score: float
    multiplier: float
    weighted_score: float
    decay_weight: float
    baseline_mean: float
    baseline_std: float
    sample_size: int


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
    # Sprint 2 — arithmetic mean of activation_rate across all posts in
    # the window (plattform-spezifische Formel siehe
    # ``services/insight_engine.compute_activation_rate``). Default 0.0 so
    # older persisted briefs that pre-date this field validate cleanly on
    # cache-hit-rehydrate; cleanly distinguishable from "low rate" because
    # ``posts_count > 0 and avg_activation_rate == 0.0`` is rare in
    # practice (would mean every post has views=0).
    avg_activation_rate: float = 0.0
    # Sprint-Trailerhaus-Prompt-v1: top historical posts from BEFORE the
    # current window. The LLM uses these as ground truth for the
    # ``vergleichbare_posts`` section. Default empty so existing fixtures
    # and old reports remain valid.
    historical_top_posts: list[TopPost] = []
    # Sprint 2 — Top-N posts for the Ranking-Sektion. Stable backend
    # default sort by ``engagement_sum desc``; Frontend re-sorts
    # clientseitig via the sort dropdown. Default empty so older persisted
    # briefs (pre-Sprint-2) still load — Frontend graceful-degrades and
    # hides the section when this list is empty.
    ranked_posts: list[RankedPost] = []

    # Sprint 28.05.2026 (Punkt 4) — Top-N RankedPosts dieses Channels
    # sortiert nach ``breakout_score.weighted_score`` desc (also relative
    # Ausreisser, die zusaetzlich jung sind). Speist die Frontend-Sektion
    # "Breakouts dieser Woche" — eine kleine Karte pro Post mit
    # sichtbarem ``multiplier`` (z.B. "4,7x ueber Kanal-Schnitt"), die
    # parallel zu ``top_posts`` (absolute Spitze) laeuft.
    #
    # Inhaltlich eine Teilmenge von ``ranked_posts`` (gleiche RankedPost-
    # Objekte mit gesetztem ``breakout_score``). Die Duplikation kostet
    # ein paar Bytes im Payload, vermeidet aber Frontend-seitiges Re-
    # Slicing und haelt die Sektion bei Backend-Aenderungen
    # autoritativ. Default leere Liste damit persistierte Briefe von
    # vor Punkt 4 sauber via ``model_validate`` parsen.
    breakouts: list[RankedPost] = []


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
    # Sprint UK-B1 — UK als 3. Markt additiv ergänzt. Defaults sorgen
    # dafür, dass persistierte Briefe von vor B1 weiter via
    # ``model_validate`` parsen. ``titles_in_both_markets`` behält die
    # DE∩US-Semantik in B1; eine Triple-Intersection (DE∩UK / US∩UK /
    # DE∩US∩UK) ist B2-Scope.
    uk_only_titles: list[str] = []
    uk_assets_with_title: int = 0
    uk_assets_total: int = 0
    overall_coverage_pct: float


class PlatformAggregation(BaseModel):
    """Sprint-4 — per-platform slice of a PairAggregation. One platform's
    DE/US/UK channel stats, cross-market matches, and title coverage live
    here so the LLM and Frontend can inspect each platform independently.

    ``de_channel`` / ``us_channel`` / ``uk_channel`` are Optional because
    some pairs only ship one or two market sides on a given platform
    (Disney/Prime/Paramount YouTube US-only; UK still rolling out for
    several pairs). ``_aggregate_platform`` handles the missing-market
    case by leaving the side at None; the Frontend hides empty halves.

    Sprint UK-B1 (2026-05-12): ``uk_channel`` als 3. Markt additiv
    ergänzt. Frontend bleibt 2-Spalten (DE/US) bis B3 — der LLM sieht
    UK aber bereits in Markdown- und JSON-Anhang und kann ab B1 in der
    Brief-Prosa darauf referenzieren.
    """
    platform: str  # tiktok | instagram | youtube
    de_channel: Optional[ChannelStats] = None
    us_channel: Optional[ChannelStats] = None
    # Sprint UK-B1 — UK als 3. Markt additiv. Default ``None`` damit
    # Pairs ohne UK-Spec (Phase-A-Gaps, ggf. künftige disabled Pairs)
    # sowie persistierte Briefe vor B1 weiter sauber parsen. Frontend bleibt
    # in B1 2-Spalten (B3-Scope), das LLM sieht UK aber bereits im
    # Markdown- und JSON-Block.
    uk_channel: Optional[ChannelStats] = None
    # ``cross_market_matches`` ist historisch der DE↔US-Slot (Name
    # vor B2 nicht umbenannt, damit persistierte Briefe weiter
    # validieren). Sprint B2 (27.05.2026) ergaenzt die zwei UK-Achsen
    # additiv — alte Briefe haben hier per Default eine leere Liste.
    cross_market_matches: list[CrossMarketMatch] = []
    de_uk_matches: list[CrossMarketMatch] = []
    us_uk_matches: list[CrossMarketMatch] = []
    title_coverage: TitleCoverage
    notes: list[str] = []


class RecommendedAction(BaseModel):
    """Sprint 29.05.2026 (Stufe-2 PR-C / P3) — Empfehlungs-Baustein
    aus dem Aggregator. Server rechnet, LLM formuliert (in dieser PR
    bleibt ``why`` leer — eigener Strang).

    Vier Cross-Tabs liefern Bausteine, gemappt auf zwei Dimensions:
    - ``format``: aus ``format``-Vocab (5.3.1) oder
      ``duration_bucket``.
    - ``cadence``: aus ``lifecycle_stage`` (5.3.1) oder
      ``days_to_release_bucket`` (PR-B).

    Bausteine entstehen nur, wenn ALLE Ehrlich-Klausel-Filter
    passieren (Confidence >= 0.7, Sample-Size >= 3, Effect-Size > 1.5x
    Baseline ODER < 0.5x Baseline). Sonst bleibt
    ``recommendation_candidates`` einfach leer — kein Notfall-Eintrag.

    ``cited_post_ids`` zitiert 3-5 belegende Posts aus dem
    Sample-Set; jede ID muss aus dem Pair-Post-Set der Woche stammen
    (Allow-Set, andockend an #189-Evidenz-Infrastruktur).
    """
    dimension: str  # "format" | "cadence" — Pydantic-Literal hier
                    # bewusst vermieden, weil das Set spaeter um "hook"
                    # erweitert werden koennte.
    recommended_value: str        # Closed-Vocab-Wert, z.B. "trailer",
                                  # "<15s", "post_launch", "1-4w_pre".
    evidence_metric: str          # "Activation 14,2 %"
    evidence_baseline: str        # "Pair-Median 6,8 %"
    # Sprint 29.05.2026 (Stufe-2 PR-C Iteration / Wolf-Befund) —
    # ``effect_size`` als Erstklass-Feld. Berechnung
    # ``metric_value / baseline_value``. Werte ueber 1.0 = ueber-
    # durchschnittlich, unter 1.0 = unter Schnitt. Schwelle aus dem
    # Briefing: nur Bausteine mit ``> 1.5`` ODER ``< 0.5`` ueberleben
    # den Filter; alles dazwischen wird verworfen. Sortier-Anker fuer
    # spaetere Top-N-Auswahl + Sicht-Check-Anzeige.
    # Default ``1.0`` damit persistierte Briefs vor diesem Feld-Add
    # weiter parsen — ein gepersistierter Brief vor dem Feld haette
    # implizit "Effect 1.0" gemeldet, was im Filter ohnehin
    # rausgefallen waere, also kein Daten-Drift.
    effect_size: float = 1.0
    cited_post_ids: list[str]     # 3-5 IDs aus dem Sample-Set
    sample_size: int              # Anzahl Posts im Cross-Tab-Wert
    confidence_avg: float         # Durchschnitt der ``confidence``-Werte
                                  # der zitierten Posts (aus Post.analysis)
    why: Optional[str] = None     # Platzhalter, in PR-C leer
    # Sprint 29.05.2026 (Stufe-2 PR-C Iteration) — Dedup-Transparenz.
    # Wenn ein Baustein durch die Jaccard-Dedup-Logik verworfen wurde,
    # zeigt das Feld die ``dimension/value``-Kombination des
    # gewinnenden Bausteins. ``None`` bei Siegern (Default-Output).
    # Verworfene Bausteine landen in einem separaten Feld
    # ``PairAggregation.recommendation_suppressed`` fuer Debug + Sicht-
    # Check; der API-Default-Output ``recommendation_candidates``
    # enthaelt ausschliesslich Sieger (suppressed_by == None).
    suppressed_by: Optional[str] = None


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
    # Sprint UK-B1 — Mirror-Feld für UK. Default ``None`` damit alte
    # persistierte Briefe parsen und Pairs ohne UK-Spec keine speziellen
    # Code-Pfade brauchen.
    uk_channel: Optional[ChannelStats] = None
    cross_market_matches: list[CrossMarketMatch]
    # Sprint B2 — Mirror der zwei UK-Achsen aus dem ersten Platform-
    # Block. Default-leere Listen damit Briefe vor B2 weiter parsen.
    de_uk_matches: list[CrossMarketMatch] = []
    us_uk_matches: list[CrossMarketMatch] = []
    title_coverage: TitleCoverage
    notes: list[str]
    # Sprint-4 multi-platform v2a: per-platform aggregations live here.
    # Default empty so persisted briefs from before Sprint-4 still parse
    # via ``model_validate`` on cache-hit re-hydrate (Sprint-1 persistence
    # contract). Old briefs render with the legacy mirror fields above
    # until they are force-regenerated; new briefs populate both.
    per_platform: list[PlatformAggregation] = []
    # Sprint 29.05.2026 (Stufe-2 PR-B / P1) — Verteilung der Posts
    # ueber die ``DaysToReleaseBucket``-Klassen, gepoolt ueber alle
    # Plattformen und Channels des Pairs im 7d-Window. Schluessel sind
    # die Enum-Werte (``">4w_pre"``, ``"1-4w_pre"``,
    # ``"release_week"``, ``"1-4w_post"``, ``">4w_post"``,
    # ``"evergreen"``, ``"unknown"``); Werte sind Post-Counts. Default
    # leer damit persistierte Briefe vor PR-B sauber re-hydraten.
    # Streaming-Pairs (primevideo, paramountplus, netflix) haben hoehe
    # ``unknown``-Anteile — das ist die Datenrealitaet (niedrige
    # Title-Kopplung), kein Aggregator-Bug.
    days_to_release_distribution: dict[str, int] = {}
    # Sprint 29.05.2026 (Stufe-2 PR-C / P3) — Empfehlungs-Bausteine,
    # die der Aggregator pro 7d-Window aus vier Cross-Tabs ableitet
    # (format × activation, duration_bucket × activation,
    # lifecycle_stage × activation, days_to_release_bucket × activation).
    # Ehrlich-Klausel: leer, wenn nichts den Sample-Size- und
    # Effect-Size-Filter passiert. Default leer fuer Backwards-Compat
    # persistierter Briefs vor PR-C.
    #
    # Sprint 29.05.2026 (Iteration nach #206-Sicht-Check): Dedup via
    # Jaccard-Index ueber ``cited_post_ids``. Sieger bleiben hier,
    # Verworfene (mit gesetztem ``suppressed_by``) wandern in
    # ``recommendation_suppressed`` (Debug-/Sicht-Check-Output).
    recommendation_candidates: list[RecommendedAction] = []
    # Sprint 29.05.2026 (Iteration nach #206-Sicht-Check) — Dedup-
    # Verworfene. Default leer fuer Backwards-Compat. Enthaelt
    # ``RecommendedAction``-Objekte mit ``suppressed_by`` gesetzt auf
    # die ``dimension/value``-Kombi des Gewinners. Fuer Sicht-Check-
    # Skript + Debug; default-API-Output bleibt sauber.
    recommendation_suppressed: list[RecommendedAction] = []


class Trend(BaseModel):
    name: str
    evidence: str
    implication_for_creation: str
    # Sprint 28.05.2026 — Evidenz-Block / Quellen-Attribution. Liste der
    # exakten post_url-, asset_id- oder match_key-Strings aus der
    # ``PairAggregation``, auf denen ``evidence`` beruht. Default leere
    # Liste: persistierte Briefe vor diesem Sprint validieren weiter
    # via ``model_validate``; das LLM ist via System-Prompt zur
    # Befuellung verpflichtet (siehe EVIDENZ-PFLICHT im SYSTEM_PROMPT),
    # der Validator in ``insight_engine`` loggt fehlende oder
    # nicht-belegte IDs als ``insight-engine-citation-unverified``.
    cited_post_ids: list[str] = Field(default_factory=list)


class Action(BaseModel):
    what: str
    why: str
    for_whom: str
    # Sprint 28.05.2026 — Evidenz-Block. Siehe ``Trend.cited_post_ids``.
    cited_post_ids: list[str] = Field(default_factory=list)


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
    # Sprint 28.05.2026 — Evidenz-Block. Siehe ``Trend.cited_post_ids``.
    cited_post_ids: list[str] = Field(default_factory=list)


class VerdictEnum(str, Enum):
    """Sprint 7 — Voice-2.5-Verdict-Vokabular.

    Vorher (Sprint 1-6): freier String, in der Praxis ``trägt`` /
    ``zerläuft`` / ``sitzt`` / ``ausbaufähig`` / ``zweischneidig``.
    Wolf-Kritikpunkt: "Friedhof"-/"zerläuft"-Vokabel ist Berater-
    Sprache, nicht das, was er einem Cutter im Schnittraum sagen
    würde. Die drei neuen Werte sind bewusst alltagssprachlich.

    Backwards-Compat siehe ``TitelImFokus.normalize_old_verdict`` —
    persistierte Briefe aus Sprint 1-6 enthalten die alten Werte und
    werden beim Re-Hydrate auf die neuen drei normalisiert.
    """
    FUNKTIONIERT = "funktioniert"
    KOMMT_NICHT_AN = "kommt nicht an"
    NOCH_AUSBAUFAEHIG = "noch ausbaufähig"


_VERDICT_BACKCOMPAT_MAP: dict[str, str] = {
    # Sprint 1-3 hatten drei Werte. Sprint-Trailerhaus-Prompt-v2.2
    # erweiterte um zwei weitere; alle fünf werden auf die neuen drei
    # gemappt, damit kein persistierter Brief beim Re-Hydrate platzt.
    "trägt": VerdictEnum.FUNKTIONIERT.value,
    "sitzt": VerdictEnum.FUNKTIONIERT.value,
    "zerläuft": VerdictEnum.KOMMT_NICHT_AN.value,
    "ausbaufähig": VerdictEnum.NOCH_AUSBAUFAEHIG.value,
    "zweischneidig": VerdictEnum.NOCH_AUSBAUFAEHIG.value,
}


class TitelImFokus(BaseModel):
    """Ein Titel, eine Kampagne oder ein Format-Block, der diese Woche
    sichtbar im Material auftaucht. Sektion 'Worum geht's diese Woche'
    gibt einem Cutter in 10 Sekunden Ueberblick, welche konkreten Titel
    in den Aufgaben weiter unten gemeint sind.
    Sprint-Trailerhaus-Prompt-v2.2.

    post_url (v2.4): URL des Referenz-Posts, falls vorhanden. Macht den
    Titel im Frontend klickbar — Cutter kann den Spot direkt ansehen.
    Nur exakte URLs aus dem Input verwenden, niemals erfinden.

    verdict (Sprint 7): Voice-2.5 Vokabular — "funktioniert" /
    "kommt nicht an" / "noch ausbaufähig". Alte Werte aus Sprint 1-6
    werden via ``normalize_old_verdict`` auf die neuen drei
    normalisiert."""
    titel: str
    markt: str
    format_typ: str
    kennzahl: str
    release_datum: Optional[str] = None
    verdict: Optional[VerdictEnum] = None
    post_url: Optional[str] = None
    # Sprint 28.05.2026 — Evidenz-Block. ``post_url`` ist der Klick-
    # Anker (single Headline-Post), ``cited_post_ids`` listet die IDs
    # aus der ``PairAggregation``, auf denen die ``kennzahl`` beruht
    # (kann mehrere Posts oder einen ``match_key`` einschliessen).
    cited_post_ids: list[str] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_old_verdict(cls, v):
        """Sprint-1-6-Briefe haben ``trägt``/``sitzt``/``zerläuft``/
        ``ausbaufähig``/``zweischneidig`` im verdict-Feld. Sprint 7
        normalisiert vor der Enum-Validation auf die neuen drei
        Werte; alles andere geht unverändert durch und stolpert dann
        ggf. über die Standard-Enum-Validation (das ist Absicht: ein
        unbekannter neuer Wert soll laut auffallen, nicht silently
        durchgehen)."""
        if v is None:
            return v
        if isinstance(v, VerdictEnum):
            return v
        if isinstance(v, str):
            stripped = v.strip()
            if stripped in _VERDICT_BACKCOMPAT_MAP:
                return _VERDICT_BACKCOMPAT_MAP[stripped]
            return stripped
        return v




class CrossMarketInsight(BaseModel):
    de_vs_us: str
    # Sprint B2 (27.05.2026) — zwei zusaetzliche pairwise-Achsen
    # additiv ergaenzt. Optional damit alte persistierte Briefs vor B2
    # weiter validieren und die LLM die Achse weglassen kann, wenn keine
    # UK-Matches da sind. ``transfer_opportunity`` bleibt
    # Markt-unabhaengiger Empfehlungs-Slot (jetzt kann die LLM darin
    # mehrere Transferrichtungen formulieren).
    de_vs_uk: Optional[str] = None
    us_vs_uk: Optional[str] = None
    transfer_opportunity: str
    # Sprint 28.05.2026 — Evidenz-Block. Sammlung der IDs (post_url /
    # asset_id / match_key) aus der ``PairAggregation``, auf denen die
    # drei Narrative-Achsen + ``transfer_opportunity`` insgesamt
    # beruhen. Pro-Achse-Granularitaet ist bewusst NICHT modelliert —
    # die Achsen referenzieren oft dieselben matches und eine flache
    # Liste haelt den Schema-Aufwand klein. Phase-2-Strikt-Cutover kann
    # spaeter pro-Achse-Felder additiv ergaenzen, wenn die
    # Phase-1-Telemetrie zeigt, dass die Granularitaet noetig ist.
    cited_post_ids: list[str] = Field(default_factory=list)


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
    """Sprint 7-iter-2: Compliance-Listen (``must_show`` / ``no_go``) raus,
    stattdessen ``was_diese_woche`` als Fließtext-Absatz. Listen-Schema-
    Felder erzwingen Bullet-Output, egal was der Prompt sagt; ein Free-
    Text-Feld zwingt den LLM zur Erzählung. ``extra='ignore'`` lässt
    persistierte Sprint-1-7-Briefe mit ``must_show``/``no_go`` weiter
    parsen — die Listen werden beim Re-Hydrate stillschweigend
    verworfen, das Frontend rendert sie sowieso nicht mehr."""
    model_config = ConfigDict(extra="ignore")

    schnitt_pace: Optional[str] = None
    hook_strategie: Optional[str] = None
    empfohlene_laengen: Optional[str] = None
    was_diese_woche: Optional[str] = None


class FuerMotionDesigner(BaseModel):
    """Sprint 7-iter-2: ``was_diese_woche`` als Fließtext-Absatz analog
    zu ``FuerCutter``. ``extra='ignore'`` deckt persistierte Briefe ab,
    falls dort z. B. zukünftig zusätzliche Felder eingebaut wurden."""
    model_config = ConfigDict(extra="ignore")

    caption_style: Optional[str] = None
    text_overlay: Optional[str] = None
    branding_einsatz: Optional[str] = None
    was_diese_woche: Optional[str] = None


class FuerCreativeProducer(BaseModel):
    """Sprint 7-iter-2: ``was_diese_woche`` als Fließtext-Absatz analog
    zu ``FuerCutter``. ``extra='ignore'`` für Backwards-Compat."""
    model_config = ConfigDict(extra="ignore")

    strategische_pattern: Optional[str] = None
    cross_market_chancen: Optional[str] = None
    format_empfehlungen: Optional[str] = None
    was_diese_woche: Optional[str] = None


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
    # Hard cap at 8 (2026-06): 10 verbose entries early in the document were
    # the main token hog driving tail-truncation of the last Optional
    # sections. Prompt asks for 6-8; the schema enforces the ceiling.
    ganz_konkret: Optional[list[SchnittAufgabe]] = Field(default=None, max_length=8)
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


class TitleLLMReport(BaseModel):
    """Title-centric brief (Variante 1) — describes ONE title across all
    channels/platforms/markets. Mirrors the LLMReport contract: required core
    (headline, tldr, plattform_vergleich, data_caveats) + optional tail. The
    #224 truncation guard catches a dropped required field (schema-fail ->
    persist-skip), so a truncated title brief is never silently persisted.

    Field names are title-specific (plattform_vergleich / markt_vergleich /
    verlauf) — NOT the pair brief's cross_market_insight / aktuell_im_fokus.
    """
    model_config = ConfigDict(extra="ignore")

    headline: str
    tldr: str
    plattform_vergleich: str  # core: what carries where for THIS title, with numbers
    data_caveats: list[str]
    # Optional tail — null when the data doesn't support the section.
    markt_vergleich: Optional[str] = None        # null for single-market titles
    verlauf: Optional[str] = None                # campaign arc; filled when >=2 weekly buckets
    top_post_kommentar: Optional[str] = None
    fuer_cutter: Optional[FuerCutter] = None
    cited_post_ids: list[str] = Field(default_factory=list)


class TitleInsightReport(BaseModel):
    """Pydantic wrapper for a generated title brief — the title analogue of
    ``InsightReport``. ``aggregation`` is the serialised ``TitleAggregation``
    dict (the dataclass from ``services.title_aggregation``)."""
    title_id: str
    title_original: str
    iso_week: int
    iso_year: int
    window_days: int
    generated_at: datetime
    model: str
    dry_run: bool = False
    llm_output: Optional[TitleLLMReport] = None
    aggregation: dict
    cost_usd_estimate: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    raw_llm_text: Optional[str] = Field(
        default=None,
        description="Raw assistant text — populated only when JSON parsing fails.",
    )


class PairInfo(BaseModel):
    """One enabled pair as exposed by ``GET /api/pairs`` (Sprint 2026-05-12).

    Frontend-ready: ``display_name`` is the curated human label, ``markets``
    is ordered DE → US → UK (visual order on the landing-page card,
    independent of insertion order in the PAIRS dict), and
    ``frequency_label`` is the briefing cadence shown next to the markets.

    Sprint 28.05.2026 (Studio-Kennzahl): zwei zusaetzliche Felder
    fuer die Live-Anzeige auf der Startseiten-Kachel. Beide haben
    Defaults, damit bestehende Tests und Frontend-Versionen ohne
    Felder-Awareness weiter parsen.
    - ``posts_count_this_week``: Live-Aggregat (kein LLM, kein Cron-
      Bezug). Posts mit ``published_at >= week_start_iso`` ODER
      ``published_at IS NULL AND detected_at >= week_start_iso``
      (gleicher Fallback wie der Breakout-Score in #190 + die
      Aggregations-Fenster in ``_channel_stats``).
    - ``last_generated_at``: Timestamp des juengsten persistierten
      ``InsightReport`` fuer das Pair (max ueber alle KWs). ``None``
      wenn fuer das Pair noch nie ein Brief generiert wurde —
      Frontend rendert "Aktualisiert —".
    """

    pair_key: str
    display_name: str
    markets: list[str]
    frequency_label: str
    enabled: bool
    posts_count_this_week: int = 0
    last_generated_at: Optional[datetime] = None
    # Sprint 28.05.2026 (Option B / Headline pro Kachel) — die
    # LLM-headline des juengsten persistierten Briefs (90-Zeichen-
    # Marketingtext). ``None`` wenn:
    # - noch kein Brief generiert (UK-Pairs vor erstem Cron),
    # - Brief existiert aber ``llm_output`` ist ``{}`` / ``None``
    #   (Persist-Skip nach JSON-Parse-Fail),
    # - ``headline``-Feld ist im JSON nicht vorhanden oder leer.
    # In jedem Fall darf Frontend auf ``has_brief`` testen — der
    # Bool ist autoritativ fuer "gibt's irgendeine Brief-Row?".
    headline: Optional[str] = None
    has_brief: bool = False


class PairsResponse(BaseModel):
    pairs: list[PairInfo]


# ---------- Segment-Roundup (Master-Plan-Schritt-3, non-pair) -------------

class ChannelRoundupStats(BaseModel):
    """Per-Channel-Sicht im Roundup. Schlankere Form als ``ChannelStats``
    aus der Pair-Pipeline: keine cross-market-Felder, keine
    title_coverage (Roundup-Charakter ist deskriptiv, kein Vergleich).
    Enthaelt genug Material, dass der LLM-Prompt pro Channel einen
    Aktivitaets-Abriss formulieren kann."""
    channel_id: Optional[str]
    handle: str
    platform: str
    market: Optional[str]
    posts_count: int
    avg_engagement: float
    avg_caption_length: float
    avg_duration_seconds: Optional[float]
    top_hashtags: list[HashtagFrequency]
    top_posts: list[RankedPost]


class SegmentAggregation(BaseModel):
    """Header + Channel-Slate fuer einen Segment-Roundup. Wird im
    ``segment_roundup.channels_aggregation``-JSON-Blob persistiert.
    Audit-Trail + Frontend-Render-Material in einem Pass.
    """
    segment: str
    iso_year: int
    iso_week: int
    window_days: int
    window_start: datetime
    window_end: datetime
    channels_evaluated: int
    channels_with_posts: int
    total_posts: int
    channels: list[ChannelRoundupStats]


class RoundupTitelImFokus(BaseModel):
    """Ein Titel-/Kampagnen-Block im Segment-Roundup. Pendant zu
    ``TitelImFokus`` aus der Pair-Pipeline, eine Abweichung: ``channel``
    statt ``markt``.

    Begruendung der Abweichung (Wolf-Ping-1, 26.05.): der Markt ist im
    Single-Segment-Roundup informationslos (im ``us_major``-Roundup ist
    alles "US"). Die nuetzliche Achse ist, **welcher Channel** den Post
    abgesetzt hat — Verleiher-/Handle-Identifikation.

    Schritt-3d (26.05.): ``verdict`` ist aus dem Roundup-Schema entfernt.
    Begruendung Wolf: Die Pills behaupten ein Urteil, fuer das kein
    definierter Massstab existiert; das LLM erfindet implizit eine
    Schwelle. Die konkrete ``kennzahl`` pro Titel spricht fuer sich.
    Der Pair-Brief nutzt ``VerdictEnum`` weiterhin — Pair-Pipeline
    bleibt unangetastet. Pydantic-Default: unbekannte Felder in der
    Roundup-Row (z.B. ein ``verdict`` in einer KW-22-Pre-3d-Row) werden
    bei ``model_validate`` ignoriert, das Frontend muss in seinem
    Pfad robust dagegen sein.
    """
    titel: str
    channel: str
    format_typ: str
    kennzahl: str
    release_datum: Optional[str] = None
    post_url: Optional[str] = None


class SegmentRoundupLLMReport(BaseModel):
    """Deskriptive LLM-Synthese fuer einen Segment-Roundup.

    Master-Plan-Schritt-3c (2026-05-26 — Qualitaets-Anhebung): Schema
    rueckt stilistisch an den Pair-Brief heran, ohne den Markt-Vergleich.
    "Deskriptiv" heisst **kein Markt-Vergleich**.

    Schritt-3d (26.05.): Das ``verdict``-Feld in jedem Titel-Block ist
    entfernt — die Pills behaupten ein Urteil, fuer das kein definierter
    Massstab existiert. Die konkrete ``kennzahl`` pro Titel spricht
    fuer sich.

    - ``headline`` und ``tldr``: Segment-Kopf mit Haltung, Pair-Brief-
      Stil. 1 Satz / 2-3 Saetze.
    - ``titles`` (required, kann leer sein): Herzstueck. Pro Titel ein
      Block mit Channel/Verleiher, Format, Kennzahl, Bewertung — analog
      ``aktuell_im_fokus`` im Pair-Brief. Anzahl folgt der Substanz:
      typischerweise 5-7 bei aktiven Segmenten, deutlich weniger bei
      ruhigen. Lieber 2 echte Blöcke als 6 mit aufgeblasener Substanz.
    - ``themes`` (Optional, 2-5 Bullets): wiederkehrende Themen/Motive
      ueber Channels hinweg.
    - ``data_caveats`` (required): Lautstaerke-Hinweise (z.B. "12 von
      33 Channels ohne Posts in diesem Fenster"). Bleibt sichtbar, ist
      aber nicht mehr der dominante Inhalt — die Substanz liegt in
      ``titles``.

    Felder aus dem Schritt-3-Schema, die mit 3c entfallen:
    - ``what_ran``: Inhalt wandert in ``titles`` (konkret) + ``tldr``
      (Erzaehl-Bogen).
    - ``channels_in_focus``: Channel-Information steht jetzt pro Titel-
      Block im ``channel``-Feld.
    """
    headline: str
    tldr: str
    titles: list[RoundupTitelImFokus] = Field(default_factory=list)
    themes: Optional[list[str]] = None
    data_caveats: list[str]


class SegmentRoundupReport(BaseModel):
    """Vollstaendiger Roundup-Bericht — Persistenz-Stand + LLM-Synthese.
    Pendant zu ``InsightReport`` der Pair-Pipeline, aber strikt disjunkt:
    teilt keinen Code, kein Schema, keine Tabelle.
    """
    segment: str
    iso_year: int
    iso_week: int
    window_days: int
    generated_at: datetime
    model: str
    aggregation: SegmentAggregation
    llm_output: Optional[SegmentRoundupLLMReport] = None
    cost_usd_estimate: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    raw_llm_text: Optional[str] = Field(
        default=None,
        description="Raw assistant text — populated only when JSON parsing fails.",
    )


# ---------- Roundup-Read-Endpoint (Master-Plan-Schritt-3b) ----------------


class SegmentRoundupSummary(BaseModel):
    """Eine Zeile in der Antwort von ``GET /api/roundups/latest`` — Frontend-
    bereite Sicht auf den jeweils neuesten Roundup eines Segments.

    Pendant zu ``PairInfo`` der Pair-Pipeline: schlank, deskriptiv, ohne
    Audit-Trail-Felder (``channels_aggregation`` wandert nicht ueber den
    Wire; das volle Audit-Material bleibt in der DB und ist via
    ``/api/admin/roundups/generate`` und DB-Inspektion erreichbar).

    Felder fuer die Kachel (Kurzform): ``segment``, ``iso_year``,
    ``iso_week``, ``channels_with_posts``, ``total_posts``,
    ``llm_output.headline`` bzw. ``llm_output.tldr``.

    Felder fuer den Aufklapp-Bereich (`<details>`): ``llm_output`` voll —
    inkl. ``data_caveats``, das bei duennen Segmenten den Unterschied
    macht zwischen "duenner Brief" und "erklaerte ruhige Woche".
    """
    segment: str
    iso_year: int
    iso_week: int
    window_days: int
    generated_at: datetime
    channels_evaluated: int
    channels_with_posts: int
    total_posts: int
    llm_output: SegmentRoundupLLMReport


class SegmentRoundupListResponse(BaseModel):
    """Antwort-Hülle fuer ``GET /api/roundups/latest``. Liste der jeweils
    neuesten Roundups pro Segment, sortiert in ``ChannelSegment``-ENUM-
    Reihenfolge (us_major, us_independent, uk_major, uk_independent,
    de_verleih, de_independent) — deterministisch und unabhaengig von der
    Insert-Reihenfolge in der Tabelle. Segmente ohne Roundup-Row sind
    nicht enthalten; der Frontend-Block behandelt das ueber seine
    eigene Segment-Liste als "noch kein Roundup"-Zustand.
    """
    roundups: list[SegmentRoundupSummary]
