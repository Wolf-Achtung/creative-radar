"""Fehlende Titel bedarfsgetrieben aus TMDb nachladen (24.08.2026).

Wolfs Befund am Abend des 24.08.: Die KI-Prüfung nannte in jeder zweiten
Notiz ein Werk, das der Katalog nicht kennt — „Desperate Housewives",
„SAKAMOTO DAYS", „The Fox", „Steckerlfisch Fiasko", „Lanterns",
„Cadet Kelly". Von 58 geprüften Vorschlägen ließ sich genau **einer**
automatisch zuordnen; 43 mussten von Hand angelegt werden.

Die Ursache liegt nicht im Matcher, sondern im Zuschnitt des Katalogs:
``title_sync`` deckt sechs Produktions-Studios (``PAIR_COMPANY_SETS``)
und drei Streamer-Networks (``STREAMER_NETWORK_SETS``) ab. Beobachtet
werden aber über 200 Kanäle — HBO Max, Hulu, Disney Channel, NatGeo,
Sky, MGM+, dazu Dutzende Verleiher wie Constantin, Pandora, Arrow oder
Signature. Deren Titel KÖNNEN nicht im Katalog sein.

Warum nicht einfach mehr Networks eintragen
===========================================

Das wäre der naheliegende Weg und ist der falsche. Der Katalog ist am
22.08. um 8.940 Streamer-Serien gewachsen — und genau dieser Zuwachs
hat am 24.08. die 83 Autopilot-Fehlzuordnungen ausgelöst, weil er
hunderte generische Ein-Wort-Titel mitbrachte („Driven", „Focus",
„Classified"). Jeder weitere Block hebt dieses Risiko erneut.

Dieser Weg lädt stattdessen **bedarfsgetrieben** nach: Ein Titel kommt
in den Katalog, wenn ein beobachteter Post ihn nachweislich bewirbt.
Der Katalog wächst dann entlang dessen, was tatsächlich läuft, statt
entlang dessen, was TMDb hergibt.

Drei Wächter, alle im Code
==========================

1. **Beleg im Post-Text.** Der von der KI genannte Name muss wörtlich in
   Caption oder Bildtext stehen (``_im_text_belegt`` aus dem Assist).
   Eine bloße Behauptung legt keinen Titel an.
2. **Eindeutiger TMDb-Treffer.** ``_exakte_aktuelle_treffer`` verlangt
   Namensgleichheit UND ein Datum im Aktualitätsfenster; bei null oder
   mehreren Treffern passiert nichts. Identisch zum bewährten Pfad in
   ``enrich_titles_without_tmdb_id``.
3. **Nicht schon vorhanden.** Der Exakt-Lookup des Autopiloten fängt
   Titel ab, die zwischenzeitlich angelegt wurden.

Erst wenn alle drei zutreffen, entsteht ein Titel — und das Asset wird
ihm zugeordnet, wie beim manuellen „Titel anlegen + zuordnen"-Klick.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.entities import (
    Asset,
    CandidateStatus,
    Post,
    Title,
    TitleCandidate,
    utc_now,
)
from app.services.candidate_autopilot import _build_exact_title_lookup, _normalize
from app.services.candidate_llm_assist import _im_text_belegt
from app.services.match_key import slugify_match_key
from app.services.title_candidates import resolve_open_candidates_for_asset
from app.services.title_sync import (
    _ensure_alias_keywords,
    _exakte_aktuelle_treffer,
    _MANUAL_ENRICH_MAX_AGE_DAYS,
)
from app.services.tmdb_client import TMDbClient

logger = logging.getLogger(__name__)

# Marker, den der LLM-Assist in die Notiz schreibt, wenn er ein Werk
# erkannt hat, das der Katalog nicht kennt (candidate_llm_assist.py).
NICHT_IM_KATALOG = "(nicht im Katalog)"

# Ein Lauf je Klick/Cron-Stage. Jeder Kandidat kostet bis zu zwei
# TMDb-Suchen (Film, dann Serie) — bei 20 also hoechstens 40 Aufrufe,
# weit unter jedem Rate-Limit und in Sekunden erledigt.
DEFAULT_MAX_KANDIDATEN = 20


@dataclass
class NachladeSummary:
    geprueft: int = 0
    angelegt: int = 0
    zugeordnet: int = 0
    schon_vorhanden: int = 0
    nicht_belegt: int = 0
    tmdb_unklar: int = 0
    katalog_mehrdeutig: int = 0
    fehler: int = 0
    offen_danach: int = 0
    angelegte_titel: list[str] = field(default_factory=list)
    vorschau: bool = True

    def to_dict(self) -> dict:
        return {
            "geprueft": self.geprueft,
            "angelegt": self.angelegt,
            "zugeordnet": self.zugeordnet,
            "schon_vorhanden": self.schon_vorhanden,
            "nicht_belegt": self.nicht_belegt,
            "tmdb_unklar": self.tmdb_unklar,
            "katalog_mehrdeutig": self.katalog_mehrdeutig,
            "fehler": self.fehler,
            "offen_danach": self.offen_danach,
            "angelegte_titel": self.angelegte_titel[:20],
            # Ohne dieses Feld kann das Frontend eine Vorschau nicht von
            # einem echten Lauf unterscheiden — dieselben Zahlen, zwei
            # voellig verschiedene Bedeutungen.
            "vorschau": self.vorschau,
        }


def _kandidaten_mit_luecke(session: Session) -> list[TitleCandidate]:
    """Offene Kandidaten, denen die KI ein nicht katalogisiertes Werk
    zugeschrieben hat. ``suggested_title`` traegt seit #428 den von der
    KI genannten Namen — nicht mehr den Matcher-Fehlgriff."""
    return [
        c
        for c in session.exec(
            select(TitleCandidate).where(
                TitleCandidate.status == CandidateStatus.OPEN
            )
        ).all()
        if NICHT_IM_KATALOG in (c.llm_note or "")
    ]


async def lade_fehlende_titel_nach(
    session: Session,
    *,
    client: TMDbClient | None = None,
    max_kandidaten: int = DEFAULT_MAX_KANDIDATEN,
    anwenden: bool = False,
) -> NachladeSummary:
    """Legt Titel an, die ein beobachteter Post nachweislich bewirbt.

    ``anwenden=False`` (Vorgabe) ist eine **Vorschau**: derselbe Code
    laeuft vollstaendig durch — Text-Beleg, TMDb-Abfrage, Anlage,
    Zuordnung — und wird am Ende zurueckgerollt statt festgeschrieben.
    Die Vorschau zeigt damit nicht, was der Code tun *sollte*, sondern
    was er *taete*; ein Wunschzettel neben dem echten Pfad waere
    wertlos, sobald die beiden auseinanderlaufen.

    Der Grund fuer die Vorschau (25.08.2026): Dieses Feature ist in
    Staging nicht erprobbar. Seine Eingabe ist der Marker
    ``(nicht im Katalog)``, den nur die KI-Pruefung setzt — und die
    braucht den Anthropic-Key, den Staging bewusst nicht hat. Wolfs
    Freigabe-Modell (Staging zuerst) greift hier also ins Leere. Die
    Vorschau ist der Ersatz: erst sehen, was passieren wuerde, dann
    entscheiden.
    """
    summary = NachladeSummary(vorschau=not anwenden)
    kandidaten = _kandidaten_mit_luecke(session)
    summary.offen_danach = max(len(kandidaten) - max_kandidaten, 0)
    if not kandidaten:
        return summary

    client = client or TMDbClient()
    lookup = _build_exact_title_lookup(session)
    aeltestes = datetime.now(timezone.utc).date() - timedelta(
        days=_MANUAL_ENRICH_MAX_AGE_DAYS
    )

    for candidate in kandidaten[:max_kandidaten]:
        summary.geprueft += 1
        name = (candidate.suggested_title or "").strip()
        if not name:
            continue

        asset = session.get(Asset, candidate.asset_id)
        if asset is None or asset.title_id is not None:
            continue

        # Waechter 1: steht der Name woertlich im Post?
        post = session.get(Post, asset.post_id) if asset.post_id else None
        volltext = f"{post.caption if post else ''} {asset.ocr_text or ''}"
        if not _im_text_belegt(name, volltext):
            summary.nicht_belegt += 1
            continue

        # Waechter 3 (vorgezogen, spart die TMDb-Aufrufe): steht er
        # inzwischen doch im Katalog? Dann nur zuordnen.
        #
        # ``_build_exact_title_lookup`` kennt DREI Zustaende, und die
        # Unterscheidung ist hier sicherheitsrelevant:
        #   Schluessel fehlt        -> wirklich nicht im Katalog
        #   Schluessel da, Wert da  -> eindeutig, zuordnen
        #   Schluessel da, None     -> MEHRDEUTIG (zwei aktive Titel
        #                              gleichen Normalnamens)
        # Ein ``lookup.get(...)`` wirft die beiden letzten zusammen. Im
        # KI-Assist ist das harmlos (kein eindeutiger Treffer -> nicht
        # zuordnen). Hier kippt es ins Gegenteil: der mehrdeutige Fall
        # fiele in den TMDb-Pfad und legte NOCH einen Titel gleichen
        # Namens an — was den Namen noch mehrdeutiger macht. Eine
        # Doubletten-Schleife, jede Runde eine Zeile mehr. Genau das
        # passierte am 25.08.2026 in Production: dieselben zwei Titel
        # standen nach dem Anlegen in der naechsten Vorschau wieder
        # als Neuanlage.
        schluessel = _normalize(name)
        if schluessel in lookup:
            vorhanden = lookup[schluessel]
            if vorhanden is None:
                summary.katalog_mehrdeutig += 1
                logger.info(
                    "katalog-nachladen mehrdeutig name=%s asset=%s", name, asset.id
                )
                continue
            _zuordnen(session, asset, candidate, vorhanden)
            summary.schon_vorhanden += 1
            summary.zugeordnet += 1
            continue

        # Waechter 2: kennt TMDb den Namen eindeutig?
        try:
            filme = await client.search_movies(name)
            passende = _exakte_aktuelle_treffer(
                [name], filme, is_series=False, aeltestes=aeltestes
            )
            is_series = False
            if not passende:
                serien = await client.search_series(name)
                passende = _exakte_aktuelle_treffer(
                    [name], serien, is_series=True, aeltestes=aeltestes
                )
                is_series = True
        except Exception:  # noqa: BLE001 — ein zaeher Titel stoppt nicht den Lauf
            logger.exception(
                "katalog-nachladen tmdb-suche fehlgeschlagen name=%s", name
            )
            summary.fehler += 1
            continue

        if len(passende) != 1:
            summary.tmdb_unklar += 1
            continue

        normalized = (
            client.normalize_tmdb_series(passende[0])
            if is_series
            else client.normalize_tmdb_movie(passende[0])
        )
        titel = Title(
            title_original=normalized.get("title_original") or name,
            title_local=normalized.get("title_local"),
            tmdb_id=normalized.get("tmdb_id"),
            content_type="Series" if is_series else "Movie",
            genres=list(normalized.get("genres") or []),
            source="TMDb",
            active=True,
        )
        session.add(titel)
        session.flush()
        session.refresh(titel)
        _ensure_alias_keywords(session, titel, normalized.get("aliases"))
        summary.angelegt += 1
        summary.angelegte_titel.append(titel.title_original)
        # Der frische Titel gehoert sofort in den Lookup: zwei Posts
        # derselben Serie in einem Lauf wuerden sonst zwei Zeilen anlegen.
        lookup[_normalize(titel.title_original)] = titel

        _zuordnen(session, asset, candidate, titel)
        summary.zugeordnet += 1
        logger.info(
            "katalog-nachladen angelegt titel=%s tmdb_id=%s asset=%s",
            titel.title_original, titel.tmdb_id, asset.id,
        )

    if anwenden:
        session.commit()
    else:
        # Der einzige Unterschied zwischen Vorschau und Ernstfall. Alles
        # davor ist identisch geloffen, inklusive der TMDb-Abfragen —
        # deshalb stimmt die Vorschau auch dann, wenn TMDb heute anders
        # antwortet als gestern.
        session.rollback()
    logger.info("katalog-nachladen done %s", summary.to_dict())
    return summary


def _zuordnen(
    session: Session, asset: Asset, candidate: TitleCandidate, titel: Title
) -> None:
    """Identisch zum manuellen „Titel anlegen + zuordnen"-Klick."""
    asset.title_id = titel.id
    asset.de_us_match_key = slugify_match_key(
        titel.franchise or titel.title_original
    )
    asset.updated_at = utc_now()
    session.add(asset)
    candidate.llm_note = (
        f"Katalog ergaenzt: '{titel.title_original}' aus TMDb angelegt "
        "und zugeordnet."
    )[:300]
    session.add(candidate)
    resolve_open_candidates_for_asset(session, asset.id, commit=False)
