"""Trailer-Intelligence Stufe 1, Schritt 2 — korpusweite Muster-Aggregation.

Beantwortet die Frage aus dem Briefing: *welche Merkmale gehen mit
ueberdurchschnittlicher Reichweite einher?* — ueber den gesamten Bestand,
nicht pro Studio-Pair und nicht fuer eine einzelne Woche.

Abgrenzung zum bestehenden Empfehlungs-Baustein
===============================================

``insight_engine._compute_recommendation_pair`` beantwortet eine andere
Frage und bleibt unveraendert:

|                | Empfehlungs-Baustein        | dieses Modul                  |
|----------------|-----------------------------|-------------------------------|
| Zuschnitt      | ein Pair (Studio DE+US)     | ganzer Bestand, opt. je Markt |
| Fenster        | 7 Tage                      | 90 Tage (parametrisierbar)    |
| Zweck          | "diese Woche auffaellig"    | stabiles Strukturmuster       |
| Baseline       | Median des Pairs            | Median des **Kanals**         |

Wiederverwendet werden ``compute_activation_rate``, ``_duration_bucket``
und ``_median`` — die Kennzahlen sollen zwischen beiden Sichten
vergleichbar bleiben.

Warum Kanal-Baseline statt Rohwert
==================================

Ein Netflix-Post startet nicht bei derselben Reichweite wie der eines
kleinen Kanals. Wer Roh-Reichweiten korpusweit mittelt, misst am Ende
Kanalgroesse, nicht Kreativ-Wirkung — genau der Fehler, vor dem die
Inventur (Abschnitt 2, "Vorbehalt") gewarnt hat.

Deshalb rechnet dieses Modul pro Post einen **Lift**:

    lift = activation_rate(post) / median(activation_rate aller Posts
                                          desselben Kanals im Fenster)

Ein Lift von 1,0 heisst "so gut wie dieser Kanal ueblicherweise
abschneidet", 1,8 heisst "80 % besser als der eigene Schnitt". Damit
werden grosse und kleine Kanaele vergleichbar, und aggregiert wird
ausschliesslich ueber Lifts.

**Interpretationsfalle beim Lesen der Ausgabe.** Der Median ist der des
Kanals, also der seines eigenen Output-Mix. Macht ein Format die
Mehrheit der Posts eines Kanals aus, bestimmt es den Median mit und kann
rechnerisch kaum darueber liegen — das Signal erscheint dann gespiegelt:
nicht "Trailer over", sondern "alles andere under". Wer nur auf
``verdict == "over"`` schaut, uebersieht diesen Fall. Immer beide
Richtungen einer Dimension zusammen lesen.
(Test: ``test_dominant_format_defines_its_own_baseline``.)

Ehrlichkeits-Regeln
===================

Uebernommen vom Empfehlungs-Baustein, plus eine zusaetzliche:

1. **Mindest-Stichprobe** je Zelle (Default 5).
2. **Mindest-Kanalzahl** je Zelle (Default 3) — NEU. Korpusweit kann ein
   einzelner Vielposter sonst im Alleingang ein "Muster" erzeugen. Der
   Pair-Pfad braucht das nicht, weil er ohnehin nur zwei Kanaele sieht.
3. **Effektstaerke** > 1,5x (over) bzw. < 0,5x (under), sonst neutral.
4. **Konfidenz >= 0,7** — aber nur fuer Dimensionen, die aus dem
   Analyzer stammen. Dauer und Musik sind *gemessen*, nicht klassifiziert;
   sie durch den Konfidenz-Filter zu schicken wuerde bei der aktuellen
   Klassifikations-Abdeckung (12 %, Inventur Abschnitt 2) rund sieben
   Achtel brauchbarer Daten wegwerfen, ohne die Qualitaet zu erhoehen.

Zellen, die 1 oder 2 reissen, verschwinden nicht — sie werden mit
``verdict="insufficient"`` gemeldet. Eine Luecke ist ein Befund; sie
stillschweigend wegzufiltern wuerde den Bestand besser aussehen lassen,
als er ist.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.models.entities import Channel, Post
from app.services.insight_engine import (
    _duration_bucket,
    _median,
    compute_activation_rate,
)

logger = logging.getLogger(__name__)


DEFAULT_WINDOW_DAYS = 90
MIN_SAMPLE_PER_CELL = 5
MIN_CHANNELS_PER_CELL = 3
CONFIDENCE_THRESHOLD = 0.7
OVER_THRESHOLD = 1.5
UNDER_THRESHOLD = 0.5

# Ein Kanal braucht selbst genug Posts, damit sein Median als Baseline
# taugt. Darunter ist der Median ein Zufallswert und der daraus
# abgeleitete Lift verzerrt jede Zelle, in die er einfliesst.
MIN_POSTS_PER_CHANNEL_BASELINE = 4


@dataclass
class PatternCell:
    """Eine Auspraegung einer Dimension, z.B. format="trailer"."""

    value: str
    sample_size: int
    channel_count: int
    median_lift: float
    median_activation: float
    median_views: Optional[int]
    verdict: str  # "over" | "under" | "neutral" | "insufficient"
    reason: Optional[str] = None  # nur bei "insufficient"

    def to_dict(self) -> dict:
        out = {
            "value": self.value,
            "sample_size": self.sample_size,
            "channel_count": self.channel_count,
            "median_lift": round(self.median_lift, 3),
            "median_activation": round(self.median_activation, 5),
            "median_views": self.median_views,
            "verdict": self.verdict,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class TrailerPatternReport:
    window_days: int
    window_start: datetime
    window_end: datetime
    market: Optional[str]
    posts_in_window: int
    posts_with_baseline: int
    channels_covered: int
    analysis_coverage: float
    dimensions: dict[str, list[PatternCell]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "market": self.market,
            "posts_in_window": self.posts_in_window,
            "posts_with_baseline": self.posts_with_baseline,
            "channels_covered": self.channels_covered,
            "analysis_coverage": round(self.analysis_coverage, 4),
            "dimensions": {
                name: [c.to_dict() for c in cells]
                for name, cells in self.dimensions.items()
            },
            "notes": self.notes,
        }


# ---------- Dimensions-Extraktoren -------------------------------------


def _analysis_of(post: Post) -> dict:
    """``Post.analysis`` sicher als Dict.

    Achtung: ``sa.JSON()`` persistiert Python-``None`` als JSON-Skalar
    ``null`` (``none_as_null=False``), nicht als SQL-NULL. Eine nie
    analysierte Zeile liefert hier also ``None``, kein ``{}`` — und
    ``count(analysis)`` in SQL zaehlt sie faelschlich mit. Genau dieser
    Stolperstein hat bei der Inventur zu einer falschen
    Abdeckungs-Messung gefuehrt (12 % statt vermeintlich 100 %).
    """
    a = post.analysis
    return a if isinstance(a, dict) else {}


def _post_confidence(post: Post) -> Optional[float]:
    conf = _analysis_of(post).get("confidence")
    return float(conf) if isinstance(conf, (int, float)) else None


def _extract_format(post: Post) -> Optional[str]:
    v = _analysis_of(post).get("format")
    return v if isinstance(v, str) and v else None


def _extract_tone(post: Post) -> Optional[str]:
    v = _analysis_of(post).get("tone")
    return v if isinstance(v, str) and v else None


def _extract_lifecycle(post: Post) -> Optional[str]:
    v = _analysis_of(post).get("lifecycle_stage")
    return v if isinstance(v, str) and v else None


def _extract_duration_bucket(post: Post) -> Optional[str]:
    if post.duration_seconds is None:
        return None
    return _duration_bucket(post.duration_seconds)


def _extract_music_kind(post: Post) -> Optional[str]:
    """TikTok-Musikart aus ``raw_payload['_creative_radar_music']``.

    Der Apify-TikTok-Connector sichert ``musicMeta`` unter diesem
    Schluessel (apify_connector.normalize_tiktok_item) — bislang las das
    niemand aus. Die Inventur fand die Abdeckung bei 2.335 von 2.335
    TikTok-Posts, also 100 %.

    Bewusst defensiv: das Feld ``musicOriginal`` ist Apify-seitig nicht
    vertraglich zugesichert und kann als bool, als String oder gar nicht
    kommen. Alles Unklare wird ``unknown`` statt geraten — eine falsch
    einsortierte Zelle waere schlimmer als eine fehlende.
    """
    raw = post.raw_payload if isinstance(post.raw_payload, dict) else {}
    music = raw.get("_creative_radar_music")
    if not isinstance(music, dict) or not music:
        return None
    original = music.get("musicOriginal")
    if isinstance(original, bool):
        return "original_sound" if original else "licensed_track"
    if isinstance(original, str):
        low = original.strip().lower()
        if low in ("true", "1", "yes"):
            return "original_sound"
        if low in ("false", "0", "no"):
            return "licensed_track"
    return "unknown"


@dataclass(frozen=True)
class _Dimension:
    name: str
    extract: Callable[[Post], Optional[str]]
    requires_analysis: bool


# ``requires_analysis`` entscheidet ueber den Konfidenz-Filter: nur
# modell-erzeugte Werte muessen ihn passieren. duration/music sind
# gemessen und wuerden bei 12 % Klassifikations-Abdeckung sonst
# unnoetig auf ein Achtel schrumpfen.
DIMENSIONS: tuple[_Dimension, ...] = (
    _Dimension("format", _extract_format, True),
    _Dimension("tone", _extract_tone, True),
    _Dimension("lifecycle_stage", _extract_lifecycle, True),
    _Dimension("duration_bucket", _extract_duration_bucket, False),
    _Dimension("music_kind", _extract_music_kind, False),
)


# ---------- Aggregation -------------------------------------------------


def _verdict_for(lift: float) -> str:
    if lift >= OVER_THRESHOLD:
        return "over"
    if lift <= UNDER_THRESHOLD:
        return "under"
    return "neutral"


def compute_trailer_patterns(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    market: Optional[str] = None,
    now: Optional[datetime] = None,
    min_sample: int = MIN_SAMPLE_PER_CELL,
    min_channels: int = MIN_CHANNELS_PER_CELL,
) -> TrailerPatternReport:
    """Aggregiert Reichweiten-Muster ueber den Bestand.

    ``market`` filtert auf die Kanal-Spalte (z.B. "DE"); ``None`` nimmt
    alle. ``now`` ist der Fensterendpunkt und existiert, damit Tests ein
    festes Fenster setzen koennen.
    """
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)

    channel_stmt = select(Channel)
    if market:
        channel_stmt = channel_stmt.where(Channel.market == market)
    channels = list(session.exec(channel_stmt).all())
    platform_by_channel = {c.id: c.platform for c in channels}
    if not channels:
        return TrailerPatternReport(
            window_days=window_days,
            window_start=window_start,
            window_end=window_end,
            market=market,
            posts_in_window=0,
            posts_with_baseline=0,
            channels_covered=0,
            analysis_coverage=0.0,
            notes=[f"Keine Kanaele fuer market={market!r}."],
        )

    posts = list(
        session.exec(
            select(Post)
            .where(Post.channel_id.in_(list(platform_by_channel.keys())))
            .where(Post.detected_at >= window_start)
            .where(Post.detected_at <= window_end)
        ).all()
    )

    notes: list[str] = []
    if not posts:
        return TrailerPatternReport(
            window_days=window_days,
            window_start=window_start,
            window_end=window_end,
            market=market,
            posts_in_window=0,
            posts_with_baseline=0,
            channels_covered=0,
            analysis_coverage=0.0,
            notes=["Keine Posts im Fenster."],
        )

    # ---- Kanal-Baselines ------------------------------------------------
    by_channel: dict[Any, list[Post]] = defaultdict(list)
    for p in posts:
        by_channel[p.channel_id].append(p)

    activation_by_post: dict[Any, float] = {}
    for channel_id, channel_posts in by_channel.items():
        platform = platform_by_channel.get(channel_id, "tiktok")
        for p in channel_posts:
            activation_by_post[p.id] = compute_activation_rate(p, platform)

    baseline_by_channel: dict[Any, float] = {}
    thin_channels = 0
    for channel_id, channel_posts in by_channel.items():
        if len(channel_posts) < MIN_POSTS_PER_CHANNEL_BASELINE:
            thin_channels += 1
            continue
        med = _median([activation_by_post[p.id] for p in channel_posts])
        if med <= 0:
            # Kanal ohne messbare Aktivierung im Fenster (z.B. nur Posts
            # ohne views). Ein Lift waere hier eine Division durch ~0.
            continue
        baseline_by_channel[channel_id] = med

    if thin_channels:
        notes.append(
            f"{thin_channels} Kanaele mit weniger als "
            f"{MIN_POSTS_PER_CHANNEL_BASELINE} Posts im Fenster uebersprungen "
            f"(Median waere als Baseline nicht belastbar)."
        )

    # ---- Lifts ----------------------------------------------------------
    lift_by_post: dict[Any, float] = {}
    usable: list[Post] = []
    for p in posts:
        base = baseline_by_channel.get(p.channel_id)
        if base is None:
            continue
        lift_by_post[p.id] = activation_by_post[p.id] / base
        usable.append(p)

    if not usable:
        return TrailerPatternReport(
            window_days=window_days,
            window_start=window_start,
            window_end=window_end,
            market=market,
            posts_in_window=len(posts),
            posts_with_baseline=0,
            channels_covered=0,
            analysis_coverage=0.0,
            notes=notes + ["Kein Kanal hatte genug Posts fuer eine Baseline."],
        )

    analysed = sum(1 for p in usable if _extract_format(p) is not None)
    coverage = analysed / len(usable)
    if coverage < 0.5:
        notes.append(
            f"Klassifikations-Abdeckung liegt bei {coverage:.0%}. Die "
            f"Dimensionen format/tone/lifecycle_stage stuetzen sich auf "
            f"diesen Ausschnitt; duration_bucket und music_kind nutzen den "
            f"vollen Bestand."
        )

    # ---- Cross-Tabs -----------------------------------------------------
    dimensions: dict[str, list[PatternCell]] = {}
    for dim in DIMENSIONS:
        buckets: dict[str, list[Post]] = defaultdict(list)
        for p in usable:
            if dim.requires_analysis:
                conf = _post_confidence(p)
                if conf is None or conf < CONFIDENCE_THRESHOLD:
                    continue
            value = dim.extract(p)
            if value is None:
                continue
            buckets[value].append(p)

        cells: list[PatternCell] = []
        for value, cell_posts in buckets.items():
            channel_count = len({p.channel_id for p in cell_posts})
            lifts = [lift_by_post[p.id] for p in cell_posts]
            activations = [activation_by_post[p.id] for p in cell_posts]
            views = [int(p.visible_views) for p in cell_posts if p.visible_views]
            median_lift = _median(lifts)

            reason = None
            if len(cell_posts) < min_sample:
                verdict = "insufficient"
                reason = f"nur {len(cell_posts)} Posts (Minimum {min_sample})"
            elif channel_count < min_channels:
                verdict = "insufficient"
                reason = (
                    f"nur {channel_count} Kanaele (Minimum {min_channels}) — "
                    f"ein einzelner Vielposter wuerde das Muster tragen"
                )
            else:
                verdict = _verdict_for(median_lift)

            cells.append(
                PatternCell(
                    value=value,
                    sample_size=len(cell_posts),
                    channel_count=channel_count,
                    median_lift=median_lift,
                    median_activation=_median(activations),
                    median_views=int(_median([float(v) for v in views])) if views else None,
                    verdict=verdict,
                    reason=reason,
                )
            )

        # Stabile Reihenfolge: belastbare Zellen zuerst, darin nach Lift.
        cells.sort(key=lambda c: (c.verdict == "insufficient", -c.median_lift))
        dimensions[dim.name] = cells

    return TrailerPatternReport(
        window_days=window_days,
        window_start=window_start,
        window_end=window_end,
        market=market,
        posts_in_window=len(posts),
        posts_with_baseline=len(usable),
        channels_covered=len(baseline_by_channel),
        analysis_coverage=coverage,
        dimensions=dimensions,
        notes=notes,
    )
