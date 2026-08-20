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

Das ``breakout_verdict`` benutzt einen z-Test statt eines festen
Faktors — Begruendung bei ``_breakout_z``. ``p90_lift`` zeigt
zusaetzlich, wie hoch die guten Faelle einer Zelle reichen.

Interpretationsfalle Nummer zwei: ``median_lift`` und ``breakout_rate``
koennen in verschiedene Richtungen zeigen (siehe behind_the_scenes).
Das ist kein Widerspruch, sondern die eigentliche Information — beide
Spalten gehoeren zusammen gelesen.

Warum gegen die Plattform-Mischung geprueft wird, nicht gegen den Korpus
=======================================================================

Die Trefferquote wurde zunaechst gegen eine korpusweite Basisquote von
20 % geprueft. Eine Aufteilung nach Plattform (07.08.2026) hat gezeigt,
dass diese 20 % ein Mittelwert ohne Bedeutung sind: die Plattformen
haben verschiedene Basisquoten, und eine Zelle, die ueberwiegend auf
einer starken Plattform liegt, sieht dagegen automatisch gut aus.

Jede Zelle bekommt deshalb ihren eigenen Erwartungswert: das mit ihrer
Besetzung gewichtete Mittel der Plattform-Quoten
(``_expected_breakout_rate``). Geprueft wird die Zellen-Trefferquote
gegen diesen Wert, nicht gegen die Korpus-Quote.
(Test: ``test_platform_composition_alone_is_not_a_pattern``.)

Der Produktionslauf danach lieferte den Beleg in Reinform. ``music_kind``
stammt aus dem TikTok-Connector und existiert nur fuer TikTok-Posts, hat
also eine reine Plattform-Mischung. ``original_sound`` liegt bei 10,1 %:

    gegen die Korpusquote 20,0 %  ->  z = -11,2  (Einbruch)
    gegen die TikTok-Quote 10,1 % ->  z =  +0,0  (durchschnittlich)

Dieselbe Zahl, zwei gegensaetzliche Aussagen.

Warum Posts ohne Views ausgeschlossen werden
============================================

Derselbe Lauf hat aber auch gezeigt, dass die Korrektur allein nicht
reicht: **alle vier** Dauer-Buckets kamen mit z zwischen 3,1 und 8,7
ueber ihrer Erwartung heraus, waehrend jede andere Zelle auf neutral
fiel. Vier gleichgerichtete Ausschlaege in einer Dimension sind kein
Befund, sondern ein Hinweis auf einen systematischen Fehler.

Er lag in der Grundgesamtheit der Plattform-Quote. Instagram hatte im
Fenster 3.130 Posts mit einer Trefferquote von 28,6 %, davon 2.125 mit
Dauer-Angabe und einer Quote von 42,1 %. Die Differenz:

    1.005 Posts ohne Dauer  ->  0 Treffer (nachgerechnet: 0,6)

Diese 1.005 Posts sind Bilder und Karussells, fuer die Instagram keine
Views ausliefert. Ihre Aktivierung ist 0,0 — keine Messung, sondern eine
Leerstelle. Sie koennen per Konstruktion nie ein Treffer sein, sitzen
aber im Nenner der Plattform-Quote. Jede Zelle, die nur Posts mit Dauer
enthaelt (also jeder Dauer-Bucket), lag dadurch automatisch darueber.

Schlimmer noch: die 0,0 geht in den Kanal-Median ein und druckt ihn,
womit die Lifts **aller uebrigen** Posts desselben Kanals steigen.
Gemessen (Query B, 07.08.2026):

    | Plattform | mit 0,0-Posts | ohne | Differenz |
    |-----------|---------------|------|-----------|
    | Instagram |        28,6 % | 15,1 % | -13,5 pp |
    | YouTube   |        15,9 % | 15,9 % |        0 |
    | TikTok    |        10,1 % | 10,1 % |        0 |

TikTok und YouTube haben keinen einzigen solchen Post; der Effekt ist
rein instagram-seitig, dort aber halbiert er die Quote.

``_has_measurable_views`` schliesst diese Posts deshalb aus — vor der
Baseline, damit der Median sauber bleibt. Die tatsaechliche
Plattform-Spanne betraegt danach 10,1 % bis 15,9 % statt der zunaechst
berichteten vier Groessenordnungen; die Plattform-Korrektur bleibt
richtig, ihr Ausmass war ueberzeichnet.
(Test: ``test_posts_without_views_do_not_inflate_the_rest``.)

Formatklassen: Langform und Kurzform sind nicht dasselbe Spiel
==============================================================

Langformate (Trailer, Teaser, Promo) beginnen bei rund einer Minute und
sind auf Aufbau, Wendepunkt und Aufloesung gebaut. Kurzformate (TV- und
Social-Spots, 5 bis rund 90 Sekunden) muessen in den ersten Sekunden
alles unterbringen. Beide in einen Topf zu werfen und nach "dem"
erfolgreichen Muster zu suchen, mischt zwei verschiedene Handwerke.

Die Dimension ``format_class`` macht die Trennung sichtbar, der
Parameter ``format_class`` von ``compute_trailer_patterns`` grenzt die
gesamte Auswertung darauf ein. Eingegrenzt laeuft alles Weitere — auch
die Plattform-Quoten — innerhalb dieser Klasse, sodass Langformate nur
noch mit Langformaten verglichen werden.

Drei Klassen, rein aus der Dauer:

    kurzform            < 60 s
    uebergang_60_90s    60 bis unter 90 s
    langform            >= 90 s

**Warum nicht aus dem Format-Label.** Das laege naeher — ``trailer`` ist
per Definition Langform. Aber das Label existiert fuer rund 14 % der
Posts, die Dauer fuer rund 90 %. Eine Klasseneinteilung auf Label-Basis
haette in genau der Frage, wegen der sie gebaut wurde, fast keine
Datengrundlage.

**Warum die Grauzone eine eigene Klasse ist.** Zwischen 60 und 90
Sekunden ueberlappen sich beide Branchendefinitionen. Ein
75-Sekunden-Stueck kann ein kurzer Trailer oder ein langer Spot sein;
das entscheidet der Aufbau, nicht die Sekundenzahl. Die Zone bekommt
deshalb einen eigenen Namen statt einer geratenen Zuordnung — dieselbe
Regel wie bei ``verdict="insufficient"``: eine Luecke zeigen ist besser
als sie zu fuellen.

**Warum die Kanal-Baseline nicht mitgefiltert wird.** Bei eingegrenzter
Auswertung bleibt der Nenner des Lifts der Median des *gesamten*
Kanal-Outputs. Wuerde er mitgefiltert, verglichen sich Langformate nur
noch mit Langformaten desselben Kanals — und die Frage "traegt Langform
ueberhaupt?" waere per Konstruktion nicht mehr beantwortbar, weil die
Antwort dann immer 1,0 lautet.
(Test: ``test_scoped_report_keeps_the_full_channel_baseline``.)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Post, Title
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

# Ab welchem z-Wert die Abweichung der Zellen-Trefferquote von ihrer
# erwarteten Quote als belastbar gilt. 2.0 entspricht grob dem
# 95-%-Niveau bei einem Binomialanteil.
BREAKOUT_Z_THRESHOLD = 2.0

# Eine Plattform braucht selbst genug Posts, damit ihre Trefferquote als
# Erwartungswert taugt. Darunter wuerde die Zelle faktisch gegen sich
# selbst geprueft und koennte nie auffallen; solche Plattformen fallen
# auf die Korpus-Quote zurueck.
MIN_POSTS_PER_PLATFORM_BASELINE = 30

# ---- Formatklassen (s. Modul-Docstring) --------------------------------
#
# Grenzen aus der Branchendefinition: Langformate (Trailer, Teaser,
# Promo) beginnen bei rund einer Minute, Kurzformate (TV- und
# Social-Spots) reichen von 5 Sekunden bis maximal rund 90 Sekunden.
# Zwischen 60 und 90 Sekunden ueberlappen sich beide Definitionen — das
# ist kein Fehler der Grenzen, sondern eine echte Grauzone.
FORMAT_CLASS_LOWER_SECONDS = 60
FORMAT_CLASS_UPPER_SECONDS = 90

FORMAT_CLASS_KURZFORM = "kurzform"
FORMAT_CLASS_LANGFORM = "langform"
FORMAT_CLASS_UEBERGANG = "uebergang_60_90s"
FORMAT_CLASSES = (
    FORMAT_CLASS_KURZFORM,
    FORMAT_CLASS_UEBERGANG,
    FORMAT_CLASS_LANGFORM,
)

# Ein Kanal braucht selbst genug Posts, damit sein Median als Baseline
# taugt. Darunter ist der Median ein Zufallswert und der daraus
# abgeleitete Lift verzerrt jede Zelle, in die er einfliesst.
MIN_POSTS_PER_CHANNEL_BASELINE = 4


def _has_measurable_views(post: Post) -> bool:
    """Traegt dieser Post eine Reichweite, gegen die sich Aktivierung
    ueberhaupt rechnen laesst?

    ``compute_activation_rate`` liefert fuer views=0 eine 0,0 — was wie
    eine Messung aussieht, aber eine Leerstelle ist. Bei Instagram
    betrifft das 676 von 3.130 Posts im Fenster (Bilder und Karussells,
    fuer die Instagram keine Views ausliefert); TikTok und YouTube haben
    keinen einzigen solchen Post.

    Warum das nicht harmlos ist: die Null geht in den Kanal-Median ein
    und druckt ihn. Dadurch steigen die Lifts **aller anderen** Posts
    desselben Kanals. Gemessen am 07.08.2026 hob das Instagrams
    Trefferquote von 15,1 % auf 28,6 % — die Verzerrung trifft also
    nicht die Posts ohne Views, sondern alle uebrigen.
    """
    v = post.visible_views
    return v is not None and v > 0


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
    # verglichen mit der Quote, die aus der Plattform-Mischung dieser
    # Zelle zu erwarten waere (``expected_breakout_rate``).
    breakout_rate: float = 0.0
    expected_breakout_rate: float = 0.0
    breakout_z: Optional[float] = None
    breakout_verdict: str = "insufficient"
    p90_lift: float = 0.0

    # Posts je Plattform in dieser Zelle. Steht in der Ausgabe, damit
    # nachvollziehbar bleibt, woher der Erwartungswert kommt.
    platform_mix: dict[str, int] = field(default_factory=dict)

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
            "expected_breakout_rate": round(self.expected_breakout_rate, 4),
            "breakout_z": round(self.breakout_z, 2) if self.breakout_z is not None else None,
            "breakout_verdict": self.breakout_verdict,
            "p90_lift": round(self.p90_lift, 3),
            "platform_mix": dict(sorted(self.platform_mix.items())),
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
    # Auf welche Formatklasse die Auswertung eingegrenzt wurde; None =
    # alle. Steht im Bericht, weil sonst nicht erkennbar waere, dass die
    # Zahlen nur fuer einen Ausschnitt gelten.
    format_class: Optional[str] = None
    # Trefferquote ueber den gesamten normierten Bestand. Nur noch
    # Rueckfall-Referenz und Kontextwert — verglichen wird je Zelle gegen
    # ``platform_breakout_rates``, gewichtet mit ihrer Plattform-Mischung.
    baseline_breakout_rate: float = 0.0
    # Trefferquote je Plattform. Der eigentliche Massstab, seit die
    # Auswertung vom 07.08.2026 gezeigt hat, wie weit die Plattformen
    # auseinanderliegen (s. Modul-Docstring).
    platform_breakout_rates: dict[str, float] = field(default_factory=dict)
    dimensions: dict[str, list[PatternCell]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "market": self.market,
            "format_class": self.format_class,
            "posts_in_window": self.posts_in_window,
            "posts_with_baseline": self.posts_with_baseline,
            "channels_covered": self.channels_covered,
            "analysis_coverage": round(self.analysis_coverage, 4),
            "baseline_breakout_rate": round(self.baseline_breakout_rate, 4),
            "platform_breakout_rates": {
                pl: round(rate, 4)
                for pl, rate in sorted(self.platform_breakout_rates.items())
            },
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


def _format_class_of(post: Post) -> Optional[str]:
    """Kurzform, Langform oder Grauzone — allein aus der Dauer.

    Bewusst nicht aus dem ``format``-Label abgeleitet, obwohl das
    naeherliegt: das Label existiert fuer rund 14 % der Posts, die Dauer
    fuer rund 90 %. Eine Klasseneinteilung, die auf dem Label aufsetzt,
    haette in genau der Frage, wegen der sie gebaut wurde, fast keine
    Datengrundlage.

    Die Grauzone bekommt eine eigene Klasse statt einer Zuordnung. Ein
    75-Sekunden-Stueck kann ein kurzer Trailer oder ein langer Spot sein;
    das entscheidet der Aufbau, nicht die Sekundenzahl. Zu raten waere
    hier schlimmer als die Luecke zu zeigen — dieselbe Regel wie bei
    ``verdict="insufficient"``.
    """
    d = post.duration_seconds
    if d is None:
        return None
    if d < FORMAT_CLASS_LOWER_SECONDS:
        return FORMAT_CLASS_KURZFORM
    if d < FORMAT_CLASS_UPPER_SECONDS:
        return FORMAT_CLASS_UEBERGANG
    return FORMAT_CLASS_LANGFORM


def _extract_format_class(post: Post) -> Optional[str]:
    return _format_class_of(post)


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


def _title_by_post(session: Session, posts: list[Post]) -> dict[Any, Title]:
    """Post → ``Title``-Row, ueber das aelteste Asset mit ``title_id``.

    Gemeinsame Grundlage der Genre-Dimension und des Titel-Modus im
    Pattern-Briefing: beide haengen am Titel, der Titel am Asset.
    "Aeltestes Asset zuerst" macht die Wahl deterministisch, falls
    Assets desselben Posts nach Review-Korrekturen auf verschiedene
    Titel zeigen. Posts ohne Titel fehlen im Mapping.
    """
    if not posts:
        return {}
    post_ids = [p.id for p in posts]
    assets = session.exec(
        select(Asset)
        .where(Asset.post_id.in_(post_ids), Asset.title_id.is_not(None))
        .order_by(Asset.created_at.asc())
    ).all()
    title_id_by_post: dict[Any, Any] = {}
    for asset in assets:
        title_id_by_post.setdefault(asset.post_id, asset.title_id)
    if not title_id_by_post:
        return {}
    titles = {
        t.id: t
        for t in session.exec(
            select(Title).where(Title.id.in_(set(title_id_by_post.values())))
        ).all()
    }
    return {
        post_id: titles[title_id]
        for post_id, title_id in title_id_by_post.items()
        if title_id in titles
    }


def _genre_by_post(session: Session, posts: list[Post]) -> dict[Any, str]:
    """Post → primaeres TMDb-Genre (Trailer-Intelligence
    Genre-Nachrüstung, 20.08.2026).

    Primaer = erstes Element von ``title.genres`` — der Sync erhaelt die
    TMDb-Reihenfolge genau dafuer (title_sync, kein sorted-Merge).
    Posts ohne Titel oder mit leerer Genre-Liste fehlen im Mapping und
    fallen in der Cross-Tab-Schleife wie jeder ``None``-Extrakt heraus;
    die Abdeckung steht als Note im Bericht.
    """
    mapping: dict[Any, str] = {}
    for post_id, title in _title_by_post(session, posts).items():
        genres = title.genres
        if isinstance(genres, list) and genres and isinstance(genres[0], str) and genres[0].strip():
            mapping[post_id] = genres[0].strip()
    return mapping


def _title_name_by_post(session: Session, posts: list[Post]) -> dict[Any, str]:
    """Post → Titel-Name fuer den Titel-Modus des Pattern-Briefings
    (Stufe 1, "Beides, Genre zuerst" — zweiter Teil, 20.08.2026).

    ``title_original`` als Zellen-Identitaet: der Title-Sync haelt eine
    Row je ``tmdb_id``, der Original-Titel ist damit die kanonische,
    marktuebergreifende Bezeichnung — ``title_local`` waere je Markt
    verschieden und wuerde dieselbe Kampagne in zwei Zellen spalten.
    """
    mapping: dict[Any, str] = {}
    for post_id, title in _title_by_post(session, posts).items():
        name = (title.title_original or "").strip()
        if name:
            mapping[post_id] = name
    return mapping


# ``requires_analysis`` entscheidet ueber den Konfidenz-Filter: nur
# modell-erzeugte Werte muessen ihn passieren. duration/music sind
# gemessen und wuerden bei 12 % Klassifikations-Abdeckung sonst
# unnoetig auf ein Achtel schrumpfen. Genre (unten, dynamisch in
# ``compute_trailer_patterns``) ist ebenfalls gemessen — es kommt aus
# TMDb, nicht aus einem Klassifikator.
DIMENSIONS: tuple[_Dimension, ...] = (
    _Dimension("format_class", _extract_format_class, False),
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
    """z-Wert der Zellen-Trefferquote gegen ihre erwartete Quote.

    Warum ein z-Test statt eines festen Schwellwerts wie "1,25x der
    Basisquote": die Stichprobengroessen unterscheiden sich um zwei
    Groessenordnungen (43 Posts bei behind_the_scenes gegen 1.577 bei
    >60s). Ein fester Faktor behandelt beide gleich und erklaert eine
    Abweichung, die bei n=43 blosses Rauschen sein kann, zum Befund. Der
    z-Wert skaliert dagegen mit der Wurzel aus n und macht den
    Unterschied rechnerisch statt nur optisch sichtbar.

    ``baseline_rate`` ist seit der Plattform-Korrektur die *erwartete*
    Quote der Zelle (``_expected_breakout_rate``), nicht mehr die
    Korpus-Quote. Die Rechnung selbst bleibt gleich; nur die
    Nullhypothese ist schaerfer geworden: statt "Zelle verhaelt sich wie
    der Korpus" nun "Zelle verhaelt sich wie ihre eigene
    Plattform-Mischung".

    Standardfehler des Binomialanteils unter dieser Nullhypothese:
    sqrt(p*(1-p)/n) mit p = baseline_rate. Gibt None zurueck, wenn keine
    sinnvolle Referenz existiert (leerer Korpus oder Basisquote 0/1).

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


def _expected_breakout_rate(
    platforms: list[str],
    rate_by_platform: dict[str, float],
    fallback: float,
) -> float:
    """Trefferquote, die eine Zelle allein wegen ihrer Plattform-Mischung
    haette — ohne jeden inhaltlichen Effekt.

    Entspricht dem mit der Zellbesetzung gewichteten Mittel der
    Plattform-Quoten. Plattformen ohne belastbare eigene Quote (unter
    ``MIN_POSTS_PER_PLATFORM_BASELINE``) gehen mit ``fallback``, der
    Korpus-Quote, ein.
    """
    if not platforms:
        return fallback
    total = sum(rate_by_platform.get(pl, fallback) for pl in platforms)
    return total / len(platforms)


def _breakout_verdict_for(z: Optional[float]) -> str:
    if z is None:
        return "insufficient"
    if z >= BREAKOUT_Z_THRESHOLD:
        return "over"
    if z <= -BREAKOUT_Z_THRESHOLD:
        return "under"
    return "neutral"


def _corpus_breakout_rates(
    usable: list[Post],
    lift_by_post: dict[Any, float],
    platform_by_channel: dict[Any, str],
) -> tuple[float, dict[str, float]]:
    """Korpus- und Plattform-Trefferquoten — die Referenz, gegen die
    jede Zelle geprueft wird. Extrahiert (20.08.2026), damit der
    Titel-Modus des Pattern-Briefings (``compute_cells_for_mapping``)
    exakt dieselben Quoten rechnet wie der Muster-Bericht."""
    breakouts_total = sum(
        1 for p in usable if lift_by_post[p.id] >= BREAKOUT_LIFT_THRESHOLD
    )
    baseline_breakout_rate = breakouts_total / len(usable)

    posts_by_platform: dict[str, list[Post]] = defaultdict(list)
    for p in usable:
        posts_by_platform[platform_by_channel.get(p.channel_id, "unknown")].append(p)

    platform_breakout_rates: dict[str, float] = {}
    for platform, platform_posts in posts_by_platform.items():
        if len(platform_posts) < MIN_POSTS_PER_PLATFORM_BASELINE:
            continue
        hits = sum(
            1
            for p in platform_posts
            if lift_by_post[p.id] >= BREAKOUT_LIFT_THRESHOLD
        )
        platform_breakout_rates[platform] = hits / len(platform_posts)
    return baseline_breakout_rate, platform_breakout_rates


def _build_cell(
    value: str,
    cell_posts: list[Post],
    *,
    lift_by_post: dict[Any, float],
    activation_by_post: dict[Any, float],
    platform_by_channel: dict[Any, str],
    platform_breakout_rates: dict[str, float],
    baseline_breakout_rate: float,
    min_sample: int,
    min_channels: int,
) -> PatternCell:
    """Eine Zelle aus einem Bucket — Median-Lift, Trefferquote gegen die
    eigene Plattform-Mischung, Mindest-Stichprobe/-Kanalzahl. Extrahiert
    aus der Cross-Tab-Schleife (20.08.2026), unveraendert: der
    Titel-Modus soll dieselben Ehrlichkeits-Regeln durchlaufen wie jede
    Berichts-Dimension."""
    channel_count = len({p.channel_id for p in cell_posts})
    lifts = [lift_by_post[p.id] for p in cell_posts]
    activations = [activation_by_post[p.id] for p in cell_posts]
    views = [int(p.visible_views) for p in cell_posts if p.visible_views]
    median_lift = _median(lifts)

    breakout_hits = sum(1 for x in lifts if x >= BREAKOUT_LIFT_THRESHOLD)
    breakout_rate = breakout_hits / len(lifts)

    cell_platforms = [
        platform_by_channel.get(p.channel_id, "unknown") for p in cell_posts
    ]
    platform_mix: dict[str, int] = defaultdict(int)
    for pl in cell_platforms:
        platform_mix[pl] += 1
    expected_rate = _expected_breakout_rate(
        cell_platforms, platform_breakout_rates, baseline_breakout_rate
    )

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
            breakout_rate, expected_rate, len(cell_posts)
        )
        breakout_verdict = _breakout_verdict_for(breakout_z)

    return PatternCell(
        value=value,
        sample_size=len(cell_posts),
        channel_count=channel_count,
        median_lift=median_lift,
        median_activation=_median(activations),
        median_views=int(_median([float(v) for v in views])) if views else None,
        verdict=verdict,
        breakout_rate=breakout_rate,
        expected_breakout_rate=expected_rate,
        breakout_z=breakout_z,
        breakout_verdict=breakout_verdict,
        p90_lift=_percentile(lifts, 0.9),
        platform_mix=dict(platform_mix),
        reason=reason,
    )


def _sort_cells(cells: list[PatternCell]) -> None:
    """Stabile Reihenfolge: belastbare Zellen zuerst, darin nach
    Auffaelligkeit der Trefferquote. Sortiert wird nach breakout_z
    statt nach median_lift, weil der Median bei Korpusgroesse gegen
    1,0 regrediert und die Reihenfolge dann kaum noch etwas aussagt
    (s. Modul-Docstring)."""
    cells.sort(
        key=lambda c: (
            c.verdict == "insufficient",
            -(c.breakout_z if c.breakout_z is not None else float("-inf")),
        )
    )


@dataclass
class LiftContext:
    """Kanal-normierte Lifts plus alles, was zum Deuten noetig ist.

    Gemeinsame Grundlage fuer ``compute_trailer_patterns`` (Stufe 1) und
    ``langform_analysis`` (Stufe 3). Bewusst geteilt statt kopiert: hier
    stecken die Ehrlichkeits-Regeln, die drei Korrekturrunden gekostet
    haben — Mindest-Postzahl je Kanal, Ausschluss der Posts ohne
    messbare Views, Median als Baseline. Zwei Kopien davon liefen
    auseinander, und dann waeren die Zahlen der beiden Stufen nicht mehr
    vergleichbar.

    ``usable`` ist leer, wenn kein Post eine Baseline bekommen hat; der
    Grund steht dann in ``notes``.
    """

    window_start: datetime
    window_end: datetime
    posts_in_window: int
    usable: list[Post] = field(default_factory=list)
    lift_by_post: dict[Any, float] = field(default_factory=dict)
    activation_by_post: dict[Any, float] = field(default_factory=dict)
    platform_by_channel: dict[Any, str] = field(default_factory=dict)
    baseline_by_channel: dict[Any, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def build_lift_context(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    market: Optional[str] = None,
    now: Optional[datetime] = None,
) -> LiftContext:
    """Laedt das Fenster und rechnet je Post den Kanal-normierten Lift.

    Die Reihenfolge ist wesentlich und in dieser Form das Ergebnis der
    Korrekturen vom 07.08.2026:

    1. Kanaele (optional je Markt) und ihre Posts im Fenster laden.
    2. Posts ohne messbare Views **ausschliessen** — vor allem anderen,
       weil ihre 0,0-Aktivierung sonst den Kanal-Median druecken und die
       Lifts aller uebrigen Posts anheben wuerde.
    3. Je Kanal den Median der Aktivierung als Baseline bilden, aber nur
       ab ``MIN_POSTS_PER_CHANNEL_BASELINE`` Posts und nur bei Median > 0.
    4. Lift = Aktivierung / Kanal-Baseline.
    """
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)

    channel_stmt = select(Channel)
    if market:
        channel_stmt = channel_stmt.where(Channel.market == market)
    channels = list(session.exec(channel_stmt).all())
    platform_by_channel = {c.id: c.platform for c in channels}
    if not channels:
        return LiftContext(
            window_start=window_start,
            window_end=window_end,
            posts_in_window=0,
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
    posts_in_window = len(posts)
    if not posts:
        return LiftContext(
            window_start=window_start,
            window_end=window_end,
            posts_in_window=0,
            platform_by_channel=platform_by_channel,
            notes=["Keine Posts im Fenster."],
        )

    notes: list[str] = []

    # ---- Posts ohne messbare Reichweite ausschliessen ---------------------
    without_views = [p for p in posts if not _has_measurable_views(p)]
    if without_views:
        posts = [p for p in posts if _has_measurable_views(p)]
        by_platform_missing: dict[str, int] = defaultdict(int)
        for p in without_views:
            by_platform_missing[platform_by_channel.get(p.channel_id, "unknown")] += 1
        spread = ", ".join(
            f"{pl} {n}"
            for pl, n in sorted(by_platform_missing.items(), key=lambda kv: -kv[1])
        )
        notes.append(
            f"{len(without_views)} von {posts_in_window} Posts ohne messbare "
            f"Views ausgeschlossen ({spread}). Ihre Aktivierung waere 0,0 — "
            f"eine Leerstelle, keine Messung, die den Kanal-Median druecken "
            f"und die Lifts aller uebrigen Posts anheben wuerde."
        )
    if not posts:
        return LiftContext(
            window_start=window_start,
            window_end=window_end,
            posts_in_window=posts_in_window,
            platform_by_channel=platform_by_channel,
            notes=notes + ["Kein Post im Fenster hat messbare Views."],
        )

    # ---- Kanal-Baselines --------------------------------------------------
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
            # Kanal ohne messbare Aktivierung im Fenster. Ein Lift waere
            # hier eine Division durch ~0.
            continue
        baseline_by_channel[channel_id] = med

    if thin_channels:
        notes.append(
            f"{thin_channels} Kanaele mit weniger als "
            f"{MIN_POSTS_PER_CHANNEL_BASELINE} Posts im Fenster uebersprungen "
            f"(Median waere als Baseline nicht belastbar)."
        )

    # ---- Lifts ------------------------------------------------------------
    lift_by_post: dict[Any, float] = {}
    usable: list[Post] = []
    for p in posts:
        base = baseline_by_channel.get(p.channel_id)
        if base is None:
            continue
        lift_by_post[p.id] = activation_by_post[p.id] / base
        usable.append(p)

    if not usable:
        notes.append("Kein Kanal hatte genug Posts fuer eine Baseline.")

    return LiftContext(
        window_start=window_start,
        window_end=window_end,
        posts_in_window=posts_in_window,
        usable=usable,
        lift_by_post=lift_by_post,
        activation_by_post=activation_by_post,
        platform_by_channel=platform_by_channel,
        baseline_by_channel=baseline_by_channel,
        notes=notes,
    )


def compute_cells_for_mapping(
    ctx: LiftContext,
    value_by_post: dict[Any, str],
    *,
    min_sample: int = MIN_SAMPLE_PER_CELL,
    min_channels: int = MIN_CHANNELS_PER_CELL,
) -> list[PatternCell]:
    """Zellen fuer eine externe Post→Wert-Zuordnung — dieselbe Statistik
    wie die Berichts-Dimensionen (Korpus-Referenzquoten, z-Test gegen
    die eigene Plattform-Mischung, Mindest-Stichprobe/-Kanalzahl,
    breakout_z-Sortierung), nur die Gruppierung kommt von aussen.

    Gebaut fuer den Titel-Modus des Pattern-Briefings (20.08.2026):
    Titel sind keine Berichts-Dimension (hunderte Zellen wuerden jeden
    Report aufblaehen), aber ihre Zellen muessen mit den Berichts-Zahlen
    vergleichbar bleiben — deshalb hier, neben den geteilten Helfern,
    statt als Zweitrechnung im Briefing-Service.
    """
    if not ctx.usable:
        return []
    buckets: dict[str, list[Post]] = defaultdict(list)
    for p in ctx.usable:
        value = value_by_post.get(p.id)
        if value is not None:
            buckets[value].append(p)
    baseline_breakout_rate, platform_breakout_rates = _corpus_breakout_rates(
        ctx.usable, ctx.lift_by_post, ctx.platform_by_channel
    )
    cells = [
        _build_cell(
            value,
            cell_posts,
            lift_by_post=ctx.lift_by_post,
            activation_by_post=ctx.activation_by_post,
            platform_by_channel=ctx.platform_by_channel,
            platform_breakout_rates=platform_breakout_rates,
            baseline_breakout_rate=baseline_breakout_rate,
            min_sample=min_sample,
            min_channels=min_channels,
        )
        for value, cell_posts in buckets.items()
    ]
    _sort_cells(cells)
    return cells


def posts_for_cell(
    session: Session,
    ctx: LiftContext,
    dimension: str,
    value: str,
) -> list[Post]:
    """Die Mitglieder einer Berichts-Zelle — mit exakt den Regeln der
    Cross-Tab-Schleife: Konfidenz-Filter fuer modell-erzeugte
    Dimensionen, Session-Mapping fuer Genre. Fuer den
    Beispiel-Posts-Endpoint (Aufwertung B, 20.08.2026): "langform laeuft
    ueber Schnitt" wird erst mit den konkreten Posts dahinter zu
    Referenzmaterial fuer Cutter.

    Bewusst KEINE Zweitimplementierung der Zugehoerigkeit im Endpoint:
    wuerde der Konfidenz-Filter hier fehlen, zeigten die Beispiele Posts,
    die in der Zelle gar nicht mitgezaehlt wurden. Unbekannte Dimension
    → ``ValueError`` (der Endpoint macht 422 daraus).
    """
    if dimension == "genre":
        mapping = _genre_by_post(session, ctx.usable)
        dim = _Dimension("genre", lambda p: mapping.get(p.id), False)
    else:
        by_name = {d.name: d for d in DIMENSIONS}
        if dimension not in by_name:
            raise ValueError(f"Unbekannte Dimension: {dimension!r}")
        dim = by_name[dimension]
    members: list[Post] = []
    for p in ctx.usable:
        if dim.requires_analysis:
            conf = _post_confidence(p)
            if conf is None or conf < CONFIDENCE_THRESHOLD:
                continue
        if dim.extract(p) == value:
            members.append(p)
    return members


# Vorwochen-Vergleich (Aufwertung C, 20.08.2026): die "Vorwoche" ist
# KEINE persistierte Zeitreihe, sondern dieselbe deterministische
# Rechnung mit um 7 Tage verschobenem Fenster — jederzeit reproduzierbar,
# keine Migration, keine Cron-Abhaengigkeit. Bei 90-Tage-Fenstern
# ueberlappen sich beide Rechnungen zu ~92 %; Bewegung kommt also von
# den Raendern (neue Posts hinein, alte heraus). Genau das ist gewollt:
# der Trend zeigt, was sich DIESE Woche geaendert hat.
TREND_WINDOW_SHIFT_DAYS = 7


def apply_weekly_trend(
    current: TrailerPatternReport,
    previous: TrailerPatternReport,
) -> dict:
    """``current.to_dict()``, je belastbarer Zelle ergaenzt um:

    - ``vorwoche``: ``{breakout_rate, breakout_verdict}`` der Vorwoche,
      ``None``, wenn die Zelle dort fehlte oder ``insufficient`` war.
    - ``trend``: ``"neu"`` (Vorwoche fehlte oder war duenn — die Zelle
      ist neu belastbar), ``"gewechselt"`` (Verdikt hat sich geaendert),
      ``"stabil"`` (gleiches Verdikt). ``None`` fuer Zellen, die selbst
      ``insufficient`` sind — eine duenne Zelle hat keinen Trend.

    Verglichen wird das VERDIKT, nicht die rohe Quote: die Quote wackelt
    an den Fensterraendern von selbst, das z-Test-Verdikt erst, wenn die
    Bewegung Signalstaerke erreicht.
    """
    prev_by_key = {
        (dim, cell.value): cell
        for dim, cells in previous.dimensions.items()
        for cell in cells
    }
    data = current.to_dict()
    for dim, cells in data["dimensions"].items():
        for cell in cells:
            if cell["breakout_verdict"] == "insufficient":
                cell["vorwoche"] = None
                cell["trend"] = None
                continue
            prev = prev_by_key.get((dim, cell["value"]))
            if prev is None or prev.breakout_verdict == "insufficient":
                cell["vorwoche"] = None
                cell["trend"] = "neu"
                continue
            cell["vorwoche"] = {
                "breakout_rate": round(prev.breakout_rate, 4),
                "breakout_verdict": prev.breakout_verdict,
            }
            cell["trend"] = (
                "stabil"
                if prev.breakout_verdict == cell["breakout_verdict"]
                else "gewechselt"
            )
    return data


def compute_trailer_patterns(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    market: Optional[str] = None,
    now: Optional[datetime] = None,
    min_sample: int = MIN_SAMPLE_PER_CELL,
    min_channels: int = MIN_CHANNELS_PER_CELL,
    format_class: Optional[str] = None,
) -> TrailerPatternReport:
    """Aggregiert Reichweiten-Muster ueber den Bestand.

    ``market`` filtert auf die Kanal-Spalte (z.B. "DE"); ``None`` nimmt
    alle. ``now`` ist der Fensterendpunkt und existiert, damit Tests ein
    festes Fenster setzen koennen.

    ``format_class`` grenzt die Auswertung auf eine Formatklasse ein
    (``kurzform``, ``langform``, ``uebergang_60_90s``). Die
    Kanal-Baseline bleibt dabei bewusst die des **gesamten** Kanal-
    Outputs: der Lift soll weiter "verglichen mit dem, was dieser Kanal
    ueblicherweise erreicht" heissen. Wuerde die Baseline mitgefiltert,
    verglichen sich Langformate nur noch mit Langformaten desselben
    Kanals, und die Frage "traegt Langform ueberhaupt?" waere per
    Konstruktion nicht mehr beantwortbar.
    """
    if format_class is not None and format_class not in FORMAT_CLASSES:
        raise ValueError(
            f"format_class={format_class!r} unbekannt, erlaubt: {FORMAT_CLASSES}"
        )

    ctx = build_lift_context(
        session, window_days=window_days, market=market, now=now
    )
    window_start, window_end = ctx.window_start, ctx.window_end
    platform_by_channel = ctx.platform_by_channel
    activation_by_post = ctx.activation_by_post
    lift_by_post = ctx.lift_by_post
    baseline_by_channel = ctx.baseline_by_channel
    posts_in_window = ctx.posts_in_window
    notes = list(ctx.notes)
    usable = ctx.usable

    if not usable:
        return TrailerPatternReport(
            window_days=window_days,
            window_start=window_start,
            window_end=window_end,
            market=market,
            posts_in_window=posts_in_window,
            posts_with_baseline=0,
            channels_covered=0,
            analysis_coverage=0.0,
            format_class=format_class,
            notes=notes,
        )

    # ---- Eingrenzung auf eine Formatklasse -------------------------------
    #
    # Erst hier, nicht schon bei den Baselines: der Lift soll weiter
    # gegen den vollen Kanal-Output normiert sein (s. Docstring).
    if format_class is not None:
        before = len(usable)
        usable = [p for p in usable if _format_class_of(p) == format_class]
        notes.append(
            f"Eingegrenzt auf format_class={format_class!r}: {len(usable)} "
            f"von {before} Posts mit Baseline. Die Kanal-Baseline stammt "
            f"weiterhin aus dem vollen Output des jeweiligen Kanals."
        )
        if not usable:
            return TrailerPatternReport(
                window_days=window_days,
                window_start=window_start,
                window_end=window_end,
                market=market,
                posts_in_window=posts_in_window,
                posts_with_baseline=0,
                channels_covered=0,
                analysis_coverage=0.0,
                format_class=format_class,
                notes=notes,
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

    # ---- Trefferquoten als Referenz --------------------------------------
    baseline_breakout_rate, platform_breakout_rates = _corpus_breakout_rates(
        usable, lift_by_post, platform_by_channel
    )

    if len(platform_breakout_rates) >= 2:
        lo = min(platform_breakout_rates.values())
        hi = max(platform_breakout_rates.values())
        if lo > 0 and hi / lo >= 1.5:
            spread = ", ".join(
                f"{pl} {rate:.1%}"
                for pl, rate in sorted(
                    platform_breakout_rates.items(),
                    key=lambda kv: -kv[1],
                )
            )
            notes.append(
                f"Trefferquoten liegen je Plattform weit auseinander "
                f"({spread}). Jede Zelle wird deshalb gegen ihre eigene "
                f"Plattform-Mischung geprueft, nicht gegen die "
                f"Korpus-Quote von {baseline_breakout_rate:.1%}."
            )

    # ---- Genre-Dimension (20.08.2026) ------------------------------------
    #
    # Dynamisch statt in DIMENSIONS: die sechs statischen Extraktoren
    # lesen nur den Post, Genre braucht die Session (Asset → Titel).
    # requires_analysis=False — TMDb-Fakt, kein Klassifikator-Ergebnis,
    # der Konfidenz-Filter wuerde nur Abdeckung kosten.
    genre_by_post = _genre_by_post(session, usable)
    genre_coverage = len([p for p in usable if p.id in genre_by_post]) / len(usable)
    if genre_coverage < 0.5:
        notes.append(
            f"Genre-Abdeckung liegt bei {genre_coverage:.0%} — Genres "
            f"kommen aus TMDb ueber die Titel-Zuordnung und fuellen sich "
            f"mit jedem Title-Sync-Lauf. Die Genre-Zellen beschreiben nur "
            f"diesen Ausschnitt."
        )
    genre_dimension = _Dimension("genre", lambda p: genre_by_post.get(p.id), False)

    # ---- Cross-Tabs -----------------------------------------------------
    dimensions: dict[str, list[PatternCell]] = {}
    for dim in DIMENSIONS + (genre_dimension,):
        # Bei eingegrenzter Auswertung waere die Formatklassen-Dimension
        # eine einzige Zelle mit z = 0 gegen sich selbst — kein Befund,
        # nur Rauschen in der Ausgabe.
        if dim.name == "format_class" and format_class is not None:
            continue
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

        cells = [
            _build_cell(
                value,
                cell_posts,
                lift_by_post=lift_by_post,
                activation_by_post=activation_by_post,
                platform_by_channel=platform_by_channel,
                platform_breakout_rates=platform_breakout_rates,
                baseline_breakout_rate=baseline_breakout_rate,
                min_sample=min_sample,
                min_channels=min_channels,
            )
            for value, cell_posts in buckets.items()
        ]
        _sort_cells(cells)
        dimensions[dim.name] = cells

    return TrailerPatternReport(
        window_days=window_days,
        window_start=window_start,
        window_end=window_end,
        market=market,
        posts_in_window=posts_in_window,
        posts_with_baseline=len(usable),
        channels_covered=len(baseline_by_channel),
        analysis_coverage=coverage,
        format_class=format_class,
        baseline_breakout_rate=baseline_breakout_rate,
        platform_breakout_rates=platform_breakout_rates,
        dimensions=dimensions,
        notes=notes,
    )
