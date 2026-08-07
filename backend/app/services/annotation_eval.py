"""Trailer-Intelligence Stufe 5, Plan B — vom Tippen zur Shot-Liste.

Der Tap-Along-Weg (``TRAILER_INTELLIGENCE_STUFE5_PLAN_B.md``, Abschnitt
2): ein Mensch schaut ein Video im nutzungsbedingungskonformen Player
und tippt bei jedem Schnitt. Dieses Modul macht daraus belastbare
Zahlen — und zwar in genau zwei Schritten, die getrennt bleiben:

1. **Uebersetzen** (``taps_to_shots``, ``annotation_to_features``):
   getippte Schnittzeitpunkte werden zu einer Shot-Liste, die
   ``extract_features`` versteht. Keine Interpretation, nur Form.
2. **Kalibrieren** (``evaluate_against_truth``): eine getippte Liste
   wird gegen eine *bekannte* Schnittliste gehalten (synthetische
   Clips aus ``scripts/make_calibration_clips.py``). Heraus kommen
   Latenz, Jitter, Trefferquote und ASL-Fehler der annotierenden
   Person — Messwerte statt Annahmen.

Warum die Kalibrierung nicht optional ist: Plan B stuetzt sich auf
zwei Eigenschaften des Tippens — konstante Latenz verschiebt alle
Grenzen gleich und laesst die Einstellungslaengen unveraendert, und
verpasste Schnitte stauchen Unterschiede, statt sie aufzublaehen.
Beides sind Behauptungen ueber die tippende Person, und
``evaluate_against_truth`` ist der Ort, an dem sie fuer diese Person
belegt oder widerlegt werden, bevor ihre Annotationen in einen
Vergleich einfliessen.

Zeitbasis: Der Annotator liest ``getCurrentTime()`` bzw.
``video.currentTime`` — das ist **Medienzeit**, keine Wanduhrzeit. Bei
halber Abspielgeschwindigkeit muss deshalb nichts zurueckgerechnet
werden; die Reaktionslatenz halbiert sich in Medienzeit sogar. Die
Abspielrate wird trotzdem gespeichert, damit Kalibrierung und
Annotation bei derselben Rate verglichen werden koennen.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from statistics import median, pstdev
from typing import Any, Optional, Sequence

from app.services.video_features import VideoFeatures, extract_features

logger = logging.getLogger(__name__)


ANNOTATION_SCHEMA = "tap-annotation/1"
TRUTH_SCHEMA = "tap-truth/1"

# Unterhalb dieses Abstands gelten zwei Taps als Doppelausloesung
# derselben Schnittwahrnehmung (Tastenprellen, Doppel-Tipp) und werden
# zu einem zusammengefasst. Bewusst unter jeder realen ASL — selbst
# extrem schnell geschnittene Passagen liegen ueber 0,2 s.
MIN_SHOT_SECONDS = 0.2

# Fenster, in dem ein Tap einem wahren Schnitt zugeordnet wird.
# Menschliche Reaktionszeit auf einen visuellen Reiz liegt um 0,2-0,3 s
# (Wanduhr); bei halber Abspielgeschwindigkeit entspricht das
# 0,1-0,15 s Medienzeit. 0,75 s laesst reichlich Rand, ohne dass bei
# ASL >= 1,5 s Nachbar-Schnitte ins Fenster ruecken.
DEFAULT_MATCH_TOLERANCE_SECONDS = 0.75


class AnnotationError(ValueError):
    """Das Annotations- oder Wahrheits-JSON ist nicht auswertbar.

    Hart statt still repariert — dieselbe Linie wie ``ShotListError``
    im Fundament: eine kaputte Eingabe deutet auf einen Fehler im
    erzeugenden Werkzeug oder Ablauf hin, und den zu verschleiern
    macht die Zahlen unbrauchbar, ohne dass es jemand merkt.
    """


def taps_to_shots(
    taps: Sequence[float],
    *,
    duration_seconds: float,
    min_shot_seconds: float = MIN_SHOT_SECONDS,
) -> list[tuple[float, float]]:
    """Getippte Schnittzeitpunkte -> Shot-Liste fuer ``extract_features``.

    Taps sind **innere Grenzen**: n Taps ergeben n+1 Einstellungen,
    Anfang und Ende des Videos sind implizit. Bereinigt wird nur, was
    physikalisch keine zweite Wahrnehmung sein kann (Doppelausloesungen
    unter ``min_shot_seconds`` Abstand, Taps ausserhalb der Laufzeit).
    Alles andere bleibt, wie getippt — Interpretation findet hier nicht
    statt.
    """
    if duration_seconds <= 0:
        raise AnnotationError(
            f"Laufzeit {duration_seconds} ist nicht positiv — ohne sie gibt "
            f"es keine Shot-Liste."
        )
    cleaned: list[float] = []
    for t in sorted(taps):
        if t < min_shot_seconds or t > duration_seconds - min_shot_seconds:
            # Tap praktisch auf Video-Anfang/-Ende: keine innere Grenze.
            continue
        if cleaned and t - cleaned[-1] < min_shot_seconds:
            continue  # Doppelauslösung
        cleaned.append(t)

    boundaries = [0.0, *cleaned, float(duration_seconds)]
    return [
        (boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)
    ]


@dataclass
class CalibrationReport:
    """Messwerte einer Person an Clips mit bekannter Schnittliste."""

    n_true_cuts: int
    n_taps: int
    n_matched: int
    recall: float  # Anteil wahrer Schnitte, die getippt wurden
    precision: float  # Anteil Taps, die einem wahren Schnitt entsprechen
    latency_mean_seconds: Optional[float]  # positiv = zu spaet getippt
    latency_std_seconds: Optional[float]  # der eigentliche Stoerfaktor
    asl_true_seconds: float
    asl_tapped_seconds: float
    tolerance_seconds: float
    notes: list[str] = field(default_factory=list)

    @property
    def asl_error_ratio(self) -> float:
        """Getippte ASL relativ zur wahren — 1,0 waere perfekt.

        Werte ueber 1 heissen: Schnitte verpasst, Einstellungen wirken
        laenger als sie sind. Genau die konservative Richtung, auf die
        sich Plan B stuetzt.
        """
        if self.asl_true_seconds <= 0:
            return float("nan")
        return self.asl_tapped_seconds / self.asl_true_seconds

    def to_dict(self) -> dict:
        out = {
            "n_true_cuts": self.n_true_cuts,
            "n_taps": self.n_taps,
            "n_matched": self.n_matched,
            "recall": round(self.recall, 3),
            "precision": round(self.precision, 3),
            "latency_mean_seconds": (
                round(self.latency_mean_seconds, 3)
                if self.latency_mean_seconds is not None
                else None
            ),
            "latency_std_seconds": (
                round(self.latency_std_seconds, 3)
                if self.latency_std_seconds is not None
                else None
            ),
            "asl_true_seconds": round(self.asl_true_seconds, 3),
            "asl_tapped_seconds": round(self.asl_tapped_seconds, 3),
            "asl_error_ratio": round(self.asl_error_ratio, 3),
            "tolerance_seconds": self.tolerance_seconds,
        }
        if self.notes:
            out["notes"] = self.notes
        return out


def evaluate_against_truth(
    true_cuts: Sequence[float],
    taps: Sequence[float],
    *,
    duration_seconds: float,
    tolerance_seconds: float = DEFAULT_MATCH_TOLERANCE_SECONDS,
) -> CalibrationReport:
    """Haelt getippte Schnitte gegen eine bekannte Schnittliste.

    Zuordnung eins-zu-eins, global nach kleinstem Zeitabstand innerhalb
    der Toleranz — kein Tap belegt zwei Schnitte, kein Schnitt zwei
    Taps. Die Latenz ist ``tap - wahr`` ueber die zugeordneten Paare;
    ihr Mittel ist die (harmlose) systematische Verspaetung, ihre
    Streuung der eigentliche Stoerfaktor.
    """
    if duration_seconds <= 0:
        raise AnnotationError("Laufzeit muss positiv sein.")
    truth = sorted(float(t) for t in true_cuts)
    tapped = sorted(float(t) for t in taps)

    candidates = [
        (abs(tap - cut), i, j)
        for i, cut in enumerate(truth)
        for j, tap in enumerate(tapped)
        if abs(tap - cut) <= tolerance_seconds
    ]
    candidates.sort()
    used_truth: set[int] = set()
    used_taps: set[int] = set()
    offsets: list[float] = []
    for _, i, j in candidates:
        if i in used_truth or j in used_taps:
            continue
        used_truth.add(i)
        used_taps.add(j)
        offsets.append(tapped[j] - truth[i])

    n_matched = len(offsets)
    notes: list[str] = []
    if not truth:
        notes.append("Wahrheitsliste ohne Schnitte — nur Praezision messbar.")

    tapped_shots = taps_to_shots(tapped, duration_seconds=duration_seconds)
    return CalibrationReport(
        n_true_cuts=len(truth),
        n_taps=len(tapped),
        n_matched=n_matched,
        recall=(n_matched / len(truth)) if truth else 1.0,
        precision=(n_matched / len(tapped)) if tapped else 1.0,
        latency_mean_seconds=(
            sum(offsets) / n_matched if n_matched else None
        ),
        latency_std_seconds=(pstdev(offsets) if n_matched >= 2 else None),
        asl_true_seconds=duration_seconds / (len(truth) + 1),
        asl_tapped_seconds=duration_seconds / len(tapped_shots),
        tolerance_seconds=tolerance_seconds,
        notes=notes,
    )


# ---------- Annotations-JSON aus dem Tap-Along-Annotator ----------


@dataclass(frozen=True)
class Annotation:
    """Eine exportierte Annotation, geparst und validiert."""

    video: str
    format_class: str  # "langform" | "kurzform"
    pair_key: str
    duration_seconds: float
    taps: tuple[float, ...]
    playback_rate: Optional[float] = None
    music_entry: Optional[float] = None
    title_cards: tuple[float, ...] = ()
    first_word: Optional[float] = None
    annotator: Optional[str] = None
    is_trailer: bool = True
    is_cutdown: bool = True


def parse_annotation(data: dict[str, Any]) -> Annotation:
    """Validiert das Export-JSON des Annotators. Hart bei Pflichtfeldern."""
    schema = data.get("schema")
    if schema != ANNOTATION_SCHEMA:
        raise AnnotationError(
            f"Unbekanntes Schema {schema!r} (erwartet {ANNOTATION_SCHEMA!r})."
        )
    missing = [
        k
        for k in ("video", "format_class", "pair_key", "duration_seconds")
        if not data.get(k)
    ]
    if missing:
        raise AnnotationError(f"Pflichtfelder fehlen: {missing}.")
    format_class = str(data["format_class"])
    if format_class not in ("langform", "kurzform"):
        raise AnnotationError(
            f"format_class {format_class!r} — erwartet 'langform' oder "
            f"'kurzform'."
        )
    flags = data.get("flags") or {}
    return Annotation(
        video=str(data["video"]),
        format_class=format_class,
        pair_key=str(data["pair_key"]),
        duration_seconds=float(data["duration_seconds"]),
        taps=tuple(float(t) for t in (data.get("taps") or [])),
        playback_rate=(
            float(data["playback_rate"])
            if data.get("playback_rate") is not None
            else None
        ),
        music_entry=(
            float(data["music_entry"])
            if data.get("music_entry") is not None
            else None
        ),
        title_cards=tuple(float(t) for t in (data.get("title_cards") or [])),
        first_word=(
            float(data["first_word"])
            if data.get("first_word") is not None
            else None
        ),
        annotator=data.get("annotator") or None,
        is_trailer=bool(flags.get("ist_trailer", True)),
        is_cutdown=bool(flags.get("ist_cutdown", True)),
    )


def annotation_to_features(ann: Annotation) -> VideoFeatures:
    """Annotation -> ``VideoFeatures``, inkl. Musikeinsatz.

    ``music_entry_position`` wird hier gesetzt und nirgendwo sonst:
    nur eine menschliche Annotation darf behaupten, dass Musik
    einsetzt — ``extract_features`` laesst das Feld grundsaetzlich
    leer.
    """
    shots = taps_to_shots(ann.taps, duration_seconds=ann.duration_seconds)
    feats = extract_features(shots, duration_seconds=ann.duration_seconds)
    if ann.music_entry is not None and 0 <= ann.music_entry <= ann.duration_seconds:
        feats = dataclasses.replace(
            feats,
            music_entry_position=ann.music_entry / ann.duration_seconds,
        )
    return feats


@dataclass
class PairBuildResult:
    """Ergebnis der Paar-Bildung aus einem Satz Annotationen.

    ``cutdown_pairs`` sind die Kern-Paare (Kurzform erkennbar aus dem
    Langform-Material geschnitten), ``other_pairs`` eigenstaendige
    Kurzformate — getrennt gehalten, weil beides in einen Topf zu
    werfen der naechste selbstgebaute Confound waere (Plan B,
    Abschnitt 2.4).
    """

    cutdown_pairs: list[tuple[VideoFeatures, VideoFeatures]]
    other_pairs: list[tuple[VideoFeatures, VideoFeatures]]
    skipped: list[str]


def build_pairs(annotations: Sequence[Annotation]) -> PairBuildResult:
    """Gruppiert Annotationen nach ``pair_key`` zu (Langform, Kurzform).

    Nicht-Trailer fliegen ganz heraus (ein Featurette ist keine
    Langform im Sinne der Frage). Mehrfache Annotationen derselben
    Seite eines Paars sind ein Ablauffehler und werden benannt, nicht
    stillschweigend gemittelt.
    """
    by_key: dict[str, dict[str, list[Annotation]]] = {}
    skipped: list[str] = []
    for ann in annotations:
        if not ann.is_trailer:
            skipped.append(
                f"{ann.pair_key}/{ann.format_class}: als Nicht-Trailer "
                f"markiert — ausgeschlossen."
            )
            continue
        by_key.setdefault(ann.pair_key, {}).setdefault(
            ann.format_class, []
        ).append(ann)

    cutdown_pairs: list[tuple[VideoFeatures, VideoFeatures]] = []
    other_pairs: list[tuple[VideoFeatures, VideoFeatures]] = []
    for key in sorted(by_key):
        sides = by_key[key]
        lang = sides.get("langform", [])
        kurz = sides.get("kurzform", [])
        if len(lang) != 1 or len(kurz) != 1:
            skipped.append(
                f"{key}: {len(lang)} Langform- und {len(kurz)} "
                f"Kurzform-Annotationen — erwartet je genau eine."
            )
            continue
        pair = (
            annotation_to_features(lang[0]),
            annotation_to_features(kurz[0]),
        )
        if kurz[0].is_cutdown:
            cutdown_pairs.append(pair)
        else:
            other_pairs.append(pair)
    return PairBuildResult(
        cutdown_pairs=cutdown_pairs, other_pairs=other_pairs, skipped=skipped
    )


def parse_truth(data: dict[str, Any]) -> tuple[list[float], float]:
    """Wahrheits-JSON eines Kalibrier-Clips -> (Schnitte, Laufzeit)."""
    if data.get("schema") != TRUTH_SCHEMA:
        raise AnnotationError(
            f"Unbekanntes Schema {data.get('schema')!r} "
            f"(erwartet {TRUTH_SCHEMA!r})."
        )
    duration = float(data.get("duration_seconds") or 0)
    if duration <= 0:
        raise AnnotationError("Wahrheits-JSON ohne positive Laufzeit.")
    cuts = [float(t) for t in (data.get("cuts") or [])]
    return sorted(cuts), duration


def summarize_pair_medians(
    pairs: Sequence[tuple[VideoFeatures, VideoFeatures]],
    feature: str,
) -> tuple[Optional[float], Optional[float]]:
    """Mediane (Langform, Kurzform) eines Merkmals ueber die Paare."""
    lang = [
        getattr(a, feature)
        for a, _ in pairs
        if getattr(a, feature) is not None
    ]
    kurz = [
        getattr(b, feature)
        for _, b in pairs
        if getattr(b, feature) is not None
    ]
    return (median(lang) if lang else None, median(kurz) if kurz else None)
