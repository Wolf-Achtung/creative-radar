"""Kampagnen-Timing (22.08.2026) — wann startet die Trailer-Welle?

Tellerrand-Idee aus dem Stufen-Fahrplan: alles fuer diese Auswertung
liegt laengst in der DB — TMDb-Release-Dates am Titel, Zeitstempel an
den Posts, die Titel-Zuordnung aus der Pruef-Queue. Neu ist nur die
Rechnung: WANN postet welcher Kanal relativ zum Kinostart, und wie
sieht die Eskalationskurve aus (Teaser frueh, Countdown kurz vorm
Start)? Fuer Trailerhaus direkt verwertbar: "Verleih X startet im
Median Y Wochen vor Release — die US-Studios starten frueher."

Regeln, ehrlich benannt:
- Release-Datum marktgerecht: DE-Kanaele messen gegen
  ``release_date_de``, alle anderen gegen ``release_date_us`` —
  Fallback jeweils aufs andere Datum, wenn nur eines existiert.
- Post-Zeitpunkt: ``published_at``, Fallback ``detected_at`` (bei
  Alt-Scrapes fehlt published_at gelegentlich).
- Titel brauchen ``min_posts`` zugeordnete Posts im Betrachtungs-
  bereich, sonst verzerrt ein Einzel-Post die Kanal-Mediane.
- Vorlauf > 0 heisst VOR dem Release; negative Werte sind Posts nach
  dem Start (Home-Release-Pushes, Erfolgs-Meldungen).

Korrelation, keine Kausalitaet — die Auswertung beschreibt das
Timing-Verhalten des Markts, sie beweist nicht, dass frueher besser
ist. Deterministisch, LLM-frei, rein lesend.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Optional

from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Market, Post, Title

logger = logging.getLogger(__name__)

# Betrachtet werden Posts im Band [MAX_WOCHEN_VOR .. MAX_WOCHEN_NACH]
# um den Release — was frueher liegt, ist idR kein Kampagnen-Post
# (Set-Fotos Jahre vorher), was spaeter liegt, gehoert zur
# Zweitverwertung.
MAX_WOCHEN_VOR = 26
MAX_WOCHEN_NACH = 8
MIN_POSTS_PER_TITLE = 3


def _release_fuer_markt(title: Title, market: Market) -> Optional[date]:
    if market == Market.DE:
        return title.release_date_de or title.release_date_us
    return title.release_date_us or title.release_date_de


def _post_moment(post: Post) -> Optional[datetime]:
    moment = post.published_at or post.detected_at
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def compute_campaign_timing(
    session: Session,
    *,
    min_posts: int = MIN_POSTS_PER_TITLE,
    max_titles: int = 20,
) -> dict:
    titles = {
        t.id: t
        for t in session.exec(
            select(Title).where(
                (Title.release_date_de.is_not(None))
                | (Title.release_date_us.is_not(None))
            )
        ).all()
    }
    if not titles:
        return {
            "titel_ausgewertet": 0,
            "posts_ausgewertet": 0,
            "kurve": [],
            "kanaele": [],
            "titel": [],
            "note": "Noch kein Titel mit Release-Datum im Katalog.",
        }

    # Post -> Titel ueber das aelteste Asset mit title_id — dieselbe
    # Konvention wie _title_by_post im Muster-Bericht.
    title_id_by_post: dict = {}
    for asset in session.exec(
        select(Asset)
        .where(Asset.title_id.in_(list(titles.keys())))
        .order_by(Asset.created_at.asc())
    ).all():
        title_id_by_post.setdefault(asset.post_id, asset.title_id)
    if not title_id_by_post:
        return {
            "titel_ausgewertet": 0,
            "posts_ausgewertet": 0,
            "kurve": [],
            "kanaele": [],
            "titel": [],
            "note": (
                "Kein Post ist einem Titel mit Release-Datum zugeordnet — "
                "die Auswertung waechst mit der Zuordnungs-Arbeit in der "
                "Prüf-Queue."
            ),
        }

    posts = session.exec(
        select(Post).where(Post.id.in_(list(title_id_by_post.keys())))
    ).all()
    channels = {
        c.id: c
        for c in session.exec(
            select(Channel).where(Channel.id.in_({p.channel_id for p in posts}))
        ).all()
    }

    # Vorlauf je Post: Tage zwischen Post und marktgerechtem Release.
    # > 0 = vor dem Release.
    vorlauf_by_title: dict = {}
    posts_ausgewertet = 0
    for post in posts:
        channel = channels.get(post.channel_id)
        title = titles.get(title_id_by_post.get(post.id))
        moment = _post_moment(post)
        if channel is None or title is None or moment is None:
            continue
        release = _release_fuer_markt(title, channel.market)
        if release is None:
            continue
        vorlauf_tage = (
            datetime.combine(release, datetime.min.time(), tzinfo=timezone.utc)
            - moment
        ) / timedelta(days=1)
        if not (-MAX_WOCHEN_NACH * 7 <= vorlauf_tage <= MAX_WOCHEN_VOR * 7):
            continue
        posts_ausgewertet += 1
        vorlauf_by_title.setdefault(title.id, []).append(
            {"vorlauf_tage": vorlauf_tage, "channel": channel, "release": release}
        )

    # Duenne Titel raus — ein Einzel-Post ist kein Kampagnen-Muster.
    vorlauf_by_title = {
        tid: rows for tid, rows in vorlauf_by_title.items() if len(rows) >= min_posts
    }

    kurve_counts: dict[int, int] = {}
    kanal_rows: dict = {}
    titel_rows: list[dict] = []
    for tid, rows in vorlauf_by_title.items():
        title = titles[tid]
        vorlaeufe = [r["vorlauf_tage"] for r in rows]
        for r in rows:
            woche = int(r["vorlauf_tage"] // 7)
            kurve_counts[woche] = kurve_counts.get(woche, 0) + 1
            eintrag = kanal_rows.setdefault(
                r["channel"].id,
                {
                    "handle": r["channel"].handle or r["channel"].name,
                    "market": r["channel"].market.value,
                    # Ein Handle existiert je Plattform als eigener
                    # Kanal-Datensatz (@warnerbros auf Instagram UND
                    # TikTok). Ohne diese Spalte sehen die Zeilen in der
                    # Tabelle wie Doubletten aus — Wolfs Befund bei der
                    # Staging-Abnahme am 25.08.2026.
                    "platform": r["channel"].platform,
                    "posts": 0,
                    "titel_ids": set(),
                    "erste_vorlaeufe": {},
                },
            )
            eintrag["posts"] += 1
            eintrag["titel_ids"].add(tid)
            bisher = eintrag["erste_vorlaeufe"].get(tid)
            if bisher is None or r["vorlauf_tage"] > bisher:
                eintrag["erste_vorlaeufe"][tid] = r["vorlauf_tage"]
        titel_rows.append({
            "title_original": title.title_original,
            "release": min(r["release"] for r in rows).isoformat(),
            "posts": len(rows),
            "kampagnenstart_vorlauf_tage": round(max(vorlaeufe)),
            "letzter_post_vorlauf_tage": round(min(vorlaeufe)),
            "kanaele": len({r["channel"].id for r in rows}),
        })

    titel_rows.sort(key=lambda t: -t["posts"])

    kanaele = [
        {
            "handle": e["handle"],
            "market": e["market"],
            "platform": e["platform"],
            "posts": e["posts"],
            "titel": len(e["titel_ids"]),
            # Kampagnenstart je Titel = fruehester Post; der Kanal-Wert
            # ist der Median dieser Starts — "so frueh faengt dieser
            # Kanal typischerweise an".
            "median_kampagnenstart_tage": round(
                median(e["erste_vorlaeufe"].values())
            ),
        }
        for e in kanal_rows.values()
    ]
    kanaele.sort(key=lambda k: -k["median_kampagnenstart_tage"])

    ergebnis = {
        "titel_ausgewertet": len(vorlauf_by_title),
        "posts_ausgewertet": posts_ausgewertet,
        "kurve": [
            {"wochen_vor_release": woche, "posts": kurve_counts[woche]}
            for woche in sorted(kurve_counts, reverse=True)
        ],
        "kanaele": kanaele,
        "titel": titel_rows[:max_titles],
        "note": None,
    }
    logger.info(
        "campaign_timing.computed titel=%s posts=%s kanaele=%s",
        ergebnis["titel_ausgewertet"], posts_ausgewertet, len(kanaele),
    )
    return ergebnis
