"""Release-Countdown je Wir-Projekt (Roadmap Schritt 2, 25.08.2026).

Drei vorhandene Auswertungen, verbunden zu einem Plan pro Projekt:

- **Wir-Projekt** (``Title.is_own_project``) traegt das Release-Datum,
- **Kampagnen-Timing** sagt, wie frueh der Markt vergleichbare
  Kampagnen startet (Median der Kampagnenstarts ueber alle
  ausgewerteten Titel),
- **Trailer-Patterns** sagt, welche Machart in der aktuellen
  Kampagnenphase ueber dem Schnitt laeuft.

Daraus wird je markiertem Projekt eine Einordnung: "Release in 9
Wochen, der Markt startet im Median 11 Wochen vorher — ihr seid spaet
dran. In dieser Phase laufen Behind-the-Scenes ueber dem Schnitt."

Bewusst KEINE Zweitrechnung: der Markt-Median kommt aus
``compute_campaign_timing`` (dieselben Band- und Mindest-Post-Regeln),
die Phasen-Muster aus ``build_lift_context`` +
``compute_cells_for_mapping`` (dieselbe Statistik wie der
Muster-Bericht, inklusive Mindest-Stichprobe und z-Test). Korrelation,
keine Kausalitaet — wie ueberall im Radar.

Deterministisch, LLM-frei, rein lesend. Gate:
``FEATURE_RELEASE_COUNTDOWN_ENABLED`` (Endpoint in ``api/admin.py``).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from sqlmodel import Session, select

from app.models.entities import Asset, Market, Post, Title
from app.services.campaign_timing import (
    MAX_WOCHEN_NACH,
    MAX_WOCHEN_VOR,
    _post_moment,
    compute_campaign_timing,
)
from app.services.trailer_patterns import (
    build_lift_context,
    compute_cells_for_mapping,
    facetten_werte_je_post,
)

logger = logging.getLogger(__name__)

# Phasengrenzen in Tagen bis Release: mehr als eine Woche vor dem Start
# ist Pre-Launch, die Woche um den Start herum Launch, danach
# Post-Launch. Die Namen sind bewusst die des lifecycle-Vokabulars aus
# der Post-Analyse — nur so lassen sich die Phasen-Muster direkt aus
# der lifecycle_stage-Dimension ziehen.
LAUNCH_FENSTER_TAGE = 7

# Hoechstens so viele belastbare Zellen je Phase — der Countdown ist
# ein Plan, kein zweiter Muster-Bericht.
MAX_MUSTER_JE_PHASE = 6


def _phase_fuer(tage_bis_release: int) -> str:
    if tage_bis_release > LAUNCH_FENSTER_TAGE:
        return "pre_launch"
    if tage_bis_release >= -LAUNCH_FENSTER_TAGE:
        return "launch"
    return "post_launch"


def _release_fuer_projekt(title: Title) -> tuple[Optional[date], Optional[str]]:
    """Wir-Projekte sind DE-Kampagnen: das DE-Datum zaehlt, das
    US-Datum ist nur Rueckfall, wenn kein DE-Datum gepflegt ist."""
    if title.release_date_de:
        return title.release_date_de, Market.DE.value
    if title.release_date_us:
        return title.release_date_us, Market.US.value
    return None, None


def _markt_kampagnenstart(session: Session) -> dict:
    """Median der Kampagnenstarts ueber ALLE ausgewerteten Titel des
    Kampagnen-Timings — nicht nur die Anzeige-Top-20, deshalb der hohe
    ``max_titles``-Wert."""
    timing = compute_campaign_timing(session, max_titles=100_000)
    starts = [t["kampagnenstart_vorlauf_tage"] for t in timing["titel"]]
    return {
        "median_vorlauf_tage": round(median(starts)) if starts else None,
        "titel_basis": len(starts),
    }


def _eigene_posts(
    session: Session, projekt_ids: list[Any]
) -> dict[Any, list[Post]]:
    """Projekt-Titel → zugeordnete Posts, aelteste Asset-Zuordnung je
    Post (dieselbe Konvention wie ueberall: ``_title_by_post``)."""
    if not projekt_ids:
        return {}
    title_id_by_post: dict[Any, Any] = {}
    for asset in session.exec(
        select(Asset)
        .where(Asset.title_id.in_(projekt_ids))
        .order_by(Asset.created_at.asc())
    ).all():
        title_id_by_post.setdefault(asset.post_id, asset.title_id)
    if not title_id_by_post:
        return {}
    posts = session.exec(
        select(Post).where(Post.id.in_(list(title_id_by_post.keys())))
    ).all()
    mapping: dict[Any, list[Post]] = {}
    for post in posts:
        mapping.setdefault(title_id_by_post[post.id], []).append(post)
    return mapping


def _phasen_muster(session: Session, phasen: set[str]) -> dict[str, list[dict]]:
    """Je gebrauchter Phase: die belastbaren over/under-Zellen der
    uebrigen Dimensionen, gerechnet NUR auf den Posts dieser Phase.

    Dieselbe Statistik wie der Muster-Bericht: die Post→Wert-Karten
    kommen aus ``facetten_werte_je_post`` (Konfidenz-Regeln inklusive),
    die Zellen aus ``compute_cells_for_mapping`` (Mindest-Stichprobe,
    Mindest-Kanalzahl, z-Test gegen die Plattform-Mischung).
    ``insufficient``-Zellen bleiben weg — der Countdown zeigt einen
    Plan, keine Rohdaten; wie viel dahinter steht, sagt sample_size.
    """
    if not phasen:
        return {}
    ctx = build_lift_context(session)
    karten = facetten_werte_je_post(session, ctx.usable)
    phase_by_post = karten.get("lifecycle_stage", {})
    ergebnis: dict[str, list[dict]] = {}
    for phase in sorted(phasen):
        phasen_ids = {
            post_id for post_id, wert in phase_by_post.items() if wert == phase
        }
        zellen: list[dict] = []
        for dimension, karte in karten.items():
            if dimension == "lifecycle_stage":
                continue
            eingeschraenkt = {
                post_id: wert
                for post_id, wert in karte.items()
                if post_id in phasen_ids
            }
            for cell in compute_cells_for_mapping(ctx, eingeschraenkt):
                if cell.breakout_verdict not in ("over", "under"):
                    continue
                zellen.append({"dimension": dimension, **cell.to_dict()})
        zellen.sort(key=lambda z: -abs(z.get("breakout_z") or 0.0))
        ergebnis[phase] = zellen[:MAX_MUSTER_JE_PHASE]
    return ergebnis


def compute_release_countdown(
    session: Session, *, now: Optional[datetime] = None
) -> dict:
    now = now or datetime.now(timezone.utc)
    heute = now.date()

    projekte = session.exec(
        select(Title).where(Title.is_own_project.is_(True))
    ).all()
    if not projekte:
        return {
            "projekte": [],
            "markt_kampagnenstart": {"median_vorlauf_tage": None, "titel_basis": 0},
            "phasen_muster": {},
            "note": (
                "Noch kein Titel als Wir-Projekt markiert — die "
                "Markierung sitzt in der Titel-Verwaltung."
            ),
        }

    posts_by_projekt = _eigene_posts(session, [t.id for t in projekte])
    markt = _markt_kampagnenstart(session)

    zeilen: list[dict] = []
    gebrauchte_phasen: set[str] = set()
    for titel in projekte:
        release, release_markt = _release_fuer_projekt(titel)
        eigene = posts_by_projekt.get(titel.id, [])

        eigener_start: Optional[int] = None
        if release is not None:
            release_dt = datetime.combine(
                release, datetime.min.time(), tzinfo=timezone.utc
            )
            vorlaeufe = []
            for post in eigene:
                moment = _post_moment(post)
                if moment is None:
                    continue
                vorlauf_tage = (release_dt - moment) / timedelta(days=1)
                if -MAX_WOCHEN_NACH * 7 <= vorlauf_tage <= MAX_WOCHEN_VOR * 7:
                    vorlaeufe.append(vorlauf_tage)
            if vorlaeufe:
                eigener_start = round(max(vorlaeufe))

        if release is None:
            zeilen.append({
                "titel": titel.title_original,
                "title_id": str(titel.id),
                "release_date": None,
                "release_markt": None,
                "tage_bis_release": None,
                "wochen_bis_release": None,
                "phase": None,
                "eigene_posts": len(eigene),
                "eigener_start_vorlauf_tage": None,
                "hinweis": (
                    "Kein Release-Datum am Titel — ohne Datum keine "
                    "Zeitleiste. Datum kommt ueber den TMDb-Sync oder "
                    "die Titel-Verwaltung."
                ),
            })
            continue

        tage = (release - heute).days
        phase = _phase_fuer(tage)
        gebrauchte_phasen.add(phase)
        zeilen.append({
            "titel": titel.title_original,
            "title_id": str(titel.id),
            "release_date": release.isoformat(),
            "release_markt": release_markt,
            "tage_bis_release": tage,
            "wochen_bis_release": round(tage / 7, 1),
            "phase": phase,
            "eigene_posts": len(eigene),
            "eigener_start_vorlauf_tage": eigener_start,
            "hinweis": None,
        })

    # Naechster Release zuerst; Projekte ohne Datum ans Ende.
    zeilen.sort(
        key=lambda z: (
            z["tage_bis_release"] is None,
            z["tage_bis_release"] if z["tage_bis_release"] is not None else 0,
        )
    )

    ergebnis = {
        "projekte": zeilen,
        "markt_kampagnenstart": markt,
        "phasen_muster": _phasen_muster(session, gebrauchte_phasen),
        "note": None,
    }
    logger.info(
        "release_countdown.computed projekte=%s markt_basis=%s",
        len(zeilen), markt["titel_basis"],
    )
    return ergebnis
