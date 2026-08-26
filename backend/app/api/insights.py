import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from sqlmodel import Session, select

from app.admin_session import require_admin_session
from app.database import get_session
from app.services.rate_limit import rate_limit
from app.services.usage_log import log_usage
from app.user_session import request_user_email

logger = logging.getLogger(__name__)
from app.models.entities import (
    Asset,
    Channel,
    ChannelSegment,
    InsightReport as InsightReportRow,
    Market,
    PatternBriefing,
    Post,
    SegmentRoundup,
    Title,
)
from app.schemas.insights import (
    ForecastResponse,
    InsightReport,
    MarketForecast,
    MarketTimelinePoint,
    MarketTimelineResponse,
    PairInfo,
    PairsResponse,
    SegmentRoundupListResponse,
    SegmentRoundupLLMReport,
    SegmentRoundupSummary,
    TimelineWeek,
    TitleMarketPosts,
    TitlePlatformPosts,
    TitlePostRef,
    TitlePostsResponse,
)
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    AnthropicRateLimitError,
)
from app.services.insight_engine import (
    INSIGHT_FREQUENCY_LABEL,
    MARKETS_DISPLAY_ORDER,
    PAIRS,
    _platforms_dict_for,
    compute_breakout_feed,
    generate_and_persist_report,
    generate_weekly_report,
    last_completed_iso_week_anchor,
)
from app.core.feature_flags import (
    is_post_check_enabled,
    is_referenz_suche_enabled,
    is_trailer_intelligence_enabled,
)
from app.services.forecast import generate_er_forecast
from app.services.insights import build_overview
from app.services.post_check import (
    FORMAT_WERTE,
    TON_WERTE,
    pruefe_post,
)
from app.services.recommendation_snapshot import (
    annotiere_bestaendigkeit,
    compute_bewaehrung,
)
from app.services.referenz_suche import suche_referenzen
from app.services.trailer_patterns import (
    TREND_WINDOW_SHIFT_DAYS,
    apply_weekly_trend,
    build_lift_context,
    compute_trailer_patterns,
    posts_for_cell,
)
from app.services.market_timeline import compute_market_timeline, pair_handles

router = APIRouter(prefix="/api/insights", tags=["insights"])

# Separate router so the URL is ``/api/pairs`` instead of nested under
# ``/api/insights``. Same module to keep the PAIRS import single-source.
pairs_router = APIRouter(prefix="/api", tags=["pairs"])

# Roundup-Read-Router (Master-Plan-Schritt-3b). Eigener APIRouter analog
# zu ``pairs_router``, damit ``/api/roundups/latest`` flach unter ``/api``
# liegt — spiegelt das Pair-Pattern und haelt die URL stabil, falls der
# insights-Prefix sich spaeter aendert. Public (kein Auth-Dependency,
# Wolf-Festlegung 26.05.); der teure Generier-Pfad
# ``POST /api/admin/roundups/generate`` bleibt unveraendert hinter
# Bearer-Auth.
roundups_router = APIRouter(prefix="/api", tags=["roundups"])


def _enabled_pair_keys() -> list[str]:
    return sorted(k for k, v in PAIRS.items() if v.get("enabled", True))


def _markets_for_pair(pair_def: dict) -> list[str]:
    """Return the pair's surface market codes in DE → US → UK display order.

    Primary source: the explicit ``markets`` field on the PAIRS entry —
    der kuratierte Satz der Maerkte, die der LLM-Brief auch wirklich
    abdeckt. Stand 27.05.2026: alle acht Major-Pairs decken DE+US+UK ab
    (UK-B1 hat die UK-Sektion automatisch in den Brief eingefuegt + die
    UK-Channel-Integration hat die Pools verifiziert), Lionsgate ist
    US+UK-only (kein DE-Auftritt, Vertrieb via Leonine/Studiocanal).

    Mechanik bleibt: ``markets`` kann bewusst enger als der Channel-Pool
    gehalten werden, falls ein Markt zwar Channels hat aber der Brief ihn
    nicht surface-en soll. Aktuell nicht genutzt — alle markets-Werte
    decken sich mit den Pool-Maerkten — aber als Override-Hebel verfuegbar.

    Fallback: wenn ein Pair kein ``markets`` traegt, leiten wir aus dem
    Channel-Pool-Union ab.
    """
    explicit_markets = pair_def.get("markets")
    if explicit_markets:
        return [code for code in MARKETS_DISPLAY_ORDER if code in explicit_markets]

    seen: set[str] = set()
    platforms = pair_def.get("platforms") or {}
    if platforms:
        for channel_list in platforms.values():
            for channel in channel_list:
                market = channel.get("market")
                if market:
                    seen.add(market)
    else:
        for channel in pair_def.get("channels", []) or []:
            market = channel.get("market")
            if market:
                seen.add(market)
    return [code for code in MARKETS_DISPLAY_ORDER if code in seen]


def _iso_week_start_utc(now: datetime | None = None) -> datetime:
    """Sprint 28.05.2026 (Studio-Kennzahl) — Montag 00:00 UTC der
    aktuellen ISO-Woche. ``now.weekday()`` ist 0 fuer Montag, also
    schneiden wir den Tages-Offset und die Uhrzeit weg. Bewusst
    NICHT identisch zum 30-Tage-Rolling-Window aus ``aggregate_pair``
    (das Brief-Fenster ist roll, die Kachel-Kennzahl ist KW-Anker)
    — siehe Briefing-Konvention "diese Woche" auf der Kachel."""
    now = now or datetime.now(timezone.utc)
    week_start_date = (now - timedelta(days=now.weekday())).date()
    return datetime(
        week_start_date.year, week_start_date.month, week_start_date.day,
        tzinfo=timezone.utc,
    )


def _handles_for_pair(pair_def: dict) -> list[str]:
    """Alle Channel-Handles eines Pairs ueber alle Plattformen, lowercased
    fuer den case-insensitive DB-Lookup. Delegiert an die geteilte
    ``services.market_timeline.pair_handles`` — eine Quelle der Wahrheit,
    auch vom Timeline-/Forecast-Pfad genutzt."""
    return pair_handles(pair_def)


@pairs_router.get("/pairs", response_model=PairsResponse)
def pairs(request: Request, session: Session = Depends(get_session)) -> PairsResponse:
    # Sprint User-Login 2026-07: /api/pairs ist der eine Call, den jede
    # Startseiten-Ansicht macht — als ``landing_view`` der Proxy fuer
    # "Startseite geoeffnet". No-Op ohne eingeloggten User (Auth aus,
    # Admin-Session, Rollout-Phase).
    log_usage(request_user_email(request), "landing_view", {})
    """List enabled pairs with Frontend-ready metadata.

    Drives the landing-page card grid. Returns only ``enabled=True`` pairs
    in PAIRS-dict insertion order (Python 3.7+ guarantees order on dict
    iteration). Markets are emitted in fixed DE → US → UK order.

    Sprint 28.05.2026 (Studio-Kennzahl): liefert zusaetzlich pro Pair
    eine Live-Kennzahl (``posts_count_completed_week`` + KW-Kennung
    ``iso_week``/``iso_year``) und den Timestamp des juengsten
    persistierten Briefs (``last_generated_at``). Drei Aggregat-Queries
    fuer ALLE Pairs zusammen — kein N+1:

    1. ``SELECT Channel.handle, Channel.id WHERE handle IN (all-handles)``
       — Handle-zu-Channel-ID-Map fuer alle Pair-Channels zusammen.
    2. ``SELECT Post.channel_id, COUNT(*) WHERE window-filter
       GROUP BY channel_id`` — Posts pro Channel im KW-Fenster.
    3. ``SELECT InsightReport.pair_key, MAX(generated_at) GROUP BY pair_key``
       — juengster Brief pro Pair.

    Window-Filter spiegelt das Pattern aus #190
    (``_channel_stats``-Window + ``_post_age_reference``):
    ``published_at >= ws OR (published_at IS NULL AND detected_at >= ws)``.
    So zaehlt die Kachel-Kennzahl identisch zum Brief-Inhalt.

    Sprint Studio-Kachel-Vorwoche (2026-06-22): die Kennzahl zaehlt jetzt
    die ABGESCHLOSSENE ISO-Woche (KW-1), beidseitig gebounded, statt der
    laufenden Woche. Anker ist ``last_completed_iso_week_anchor()`` —
    dieselbe kanonische Quelle, die Brief-Detailseite und Segment-Roundups
    nutzen. Damit laufen Kachel-Counter und Brief auch an Nicht-Montagen
    (Sonntag, manuelle Reruns) auf dasselbe Wochenfenster, und die Kachel
    zeigt nicht mehr dauerhaft 0/1 zwischen den Montags-Cron-Laeufen.
    """
    # Beide Fenster-Grenzen aus EINEM Anker: ``last_completed_iso_week_anchor``
    # liefert eine Zeit innerhalb der KW-1, ``_iso_week_start_utc`` floort sie
    # auf Montag 00:00 dieser Woche. ``+7 Tage`` ist das exklusive Ende
    # (Montag 00:00 der laufenden Woche).
    completed_anchor = last_completed_iso_week_anchor()
    week_start = _iso_week_start_utc(completed_anchor)
    week_end = week_start + timedelta(days=7)
    completed_iso_year, completed_iso_week, _ = week_start.isocalendar()

    # Bauteil 1: alle Pair-Channel-Handles sammeln, eine Channel-Lookup-
    # Query, handle→channel_id-Map.
    enabled_pairs = [
        (key, pdef) for key, pdef in PAIRS.items() if pdef.get("enabled", False)
    ]
    all_handles = sorted({
        h for _, pdef in enabled_pairs for h in _handles_for_pair(pdef)
    })
    channels_by_handle: dict[str, list[int]] = defaultdict(list)
    if all_handles:
        # case-insensitive: der Channel.handle in der DB kann
        # case-different gespeichert sein. PAIRS-Handles sind lowercased
        # in _handles_for_pair, also vergleichen wir gegen lower(handle).
        rows = session.exec(
            select(Channel.id, Channel.handle).where(
                sa.func.lower(Channel.handle).in_(all_handles)
            )
        ).all()
        for cid, chandle in rows:
            channels_by_handle[chandle.lower()].append(cid)

    # Bauteil 2: Posts-Aggregat pro channel_id im KW-Fenster, eine Query.
    all_channel_ids = [
        cid for cids in channels_by_handle.values() for cid in cids
    ]
    posts_per_channel: dict = {}
    if all_channel_ids:
        post_rows = session.exec(
            select(Post.channel_id, sa.func.count()).where(
                Post.channel_id.in_(all_channel_ids)
            ).where(
                # Beidseitig gebounded auf die abgeschlossene ISO-Woche
                # ``[week_start, week_end)`` — NULL-Fallback auf detected_at
                # identisch zu #190 / ``_channel_stats``.
                sa.or_(
                    sa.and_(
                        Post.published_at.is_not(None),
                        Post.published_at >= week_start,
                        Post.published_at < week_end,
                    ),
                    sa.and_(
                        Post.published_at.is_(None),
                        Post.detected_at >= week_start,
                        Post.detected_at < week_end,
                    ),
                )
            ).group_by(Post.channel_id)
        ).all()
        posts_per_channel = {cid: count for cid, count in post_rows}

    # Bauteil 3: pro Pair die juengste Brief-Row mit ihrer Headline UND
    # ``generated_at`` — in EINER Query via MAX-Subquery-Self-Join. So
    # bleibt der Audit-Test (max 3 Aggregat-Queries) gruen, und wir
    # ziehen nur die top-Row pro pair_key statt der ganzen Historie
    # mit JSON-Payloads.
    #
    # SQLite + Postgres beide: Subquery liefert (pair_key, max_gen),
    # outer Select joined dagegen und holt llm_output mit. Bei zwei
    # identischen Timestamps pro pair_key (theoretisch moeglich bei
    # manueller Dateninsert) produziert der Join Duplikate — wir
    # deduplizieren defensiv in Python (erstes Vorkommen gewinnt).
    latest_brief_subq = (
        select(
            InsightReportRow.pair_key.label("pkey"),
            sa.func.max(InsightReportRow.generated_at).label("max_gen"),
        )
        .group_by(InsightReportRow.pair_key)
        .subquery()
    )
    latest_brief_rows = session.exec(
        select(
            InsightReportRow.pair_key,
            InsightReportRow.generated_at,
            InsightReportRow.llm_output,
        ).join(
            latest_brief_subq,
            sa.and_(
                InsightReportRow.pair_key == latest_brief_subq.c.pkey,
                InsightReportRow.generated_at == latest_brief_subq.c.max_gen,
            ),
        )
    ).all()
    latest_brief_by_pair: dict[str, tuple] = {}
    for pkey, gen_at, llm_output in latest_brief_rows:
        if pkey not in latest_brief_by_pair:
            latest_brief_by_pair[pkey] = (gen_at, llm_output)

    items: list[PairInfo] = []
    for pair_key, pair_def in enabled_pairs:
        # ``_handles_for_pair`` kann denselben Handle mehrfach liefern,
        # wenn das Pair den gleichen Account auf mehreren Plattformen
        # listet — Channel-IDs ueber ein Set deduplizieren, sonst zaehlen
        # wir die Posts dieses Channels doppelt.
        pair_channel_ids: set[int] = set()
        for h in _handles_for_pair(pair_def):
            for cid in channels_by_handle.get(h, []):
                pair_channel_ids.add(cid)
        posts_count = sum(
            posts_per_channel.get(cid, 0) for cid in pair_channel_ids
        )
        latest_brief = latest_brief_by_pair.get(pair_key)
        last_generated_at = latest_brief[0] if latest_brief else None
        # Robust gegen fehlende Felder: llm_output kann ``{}`` sein,
        # ``None`` sein (Persist-Skip-Pfad bei JSON-Parse-Fail), oder
        # die Headline-Spalte fehlt. Jeder Pfad → headline=None.
        headline: str | None = None
        if latest_brief and isinstance(latest_brief[1], dict):
            raw_headline = latest_brief[1].get("headline")
            if isinstance(raw_headline, str) and raw_headline.strip():
                headline = raw_headline.strip()
        items.append(
            PairInfo(
                pair_key=pair_key,
                display_name=pair_def.get("display_name") or pair_key,
                markets=_markets_for_pair(pair_def),
                frequency_label=INSIGHT_FREQUENCY_LABEL,
                enabled=True,
                posts_count_completed_week=int(posts_count),
                iso_week=completed_iso_week,
                iso_year=completed_iso_year,
                last_generated_at=last_generated_at,
                headline=headline,
                has_brief=latest_brief is not None,
            )
        )
    return PairsResponse(pairs=items)


@router.get("/overview")
def overview(
    week_start: date | None = None,
    week_end: date | None = None,
    session: Session = Depends(get_session),
):
    return build_overview(session, week_start, week_end)


@router.get("/weekly", response_model=InsightReport)
def weekly(
    request: Request,
    pair: str = Query(..., description="Pair-Key, z.B. 'warnerbros'"),
    window_days: int = Query(30, ge=7, le=90, description="Datenfenster in Tagen"),
    dry_run: bool = Query(
        False,
        description="True = nur Aggregation, kein LLM-Call (für Quality-Gate ohne Cost).",
    ),
    force: bool = Query(
        False,
        description=(
            "True = Cache-Lookup überspringen und neuen LLM-Call ausführen. "
            "Der frisch generierte Brief wird trotzdem persistiert "
            "(Last-Write-Wins). Hat keine Wirkung bei dry_run=true. "
            "Audit 2026-08-17: verlangt eine Admin-Session — jeder Aufruf "
            "kostet einen Opus-Call und überschreibt den persistierten Brief."
        ),
    ),
    session: Session = Depends(get_session),
    cr_admin_session: str | None = Cookie(default=None),
    cr_user_session: str | None = Cookie(default=None),
) -> InsightReport:
    """Generiere bzw. lade den Trailerhaus-Wochenreport für einen Pair.

    Sprint 1 (Persistenz):
    - Default-Verhalten: Wenn für die aktuelle ISO-Woche bereits ein Brief
      persistiert ist, wird er ohne LLM-Call zurückgegeben (Cost = 0,
      Latenz < 100 ms). Sonst frischer Opus-Call + Persistenz.
    - ``force=true``: Cache-Lookup überspringen, LLM-Call durchführen, Brief
      persistieren (Last-Write-Wins auf der Composite-PK).
    - ``dry_run=true``: weder LLM-Call noch Persistenz — nur die
      deterministische Aggregation. Für Prompt-/Datenanalyse ohne Cost.

    Sprint-2: Pairs können in ``services/insight_engine.PAIRS`` mit
    ``enabled=False`` registriert sein, um sie als „coming soon" anzukündigen
    ohne Code-Push. Solche Pairs antworten mit 503 und einem strukturierten
    Body, den das Frontend zu einer Aktivierungs-Notiz rendert.
    """
    # Request-entry log: needed by the race-condition diagnose (PR #137 /
    # hypothesis B) to distinguish a real concurrent-curl race from an
    # edge-proxy retry of a single user trigger. Forwarded-IP, UA and
    # X-Forwarded-For are the three headers Railway / Cloudflare set;
    # Authorization stays out for security reasons.
    logger.info(
        "brief_request_received",
        extra={
            "pair": pair,
            "force": force,
            "dry_run": dry_run,
            "window_days": window_days,
            "forwarded_for": request.headers.get("x-forwarded-for"),
            "forwarded_ip": request.headers.get("x-real-ip")
            or (request.client.host if request.client else None),
            "user_agent": request.headers.get("user-agent"),
        },
    )
    if pair not in PAIRS:
        raise HTTPException(
            status_code=404,
            detail=f"Unbekannter Pair-Key: {pair!r}. Verfügbar: {_enabled_pair_keys()}",
        )
    # Audit 2026-08-17: force=true loest garantiert einen frischen Opus-Call
    # aus und ueberschreibt den persistierten Brief (Last-Write-Wins). Vorher
    # reichte der Bearer aus dem oeffentlichen Frontend-Bundle — jeder
    # Token-Besitzer konnte in einer Schleife Budget verbrennen und Briefs
    # umschreiben. Jetzt Admin-Session Pflicht (No-Op solange
    # ADMIN_AUTH_ENABLED aus ist — Production erzwingt das Flag per
    # Boot-Check in main.py). dry_run bleibt frei: kein LLM-Call.
    if force and not dry_run:
        require_admin_session(request, cr_admin_session, cr_user_session)
    # Sprint User-Login 2026-07: „Brief <pair> geoeffnet" — nach der
    # Pair-Validierung, vor dem Cache-/LLM-Pfad (Cache-Hit und frische
    # Generierung sind aus Nutzersicht dieselbe Ansicht). dry_run ist
    # Quality-Gate-Tooling, kein Nutzer-View — nicht zaehlen.
    if not dry_run:
        log_usage(
            request_user_email(request),
            "brief_view",
            {"pair": pair, "window_days": window_days},
        )
    pair_def = PAIRS[pair]
    if not pair_def.get("enabled", True):
        # 503 statt 404, weil der Pair existiert (Frontend kann ein Label
        # rendern), nur nicht ausgeliefert wird. Strukturierter Body, damit
        # der Frontend-Fehlerpfad den ``reason`` direkt zeigen kann ohne den
        # Endpoint nochmal zu re-fetchen.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "pair_not_activated",
                "pair": pair,
                "reason": pair_def.get("reason") or "Pair ist aktuell deaktiviert.",
            },
        )
    # Read the LAST COMPLETED ISO week, not the in-progress current week.
    # The Monday cron persists briefs for the just-finished week; without
    # this anchor the detail page targeted the current week (empty until a
    # live regen) AND diverged from the homepage tiles, which already show
    # the latest persisted brief (MAX(generated_at) — the cron's last-
    # completed-week row). Passing the anchor as ``now`` makes both the
    # cache lookup and any fallback aggregation key on the same week the
    # cron wrote, so the detail page is a cache hit on the cron's row.
    # ``aggregate_pair``'s default stays ``now`` (current week) — only the
    # read endpoint is shifted (cron / dry-run-internal callers unaffected).
    report_anchor = last_completed_iso_week_anchor()
    try:
        if dry_run:
            # Dry-Run-Pfad: weder Cache-Lookup noch Persistenz, aber auf
            # derselben (abgeschlossenen) Woche aggregieren wie der echte
            # Pfad, damit Quality-Gate und Anzeige dieselbe Woche treffen.
            return generate_weekly_report(
                session,
                pair,
                window_days=window_days,
                dry_run=True,
                now=report_anchor,
            )
        return generate_and_persist_report(
            session,
            pair,
            window_days=window_days,
            force=force,
            now=report_anchor,
        )
    except AnthropicAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AnthropicRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AnthropicAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------- Segment-Roundup-Read (Master-Plan-Schritt-3b) -----------------


@pairs_router.get("/breakouts")
def public_breakouts(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_session),
) -> dict:
    """Breakout-Feed fuer die Startseite (Wolf 21.07.: "kein Geheimnis,
    fuer jeden User interessant"). Dieselbe rein lesende Berechnung wie
    der Admin-Endpoint (``/api/admin/breakouts``, Platin 4) mit festen,
    konservativen Defaults: 30-Tage-Fenster, >= 2x Kanal-Schnitt. Kein
    LLM-Call, keine Budget-Auswirkung. Liegt NICHT auf der Public-
    Whitelist — Bearer + User-Login gelten wie fuer alle Inhalts-Routen.
    """
    log_usage(request_user_email(request), "breakouts_view", {})
    entries = compute_breakout_feed(
        session, window_days=30, limit=limit, min_multiplier=2.0,
    )
    return {"count": len(entries), "entries": entries}


@router.get("/patterns")
def trailer_patterns_public(
    request: Request,
    window_days: int = Query(90, ge=7, le=365),
    market: Optional[str] = Query(
        None,
        min_length=2,
        max_length=10,
        description=(
            "Auf einen Markt eingrenzen (z. B. 'DE'). Ohne Angabe laufen "
            "alle Maerkte zusammen — dann prueft die Erwartungsquote je "
            "Zelle die Markt-Mischung mit (Markt-Korrektur 26.08.2026) "
            "und belastbare Zellen tragen einen market_note-Satz."
        ),
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Trailer-Intelligence Stufe 1 fuer eingeloggte Nutzer: derselbe
    Muster-Bericht wie ``GET /api/admin/patterns`` (Kanal-normierter
    Lift, Breakout-Quoten, ehrliche insufficient-Zellen — Methodik in
    ``services/trailer_patterns.py``), nur ohne Admin-Session. Rein
    lesende Aggregation, KEIN LLM-Call; Bearer + User-Login gelten wie
    fuer alle Inhalts-Routen.

    Gate: ``FEATURE_TRAILER_INTELLIGENCE_ENABLED`` (Staging-Fundament
    20.08.2026 — in Staging an, in Production aus). Off → 503, gleiches
    Muster wie der Segment-Roundup-Pilot. Das Frontend blendet das
    Panel ohnehin nur ein, wenn ``/api/health`` das Feature meldet; der
    Admin-Endpoint bleibt bewusst UNGEGATET, damit Wolf die Auswertung
    in Production weiter sehen kann, bevor Nutzer sie sehen.
    """
    if not is_trailer_intelligence_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Trailer-Intelligence ist deaktiviert. "
                "FEATURE_TRAILER_INTELLIGENCE_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )
    log_usage(
        request_user_email(request),
        "patterns_view",
        {"window_days": window_days, "market": market},
    )
    # Vorwochen-Vergleich (Aufwertung C): dieselbe Rechnung mit um 7 Tage
    # verschobenem Fenster — deterministisch, keine Persistenz. Der
    # Admin-Endpoint bleibt bei der nackten Einzelrechnung.
    now = datetime.now(timezone.utc)
    current = compute_trailer_patterns(
        session, window_days=window_days, market=market, now=now
    )
    previous = compute_trailer_patterns(
        session,
        window_days=window_days,
        market=market,
        now=now - timedelta(days=TREND_WINDOW_SHIFT_DAYS),
    )
    data = apply_weekly_trend(current, previous)
    # Bestaendigkeits-Ausweis (26.08.): wochen_in_folge je belastbarer
    # Zelle aus den Wochen-Snapshots. NUR ungefiltert — die Snapshots
    # rechnen ueber alle Maerkte, gegen einen markt-gefilterten Bericht
    # waeren ihre Zellen nicht dieselbe Messung.
    if market is None:
        data = annotiere_bestaendigkeit(session, data, now=now)
    return data


@router.get("/patterns/bewaehrung")
def trailer_pattern_bewaehrung(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Trefferquote der eigenen Empfehlungen (26.08.2026): wie viele
    over-Zellen einer Snapshot-Woche standen im Snapshot der Folgewoche
    noch? Reiner Vergleich persistierter Wochen-Snapshots — kein
    Neu-Rechnen, kein LLM. Das System misst sich damit selbst; die Zahl
    steht im Muster-Panel, damit Leser wissen, wie belastbar die
    Empfehlungen erfahrungsgemaess sind.

    Gate und Auth wie ``GET /api/insights/patterns``. Ohne zwei
    aufeinanderfolgende Snapshot-Wochen kommt eine ehrliche note statt
    einer leeren Quote.
    """
    if not is_trailer_intelligence_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Trailer-Intelligence ist deaktiviert. "
                "FEATURE_TRAILER_INTELLIGENCE_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )
    ergebnis = compute_bewaehrung(session)
    log_usage(
        request_user_email(request),
        "patterns_bewaehrung_view",
        {"wochen_paare": ergebnis["gesamt"]["wochen_paare"]},
    )
    return ergebnis


@router.get("/patterns/examples")
def trailer_pattern_examples(
    request: Request,
    dimension: str = Query(..., min_length=1, max_length=40),
    value: str = Query(..., min_length=1, max_length=200),
    window_days: int = Query(90, ge=7, le=365),
    limit: int = Query(5, ge=1, le=10),
    market: Optional[str] = Query(
        None,
        min_length=2,
        max_length=10,
        description=(
            "Muss zum market-Filter der Muster-Anfrage passen — sonst "
            "zeigen die Beispiele Posts, die in der gefilterten Zelle "
            "gar nicht mitgezaehlt wurden."
        ),
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Die staerksten Beispiel-Posts einer Muster-Zelle (Aufwertung B,
    20.08.2026): Klick auf eine Tabellenzeile im Panel zeigt, WELCHE
    Posts hinter "laeuft ueber Schnitt" stehen — mit Lift, Kanal und
    Original-Caption als Referenzmaterial.

    Zugehoerigkeit kommt aus ``posts_for_cell`` — denselben Regeln wie
    die Zellen-Zaehlung selbst (Konfidenz-Filter inklusive), sortiert
    nach Lift absteigend. Rein lesend, kein LLM-Call; Gate und Auth wie
    ``GET /api/insights/patterns``.
    """
    if not is_trailer_intelligence_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Trailer-Intelligence ist deaktiviert. "
                "FEATURE_TRAILER_INTELLIGENCE_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )
    ctx = build_lift_context(session, window_days=window_days, market=market)
    try:
        members = posts_for_cell(session, ctx, dimension, value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    members.sort(key=lambda p: ctx.lift_by_post[p.id], reverse=True)
    top = members[:limit]
    handle_by_channel: dict = {}
    asset_by_post: dict = {}
    if top:
        channel_ids = {p.channel_id for p in top}
        for ch in session.exec(
            select(Channel).where(Channel.id.in_(channel_ids))
        ).all():
            handle_by_channel[ch.id] = ch.handle or ch.name
        # Aeltestes Asset MIT Bildquelle je Post — der Thumbnail-Proxy
        # (/api/thumbnails/{asset_id}) liefert daraus das Vorschaubild,
        # inklusive CDN-Hotlink-Umgehung und Stale-Cache. Posts ohne
        # brauchbares Asset bekommen null; das Frontend zeigt dann nur
        # Text.
        # Vorrang fuer GESPEICHERTE Bilder (Capture-Pipeline,
        # visual_evidence_url -> R2/Storage, laedt immer) vor CDN-
        # Thumbnails: die Karten zeigen die staerksten Posts, die sind
        # oft Wochen alt — und alte Instagram-CDN-Links sind tot. Ein
        # totes CDN-Bild verdraengt sonst ein ladbares gespeichertes
        # (Wolf-Befund 21.08.2026: Karten ohne Bilder trotz Bildern im
        # Admin).
        gespeichert: dict = {}
        cdn: dict = {}
        for a in session.exec(
            select(Asset)
            .where(Asset.post_id.in_([p.id for p in top]))
            .order_by(Asset.created_at.asc())
        ).all():
            if a.visual_evidence_url:
                gespeichert.setdefault(a.post_id, str(a.id))
            elif a.thumbnail_url:
                cdn.setdefault(a.post_id, str(a.id))
        asset_by_post = {**cdn, **gespeichert}
    log_usage(
        request_user_email(request),
        "patterns_examples_view",
        {"dimension": dimension, "value": value},
    )
    return {
        "dimension": dimension,
        "value": value,
        "window_days": window_days,
        "cell_size": len(members),
        "examples": [
            {
                "post_url": p.post_url,
                "asset_id": asset_by_post.get(p.id),
                "platform": ctx.platform_by_channel.get(p.channel_id, "unknown"),
                "channel_handle": handle_by_channel.get(p.channel_id, "?"),
                "lift": round(ctx.lift_by_post[p.id], 2),
                "views": int(p.visible_views) if p.visible_views else None,
                "caption": (p.caption or "")[:240],
                "detected_at": (
                    p.detected_at.date().isoformat() if p.detected_at else None
                ),
            }
            for p in top
        ],
    }


class PostCheckEntwurf(BaseModel):
    """Eingabe des Post-Checks — ein Entwurf, kein persistierter Post."""

    caption: str = Field(min_length=1, max_length=5000)
    duration_seconds: Optional[int] = Field(None, ge=1, le=7200)
    titel_im_bild: Optional[bool] = None
    format: Optional[str] = None
    tonfall: Optional[str] = None


@router.post(
    "/post-check",
    # Deterministisch und billig, aber rechnet einen vollen Bericht —
    # dasselbe Limit-Muster wie der Forecast haelt Schleifen fern.
    dependencies=[Depends(rate_limit("insights-post-check", max_calls=30, window_seconds=300))],
)
def post_check(
    entwurf: PostCheckEntwurf,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Post-Check (Roadmap-Ausbau, 25.08.2026): ein Entwurf wird VOR
    dem Posten gegen die aktuellen Befunde geprueft — dieselben
    Extraktoren und Zellen wie der Muster-Bericht, kein LLM-Call
    (``services/post_check.py``).

    Gate: ``FEATURE_POST_CHECK_ENABLED`` (Arbeitsregel 23.08.2026 —
    Staging zuerst). Off → 503."""
    if not is_post_check_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Post-Check ist deaktiviert. "
                "FEATURE_POST_CHECK_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )
    if entwurf.format is not None and entwurf.format not in FORMAT_WERTE:
        raise HTTPException(
            status_code=422,
            detail=f"Unbekanntes Format {entwurf.format!r}. Bekannt: {', '.join(FORMAT_WERTE)}",
        )
    if entwurf.tonfall is not None and entwurf.tonfall not in TON_WERTE:
        raise HTTPException(
            status_code=422,
            detail=f"Unbekannter Tonfall {entwurf.tonfall!r}. Bekannt: {', '.join(TON_WERTE)}",
        )
    ergebnis = pruefe_post(
        session,
        caption=entwurf.caption,
        duration_seconds=entwurf.duration_seconds,
        titel_im_bild=entwurf.titel_im_bild,
        format_wert=entwurf.format,
        ton_wert=entwurf.tonfall,
    )
    log_usage(
        request_user_email(request),
        "post_check",
        {
            "achtung": ergebnis["zusammenfassung"]["achtung"],
            "gut": ergebnis["zusammenfassung"]["gut"],
        },
    )
    return ergebnis


@router.get("/referenzen")
def referenz_suche(
    request: Request,
    window_days: int = Query(90, ge=7, le=365),
    market: Optional[str] = Query(None, min_length=2, max_length=10),
    platform: Optional[str] = Query(None, pattern="^(instagram|tiktok|youtube)$"),
    facette: list[str] = Query(
        default=[],
        description=(
            "Wiederholbar, je Eintrag 'dimension=wert' — z. B. "
            "facette=genre=Horror&facette=cover_titel=mit_titel. "
            "Mehrere Facetten schneiden sich."
        ),
    ),
    min_lift: Optional[float] = Query(None, ge=0, le=100),
    limit: int = Query(24, ge=1, le=60),
    session: Session = Depends(get_session),
) -> dict:
    """Referenz-Suche (Roadmap Schritt 1, 25.08.2026): Facetten-Suche
    ueber die analysierten Posts des Fensters — "Horror, Titel im Bild,
    ueber 1,5x Kanal-Schnitt" → Grid mit Thumbnails, Lift, Links.

    Lift und Facetten-Zugehoerigkeit kommen aus ``trailer_patterns``
    (dieselben Regeln wie Muster-Bericht und Beispiel-Endpoint, Details
    in ``services/referenz_suche.py``). Rein lesend, kein LLM-Call;
    Bearer + User-Login wie fuer alle Inhalts-Routen.

    Gate: ``FEATURE_REFERENZ_SUCHE_ENABLED`` (Arbeitsregel 23.08.2026 —
    Staging zuerst, Production nach Wolfs Abnahme). Off → 503.
    """
    if not is_referenz_suche_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Referenz-Suche ist deaktiviert. "
                "FEATURE_REFERENZ_SUCHE_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )
    facetten: dict[str, str] = {}
    for eintrag in facette:
        dimension, trenner, wert = eintrag.partition("=")
        if not trenner or not dimension.strip() or not wert.strip():
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Facette {eintrag!r} ist nicht lesbar — erwartet "
                    "wird 'dimension=wert'."
                ),
            )
        facetten[dimension.strip()] = wert.strip()
    try:
        ergebnis = suche_referenzen(
            session,
            window_days=window_days,
            market=market,
            platform=platform,
            facetten=facetten,
            min_lift=min_lift,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_usage(
        request_user_email(request),
        "referenz_suche",
        {
            "window_days": window_days,
            "facetten": facetten,
            "min_lift": min_lift,
            "platform": platform,
            "gesamt": ergebnis["gesamt"],
        },
    )
    return ergebnis


@router.get("/pattern-briefing")
def pattern_briefing_public(
    request: Request,
    mode: str = Query(
        "genre",
        pattern="^(genre|title)$",
        description=(
            "Baustein-Ebene: 'genre' (Genre-Muster) oder 'title' "
            "(je Titel-Kampagne). Das Panel laedt beide getrennt — "
            "je Ebene ihre juengste Woche, 404 je Ebene ist normal."
        ),
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Juengstes Pattern-Briefing der gewaehlten Ebene fuer eingeloggte
    Nutzer — Trailer-Intelligence Stufe 1, Schritt 3. Rein lesend:
    liefert die persistierte Row des Montags-Cron-Blocks (oder des
    Admin-Triggers), loest selbst NIE einen LLM-Call aus.

    Gate: ``FEATURE_TRAILER_INTELLIGENCE_ENABLED`` — gleiches Muster wie
    ``GET /api/insights/patterns``. Off → 503; noch keine Row → 404 (das
    Frontend blendet die Sektion dann einfach nicht ein).

    Ueber den Wire geht ``llm_output`` plus Meta — der ``evidence``-Blob
    bleibt beim Admin-Endpoint (Review-Material, nicht Nutzer-Inhalt);
    ``citation_dropped`` geht mit, damit die Anzeige transparent machen
    kann, wenn Bausteine an der Beleg-Pruefung gescheitert sind.
    """
    if not is_trailer_intelligence_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Trailer-Intelligence ist deaktiviert. "
                "FEATURE_TRAILER_INTELLIGENCE_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )
    row = session.exec(
        select(PatternBriefing)
        .where(PatternBriefing.mode == mode)
        .order_by(
            PatternBriefing.iso_year.desc(),
            PatternBriefing.iso_week.desc(),
        )
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Noch kein Pattern-Briefing ({mode}) persistiert.",
        )
    log_usage(
        request_user_email(request),
        "pattern_briefing_view",
        {"mode": mode, "iso_year": row.iso_year, "iso_week": row.iso_week},
    )
    return {
        "mode": row.mode,
        "iso_year": row.iso_year,
        "iso_week": row.iso_week,
        "window_days": row.window_days,
        "generated_at": row.generated_at.isoformat(),
        "model_used": row.model != "none",
        "citation_dropped": row.citation_dropped,
        "llm_output": row.llm_output,
    }


@roundups_router.get("/roundups/latest", response_model=SegmentRoundupListResponse)
def roundups_latest(
    session: Session = Depends(get_session),
) -> SegmentRoundupListResponse:
    """Latest persisted Segment-Roundup per segment.

    Drives the Roundup-Block on the landing page (Frontend macht einen
    Call fuer alle Kacheln; spiegelt das ``/api/pairs``-Muster). Pro
    Segment wird die Row mit dem hoechsten ``(iso_year, iso_week)``
    zurueckgegeben; tiebreak ist ``generated_at`` (Last-Write-Wins, der
    juengste Lauf einer Woche gewinnt).

    Antwort enthaelt das volle ``llm_output`` (Schritt-3c: headline,
    tldr, titles, themes, data_caveats) — das Frontend zeigt den
    Aufklapp-Bereich der Kachel aus dieser Antwort, ohne weiteren Call.
    Aggregations-Audit-Material (``channels_aggregation``) wandert
    bewusst **nicht** ueber den Wire — bleibt fuer DB-Inspektion und
    den admin-getriggerten Re-Run reserviert.

    Sortierung in fester ``ChannelSegment``-ENUM-Reihenfolge (us_major,
    us_independent, uk_major, uk_independent, de_verleih,
    de_independent), unabhaengig von Insert-Reihenfolge. Segmente ohne
    Row werden weggelassen — der Frontend-Block hat seine eigene
    Segment-Liste fuer den "noch kein Roundup"-Zustand.

    Public (kein Auth-Dependency). Konsistent mit ``/api/pairs``.
    """
    # Eine Row pro Segment mit (iso_year, iso_week) MAX, tiebreak
    # generated_at DESC. Bounded set (1 Row/Segment/Woche × wenige
    # Segmente × wenige Wochen) — In-Python-Reduktion ist ueberschaubar
    # und vermeidet ein DB-Dialekt-abhaengiges Window-/CTE-Konstrukt
    # (SQLite-Tests + Postgres-Prod muessen identisch funktionieren).
    rows = session.exec(
        select(SegmentRoundup).order_by(
            SegmentRoundup.iso_year.desc(),
            SegmentRoundup.iso_week.desc(),
            SegmentRoundup.generated_at.desc(),
        )
    ).all()

    latest_per_segment: dict[str, SegmentRoundup] = {}
    for row in rows:
        # Erstes Vorkommen pro Segment ist dank ORDER BY automatisch das
        # neueste — wenn schon vorhanden, ueberspringen.
        key = row.segment.value if hasattr(row.segment, "value") else str(row.segment)
        if key not in latest_per_segment:
            latest_per_segment[key] = row

    summaries: list[SegmentRoundupSummary] = []
    for segment in ChannelSegment:
        row = latest_per_segment.get(segment.value)
        if row is None:
            continue
        agg = row.channels_aggregation or {}
        try:
            llm = SegmentRoundupLLMReport(**(row.llm_output or {}))
        except Exception as exc:  # pragma: no cover - defensiv
            # Wenn eine alte Row im LLM-Output ein Schema-Drift hat,
            # ueberspringen statt 500 — der Frontend-Block soll fuer die
            # uebrigen Segmente weiter rendern.
            logger.warning(
                "segment_roundup row for %s has malformed llm_output: %s",
                segment.value,
                exc,
            )
            continue
        summaries.append(
            SegmentRoundupSummary(
                segment=segment.value,
                iso_year=row.iso_year,
                iso_week=row.iso_week,
                window_days=row.window_days,
                generated_at=row.generated_at,
                channels_evaluated=int(agg.get("channels_evaluated") or 0),
                channels_with_posts=int(agg.get("channels_with_posts") or 0),
                total_posts=int(agg.get("total_posts") or 0),
                llm_output=llm,
            )
        )

    return SegmentRoundupListResponse(roundups=summaries)


# Feste Spalten-Reihenfolge der Film-Übersicht (V3 Sprint 1). Posts in
# anderen Märkten (INT/MIXED) gehören nicht in die DE/US/UK-Drei-Spalten-UI.
_TITLE_POSTS_MARKETS = [Market.DE, Market.US, Market.UK]


def _post_engagement_rate(views, likes, comments) -> float:
    """V3 Sprint 3 — Engagement-Rate = (likes + comments) / views, NUR wenn
    views > 0; sonst 0.0 (Division-by-zero-Schutz + NULLS-LAST-Sortierung).
    Shares/Bookmarks bewusst ausgeschlossen (nicht plattformübergreifend
    verfügbar)."""
    v = views or 0
    if v <= 0:
        return 0.0
    return ((likes or 0) + (comments or 0)) / v


# Sortier-Schlüssel der Detailseite. Default ``engagement``; ungültige/fehlende
# Werte fallen auf den Default zurück. Alle absteigend, 0/None ans Ende
# (NULLS LAST), da die ``key``-Werte über ``or 0`` normalisiert werden.
_TITLE_POSTS_SORTS = {
    "engagement": lambda r: r.engagement_rate or 0.0,
    "views": lambda r: r.views or 0,
    "likes": lambda r: r.likes or 0,
}


@router.get("/title/{title_id}/posts", response_model=TitlePostsResponse)
def title_posts(
    request: Request,
    title_id: UUID,
    sort: str = Query("engagement", description="Sortierung pro Plattform: engagement|views|likes"),
    session: Session = Depends(get_session),
) -> TitlePostsResponse:
    """Read-only: alle Posts eines Titels, gruppiert nach Markt (DE/US/UK)
    und Plattform — die Datengrundlage der film-zentrierten Detailseite
    (Sprint 2). Kette title_id -> Asset -> Post -> Channel.

    ``markets`` enthält immer die drei Spalten DE/US/UK; ein Titel ohne
    Posts (oder eine unbekannte title_id) liefert wohlgeformte leere Gruppen,
    keinen Fehler. Posts werden über ``post.id`` dedupliziert (ein Titel kann
    auf mehreren Assets desselben Posts liegen).

    V3 Sprint 3: ``sort`` ordnet die Posts INNERHALB jeder Plattform-Liste
    (die market->platform->[posts]-Struktur bleibt) absteigend nach Engagement-
    Rate (Default), Views oder Likes. Sortiert wird in Python über die bereits
    in Python gebildeten Buckets — funktional identisch zu einem SQL-ORDER-BY
    mit DESC NULLS LAST, aber ohne den NULLIF-Ausdruck und konsistent mit der
    serverseitig berechneten ``engagement_rate``.
    """
    title = session.get(Title, title_id)
    # Sprint User-Login 2026-07: Film-Detailseite geoeffnet.
    log_usage(
        request_user_email(request),
        "title_view",
        {"title_id": str(title_id), "title": title.title_original if title else None},
    )
    sort_key = _TITLE_POSTS_SORTS.get(sort, _TITLE_POSTS_SORTS["engagement"])

    rows = session.exec(
        select(Post, Channel, Asset)
        .join(Asset, Asset.post_id == Post.id)
        .join(Channel, Channel.id == Post.channel_id)
        .where(Asset.title_id == title_id)
    ).all()

    # Dedupe per post.id; bucket by market -> platform.
    seen: set = set()
    # {market_value: {platform: [TitlePostRef]}}
    buckets: dict[str, dict[str, list[TitlePostRef]]] = {
        m.value: defaultdict(list) for m in _TITLE_POSTS_MARKETS
    }
    allowed = {m.value for m in _TITLE_POSTS_MARKETS}
    total = 0
    for post, channel, asset in rows:
        if post.id in seen:
            continue
        seen.add(post.id)
        market = getattr(channel.market, "value", channel.market)
        if market not in allowed:
            continue  # INT/MIXED gehören nicht in die DE/US/UK-Spalten
        platform = post.platform or "unknown"
        buckets[market][platform].append(
            TitlePostRef(
                post_url=post.post_url,
                platform=platform,
                market=market,
                asset_id=str(asset.id),
                thumbnail_url=asset.thumbnail_url,
                views=post.visible_views,
                likes=post.visible_likes,
                comments=post.visible_comments,
                shares=post.visible_shares,
                duration_seconds=post.duration_seconds,
                engagement_rate=_post_engagement_rate(
                    post.visible_views, post.visible_likes, post.visible_comments
                ),
                published_at=post.published_at,
            )
        )
        total += 1

    markets = [
        TitleMarketPosts(
            market=m.value,
            platforms=[
                TitlePlatformPosts(
                    platform=plat,
                    posts=sorted(posts, key=sort_key, reverse=True),
                )
                for plat, posts in sorted(buckets[m.value].items())
            ],
        )
        for m in _TITLE_POSTS_MARKETS
    ]

    return TitlePostsResponse(
        title_id=str(title_id),
        title_original=title.title_original if title else None,
        total_posts=total,
        markets=markets,
    )


# --- V3 Sprint 6: deskriptive Markt-Zeitreihe über Wochen -----------------
# Variante A: Kennzahlen FRISCH pro diskreter ISO-Woche × Markt aus den
# Post-Tabellen rechnen — NICHT aus den 30-Tage-rollenden Brief-Aggregaten
# (die haben kein Σ Views und eine andere ER-Definition). Spiegelt die
# Wochen-Bucket-Logik aus ``title_aggregation`` und die ER-Definition der
# FilmMarketSummaryBar. Die ``insight_report``-Tabelle dient NUR dazu, die
# Wochen-Achse zu bestimmen (welche KWs hat das Pair als Brief), nicht als
# Zahlenquelle.


def _iso_week_monday(iso_year: int, iso_week: int) -> datetime:
    """Montag 00:00 UTC der gegebenen ISO-Woche."""
    return datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=timezone.utc)


@router.get("/timeline", response_model=MarketTimelineResponse)
def market_timeline(
    pair: str = Query(..., description="Pair-Key, z.B. 'warnerbros'"),
    weeks: int | None = Query(
        None,
        ge=1,
        description=(
            "Optionale Begrenzung auf die letzten N Achsen-Wochen. Default: "
            "alle Wochen zwischen erster und letzter Brief-KW des Pairs."
        ),
    ),
    session: Session = Depends(get_session),
) -> MarketTimelineResponse:
    """Read-only deskriptive Zeitreihe pro Markt (DE/US/UK) über die Wochen,
    für die das Pair persistierte Briefe hat.

    Pro ISO-Woche × Markt: Σ Views (``views or 0``), aggregierte Engagement-
    Rate ``Σ(likes+comments)/Σ(views)`` NUR über Posts mit ``views>0`` (sonst
    ``None``), und die Post-Anzahl. KEINE Glättung, KEINE Prognose, KEINE
    Trendlinie (Sprint 7).

    Die Kern-Berechnung lebt in ``services.market_timeline`` (geteilt mit dem
    Sprint-7-Forecast-Endpoint); dieser Endpoint gießt das Ergebnis nur in die
    Pydantic-Antwort.

    Caveat (vom Frontend sichtbar zu machen): Aufrufe sind Snapshots und
    wachsen über Zeit — die Wochenwerte spiegeln den AKTUELLEN DB-Stand, nicht
    den Stand zum jeweiligen Wochenzeitpunkt.
    """
    if pair not in PAIRS:
        raise HTTPException(
            status_code=404,
            detail=f"Unbekannter Pair-Key: {pair!r}. Verfügbar: {_enabled_pair_keys()}",
        )
    result = compute_market_timeline(session, pair, PAIRS[pair], weeks=weeks)
    markets_out = {
        m: [MarketTimelinePoint(**p) for p in points]
        for m, points in result["markets"].items()
    }
    return MarketTimelineResponse(
        pair_key=pair,
        weeks=[TimelineWeek(iso_year=y, iso_week=w) for (y, w) in result["weeks"]],
        markets=markets_out,
    )


@router.post(
    "/forecast",
    response_model=ForecastResponse,
    # Audit 2026-08-17: ein Cache-Miss loest genau einen Opus-Call aus —
    # ohne Limit konnte jeder Token-Besitzer per Schleife Budget verbrennen
    # (Deckel war erst der Monats-Hard-Cap). 10 Aufrufe / 5 Min / IP decken
    # normales UI-Verhalten (ein Aufruf pro Pair-Ansicht) locker ab.
    dependencies=[Depends(rate_limit("insights-forecast", max_calls=10, window_seconds=300))],
)
def public_forecast(
    request: Request,
    pair: str = Query(..., description="Pair-Key, z.B. 'warnerbros'"),
    weeks: int | None = Query(
        None, ge=1,
        description="Optionale Begrenzung der Zeitreihe auf die letzten N Wochen.",
    ),
    session: Session = Depends(get_session),
) -> ForecastResponse:
    """#252 — Öffentliche ER-Prognose pro Markt, MIT Ehrlichkeits-Gate.

    Gleicher Service wie der Admin-Endpoint (``generate_er_forecast``), aber
    ``apply_gate=True``: Märkte mit R² < 0.5 oder weniger als 5 validen
    ER-Wochen kommen als ``status='too_volatile'`` zurück — ``n_points``/``r2``
    bleiben transparent, der Prognosewert wird NICHT mitgeschickt ("zu
    schwankend für eine Prognose" statt Trendlinie ohne Deckung).

    Kosten: Die Regression ist gratis und läuft live (Split-Cache, #252).
    Die LLM-Einordnung wird pro (pair, Ziel-Woche) gecacht — POST bleibt die
    Methode (analog Admin), weil ein Cache-Miss genau einen Opus-Call
    auslöst; jeder weitere Aufruf derselben Woche liest nur.

    Auth: nur die globale Bearer-Middleware (wie ``GET /timeline``) — kein
    Admin-Gate. Der Admin-Endpoint bleibt ungegated und sieht alle Märkte.
    """
    if pair not in PAIRS:
        raise HTTPException(
            status_code=404,
            detail=f"Unbekannter Pair-Key: {pair!r}. Verfügbar: {_enabled_pair_keys()}",
        )
    # Sprint User-Login 2026-07: ER-Prognose abgerufen.
    log_usage(request_user_email(request), "forecast_view", {"pair": pair})
    result = generate_er_forecast(
        session, pair, PAIRS[pair], weeks=weeks, apply_gate=True
    )
    nxt = result.get("next_week")
    return ForecastResponse(
        pair_key=result["pair_key"],
        n_axis_weeks=result["n_axis_weeks"],
        next_week=TimelineWeek(**nxt) if nxt else None,
        markets={m: MarketForecast(**r) for m, r in result["markets"].items()},
        einordnung=result.get("einordnung"),
    )
