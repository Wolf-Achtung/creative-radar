"""Trailer-Intelligence Stufe 5 — Schnitt-Merkmale und ihr Vergleich.

Stufe 3 hat den Suchraum festgelegt: *Was unterscheidet ein
YouTube-Langformat ab 90 Sekunden handwerklich von einem Cutdown unter
60 — bei gleichem Kanal, gleichem Titel und gleicher Produktionsroutine?*

Dieses Modul rechnet aus einer **Shot-Liste** die Merkmale, mit denen
sich diese Frage beantworten laesst, und liefert den Test, der zwei
Kohorten dagegen vergleicht.

Was hier bewusst NICHT steht
============================

Kein Download, kein Netzwerkzugriff, kein Verweis auf eine Quelle. Die
Vorstufe (``TRAILER_INTELLIGENCE_STUFE5_VIDEOQUELLEN.md``, Abschnitt 5)
hat den Engpass benannt: das Herunterladen von YouTube-Videos verstoesst
gegen die Nutzungsbedingungen, und woher das Material rechtlich sauber
kommt, ist eine Entscheidung und keine Code-Frage. Solange sie offen
ist, waere eine Download-Pipeline die falsche Reihenfolge.

Die Eingabe ist deshalb eine fertige Shot-Liste — Paare aus Start- und
Endzeit in Sekunden. Wer sie erzeugt (PySceneDetect auf eigenem
Material, ein Schnittsystem-Export, eine EDL), ist diesem Modul
gleichgueltig. Damit ist alles, was hier steht, sofort nutzbar, sobald
die erste Datei vorliegt, und bis dahin vollstaendig testbar.

Die Falle, um die es hier eigentlich geht
=========================================

Dieses Projekt hat drei Confounds hintereinander gehabt: die
Plattform-Mischung (Stufe 1), die Posts ohne Views (Stufe 1) und den
verkleideten Plattform-Vergleich beim Dauer-Bucket (Stufe 1 erneut).
Jedes Mal war die Ursache dieselbe — eine Kennzahl, die etwas anderes
mass, als ihr Name behauptete.

Hier droht derselbe Fehler ein viertes Mal, und zwar besonders
verfuehrerisch: **fast jede naheliegende Schnitt-Kennzahl haengt an der
Laufzeit.** Ein Zwei-Minuten-Trailer hat mehr Einstellungen als ein
30-Sekunden-Cutdown — das ist keine Erkenntnis ueber Handwerk, sondern
die Definition von laenger. Wer Langform und Kurzform auf
``shot_count`` vergleicht, misst die Dauer und nennt es Rhythmus.

Deshalb trennt dieses Modul strikt:

- **Skalenfreie Merkmale** (``SCALE_FREE_FEATURES``) sind Verhaeltnisse
  oder relative Positionen. Sie sind zwischen Formaten verschiedener
  Laenge vergleichbar. Nur sie gehoeren in einen Kohortenvergleich.
- **Laufzeitabhaengige Merkmale** sind als solche gekennzeichnet. Sie
  gehoeren in den Bericht, weil sie den Fall beschreiben, aber nicht in
  den Test.

``compare_cohorts`` weigert sich, ein laufzeitabhaengiges Merkmal zu
vergleichen. Das ist die Lehre aus drei Korrekturrunden, in Code
gegossen statt in einen Kommentar.

Ausnahme mit Ansage: ``asl_seconds``
------------------------------------

Die mittlere Einstellungslaenge ist eine Rate (Laufzeit geteilt durch
Anzahl) und damit rechnerisch skalenfrei — zwei Filme verschiedener
Laenge koennen dieselbe ASL haben. Sie ist zugleich die kanonische
Kennzahl der Schnittforschung. Sie zaehlt deshalb als vergleichbar.

Dass Cutdowns typischerweise schneller geschnitten sind, ist dabei
**kein Artefakt, sondern die Sache selbst** — es ist eine Entscheidung
von Cutterinnen und Cuttern, keine Folge der Laufzeit.

Warum der gepaarte Test der wichtigere ist
==========================================

Die Vorstufe empfiehlt fuer den Machbarkeitsnachweis eigenes Material
des Trailerhauses: derselbe Film in beiden Laengen, gleiches Team,
gleiche Kampagne. Diese Paarung ist statistisch Gold — sie haelt Titel,
Genre, Budget und Handschrift konstant und laesst nur die Laenge
variieren.

``compare_paired`` (Wilcoxon-Vorzeichen-Rangtest) nutzt das aus und
braucht dafuer rund ein Viertel der Stichprobe, die ein ungepaarter
Vergleich braeuchte. ``compare_unpaired`` (Mann-Whitney-U) existiert
fuer den Fall, dass nur unabhaengige Kohorten vorliegen.

Beide Tests sind rangbasiert und setzen keine Normalverteilung voraus —
bei zwanzig Werten waere jede Verteilungsannahme ohnehin nicht
pruefbar.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from statistics import median, pstdev
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


# Unter dieser Zahl an Einstellungen ist eine Drittel-Aufteilung nicht
# sinnvoll — bei fuenf Shots hat ein Drittel ein oder zwei davon, und
# der "Rhythmus" waere ein Einzelwert mit Etikett.
MIN_SHOTS_FOR_THIRDS = 9

# Der Wilcoxon-Test benutzt eine Normalapproximation. Unterhalb dieser
# Paarzahl ist sie nicht vertretbar; exakte Tabellen waeren noetig.
# Auch darueber gilt: bis rund zwanzig Paaren ist der p-Wert als
# Hinweis zu lesen, nicht als Beleg.
MIN_PAIRS_FOR_TEST = 10
MIN_SAMPLES_PER_ARM = 10

Z_THRESHOLD = 2.0


# Merkmale, die zwischen Formaten verschiedener Laenge vergleichbar
# sind. Alles andere ist laufzeitabhaengig und im Test gesperrt.
SCALE_FREE_FEATURES: frozenset[str] = frozenset(
    {
        "asl_seconds",
        "median_shot_seconds",
        "shot_length_cv",
        "asl_first_third_ratio",
        "asl_middle_third_ratio",
        "asl_last_third_ratio",
        "rhythm_ratio",
        "longest_shot_position",
        "longest_shot_ratio",
        "loudness_rise_position",
        "loudness_peak_position",
    }
)

DURATION_DEPENDENT_FEATURES: frozenset[str] = frozenset(
    {"duration_seconds", "shot_count"}
)


class ShotListError(ValueError):
    """Die Shot-Liste ist nicht auswertbar.

    Bewusst hart statt stillschweigend korrigiert: eine ueberlappende
    oder unsortierte Liste deutet auf einen Fehler im erzeugenden
    Werkzeug hin. Sie zurechtzubiegen wuerde den Fehler verschleiern
    und die Zahlen unbrauchbar machen, ohne dass es jemand merkt.
    """


@dataclass(frozen=True)
class VideoFeatures:
    """Schnitt- und Lautheitsmerkmale eines einzelnen Videos.

    Felder mit ``None`` sind nicht messbar gewesen — zu wenige
    Einstellungen fuer eine Drittel-Aufteilung, oder keine Audiospur
    uebergeben. ``None`` heisst nie null.
    """

    # --- laufzeitabhaengig: beschreiben, nicht vergleichen ---
    duration_seconds: float
    shot_count: int

    # --- skalenfrei: vergleichbar ---
    asl_seconds: float
    median_shot_seconds: float
    shot_length_cv: float
    longest_shot_position: float
    longest_shot_ratio: float

    asl_first_third_ratio: Optional[float] = None
    asl_middle_third_ratio: Optional[float] = None
    asl_last_third_ratio: Optional[float] = None
    rhythm_ratio: Optional[float] = None

    loudness_rise_position: Optional[float] = None
    loudness_peak_position: Optional[float] = None

    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in asdict(self).items()
            if k != "notes"
        }
        if self.notes:
            out["notes"] = self.notes
        return out


def _validate(shots: Sequence[tuple[float, float]], duration: Optional[float]) -> float:
    if not shots:
        raise ShotListError("Leere Shot-Liste.")
    last_end = 0.0
    for i, (start, end) in enumerate(shots):
        if end <= start:
            raise ShotListError(
                f"Einstellung {i}: Ende {end} liegt nicht nach Start {start}."
            )
        if start < last_end - 1e-6:
            raise ShotListError(
                f"Einstellung {i} beginnt bei {start} und ueberlappt die "
                f"vorherige, die bei {last_end} endet. Liste unsortiert "
                f"oder fehlerhaft."
            )
        last_end = end
    if duration is None:
        return last_end
    if duration < last_end - 1e-6:
        raise ShotListError(
            f"Laufzeit {duration} ist kuerzer als das Ende der letzten "
            f"Einstellung ({last_end})."
        )
    return duration


def _third_of(midpoint: float, duration: float) -> int:
    if duration <= 0:
        return 0
    return min(2, int(3.0 * midpoint / duration))


def _loudness_positions(
    loudness: Sequence[tuple[float, float]], duration: float
) -> tuple[Optional[float], Optional[float]]:
    """Relative Position des Lautheitsanstiegs und des lautesten Punkts.

    **Ausdruecklich nicht "Musikeinsatz".** Aus einer Lautheitskurve
    allein laesst sich Musik nicht von Dialog oder Effekt unterscheiden.
    Das Merkmal heisst deshalb, was es misst: der Punkt, an dem die
    Lautheit erstmals die Haelfte ihrer Spannweite ueberschreitet. Wer
    daraus "hier setzt die Musik ein" liest, interpretiert ueber die
    Messung hinaus.

    Die Halb-Spannweiten-Schwelle ist gegenueber der absoluten Lautheit
    invariant — ein leise gemasterter Trailer und ein lauter liefern
    denselben Wert, solange die Kurvenform gleich ist.
    """
    if not loudness or duration <= 0:
        return None, None
    levels = [lvl for _, lvl in loudness]
    lo, hi = min(levels), max(levels)
    peak_time = max(loudness, key=lambda s: s[1])[0]
    peak_pos = max(0.0, min(1.0, peak_time / duration))
    if hi <= lo:
        # Konstante Lautheit: es gibt keinen Anstieg zu finden.
        return None, peak_pos
    threshold = lo + 0.5 * (hi - lo)
    for t, lvl in loudness:
        if lvl >= threshold:
            return max(0.0, min(1.0, t / duration)), peak_pos
    return None, peak_pos


def extract_features(
    shots: Sequence[tuple[float, float]],
    *,
    duration_seconds: Optional[float] = None,
    loudness: Optional[Sequence[tuple[float, float]]] = None,
) -> VideoFeatures:
    """Rechnet die Merkmale aus einer Shot-Liste.

    ``shots`` sind Paare ``(start, ende)`` in Sekunden, aufsteigend und
    ueberlappungsfrei. ``duration_seconds`` ueberschreibt die aus der
    letzten Einstellung abgeleitete Laufzeit — noetig, wenn das Video
    nach dem letzten Schnitt noch Nachspann hat.

    ``loudness`` sind Paare ``(zeitpunkt, pegel)``; die Pegel-Einheit
    ist gleichgueltig, weil nur relative Lagen ausgewertet werden.
    """
    duration = _validate(shots, duration_seconds)
    lengths = [end - start for start, end in shots]
    n = len(lengths)
    notes: list[str] = []

    asl = duration / n
    med = median(lengths)
    mean_len = sum(lengths) / n
    cv = (pstdev(lengths) / mean_len) if mean_len > 0 else 0.0

    longest_idx = max(range(n), key=lambda i: lengths[i])
    longest_start, longest_end = shots[longest_idx]
    longest_mid = (longest_start + longest_end) / 2.0
    longest_position = max(0.0, min(1.0, longest_mid / duration))
    longest_ratio = lengths[longest_idx] / asl if asl > 0 else 0.0

    first_r = middle_r = last_r = None
    rhythm = None
    if n >= MIN_SHOTS_FOR_THIRDS:
        buckets: list[list[float]] = [[], [], []]
        for (start, end), length in zip(shots, lengths):
            buckets[_third_of((start + end) / 2.0, duration)].append(length)
        if all(buckets):
            means = [sum(b) / len(b) for b in buckets]
            first_r, middle_r, last_r = (m / asl for m in means)
            rhythm = means[2] / means[0] if means[0] > 0 else None
        else:
            leer = [i + 1 for i, b in enumerate(buckets) if not b]
            notes.append(
                f"Drittel {leer} ohne eigene Einstellung — Rhythmus nicht "
                f"bestimmbar. Meist eine sehr lange Einstellung, die ein "
                f"ganzes Drittel ueberspannt."
            )
    else:
        notes.append(
            f"Nur {n} Einstellungen (Minimum {MIN_SHOTS_FOR_THIRDS} fuer die "
            f"Drittel-Aufteilung). Rhythmus-Merkmale bleiben leer."
        )

    rise_pos, peak_pos = _loudness_positions(loudness or [], duration)
    if loudness is None:
        notes.append("Keine Audiospur uebergeben — Lautheitsmerkmale leer.")

    return VideoFeatures(
        duration_seconds=duration,
        shot_count=n,
        asl_seconds=asl,
        median_shot_seconds=med,
        shot_length_cv=cv,
        longest_shot_position=longest_position,
        longest_shot_ratio=longest_ratio,
        asl_first_third_ratio=first_r,
        asl_middle_third_ratio=middle_r,
        asl_last_third_ratio=last_r,
        rhythm_ratio=rhythm,
        loudness_rise_position=rise_pos,
        loudness_peak_position=peak_pos,
        notes=notes,
    )


# ---------- Rangstatistik (ohne scipy, wie die z-Tests der Stufen 1-3) ----


def _ranks(values: Sequence[float]) -> list[float]:
    """Raenge mit Durchschnittsrang bei Bindungen."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j + 2) / 2.0  # 1-basiert
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _tie_correction(values: Sequence[float]) -> float:
    """Summe ueber t^3 - t je Bindungsgruppe (fuer die Varianzkorrektur)."""
    counts: dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sum(t**3 - t for t in counts.values() if t > 1)


@dataclass
class ComparisonResult:
    """Ergebnis eines Kohortenvergleichs fuer **ein** Merkmal."""

    feature: str
    test: str  # "wilcoxon_paired" | "mann_whitney"
    n_a: int
    n_b: int
    median_a: Optional[float]
    median_b: Optional[float]
    effect: Optional[float]  # Cliffs Delta bzw. Rang-biseriale Korrelation
    z: Optional[float]
    verdict: str  # "differs" | "no_difference" | "insufficient"
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        out = {
            "feature": self.feature,
            "test": self.test,
            "n_a": self.n_a,
            "n_b": self.n_b,
            "median_a": round(self.median_a, 4) if self.median_a is not None else None,
            "median_b": round(self.median_b, 4) if self.median_b is not None else None,
            "effect": round(self.effect, 3) if self.effect is not None else None,
            "z": round(self.z, 2) if self.z is not None else None,
            "verdict": self.verdict,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


def _verdict(z: Optional[float]) -> str:
    if z is None:
        return "insufficient"
    return "differs" if abs(z) >= Z_THRESHOLD else "no_difference"


def compare_paired(
    feature: str,
    pairs: Sequence[tuple[float, float]],
    *,
    min_pairs: int = MIN_PAIRS_FOR_TEST,
) -> ComparisonResult:
    """Wilcoxon-Vorzeichen-Rangtest fuer nach Titel gepaarte Werte.

    ``pairs`` sind ``(langform_wert, kurzform_wert)`` desselben Films.
    Genau diese Paarung macht den Test stark: Titel, Genre, Budget und
    Handschrift sind konstant, nur die Laenge variiert.

    Paare mit Differenz null fallen heraus (Standardverfahren) — sie
    tragen keine Richtungsinformation.
    """
    _reject_duration_dependent(feature)
    diffs = [a - b for a, b in pairs if a != b]
    dropped = len(pairs) - len(diffs)
    n = len(diffs)
    med_a = median([a for a, _ in pairs]) if pairs else None
    med_b = median([b for _, b in pairs]) if pairs else None

    if n < min_pairs:
        return ComparisonResult(
            feature=feature,
            test="wilcoxon_paired",
            n_a=len(pairs),
            n_b=len(pairs),
            median_a=med_a,
            median_b=med_b,
            effect=None,
            z=None,
            verdict="insufficient",
            reason=(
                f"{n} verwertbare Paare (Minimum {min_pairs}"
                + (f", {dropped} ohne Differenz verworfen" if dropped else "")
                + "). Die Normalapproximation waere hier nicht vertretbar."
            ),
        )

    abs_ranks = _ranks([abs(d) for d in diffs])
    w_plus = sum(r for d, r in zip(diffs, abs_ranks) if d > 0)
    mean_w = n * (n + 1) / 4.0
    var_w = n * (n + 1) * (2 * n + 1) / 24.0
    var_w -= _tie_correction([abs(d) for d in diffs]) / 48.0
    if var_w <= 0:
        return ComparisonResult(
            feature=feature,
            test="wilcoxon_paired",
            n_a=len(pairs),
            n_b=len(pairs),
            median_a=med_a,
            median_b=med_b,
            effect=None,
            z=None,
            verdict="insufficient",
            reason="Alle Differenzen gleich gross — kein Rangunterschied messbar.",
        )

    z = (w_plus - mean_w) / (var_w**0.5)
    # Rang-biseriale Korrelation als Effektstaerke: -1 bis +1.
    total_rank = n * (n + 1) / 2.0
    effect = 2.0 * w_plus / total_rank - 1.0

    return ComparisonResult(
        feature=feature,
        test="wilcoxon_paired",
        n_a=len(pairs),
        n_b=len(pairs),
        median_a=med_a,
        median_b=med_b,
        effect=effect,
        z=z,
        verdict=_verdict(z),
        reason=(f"{dropped} Paare ohne Differenz verworfen" if dropped else None),
    )


def compare_unpaired(
    feature: str,
    a: Sequence[float],
    b: Sequence[float],
    *,
    min_samples: int = MIN_SAMPLES_PER_ARM,
) -> ComparisonResult:
    """Mann-Whitney-U fuer unabhaengige Kohorten.

    Der schwaechere der beiden Tests — er muss die gesamte Streuung
    zwischen verschiedenen Filmen mit aushalten. Wo eine Paarung nach
    Titel moeglich ist, gehoert ``compare_paired`` benutzt.
    """
    _reject_duration_dependent(feature)
    n_a, n_b = len(a), len(b)
    med_a = median(a) if a else None
    med_b = median(b) if b else None

    if n_a < min_samples or n_b < min_samples:
        return ComparisonResult(
            feature=feature,
            test="mann_whitney",
            n_a=n_a,
            n_b=n_b,
            median_a=med_a,
            median_b=med_b,
            effect=None,
            z=None,
            verdict="insufficient",
            reason=(
                f"Langform {n_a}, Kurzform {n_b} Werte — beide Arme brauchen "
                f"mindestens {min_samples}."
            ),
        )

    combined = list(a) + list(b)
    ranks = _ranks(combined)
    r_a = sum(ranks[:n_a])
    u_a = r_a - n_a * (n_a + 1) / 2.0
    mean_u = n_a * n_b / 2.0
    n = n_a + n_b
    var_u = n_a * n_b * (n + 1) / 12.0
    tie = _tie_correction(combined)
    if tie:
        var_u -= n_a * n_b * tie / (12.0 * n * (n - 1))
    if var_u <= 0:
        return ComparisonResult(
            feature=feature,
            test="mann_whitney",
            n_a=n_a,
            n_b=n_b,
            median_a=med_a,
            median_b=med_b,
            effect=None,
            z=None,
            verdict="insufficient",
            reason="Alle Werte identisch — kein Rangunterschied messbar.",
        )

    z = (u_a - mean_u) / (var_u**0.5)
    effect = 2.0 * u_a / (n_a * n_b) - 1.0  # Cliffs Delta

    return ComparisonResult(
        feature=feature,
        test="mann_whitney",
        n_a=n_a,
        n_b=n_b,
        median_a=med_a,
        median_b=med_b,
        effect=effect,
        z=z,
        verdict=_verdict(z),
    )


def _reject_duration_dependent(feature: str) -> None:
    """Sperrt laufzeitabhaengige Merkmale fuer den Kohortenvergleich.

    Die Lehre aus drei Confounds: ``shot_count`` zwischen Langform und
    Kurzform zu vergleichen misst die Laufzeit und nennt es Rhythmus.
    Ein Kommentar haette das nicht verhindert, ein Fehler schon.
    """
    if feature in DURATION_DEPENDENT_FEATURES:
        raise ValueError(
            f"{feature!r} haengt an der Laufzeit und darf nicht zwischen "
            f"Formatklassen verglichen werden — der Unterschied waere die "
            f"Dauer selbst, nicht das Handwerk. Vergleichbar sind: "
            f"{sorted(SCALE_FREE_FEATURES)}"
        )
    if feature not in SCALE_FREE_FEATURES:
        raise ValueError(
            f"{feature!r} ist kein bekanntes Merkmal. Vergleichbar sind: "
            f"{sorted(SCALE_FREE_FEATURES)}"
        )


def compare_cohorts(
    langform: Sequence[VideoFeatures],
    kurzform: Sequence[VideoFeatures],
    *,
    features: Optional[Sequence[str]] = None,
    min_samples: int = MIN_SAMPLES_PER_ARM,
) -> list[ComparisonResult]:
    """Ungepaarter Vergleich zweier Kohorten ueber alle skalenfreien Merkmale.

    Merkmale, die bei einem Video ``None`` sind (zu wenige Einstellungen,
    keine Audiospur), fallen fuer dieses Video heraus — nicht fuer die
    ganze Kohorte.
    """
    names = list(features) if features else sorted(SCALE_FREE_FEATURES)
    out: list[ComparisonResult] = []
    for name in names:
        vals_a = [
            v for v in (getattr(f, name) for f in langform) if v is not None
        ]
        vals_b = [
            v for v in (getattr(f, name) for f in kurzform) if v is not None
        ]
        out.append(
            compare_unpaired(name, vals_a, vals_b, min_samples=min_samples)
        )
    out.sort(
        key=lambda r: (
            r.verdict == "insufficient",
            -(abs(r.z) if r.z is not None else -1.0),
        )
    )
    return out


def compare_pairs(
    pairs: Sequence[tuple[VideoFeatures, VideoFeatures]],
    *,
    features: Optional[Sequence[str]] = None,
    min_pairs: int = MIN_PAIRS_FOR_TEST,
) -> list[ComparisonResult]:
    """Gepaarter Vergleich — der empfohlene Weg.

    ``pairs`` sind ``(langform, kurzform)`` desselben Titels. Paare, bei
    denen ein Merkmal auf einer Seite fehlt, fallen fuer dieses Merkmal
    heraus.
    """
    names = list(features) if features else sorted(SCALE_FREE_FEATURES)
    out: list[ComparisonResult] = []
    for name in names:
        usable = [
            (getattr(lang, name), getattr(kurz, name))
            for lang, kurz in pairs
            if getattr(lang, name) is not None and getattr(kurz, name) is not None
        ]
        out.append(compare_paired(name, usable, min_pairs=min_pairs))
    out.sort(
        key=lambda r: (
            r.verdict == "insufficient",
            -(abs(r.z) if r.z is not None else -1.0),
        )
    )
    return out
