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

Seit dem 26.08.2026 gilt dasselbe fuer den **Markt**: die Referenzquote
kommt je Post aus seinem (Plattform, Markt)-Stratum, mit Rueckfall auf
die Plattform-Quote und zuletzt die Korpus-Quote, wenn ein Stratum zu
duenn ist. Ein Merkmal, das nur deshalb gut aussieht, weil seine Posts
in einem Markt mit hoher Grundquote liegen (etwa US-lastig in einem
Korpus, in dem US-Kanaele generell oefter ausreissen), faellt damit
genauso durch wie ein rein plattform-getragenes. Zusaetzlich traegt
jede belastbare over/under-Zelle einen Klartext-Markt-Hinweis
(``market_note``), der sagt, welche Maerkte den Befund tragen — der
Bericht mischt DE/US/UK weiter bewusst (Einzelmarkt-Stichproben waeren
zu duenn, und US-Trends sind fuer DE ein Fruehindikator), aber der
Leser sieht, ob ein Befund auf seinen Markt uebertragbar ist.
(Tests: ``test_market_composition_alone_is_not_a_pattern`` u. a. in
``test_markt_korrektur.py``.)

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
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Market, Post, Title
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
# auf die Korpus-Quote zurueck. Dieselbe Schwelle gilt fuer die
# (Plattform, Markt)-Strata der Markt-Korrektur (26.08.2026): ein
# Stratum unter der Schwelle faellt auf seine Plattform-Quote zurueck,
# eine Plattform unter der Schwelle auf die Korpus-Quote.
MIN_POSTS_PER_PLATFORM_BASELINE = 30

# Anzeige-Reihenfolge der Maerkte im Markt-Hinweis einer Zelle —
# dieselbe Ordnung wie ueberall im Produkt (DE zuerst, dann US, UK).
_MARKT_REIHENFOLGE = ("DE", "US", "UK")

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

    # Posts je Markt (Markt-Korrektur 26.08.2026) — dieselbe Rolle wie
    # platform_mix: sichtbar machen, worauf der Erwartungswert steht.
    market_mix: dict[str, int] = field(default_factory=dict)
    # Klartext-Satz, welcher Markt den Befund traegt ("Gilt in DE und
    # US — fuer UK zu wenig Daten."). Nur bei belastbarem over/under und
    # nur, wenn die Zelle ueberhaupt mehrere Maerkte mischt.
    market_note: Optional[str] = None

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
            "market_mix": dict(sorted(self.market_mix.items())),
        }
        if self.market_note:
            out["market_note"] = self.market_note
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


# ---- Caption-Mechanik (Hook-Intelligence Teil 1, 20.08.2026) --------------
#
# Deterministische Merkmale aus dem Caption-Text — kein Klassifikator,
# kein LLM, deshalb rueckwirkend auf dem GESAMTEN Bestand verfuegbar
# (im Gegensatz zu format/tone, die eine Analyse brauchen). Das sind
# die Stellschrauben, die das Social-Team direkt aendern kann.
#
# Hygiene vor jeder Messung: URLs koennen '?' enthalten (Query-Strings)
# und Hashtag-Waende blaehen die Laenge auf, ohne Text zu sein — beides
# wird vor der jeweiligen Messung entfernt.

_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#\S+")

# Schwellen der Laengen-Buckets (Zeichen OHNE Hashtags/URLs).
CAPTION_SHORT_MAX_CHARS = 80
CAPTION_LONG_MIN_CHARS = 200

# Call-to-Action-Marker, DE + EN. Bewusst konservative, eindeutige
# Phrasen: ein uebersehener CTA landet in "ohne_cta" (Untertreibung),
# ein falsch erkannter wuerde die Zelle verfaelschen.
_CTA_MARKER: tuple[str, ...] = (
    "jetzt im kino", "nur im kino", "jetzt streamen", "jetzt ansehen",
    "tickets", "link in bio", "jetzt verfügbar", "sichert euch",
    "streamt jetzt", "ab jetzt",
    "now playing", "in theaters", "in theatres", "in cinemas",
    "watch now", "out now", "stream now", "get tickets", "on digital",
    "pre-order", "don't miss", "link in bio",
)


def _caption_text(post: Post) -> Optional[str]:
    """Caption ohne URLs, getrimmt — oder ``None``, wenn nach dem
    Aufraeumen nichts uebrig bleibt (kein Merkmal statt geratenem)."""
    caption = (post.caption or "").strip()
    if not caption:
        return None
    cleaned = _URL_RE.sub("", caption).strip()
    return cleaned or None


def _extract_caption_frage(post: Post) -> Optional[str]:
    text = _caption_text(post)
    if text is None:
        return None
    return "mit_frage" if "?" in text else "ohne_frage"


def _extract_caption_cta(post: Post) -> Optional[str]:
    text = _caption_text(post)
    if text is None:
        return None
    low = text.lower()
    return "mit_cta" if any(m in low for m in _CTA_MARKER) else "ohne_cta"


def _extract_caption_laenge(post: Post) -> Optional[str]:
    text = _caption_text(post)
    if text is None:
        return None
    kern = _HASHTAG_RE.sub("", text).strip()
    if not kern:
        # Nur-Hashtag-Captions: kein Text, dessen Laenge man messen
        # koennte — die Hashtag-Dimension sieht diese Posts trotzdem.
        return None
    n = len(kern)
    if n <= CAPTION_SHORT_MAX_CHARS:
        return "kurz"
    if n < CAPTION_LONG_MIN_CHARS:
        return "mittel"
    return "lang"


def _extract_caption_hashtags(post: Post) -> Optional[str]:
    text = _caption_text(post)
    if text is None:
        return None
    n = len(_HASHTAG_RE.findall(text))
    if n == 0:
        return "keine"
    if n <= 3:
        return "1-3"
    return "4+"


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


def _visual_by_post(session: Session, posts: list[Post]) -> dict[Any, Asset]:
    """Post → aeltestes Asset mit abgeschlossener Vision-Analyse.

    Hook-Intelligence Teil 2 (20.08.2026): die OpenAI-Vision-Stufe
    extrahiert ``has_title_placement`` / ``has_kinetic`` /
    ``kinetic_type`` laengst strukturiert je Asset — gelesen hat sie
    fuer die Muster-Aggregation bislang niemand. "Aeltestes zuerst"
    wie bei ``_title_by_post``: deterministische Wahl bei mehreren
    Assets je Post.
    """
    if not posts:
        return {}
    post_ids = [p.id for p in posts]
    assets = session.exec(
        select(Asset)
        .where(
            Asset.post_id.in_(post_ids),
            Asset.visual_analysis_status == "analyzed",
        )
        .order_by(Asset.created_at.asc())
    ).all()
    mapping: dict[Any, Asset] = {}
    for asset in assets:
        mapping.setdefault(asset.post_id, asset)
    return mapping


def _cover_dimensionen(
    session: Session, posts: list[Post]
) -> tuple[_Dimension, ...]:
    """Die Cover-Dimensionen aus der persistierten Vision-Analyse.

    Selbst-gegatet ueber ``visual_confidence_score`` >=
    ``CONFIDENCE_THRESHOLD`` — der ``requires_analysis``-Mechanismus
    greift hier nicht, denn er prueft die Konfidenz der POST-Analyse
    (Haiku/Sonnet), nicht die der Asset-Vision. Heuristik-Zeilen
    (Score ~0.35, kein echter Vision-Call) fallen damit heraus.

    Werte-Logik ``cover_kinetik``: ``has_kinetic=False`` ist eine echte
    Aussage ("ohne_kinetik"); ``has_kinetic=True`` ohne brauchbaren
    ``kinetic_type`` ist widerspruechlich → keine Zelle statt geraten.
    """
    visual = _visual_by_post(session, posts)

    def _asset_ok(post: Post) -> Optional[Asset]:
        asset = visual.get(post.id)
        if asset is None:
            return None
        score = asset.visual_confidence_score
        if score is None or score < CONFIDENCE_THRESHOLD:
            return None
        return asset

    def _titel(post: Post) -> Optional[str]:
        asset = _asset_ok(post)
        if asset is None:
            return None
        return "mit_titel" if asset.has_title_placement else "ohne_titel"

    def _kinetik(post: Post) -> Optional[str]:
        asset = _asset_ok(post)
        if asset is None:
            return None
        if not asset.has_kinetic:
            return "ohne_kinetik"
        kind = (asset.kinetic_type or "").strip()
        if kind in ("", "none", "unknown"):
            return None
        return kind

    return (
        _Dimension("cover_titel", _titel, False),
        _Dimension("cover_kinetik", _kinetik, False),
    )


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
    # Caption-Mechanik (Hook-Intelligence Teil 1): deterministisch aus
    # dem Text, deshalb ohne Konfidenz-Filter und auf dem ganzen Bestand.
    _Dimension("caption_frage", _extract_caption_frage, False),
    _Dimension("caption_cta", _extract_caption_cta, False),
    _Dimension("caption_laenge", _extract_caption_laenge, False),
    _Dimension("caption_hashtags", _extract_caption_hashtags, False),
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


def _stratum_reference_rate(
    stratum: tuple[str, str],
    stratum_rates: dict[tuple[str, str], float],
    platform_rates: dict[str, float],
    fallback: float,
) -> float:
    """Referenzquote fuer einen Post: sein (Plattform, Markt)-Stratum,
    sonst seine Plattform, sonst die Korpus-Quote. Jede Stufe greift
    nur, wenn die davor zu wenig Posts hatte
    (``MIN_POSTS_PER_PLATFORM_BASELINE``)."""
    rate = stratum_rates.get(stratum)
    if rate is not None:
        return rate
    return platform_rates.get(stratum[0], fallback)


def _expected_breakout_rate(
    strata: list[tuple[str, str]],
    stratum_rates: dict[tuple[str, str], float],
    platform_rates: dict[str, float],
    fallback: float,
) -> float:
    """Trefferquote, die eine Zelle allein wegen ihrer Plattform- und
    Markt-Mischung haette — ohne jeden inhaltlichen Effekt.

    Entspricht dem mit der Zellbesetzung gewichteten Mittel der
    Referenzquoten je (Plattform, Markt)-Stratum. Bis zum 26.08.2026
    ging nur die Plattform ein; seither korrigiert die Erwartung auch
    die Markt-Zusammensetzung — ein Merkmal, das nur deshalb gut
    aussieht, weil seine Posts in einem Markt mit hoher Grundquote
    liegen, faellt damit genauso durch wie vorher ein rein
    plattform-getragenes (Test:
    ``test_market_composition_alone_is_not_a_pattern``).
    """
    if not strata:
        return fallback
    total = sum(
        _stratum_reference_rate(s, stratum_rates, platform_rates, fallback)
        for s in strata
    )
    return total / len(strata)


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
    market_by_channel: Optional[dict[Any, str]] = None,
) -> tuple[float, dict[str, float], dict[tuple[str, str], float]]:
    """Korpus-, Plattform- und Stratum-Trefferquoten — die Referenz,
    gegen die jede Zelle geprueft wird. Extrahiert (20.08.2026), damit
    der Titel-Modus des Pattern-Briefings (``compute_cells_for_mapping``)
    exakt dieselben Quoten rechnet wie der Muster-Bericht.

    Seit der Markt-Korrektur (26.08.2026) kommt als dritte Ebene die
    Quote je (Plattform, Markt)-Stratum dazu; Strata unter
    ``MIN_POSTS_PER_PLATFORM_BASELINE`` fehlen in der Karte und fallen
    beim Nachschlagen auf die Plattform-Quote zurueck. ``market_by_channel``
    ist optional, damit Aufrufer ohne Markt-Wissen (alte Signatur)
    weiter funktionieren — dann bleibt die Stratum-Karte leer und die
    Erwartung entspricht der reinen Plattform-Korrektur."""
    market_by_channel = market_by_channel or {}
    breakouts_total = sum(
        1 for p in usable if lift_by_post[p.id] >= BREAKOUT_LIFT_THRESHOLD
    )
    baseline_breakout_rate = breakouts_total / len(usable)

    posts_by_platform: dict[str, list[Post]] = defaultdict(list)
    posts_by_stratum: dict[tuple[str, str], list[Post]] = defaultdict(list)
    for p in usable:
        platform = platform_by_channel.get(p.channel_id, "unknown")
        posts_by_platform[platform].append(p)
        if market_by_channel:
            market = market_by_channel.get(p.channel_id, "UNKNOWN")
            posts_by_stratum[(platform, market)].append(p)

    def _rate(posts: list[Post]) -> float:
        hits = sum(
            1 for p in posts if lift_by_post[p.id] >= BREAKOUT_LIFT_THRESHOLD
        )
        return hits / len(posts)

    platform_breakout_rates: dict[str, float] = {
        platform: _rate(platform_posts)
        for platform, platform_posts in posts_by_platform.items()
        if len(platform_posts) >= MIN_POSTS_PER_PLATFORM_BASELINE
    }
    stratum_breakout_rates: dict[tuple[str, str], float] = {
        stratum: _rate(stratum_posts)
        for stratum, stratum_posts in posts_by_stratum.items()
        if len(stratum_posts) >= MIN_POSTS_PER_PLATFORM_BASELINE
    }
    return baseline_breakout_rate, platform_breakout_rates, stratum_breakout_rates


def _markt_liste(maerkte: list[str]) -> str:
    """"DE", "DE und US", "DE, US und UK" — Anzeige-Reihenfolge DE/US/UK."""
    geordnet = sorted(
        maerkte,
        key=lambda m: (
            _MARKT_REIHENFOLGE.index(m) if m in _MARKT_REIHENFOLGE else 99,
            m,
        ),
    )
    if len(geordnet) == 1:
        return geordnet[0]
    return ", ".join(geordnet[:-1]) + " und " + geordnet[-1]


def _markt_hinweis(
    cell_posts: list[Post],
    richtung: str,
    *,
    lift_by_post: dict[Any, float],
    platform_by_channel: dict[Any, str],
    market_by_channel: dict[Any, str],
    stratum_breakout_rates: dict[tuple[str, str], float],
    platform_breakout_rates: dict[str, float],
    baseline_breakout_rate: float,
) -> Optional[str]:
    """Klartext-Satz, welche Maerkte einen belastbaren Befund tragen.

    Der Bericht mischt DE, US und UK bewusst (Stichproben je Einzelmarkt
    waeren zu duenn, und US-Trends sind fuer DE ein Fruehindikator) —
    aber der Leser muss sehen, ob ein Befund auf seinen Markt
    uebertragbar ist. Je Markt mit mindestens ``MIN_SAMPLE_PER_CELL``
    Posts in der Zelle wird die Markt-Trefferquote gegen die erwartete
    Quote derselben Posts gehalten: zeigt sie in die Richtung des
    Befunds, "gilt" er dort. Kein z-Test je Markt — dafuer sind die
    Teilmengen zu klein; der Satz ist ein Richtungs-Ausweis, kein
    eigener Signifikanznachweis.

    ``None``, wenn die Zelle nur einen Markt enthaelt (nichts zu
    unterscheiden) oder die Richtung nicht over/under ist.
    """
    if richtung not in ("over", "under"):
        return None
    by_market: dict[str, list[Post]] = defaultdict(list)
    for p in cell_posts:
        by_market[market_by_channel.get(p.channel_id, "UNKNOWN")].append(p)
    if len(by_market) < 2:
        return None

    gilt: list[str] = []
    gegen: list[str] = []
    duenn: list[str] = []
    for market, markt_posts in by_market.items():
        if len(markt_posts) < MIN_SAMPLE_PER_CELL:
            duenn.append(market)
            continue
        rate = sum(
            1 for p in markt_posts
            if lift_by_post[p.id] >= BREAKOUT_LIFT_THRESHOLD
        ) / len(markt_posts)
        strata = [
            (platform_by_channel.get(p.channel_id, "unknown"), market)
            for p in markt_posts
        ]
        erwartet = _expected_breakout_rate(
            strata, stratum_breakout_rates, platform_breakout_rates,
            baseline_breakout_rate,
        )
        stuetzt = rate > erwartet if richtung == "over" else rate < erwartet
        (gilt if stuetzt else gegen).append(market)

    if not gilt:
        if gegen:
            return (
                "In keinem Markt für sich belegbar — der Befund entsteht "
                "erst über alle Märkte zusammen."
            )
        return "Kein einzelner Markt hat genug Posts für eine eigene Aussage."

    teile: list[str] = []
    if len(gilt) == 1 and (gegen or duenn):
        teile.append(f"Gilt vor allem in {gilt[0]}")
    else:
        teile.append(f"Gilt in {_markt_liste(gilt)}")
    if gegen:
        teile.append(f"in {_markt_liste(gegen)} zeigt sich das nicht")
    if duenn:
        teile.append(f"für {_markt_liste(duenn)} zu wenig Daten")
    return " — ".join(teile) + "."


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
    market_by_channel: Optional[dict[Any, str]] = None,
    stratum_breakout_rates: Optional[dict[tuple[str, str], float]] = None,
) -> PatternCell:
    """Eine Zelle aus einem Bucket — Median-Lift, Trefferquote gegen die
    eigene Plattform-Mischung, Mindest-Stichprobe/-Kanalzahl. Extrahiert
    aus der Cross-Tab-Schleife (20.08.2026), unveraendert: der
    Titel-Modus soll dieselben Ehrlichkeits-Regeln durchlaufen wie jede
    Berichts-Dimension."""
    market_by_channel = market_by_channel or {}
    stratum_breakout_rates = stratum_breakout_rates or {}
    channel_count = len({p.channel_id for p in cell_posts})
    lifts = [lift_by_post[p.id] for p in cell_posts]
    activations = [activation_by_post[p.id] for p in cell_posts]
    views = [int(p.visible_views) for p in cell_posts if p.visible_views]
    median_lift = _median(lifts)

    breakout_hits = sum(1 for x in lifts if x >= BREAKOUT_LIFT_THRESHOLD)
    breakout_rate = breakout_hits / len(lifts)

    cell_strata = [
        (
            platform_by_channel.get(p.channel_id, "unknown"),
            market_by_channel.get(p.channel_id, "UNKNOWN"),
        )
        for p in cell_posts
    ]
    platform_mix: dict[str, int] = defaultdict(int)
    market_mix: dict[str, int] = defaultdict(int)
    for pl, mk in cell_strata:
        platform_mix[pl] += 1
        market_mix[mk] += 1
    expected_rate = _expected_breakout_rate(
        cell_strata, stratum_breakout_rates, platform_breakout_rates,
        baseline_breakout_rate,
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

    market_note = None
    if market_by_channel and breakout_verdict in ("over", "under"):
        market_note = _markt_hinweis(
            cell_posts,
            breakout_verdict,
            lift_by_post=lift_by_post,
            platform_by_channel=platform_by_channel,
            market_by_channel=market_by_channel,
            stratum_breakout_rates=stratum_breakout_rates,
            platform_breakout_rates=platform_breakout_rates,
            baseline_breakout_rate=baseline_breakout_rate,
        )

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
        market_mix=dict(market_mix),
        market_note=market_note,
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
    market_by_channel: dict[Any, str] = field(default_factory=dict)
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
    # Markt je Kanal fuer die Markt-Korrektur (26.08.2026). ``.value``,
    # weil Market ein str-Enum ist — als Dict-Schluessel und in der
    # Ausgabe soll der nackte Code ("DE") stehen, nicht das Enum.
    market_by_channel = {
        c.id: (c.market.value if c.market else Market.UNKNOWN.value)
        for c in channels
    }
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
            market_by_channel=market_by_channel,
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
            market_by_channel=market_by_channel,
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
        market_by_channel=market_by_channel,
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
    baseline_breakout_rate, platform_breakout_rates, stratum_breakout_rates = (
        _corpus_breakout_rates(
            ctx.usable,
            ctx.lift_by_post,
            ctx.platform_by_channel,
            ctx.market_by_channel,
        )
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
            market_by_channel=ctx.market_by_channel,
            stratum_breakout_rates=stratum_breakout_rates,
        )
        for value, cell_posts in buckets.items()
    ]
    _sort_cells(cells)
    return cells


def _mehrfachvergleichs_note(tested_cells: int) -> Optional[str]:
    """Mehrfachvergleichs-Ehrlichkeit (Hook-Intelligence Teil 1): mit den
    Caption-Dimensionen prueft der Bericht deutlich mehr Zellen gegen
    dieselbe z-Schwelle. Bei N Tests und |z| >= 2 sind ~4,6 % zufaellige
    Treffer zu erwarten — das gehoert als Zahl in den Bericht, sonst
    liest jede over/under-Zelle sich wie ein Beweis. Unter 20 Zellen
    bleibt die Note weg (die Erwartung laege unter einem Treffer)."""
    if tested_cells < 20:
        return None
    erwartet = max(1, round(tested_cells * 0.046))
    treffer = (
        "einem zufaelligen Scheinbefund"
        if erwartet == 1
        else f"{erwartet} zufaelligen Scheinbefunden"
    )
    return (
        f"{tested_cells} Zellen gegen die Schwelle |z| >= "
        f"{BREAKOUT_Z_THRESHOLD:g} geprueft — bei dieser Schwelle ist im "
        f"Schnitt mit {treffer} zu rechnen. Einzelne Befunde sind "
        f"Hinweise, keine Beweise; Bestand hat, was ueber Wochen "
        f"wiederkehrt."
    )


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
    elif dimension in ("cover_titel", "cover_kinetik"):
        cover = {d.name: d for d in _cover_dimensionen(session, ctx.usable)}
        dim = cover[dimension]
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


def facetten_werte_je_post(
    session: Session,
    posts: list[Post],
) -> dict[str, dict[Any, str]]:
    """Alle Berichts-Dimensionen als Post→Wert-Karten in einem Durchgang.

    Fuer die Referenz-Suche (Roadmap Schritt 1, 25.08.2026): eine
    Facetten-Suche braucht JEDE Dimension gleichzeitig — Filter und
    Facetten-Zaehlung. ``posts_for_cell`` je (Dimension, Wert) zu rufen
    wuerde die Genre-/Cover-Mappings pro Aufruf neu aus der DB bauen.

    Die Zugehoerigkeits-Regeln sind exakt die der Cross-Tab-Schleife
    und von ``posts_for_cell``: Konfidenz-Filter nur auf modell-
    erzeugten Dimensionen, Genre ueber das Titel-Mapping, Cover-
    Dimensionen selbst-gegatet ueber ``visual_confidence_score``. Ein
    Post ohne Wert fehlt in der jeweiligen Karte — genau wie er in der
    Zelle fehlen wuerde.
    """
    karten: dict[str, dict[Any, str]] = {"genre": dict(_genre_by_post(session, posts))}
    for dim in (*DIMENSIONS, *_cover_dimensionen(session, posts)):
        karte: dict[Any, str] = {}
        for p in posts:
            if dim.requires_analysis:
                conf = _post_confidence(p)
                if conf is None or conf < CONFIDENCE_THRESHOLD:
                    continue
            value = dim.extract(p)
            if value is not None:
                karte[p.id] = value
        karten[dim.name] = karte
    return karten


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
    baseline_breakout_rate, platform_breakout_rates, stratum_breakout_rates = (
        _corpus_breakout_rates(
            usable, lift_by_post, platform_by_channel, ctx.market_by_channel
        )
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

    # ---- Cover-Dimensionen (Hook-Intelligence Teil 2, 20.08.2026) --------
    #
    # Aus der persistierten OpenAI-Vision-Analyse — kein neuer LLM-Call,
    # rueckwirkend auf allen analysierten Assets. Abdeckung waechst mit
    # jedem Cron-Lauf (Vision-Stage arbeitet den Backlog ab).
    cover_dims = _cover_dimensionen(session, usable)
    cover_titel_dim = cover_dims[0]
    cover_coverage = (
        len([p for p in usable if cover_titel_dim.extract(p) is not None])
        / len(usable)
    )
    if cover_coverage < 0.5:
        notes.append(
            f"Cover-Merkmale (Vision) decken {cover_coverage:.0%} der Posts "
            f"— die Vision-Analyse arbeitet den Bestand mit jedem Cron-Lauf "
            f"weiter ab. Die Cover-Zellen beschreiben nur diesen Ausschnitt."
        )

    # ---- Cross-Tabs -----------------------------------------------------
    dimensions: dict[str, list[PatternCell]] = {}
    for dim in DIMENSIONS + (genre_dimension,) + cover_dims:
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
                market_by_channel=ctx.market_by_channel,
                stratum_breakout_rates=stratum_breakout_rates,
            )
            for value, cell_posts in buckets.items()
        ]
        _sort_cells(cells)
        dimensions[dim.name] = cells

    # Mehrfachvergleichs-Ehrlichkeit (Hook-Intelligence Teil 1): mit den
    # Caption-Dimensionen prueft der Bericht deutlich mehr Zellen gegen
    # dieselbe z-Schwelle. Bei N unabhaengigen Tests und Schwelle z>=2
    # sind ~4,6 % Zufallstreffer zu erwarten — das gehoert als Zahl in
    # den Bericht, sonst liest jede over/under-Zelle sich wie ein Beweis.
    tested_cells = sum(
        1
        for cells in dimensions.values()
        for c in cells
        if c.breakout_verdict != "insufficient"
    )
    note = _mehrfachvergleichs_note(tested_cells)
    if note:
        notes.append(note)

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
