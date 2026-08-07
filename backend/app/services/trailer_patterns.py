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

Zwei Kennzahlen, zwei Verdikte
==============================

Die erste Auswertung auf echten Daten (06.08.2026, Ergebnisse-Dokument)
hat gezeigt, dass der Median allein hier nichts hergibt: **alle** Zellen
lagen zwischen 0,86 und 1,29, keine einzige erreichte die 1,5x/0,5x-
Schwellen. Das ist kein Zufall, sondern Bauart — die Baseline ist der
Median desselben Kanals, gegen den gemessen wird, und ueber tausende
Posts regrediert jeder Teilmengen-Median zurueck Richtung 1,0.

Deshalb gibt es eine zweite Kennzahl, die eine andere Frage beantwortet:

- ``median_lift`` / ``verdict`` — *laeuft der typische Post besser?*
  Bleibt erhalten, spricht bei Korpusgroesse aber selten an.
- ``breakout_rate`` / ``breakout_verdict`` — *produziert dieses Merkmal
  mehr Ausreisser?* Anteil der Posts mit Lift >= 2,0, verglichen mit
  derselben Quote ueber den gesamten normierten Bestand
  (``baseline_breakout_rate``).

Erst die zweite Kennzahl brachte Signal: die Trefferquoten spannten sich
von 11,2 % (humorvoll) bis 28,7 % (>60s) bei einer Basisquote von 20 %.
Sie fand auch den Fall, den der Median strukturell verdeckt —
behind_the_scenes mit unterdurchschnittlichem Median (0,86) bei
gleichzeitig ueberdurchschnittlicher Trefferquote (27,9 %): meist
Blindgaenger, aber ueberdurchschnittlich oft ein Volltreffer.

Das ``breakout_verdict`` benutzt einen z-Test gegen die Basisquote statt
eines festen Faktors — Begruendung bei ``_breakout_z``. ``p90_lift``
zeigt zusaetzlich, wie hoch die guten Faelle einer Zelle reichen.

Interpretationsfalle Nummer zwei: ``median_lift`` und ``breakout_rate``
koennen in verschiedene Richtungen zeigen (siehe behind_the_scenes).
Das ist kein Widerspruch, sondern die eigentliche Information — beide
Spalten gehoeren zusammen gelesen.
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

# ---- Trefferquote (zweite Kennzahl, s. Modul-Docstring) ---------------
#
# Ab welchem Lift ein Post als "Treffer" zaehlt. 2.0 = doppelt so gute
# Aktivierung wie der Kanal ueblicherweise erreicht.
BREAKOUT_LIFT_THRESHOLD = 2.0

# Ab welchem z-Wert die Abweichung der Zellen-Trefferquote von der
# Korpus-Trefferquote als belastbar gilt. 2.0 entspricht grob dem
# 95-%-Niveau bei einem Binomialanteil.
BREAKOUT_Z_THRESHOLD = 2.0

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

    # Zweite Kennzahl: Anteil Posts mit Lift >= BREAKOUT_LIFT_THRESHOLD,
    # verglichen mit derselben Quote ueber den gesamten normierten Bestand.
    breakout_rate: float = 0.0
    breakout_z: Optional[float] = None
    breakout_verdict: str = "insufficient"
    p90_lift: float = 0.0

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
            "breakout_rate": round(self.breakout_rate, 4),
            "breakout_z": round(self.breakout_z, 2) if self.breakout_z is not None else None,
            "breakout_verdict": self.breakout_verdict,
            "p90_lift": round(self.p90_lift, 3),
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
    # Trefferquote ueber den gesamten normierten Bestand — die Referenz,
    # gegen die jede Zelle verglichen wird.
    baseline_breakout_rate: float = 0.0
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
            "baseline_breakout_rate": round(self.baseline_breakout_rate, 4),
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


def _percentile(values: list[float], q: float) -> float:
    """Lineares Perzentil. Fuer p90 der Lift-Verteilung einer Zelle —
    zeigt, wie hoch die guten Faelle reichen, was der Median verschweigt."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    low = int(pos)
    high = min(low + 1, len(s) - 1)
    frac = pos - low
    return s[low] + (s[high] - s[low]) * frac


def _breakout_z(cell_rate: float, baseline_rate: float, n: int) -> Optional[float]:
    """z-Wert der Zellen-Trefferquote gegen die Korpus-Trefferquote.

    Warum ein z-Test statt eines festen Schwellwerts wie "1,25x der
    Basisquote": die Stichprobengroessen unterscheiden sich um zwei
    Groessenordnungen (43 Posts bei behind_the_scenes gegen 1.577 bei
    >60s). Ein fester Faktor behandelt beide gleich und erklaert eine
    Abweichung, die bei n=43 blosses Rauschen sein kann, zum Befund. Der
    z-Wert skaliert dagegen mit der Wurzel aus n und macht den
    Unterschied rechnerisch statt nur optisch sichtbar.

    Standardfehler des Binomialanteils unter der Nullhypothese
    "Zelle verhaelt sich wie der Korpus": sqrt(p*(1-p)/n) mit p =
    baseline_rate. Gibt None zurueck, wenn keine sinnvolle Referenz
    existiert (leerer Korpus oder Basisquote 0/1).

    Bekannte Grenze, bewusst nicht korrigiert: ueber alle Dimensionen
    werden rund 20 Zellen geprueft, bei |z| >= 2 ist also etwa ein
    Zufallstreffer zu erwarten. Eine Bonferroni-Korrektur wuerde die
    Schwelle so weit anheben, dass kleine Zellen grundsaetzlich nie
    ansprechen — fuer eine Hypothesen-erzeugende Auswertung der falsche
    Tausch. Wer eine Einzelaussage belastbar braucht, prueft sie
    gesondert nach.
    """
    if n <= 0 or baseline_rate <= 0.0 or baseline_rate >= 1.0:
        return None
    se = (baseline_rate * (1.0 - baseline_rate) / n) ** 0.5
    if se == 0:
        return None
    return (cell_rate - baseline_rate) / se


def _breakout_verdict_for(z: Optional[float]) -> str:
    if z is None:
        return "insufficient"
    if z >= BREAKOUT_Z_THRESHOLD:
        return "over"
    if z <= -BREAKOUT_Z_THRESHOLD:
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

    # ---- Korpus-Trefferquote als Referenz --------------------------------
    breakouts_total = sum(
        1 for p in usable if lift_by_post[p.id] >= BREAKOUT_LIFT_THRESHOLD
    )
    baseline_breakout_rate = breakouts_total / len(usable)

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

            breakout_hits = sum(1 for x in lifts if x >= BREAKOUT_LIFT_THRESHOLD)
            breakout_rate = breakout_hits / len(lifts)

            reason = None
            if len(cell_posts) < min_sample:
                verdict = "insufficient"
                breakout_verdict = "insufficient"
                breakout_z = None
                reason = f"nur {len(cell_posts)} Posts (Minimum {min_sample})"
            elif channel_count < min_channels:
                verdict = "insufficient"
                breakout_verdict = "insufficient"
                breakout_z = None
                reason = (
                    f"nur {channel_count} Kanaele (Minimum {min_channels}) — "
                    f"ein einzelner Vielposter wuerde das Muster tragen"
                )
            else:
                verdict = _verdict_for(median_lift)
                breakout_z = _breakout_z(
                    breakout_rate, baseline_breakout_rate, len(cell_posts)
                )
                breakout_verdict = _breakout_verdict_for(breakout_z)

            cells.append(
                PatternCell(
                    value=value,
                    sample_size=len(cell_posts),
                    channel_count=channel_count,
                    median_lift=median_lift,
                    median_activation=_median(activations),
                    median_views=int(_median([float(v) for v in views])) if views else None,
                    verdict=verdict,
                    breakout_rate=breakout_rate,
                    breakout_z=breakout_z,
                    breakout_verdict=breakout_verdict,
                    p90_lift=_percentile(lifts, 0.9),
                    reason=reason,
                )
            )

        # Stabile Reihenfolge: belastbare Zellen zuerst, darin nach
        # Auffaelligkeit der Trefferquote. Sortiert wird nach breakout_z
        # statt nach median_lift, weil der Median bei Korpusgroesse
        # gegen 1,0 regrediert und die Reihenfolge dann kaum noch etwas
        # aussagt (s. Modul-Docstring).
        cells.sort(
            key=lambda c: (
                c.verdict == "insufficient",
                -(c.breakout_z if c.breakout_z is not None else float("-inf")),
            )
        )
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
        baseline_breakout_rate=baseline_breakout_rate,
        dimensions=dimensions,
        notes=notes,
    )
