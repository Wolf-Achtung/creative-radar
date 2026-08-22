from __future__ import annotations

import logging
import unicodedata
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.entities import Market, Title, TitleKeyword, TitleSyncRun
from app.services.tmdb_client import TMDbClient


# --- Studio company-axis sets (Sprint Studio-Title-Sync, 2026-06-16) ----------
# Kuratierte, Wolf-verifizierte TMDb-Company-ID-Sets je Produktionsstudio-Pair.
# Jede ID wurde via scripts/diag_resolve_company_ids gegen einen bekannten Titel
# bestaetigt (hit=True). Pipe-OR-Semantik auf dem TMDb-``with_companies``-Filter.
# Co-Producer sind bewusst NICHT enthalten (sie zoegen Fremdstoff in den Katalog).
#
# Scope: ausschliesslich die 6 PRODUKTIONSSTUDIOS. Die 3 Streamer (netflix,
# primevideo, paramountplus) fehlen absichtlich — sie bekommen kuratierte
# Originals-Sets in ihrem eigenen Sprint (Briefing §7). NICHT hier ergaenzen.
#
# Eine Quelle: dieses Dict ist der einzige Ort, an dem die Pair->Company-IDs
# definiert sind. Kuratierbar ohne weiteren Code-Change.
# Sicherheits-/Performance-Audit 2026-07-06: der Company-Axis-Sync trifft pro
# Company-Set/Markt/Medientyp den vollen TMDb-Katalog (siehe Docstring unten)
# — bei grossen Studios laufen das ueber 100 Discover-Seiten. Vorher committete
# ``_upsert_normalized_title`` nach JEDEM einzelnen Titel (2 Commits/Titel
# inkl. Alias-Schreibvorgang) — bei mehreren tausend Titeln pro Lauf tausende
# einzelne, WAL-fsync'te Postgres-Commits, der dominante Faktor an der
# beobachteten ~28-Minuten-Laufzeit (title_sync.complete duration_seconds=1671.6,
# 06.07.2026) und mutmasslich Hauptursache der frueheren 5,4h/5,8-Tage-Haenger
# (PR #287) bei ungünstiger DB-Latenz. Batch-Commit alle
# ``_TITLE_SYNC_COMMIT_BATCH_SIZE`` Titel reduziert die Commit-Anzahl um
# denselben Faktor, ohne Fetch-/Match-Semantik zu aendern.
_TITLE_SYNC_COMMIT_BATCH_SIZE = 100

PAIR_COMPANY_SETS: dict[str, list[int]] = {
    "disney": [2, 3, 420, 1, 127928, 3475, 6125],
    "sonypictures": [34, 5, 559],
    "warnerbros": [174, 12],
    "universalpictures": [33, 6704, 521],
    "paramountpictures": [4],
    "lionsgate": [1632],
}


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _upsert_normalized_title(
    session: Session,
    normalized: dict,
    market: str,
    *,
    is_series: bool,
) -> None:
    """Upsert one normalized TMDb item (movie OR series) into ``title``.

    Variante A (Movie/TV tmdb_id-Namespace-Kollision): TMDb movie- und
    tv-IDs sind getrennte Namespaces — dieselbe Ganzzahl kann ein Film und
    eine Serie sein. Der Existenz-Lookup wird deshalb nach ``content_type``
    gescoped, sodass Film und Serie mit gleicher ``tmdb_id`` als zwei Rows
    koexistieren statt sich gegenseitig zu überschreiben. Keine Migration —
    ``content_type`` ist eine bestehende Spalte.

    Der Movie-Pfad ist im Mapping unverändert; einzige Movie-seitige
    Änderung ist der ``content_type != 'Series'``-Scope am Lookup.
    """
    tmdb_id = normalized.get("tmdb_id")
    type_filter = (
        Title.content_type == "Series" if is_series else Title.content_type != "Series"
    )

    title = session.exec(
        select(Title).where(Title.tmdb_id == tmdb_id, type_filter)
    ).first()
    if not title:
        release_year = normalized.get("release_year")
        title_original = normalized.get("title_original")
        if release_year and title_original:
            candidates = session.exec(
                select(Title).where(Title.title_original == title_original, type_filter)
            ).all()
            title = next(
                (
                    item
                    for item in candidates
                    if (item.release_date_de and item.release_date_de.year == release_year)
                    or (item.release_date_us and item.release_date_us.year == release_year)
                ),
                None,
            )

    if title is None:
        title = Title(
            tmdb_id=tmdb_id,
            title_original=normalized.get("title_original") or normalized.get("title_local") or f"TMDb-{tmdb_id}",
            title_local=normalized.get("title_local"),
            source="TMDb",
            market_relevance=Market.MIXED,
        )
        if is_series:
            title.content_type = "Series"

    title.tmdb_id = tmdb_id
    title.source = title.source or "TMDb"
    title.aliases = sorted(set((title.aliases or []) + (normalized.get("aliases") or [])))
    # Genres ersetzen statt mischen: TMDb ist die einzige Quelle, und die
    # Reihenfolge (erstes = primaeres Genre) muss erhalten bleiben — ein
    # sorted-set-Merge wie bei den Aliassen wuerde sie zerstoeren. Eine
    # leere Antwort ueberschreibt nichts Vorhandenes.
    if normalized.get("genres"):
        title.genres = list(normalized["genres"])
    if normalized.get("title_local"):
        title.title_local = normalized["title_local"]

    rd = normalized.get("release_date")
    if rd:
        parsed = date.fromisoformat(rd)
        if market == "DE":
            title.release_date_de = parsed
        elif market == "US":
            title.release_date_us = parsed

    if market in ["DE", "US"] and title.market_relevance in [Market.UNKNOWN, Market.INT]:
        title.market_relevance = Market.MIXED

    session.add(title)
    session.flush()
    session.refresh(title)

    _ensure_alias_keywords(session, title, normalized.get("aliases"))


def _ensure_alias_keywords(session: Session, title: Title, aliases: list[str] | None) -> None:
    """Alias-Keyword-Rows idempotent pflegen — genutzt vom Discover-Upsert
    und der Anreicherung manuell angelegter Titel (22.08.2026)."""
    for alias in aliases or []:
        if _norm(alias) == _norm(title.title_original):
            continue
        existing_kw = session.exec(
            select(TitleKeyword).where(
                TitleKeyword.title_id == title.id,
                TitleKeyword.keyword == alias,
                TitleKeyword.keyword_type == "alias",
            )
        ).first()
        if not existing_kw:
            session.add(TitleKeyword(title_id=title.id, keyword=alias, keyword_type="alias", active=True))
    session.flush()


async def sync_titles_from_tmdb(
    session: Session,
    markets: list[str] | None = None,
    pairs: list[str] | None = None,
) -> dict:
    """Company-axis title sync (Sprint Studio-Title-Sync, 2026-06-16).

    Replaces the former popularity-window discover (``[-8w, +24w]`` +
    ``with_release_type`` + 3-page cap) with ``with_companies``-discover per
    production-studio pair. The company set is the selector, so the FULL studio
    slate — incl. back-catalogue and returning series seasons — enters the
    catalogue regardless of release date. Diagnose: the window+cap was the root
    of the 96 % no_title_match (catalog gap).

    Scope: the 6 production-studio pairs in ``PAIR_COMPANY_SETS``. The 3
    streamers are NOT synced here (own sprint, §7) — their existing rows persist.

    Localization preserved: each pair is discovered once per market/language
    (DE→de-DE, US→en-US). The per-(media, tmdb_id, MARKET) dedup key lets BOTH
    passes upsert, so ``release_date_de`` + ``release_date_us`` populate and the
    DE-Verleihtitel lands in aliases (matcher reads aliases). The former
    cross-market dedup skipped the second market's pass entirely.
    """
    client = TMDbClient()
    markets = markets or ["DE", "US"]
    region_language = {"DE": "de-DE", "US": "en-US"}

    pair_sets = (
        PAIR_COMPANY_SETS if pairs is None
        else {k: v for k, v in PAIR_COMPANY_SETS.items() if k in pairs}
    )

    today = datetime.now(timezone.utc).date()

    fetched_count = upserted_count = deduped_count = 0
    # Dedup key (media, tmdb_id, market): media because TMDb movie/tv ID
    # namespaces overlap; market so each language/region pass still upserts
    # (both release dates + both localized titles land).
    seen_keys: set[tuple[str, int, str]] = set()

    # Company axis has no release-date window; date_from/date_to are recorded as
    # the run date so the NOT-NULL audit columns stay populated (a schema change
    # to make them nullable is out of scope for this sprint).
    run = TitleSyncRun(
        source="tmdb", markets=markets, date_from=today, date_to=today, status="running"
    )
    session.add(run)
    session.commit()

    try:
        for pair_key, company_ids in pair_sets.items():
            companies = "|".join(str(c) for c in company_ids)
            for market in markets:
                language = region_language.get(market, "en-US")

                region = TMDbClient.tmdb_region(market)
                movies = await client.discover_movies_by_company(
                    companies, language=language, region=region
                )
                for raw in movies:
                    normalized = client.normalize_tmdb_movie(raw)
                    tmdb_id = normalized.get("tmdb_id")
                    if not tmdb_id:
                        continue
                    fetched_count += 1
                    key = ("movie", tmdb_id, market)
                    if key in seen_keys:
                        deduped_count += 1
                        continue
                    seen_keys.add(key)
                    _upsert_normalized_title(session, normalized, market, is_series=False)
                    upserted_count += 1
                    if upserted_count % _TITLE_SYNC_COMMIT_BATCH_SIZE == 0:
                        session.commit()

                series = await client.discover_series_by_company(
                    companies, language=language, region=region
                )
                for raw in series:
                    normalized = client.normalize_tmdb_series(raw)
                    tmdb_id = normalized.get("tmdb_id")
                    if not tmdb_id:
                        continue
                    fetched_count += 1
                    key = ("tv", tmdb_id, market)
                    if key in seen_keys:
                        deduped_count += 1
                        continue
                    seen_keys.add(key)
                    _upsert_normalized_title(session, normalized, market, is_series=True)
                    upserted_count += 1
                    if upserted_count % _TITLE_SYNC_COMMIT_BATCH_SIZE == 0:
                        session.commit()

        run.fetched_count = fetched_count
        run.upserted_count = upserted_count
        run.deduped_count = deduped_count
        run.status = "success"
        session.add(run)
        session.commit()

        # Anreicherung manuell angelegter Titel (22.08.2026): VOR dem
        # Genre-Backfill, damit frisch verknuepfte tmdb_ids im selben
        # Lauf Genres bekommen koennen. Eigener try wie beim Backfill.
        try:
            manual_enrich = await enrich_titles_without_tmdb_id(session, client=client)
        except Exception as exc:  # noqa: BLE001 — Stage-Grenze, best effort
            logger.exception("manual-enrich fehlgeschlagen")
            manual_enrich = {"error": str(exc)[:200]}

        # Genre-Backfill (21.08.2026): der Company-Discover erreicht nur
        # die Studio-Slates — Titel, die anders in den Katalog kamen
        # (Streamer-Originals ueber Kandidaten, Alt-Rows von vor der
        # Genre-Nachruestung), behalten sonst fuer immer leere Genres.
        # Eigener try: ein Backfill-Fehler darf den gelungenen Sync
        # nicht als error verbuchen.
        try:
            genre_backfill = await backfill_missing_genres(session, client=client)
        except Exception as exc:  # noqa: BLE001 — Stage-Grenze, best effort
            logger.exception("genre-backfill fehlgeschlagen")
            genre_backfill = {"error": str(exc)[:200]}

        return {
            "markets": markets,
            "pairs": list(pair_sets.keys()),
            "axis": "company",
            "fetched_count": fetched_count,
            "upserted_count": upserted_count,
            "deduped_count": deduped_count,
            "manual_enrich": manual_enrich,
            "genre_backfill": genre_backfill,
            "run_id": str(run.id),
        }
    except Exception as exc:
        run.status = "error"
        run.error_message = str(exc)
        session.add(run)
        session.commit()
        raise


logger = logging.getLogger(__name__)

# Deckel je Lauf: der Backfill ist ein Detail-Call PRO Titel. Der
# Erstlauf raeumt den Altbestand in Schueben ab (uebrig steht im
# Ergebnis); danach fallen pro Woche nur noch die wenigen neuen
# Kandidaten-Titel an. Stueckzahl reicht hier als Grenze — anders als
# bei Vision (Vorfall 20.08.) ist ein Details-GET in ~150 ms fertig,
# 500 Stueck sind gut eine Minute.
_GENRE_BACKFILL_MAX_PER_RUN = 500


async def backfill_missing_genres(
    session: Session,
    *,
    client: TMDbClient | None = None,
    max_titles: int = _GENRE_BACKFILL_MAX_PER_RUN,
) -> dict:
    """Titel mit ``tmdb_id``, aber leerer Genre-Liste ueber die
    TMDb-Details fuellen (21.08.2026).

    Der Company-Discover pflegt nur die Studio-Slates; Streamer-
    Originals und Titel aus dem Kandidaten-Flow tragen zwar eine
    ``tmdb_id``, laufen aber durch keinen Discover — ihre Genres
    blieben nach der Nachruestung (#376) dauerhaft leer. Der erste
    Prod-Blick am 21.08. zeigte die Folge: Genre-Abdeckung 52 %,
    obwohl die Titel-Zuordnung bei 67 % lag.

    Nur LEERE Listen werden gefuellt — vorhandene Genres stammen aus
    demselben TMDb und wuerden durch einen Details-Call nur ersetzt,
    nicht verbessert; der Verzicht spart die Calls.

    Die Kandidaten-Suche laedt alle Rows mit ``tmdb_id`` und filtert in
    Python: die JSON-Spalte ``genres`` hat auf sqlite (Tests) und
    Postgres (Prod) keinen gemeinsamen Leer-Praedikat-Ausdruck, und der
    Katalog (~29k schmale Rows) ist fuer einen Hintergrund-Lauf
    problemlos ladbar.
    """
    client = client or TMDbClient()
    rows = session.exec(select(Title).where(Title.tmdb_id.is_not(None))).all()
    fehlend = [
        t for t in rows
        if not (isinstance(t.genres, list) and len(t.genres) > 0)
    ]
    gefuellt = ohne_genres = fehler = 0
    for title in fehlend[:max_titles]:
        is_series = (title.content_type or "").strip().lower() == "series"
        try:
            details = await client.get_title_details(
                title.tmdb_id, is_series=is_series
            )
        except Exception:  # noqa: BLE001 — ein toter Titel stoppt nicht den Lauf
            fehler += 1
            continue
        namen = [
            g["name"].strip()
            for g in (details.get("genres") or [])
            if isinstance(g, dict)
            and isinstance(g.get("name"), str)
            and g["name"].strip()
        ]
        if namen:
            title.genres = namen
            session.add(title)
            gefuellt += 1
            if gefuellt % _TITLE_SYNC_COMMIT_BATCH_SIZE == 0:
                session.commit()
        else:
            # TMDb kennt den Titel, fuehrt aber keine Genres — merken
            # wuerde nichts bringen, der naechste Lauf prueft erneut.
            ohne_genres += 1
    session.commit()
    ergebnis = {
        "kandidaten": len(fehlend),
        "gefuellt": gefuellt,
        "ohne_genres": ohne_genres,
        "fehler": fehler,
        "uebrig": max(len(fehlend) - max_titles, 0),
    }
    logger.info("genre_backfill.complete %s", ergebnis)
    return ergebnis


# --- Anreicherung manuell angelegter Titel (22.08.2026) -----------------------
# Die Entscheidungs-Queue legt Titel per Klick an ("Titel anlegen +
# zuordnen") — ohne tmdb_id. Ohne tmdb_id: keine Genres (der Backfill
# oben greift nur MIT tmdb_id), keine Alias-Namen fuer den Auto-Matcher,
# keine Termine. Diese Stage sucht solche Titel per TMDb-Namens-Suche
# und verknuepft sie — bewusst KONSERVATIV:
#
# - Verknuepft wird nur bei GENAU EINEM exakten Namens-Treffer
#   (akzent-tolerant: "Beware Boiuna" trifft "Beware Boiúna"). Kurze
#   Allerweltsnamen ("Daniel") liefern viele exakte Treffer — die
#   bleiben unangetastet, ein falsch verknuepfter Film waere schlimmer
#   als keiner.
# - Der Treffer muss ein aktuelles Datum tragen (juenger als
#   ~2 Jahre oder in der Zukunft). Das Radar beobachtet laufende
#   Kampagnen; ein Namensvetter von 1983 ist praktisch immer falsch.
# - Kein Treffer / mehrdeutig -> Zaehler, naechster Lauf prueft erneut.
_MANUAL_ENRICH_MAX_PER_RUN = 200
_MANUAL_ENRICH_MAX_AGE_DAYS = 730


def _norm_suche(value: str | None) -> str:
    """Akzent-/Umlaut-tolerante Normalisierung (Gegenstueck zur
    Frontend-Titel-Suche): NFD + kombinierende Zeichen entfernen."""
    text = unicodedata.normalize("NFD", (value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


def _exakte_aktuelle_treffer(
    eigene_namen: list[str | None],
    results: list[dict],
    *,
    is_series: bool,
    aeltestes: date,
) -> list[dict]:
    ziele = {_norm_suche(name) for name in eigene_namen if name}
    ziele.discard("")
    name_felder = ("name", "original_name") if is_series else ("title", "original_title")
    datum_feld = "first_air_date" if is_series else "release_date"
    treffer = []
    for result in results:
        namen = [result.get(feld) for feld in name_felder]
        if not any(_norm_suche(name) in ziele for name in namen if name):
            continue
        raw = result.get(datum_feld)
        try:
            datum = date.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            datum = None
        # Ohne Datum keine Aktualitaets-Aussage -> nicht verknuepfen.
        if datum is None or datum < aeltestes:
            continue
        treffer.append(result)
    return treffer


async def enrich_titles_without_tmdb_id(
    session: Session,
    *,
    client: TMDbClient | None = None,
    max_titles: int = _MANUAL_ENRICH_MAX_PER_RUN,
) -> dict:
    """Aktive Titel ohne ``tmdb_id`` per Namens-Suche verknuepfen und
    mit Genres, Aliases und lokalisiertem Titel anreichern."""
    client = client or TMDbClient()
    rows = session.exec(
        select(Title).where(Title.tmdb_id.is_(None), Title.active == True)  # noqa: E712
    ).all()
    aeltestes = datetime.now(timezone.utc).date() - timedelta(days=_MANUAL_ENRICH_MAX_AGE_DAYS)
    verknuepft = unklar = fehler = 0
    for title in rows[:max_titles]:
        eigene_namen = [title.title_original, title.title_local]
        try:
            filme = await client.search_movies(title.title_original)
            passende = _exakte_aktuelle_treffer(
                eigene_namen, filme, is_series=False, aeltestes=aeltestes
            )
            is_series = False
            if not passende:
                serien = await client.search_series(title.title_original)
                passende = _exakte_aktuelle_treffer(
                    eigene_namen, serien, is_series=True, aeltestes=aeltestes
                )
                is_series = True
        except Exception:  # noqa: BLE001 — ein zaeher Titel stoppt nicht den Lauf
            fehler += 1
            continue

        if len(passende) != 1:
            unklar += 1
            continue

        normalized = (
            client.normalize_tmdb_series(passende[0])
            if is_series
            else client.normalize_tmdb_movie(passende[0])
        )
        title.tmdb_id = normalized.get("tmdb_id")
        title.source = title.source or "TMDb"
        title.aliases = sorted(set((title.aliases or []) + (normalized.get("aliases") or [])))
        if is_series:
            title.content_type = "Series"
        if normalized.get("genres") and not title.genres:
            title.genres = list(normalized["genres"])
        if normalized.get("title_local") and not title.title_local:
            title.title_local = normalized["title_local"]
        session.add(title)
        session.flush()
        session.refresh(title)
        _ensure_alias_keywords(session, title, normalized.get("aliases"))
        verknuepft += 1
        if verknuepft % _TITLE_SYNC_COMMIT_BATCH_SIZE == 0:
            session.commit()
    session.commit()
    ergebnis = {
        "kandidaten": len(rows),
        "verknuepft": verknuepft,
        "unklar": unklar,
        "fehler": fehler,
        "uebrig": max(len(rows) - max_titles, 0),
    }
    logger.info("manual_enrich.complete %s", ergebnis)
    return ergebnis
