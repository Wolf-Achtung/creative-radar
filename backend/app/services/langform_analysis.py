"""Trailer-Intelligence Stufe 3, Schritt 1 — warum gewinnt Langform?

Stufe 1 hat nach drei Korrekturrunden genau einen belastbaren Befund
hinterlassen (Ergebnisse-Dokument, Abschnitt 16):

    Langform  (>= 90 s)   869 Posts / 176 Kanaele   16,9 %   z = +2,27
    Uebergang (60-89 s)   758 Posts / 153 Kanaele   13,3 %   z = -0,04
    Kurzform  (<  60 s) 3.980 Posts / 180 Kanaele   12,3 %   z = -1,25

Format-Label, Ton, Musik und Lebenszyklus tragen nichts bei. Die Frage
ist damit nicht mehr *ob*, sondern *warum* — und dieses Modul beantwortet
sie nicht, sondern **grenzt sie ein**.

Was dieses Modul leistet und was nicht
======================================

Es gibt mindestens fuenf Erklaerungen fuer den Langform-Vorsprung. Vier
davon lassen sich mit vorhandenen Daten pruefen, eine nicht:

| # | Erklaerung                                    | pruefbar mit          |
|---|-----------------------------------------------|-----------------------|
| 1 | **Auswahl** — nur hochwertige Assets werden    | Title-Match, Kanal-   |
|   | ueberhaupt so lang produziert; die Laenge ist  | Gewohnheit            |
|   | Marker fuer Investition, nicht Ursache         |                       |
| 2 | **Plattform** — Langform traegt nur dort, wo   | Plattform-Schichtung  |
|   | der Player es hergibt (YouTube)                |                       |
| 3 | **Release-Fenster** — Langform haeuft sich     | days_to_release ueber |
|   | nahe am Start, wo das Interesse ohnehin hoch   | Title.release_date    |
|   | ist                                            |                       |
| 4 | **Markt** — ein DE/US/UK-Effekt                | Markt-Schichtung      |
| 5 | **Handwerk** — Langform hat Raum fuer Aufbau,  | **nur mit Video**     |
|   | Wendepunkt, Aufloesung                         | (Stufe 5)             |

Das Modul rechnet den Langform-Vorsprung **innerhalb** jeder Schicht neu.
Verschwindet er in einer Schicht, erklaert diese Schicht ihn. Ueberlebt er
ueberall, bleibt Erklaerung 5 uebrig — und dann ist bewiesen, dass die
Antwort in der Ausfuehrung steckt und nicht in den Metadaten.

Der Ergebniswert des Moduls ist deshalb ``survives_in`` von
``tested_strata``: in wie vielen belastbaren Schichten der Vorsprung
standhaelt. Das ist keine Erklaerung, sondern eine **Eingrenzung** — und
genau das ist an dieser Stelle der ehrliche Beitrag.

Warum Kurzform als Kontrollgruppe und nicht die Erwartung
=========================================================

Stufe 1 hat jede Zelle gegen einen Erwartungswert aus ihrer
Plattform-Mischung geprueft. Fuer die Frage "erklaert Schicht X den
Vorsprung?" ist das der falsche Test: dort geht es nicht um Abweichung
von einer Erwartung, sondern um die **Differenz zweier Gruppen** im
selben Kontext. Deshalb hier ein Zwei-Stichproben-z-Test auf
Anteilsdifferenz zwischen Langform und Kurzform.

Die Uebergangszone (60-89 s) bleibt aus dem Vergleich heraus. Sie liegt
statistisch exakt in der Mitte (z = -0,04 in Stufe 1) und wuerde beide
Arme verwaessern; sie wird nur als Kontextzahl mitgefuehrt.

Ehrlichkeits-Regeln
===================

Uebernommen aus ``trailer_patterns`` und um eine ergaenzt:

1. Kanal-Baselines und Lift kommen aus ``build_lift_context`` — dieselbe
   Implementierung, damit die Zahlen beider Stufen vergleichbar bleiben.
2. Eine Schicht braucht in **beiden** Armen ``MIN_POSTS_PER_ARM`` Posts.
   Ein Vergleich von 400 gegen 7 Posts ist keine Schichtung, sondern
   Rauschen mit Etikett.
3. Schichten unter der Schwelle verschwinden nicht, sie werden mit
   ``verdict="insufficient"`` und Grund gemeldet.
4. ``survives_in`` zaehlt nur belastbare Schichten. Eine Schicht, die
   mangels Daten kein Urteil zulaesst, ist kein bestandener Test.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Post, Title
from app.services.insight_engine import (
    _classify_days_to_release,
    _pick_release_date,
    _post_age_reference,
)
from app.services.trailer_patterns import (
    BREAKOUT_LIFT_THRESHOLD,
    DEFAULT_WINDOW_DAYS,
    FORMAT_CLASS_LOWER_SECONDS,
    FORMAT_CLASS_UPPER_SECONDS,
    build_lift_context,
)

logger = logging.getLogger(__name__)


# Beide Arme muessen belegt sein, sonst ist die Schicht kein Test.
# 30 ist dieselbe Groessenordnung wie MIN_POSTS_PER_PLATFORM_BASELINE in
# Stufe 1 und reicht, damit der Normalapproximation des z-Tests zu
# trauen ist.
MIN_POSTS_PER_ARM = 30

# Ab welcher Differenz in Prozentpunkten ueberhaupt von einem Vorsprung
# gesprochen wird, unabhaengig von der Signifikanz. Verhindert, dass bei
# sehr grossen Schichten ein rechnerisch signifikanter, praktisch
# bedeutungsloser Unterschied als Befund durchgeht.
MIN_GAP_PP = 1.0

GAP_Z_THRESHOLD = 2.0

# Ab welchem Langform-Anteil ein Kanal Langform gewohnheitsmaessig
# produziert. Trennt "Studio mit regelmaessigem Trailer-Output" von
# "Kanal, der einmal im Quartal ein grosses Asset raushaut" — der
# Unterschied, an dem die Auswahl-Erklaerung haengt.
LANGFORM_HABIT_SHARE = 0.25


@dataclass
class StratumComparison:
    """Langform gegen Kurzform innerhalb einer Schicht."""

    stratum: str
    langform_posts: int
    langform_rate: float
    kurzform_posts: int
    kurzform_rate: float
    gap_pp: float
    gap_z: Optional[float]
    verdict: str  # "advantage" | "none" | "reversed" | "insufficient"
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        out = {
            "stratum": self.stratum,
            "langform_posts": self.langform_posts,
            "langform_rate": round(self.langform_rate, 4),
            "kurzform_posts": self.kurzform_posts,
            "kurzform_rate": round(self.kurzform_rate, 4),
            "gap_pp": round(self.gap_pp, 2),
            "gap_z": round(self.gap_z, 2) if self.gap_z is not None else None,
            "verdict": self.verdict,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class LangformReport:
    window_days: int
    window_start: datetime
    window_end: datetime
    market: Optional[str]
    langform_posts: int
    uebergang_posts: int
    kurzform_posts: int
    overall: Optional[StratumComparison] = None
    strata: dict[str, list[StratumComparison]] = field(default_factory=dict)
    duration_gradient: list[StratumComparison] = field(default_factory=list)
    tested_strata: int = 0
    survives_in: int = 0
    explained_by: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "market": self.market,
            "langform_posts": self.langform_posts,
            "uebergang_posts": self.uebergang_posts,
            "kurzform_posts": self.kurzform_posts,
            "overall": self.overall.to_dict() if self.overall else None,
            "strata": {
                name: [c.to_dict() for c in cells]
                for name, cells in self.strata.items()
            },
            "duration_gradient": [c.to_dict() for c in self.duration_gradient],
            "tested_strata": self.tested_strata,
            "survives_in": self.survives_in,
            "explained_by": self.explained_by,
            "notes": self.notes,
        }


# ---------- Statistik ---------------------------------------------------


def _two_proportion_z(
    hits_a: int, n_a: int, hits_b: int, n_b: int
) -> Optional[float]:
    """z-Wert der Differenz zweier Anteile (gepoolter Standardfehler).

    Nullhypothese: beide Gruppen haben dieselbe Trefferquote. Unter ihr
    ist der beste Schaetzer fuer die gemeinsame Quote der gepoolte
    Anteil, und der Standardfehler der Differenz betraegt
    sqrt(p*(1-p)*(1/n_a + 1/n_b)).

    Bewusst nicht derselbe Test wie in Stufe 1: dort wird eine Zelle
    gegen einen *bekannten* Erwartungswert geprueft, hier zwei
    Stichproben gegeneinander. Der gepoolte Fehler ist hier der
    richtige, weil beide Seiten Schaetzungen mit eigener Unsicherheit
    sind.
    """
    if n_a <= 0 or n_b <= 0:
        return None
    p_pool = (hits_a + hits_b) / (n_a + n_b)
    if p_pool <= 0.0 or p_pool >= 1.0:
        return None
    se = (p_pool * (1.0 - p_pool) * (1.0 / n_a + 1.0 / n_b)) ** 0.5
    if se == 0:
        return None
    return (hits_a / n_a - hits_b / n_b) / se


def _compare(
    stratum: str,
    lang: list[float],
    kurz: list[float],
    *,
    min_posts: int = MIN_POSTS_PER_ARM,
) -> StratumComparison:
    """Baut eine Schicht-Vergleichszeile aus zwei Lift-Listen."""
    n_l, n_k = len(lang), len(kurz)
    hits_l = sum(1 for x in lang if x >= BREAKOUT_LIFT_THRESHOLD)
    hits_k = sum(1 for x in kurz if x >= BREAKOUT_LIFT_THRESHOLD)
    rate_l = hits_l / n_l if n_l else 0.0
    rate_k = hits_k / n_k if n_k else 0.0
    gap_pp = (rate_l - rate_k) * 100.0

    if n_l < min_posts or n_k < min_posts:
        return StratumComparison(
            stratum=stratum,
            langform_posts=n_l,
            langform_rate=rate_l,
            kurzform_posts=n_k,
            kurzform_rate=rate_k,
            gap_pp=gap_pp,
            gap_z=None,
            verdict="insufficient",
            reason=(
                f"Langform {n_l}, Kurzform {n_k} Posts — beide Arme "
                f"brauchen mindestens {min_posts}"
            ),
        )

    z = _two_proportion_z(hits_l, n_l, hits_k, n_k)
    if z is None:
        verdict = "insufficient"
        reason = "keine verwertbare Trefferquote in dieser Schicht"
    elif z >= GAP_Z_THRESHOLD and gap_pp >= MIN_GAP_PP:
        verdict, reason = "advantage", None
    elif z <= -GAP_Z_THRESHOLD and gap_pp <= -MIN_GAP_PP:
        verdict, reason = "reversed", None
    else:
        verdict, reason = "none", None

    return StratumComparison(
        stratum=stratum,
        langform_posts=n_l,
        langform_rate=rate_l,
        kurzform_posts=n_k,
        kurzform_rate=rate_k,
        gap_pp=gap_pp,
        gap_z=z,
        verdict=verdict,
        reason=reason,
    )


# ---------- Schicht-Merkmale --------------------------------------------


def _title_id_by_post(session: Session, post_ids: list[Any]) -> dict[Any, Any]:
    """``post_id -> title_id`` ueber die Asset-Tabelle.

    Ein Post kann mehrere Assets tragen; der erste mit gesetztem
    ``title_id`` gewinnt. Posts ohne gematchten Titel fehlen im Ergebnis
    und gelten als ``unmatched``.
    """
    if not post_ids:
        return {}
    rows = list(
        session.exec(
            select(Asset.post_id, Asset.title_id)
            .where(Asset.post_id.in_(post_ids))
            .where(Asset.title_id.is_not(None))
        ).all()
    )
    out: dict[Any, Any] = {}
    for post_id, title_id in rows:
        out.setdefault(post_id, title_id)
    return out


def _market_str(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _days_to_release_stratum(
    post: Post,
    title_id_by_post: dict[Any, Any],
    title_by_id: dict[Any, Title],
    market: str,
) -> str:
    """Release-Fenster-Bucket eines Posts, oder ``unknown``.

    Eigene Implementierung statt ``_classify_post_days_to_release`` aus
    dem insight_engine: jene Funktion liest ``post._resolved_title_id``,
    ein Attribut, das im Bestand nirgends gesetzt wird — sie liefert
    deshalb immer UNKNOWN. Die eigentliche Klassifikationslogik
    (``_pick_release_date``, ``_classify_days_to_release``) wird dagegen
    wiederverwendet, damit die Bucket-Grenzen ueberall dieselben sind.
    """
    title = title_by_id.get(title_id_by_post.get(post.id))
    if title is None:
        return "unknown"
    release = _pick_release_date(title, market)
    if release is None:
        return "unknown"
    ref = _post_age_reference(post)
    if ref is None:
        return "unknown"
    post_date = ref.date() if hasattr(ref, "date") else ref
    bucket = _classify_days_to_release((release - post_date).days)
    return bucket.value if hasattr(bucket, "value") else str(bucket)


def _duration_band(seconds: int) -> str:
    """Feinere Bänder **innerhalb** der Langform.

    Beantwortet eine eigene Frage: waechst der Vorsprung mit der Laenge
    weiter (dann ist "mehr Raum fuer Aufbau" plausibel) oder springt er
    bei 90 Sekunden und bleibt flach (dann ist es eine
    Format-Konvention)?
    """
    if seconds < 120:
        return "90-120s"
    if seconds < 180:
        return "120-180s"
    if seconds < 300:
        return "180-300s"
    return ">300s"


# ---------- Hauptfunktion -----------------------------------------------


def compute_langform_report(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    market: Optional[str] = None,
    now: Optional[datetime] = None,
    min_posts_per_arm: int = MIN_POSTS_PER_ARM,
) -> LangformReport:
    """Grenzt ein, welche Erklaerungen den Langform-Vorsprung tragen.

    Rechnet den Vorsprung von Langform (>= 90 s) gegenueber Kurzform
    (< 60 s) einmal insgesamt und dann innerhalb jeder Schicht neu. Was
    uebrig bleibt, ist der Teil, den die vorhandenen Metadaten nicht
    erklaeren — und damit der Auftrag fuer die Video-Erfassung.
    """
    ctx = build_lift_context(
        session, window_days=window_days, market=market, now=now
    )
    notes = list(ctx.notes)
    report = LangformReport(
        window_days=window_days,
        window_start=ctx.window_start,
        window_end=ctx.window_end,
        market=market,
        langform_posts=0,
        uebergang_posts=0,
        kurzform_posts=0,
        notes=notes,
    )
    if not ctx.usable:
        return report

    # ---- Kohorten -------------------------------------------------------
    langform: list[Post] = []
    kurzform: list[Post] = []
    uebergang = 0
    for p in ctx.usable:
        d = p.duration_seconds
        if d is None:
            continue
        if d >= FORMAT_CLASS_UPPER_SECONDS:
            langform.append(p)
        elif d < FORMAT_CLASS_LOWER_SECONDS:
            kurzform.append(p)
        else:
            uebergang += 1

    report.langform_posts = len(langform)
    report.uebergang_posts = uebergang
    report.kurzform_posts = len(kurzform)

    without_duration = sum(1 for p in ctx.usable if p.duration_seconds is None)
    if without_duration:
        notes.append(
            f"{without_duration} Posts ohne Dauer-Angabe bleiben aussen vor — "
            f"ohne Dauer keine Formatklasse."
        )
    notes.append(
        f"Uebergangszone (60-89 s, {uebergang} Posts) ist aus dem Vergleich "
        f"ausgenommen: sie liegt statistisch in der Mitte und wuerde beide "
        f"Arme verwaessern."
    )

    lift = ctx.lift_by_post
    lifts_of: Callable[[list[Post]], list[float]] = lambda ps: [lift[p.id] for p in ps]

    report.overall = _compare(
        "gesamt", lifts_of(langform), lifts_of(kurzform), min_posts=min_posts_per_arm
    )
    if report.overall.verdict != "advantage":
        notes.append(
            "Kein Langform-Vorsprung im Gesamtvergleich — die "
            "Schichtungen unten sind damit ohne Aussagekraft."
        )

    # ---- Kontext je Post ------------------------------------------------
    channels = {c.id: c for c in session.exec(select(Channel)).all()}
    cohort = langform + kurzform
    title_ids = _title_id_by_post(session, [p.id for p in cohort])
    titles = (
        {t.id: t for t in session.exec(select(Title).where(Title.id.in_(list(set(title_ids.values()))))).all()}
        if title_ids
        else {}
    )

    langform_share: dict[Any, float] = {}
    per_channel_total: dict[Any, int] = defaultdict(int)
    per_channel_lang: dict[Any, int] = defaultdict(int)
    for p in cohort:
        per_channel_total[p.channel_id] += 1
    for p in langform:
        per_channel_lang[p.channel_id] += 1
    for ch_id, total in per_channel_total.items():
        langform_share[ch_id] = per_channel_lang[ch_id] / total if total else 0.0

    def stratum_of(post: Post, kind: str) -> str:
        ch = channels.get(post.channel_id)
        if kind == "platform":
            return ctx.platform_by_channel.get(post.channel_id, "unknown")
        if kind == "market":
            return _market_str(ch.market) if ch else "unknown"
        if kind == "title_match":
            return "gematcht" if post.id in title_ids else "ohne Titel"
        if kind == "channel_habit":
            share = langform_share.get(post.channel_id, 0.0)
            return (
                "regelmaessig" if share >= LANGFORM_HABIT_SHARE else "gelegentlich"
            )
        if kind == "days_to_release":
            m = _market_str(ch.market) if ch else "US"
            return _days_to_release_stratum(post, title_ids, titles, m)
        raise ValueError(kind)

    # ---- Schichtungen ---------------------------------------------------
    for kind in ("platform", "market", "title_match", "channel_habit", "days_to_release"):
        by_stratum_l: dict[str, list[float]] = defaultdict(list)
        by_stratum_k: dict[str, list[float]] = defaultdict(list)
        for p in langform:
            by_stratum_l[stratum_of(p, kind)].append(lift[p.id])
        for p in kurzform:
            by_stratum_k[stratum_of(p, kind)].append(lift[p.id])

        rows = [
            _compare(
                name,
                by_stratum_l.get(name, []),
                by_stratum_k.get(name, []),
                min_posts=min_posts_per_arm,
            )
            for name in sorted(set(by_stratum_l) | set(by_stratum_k))
        ]
        rows.sort(
            key=lambda c: (
                c.verdict == "insufficient",
                -(c.gap_z if c.gap_z is not None else float("-inf")),
            )
        )
        report.strata[kind] = rows

    # ---- Dauer-Gradient innerhalb der Langform --------------------------
    kurz_lifts = lifts_of(kurzform)
    by_band: dict[str, list[float]] = defaultdict(list)
    for p in langform:
        by_band[_duration_band(int(p.duration_seconds))].append(lift[p.id])
    report.duration_gradient = [
        _compare(band, by_band.get(band, []), kurz_lifts, min_posts=min_posts_per_arm)
        for band in ("90-120s", "120-180s", "180-300s", ">300s")
    ]

    # ---- Bilanz ---------------------------------------------------------
    #
    # Gezaehlt wird ueber die Schichtungen, nicht ueber den Gradienten:
    # der Gradient beantwortet eine andere Frage und ist kein Test auf
    # eine konkurrierende Erklaerung.
    testable = [
        c
        for rows in report.strata.values()
        for c in rows
        if c.verdict != "insufficient"
    ]
    report.tested_strata = len(testable)
    report.survives_in = sum(1 for c in testable if c.verdict == "advantage")
    report.explained_by = sorted(
        {
            kind
            for kind, rows in report.strata.items()
            if any(c.verdict != "insufficient" for c in rows)
            and all(c.verdict != "advantage" for c in rows if c.verdict != "insufficient")
        }
    )

    if report.tested_strata == 0:
        notes.append(
            "Keine Schicht hatte in beiden Armen genug Posts — der "
            "Vorsprung ist mit diesem Bestand nicht eingrenzbar."
        )
    elif report.explained_by:
        notes.append(
            "Der Vorsprung verschwindet vollstaendig innerhalb von: "
            + ", ".join(report.explained_by)
            + ". Diese Erklaerung(en) kommen vor dem Handwerk."
        )
    elif report.survives_in == report.tested_strata:
        notes.append(
            f"Der Vorsprung haelt in allen {report.tested_strata} belastbaren "
            f"Schichten. Plattform, Markt, Titel-Match, Kanal-Gewohnheit und "
            f"Release-Fenster erklaeren ihn nicht — was bleibt, ist die "
            f"Ausfuehrung, und die ist ohne Video-Erfassung nicht messbar."
        )

    return report
