import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select

from app.database import get_session

logger = logging.getLogger(__name__)
from app.models.entities import Channel, ChannelSegment, InsightReport as InsightReportRow, Post, SegmentRoundup
from app.schemas.insights import (
    InsightReport,
    PairInfo,
    PairsResponse,
    SegmentRoundupListResponse,
    SegmentRoundupLLMReport,
    SegmentRoundupSummary,
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
    generate_and_persist_report,
    generate_weekly_report,
)
from app.services.insights import build_overview

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
    fuer den case-insensitive DB-Lookup. Reuse von
    ``_platforms_dict_for`` aus insight_engine — ein einziger Pfad fuer
    Pre-Sprint-4-Single-Platform UND Multi-Platform-Pairs."""
    handles: list[str] = []
    for platform_specs in _platforms_dict_for(pair_def).values():
        for spec in platform_specs:
            handle = spec.get("handle")
            if handle:
                handles.append(handle.lower())
    return handles


@pairs_router.get("/pairs", response_model=PairsResponse)
def pairs(session: Session = Depends(get_session)) -> PairsResponse:
    """List enabled pairs with Frontend-ready metadata.

    Drives the landing-page card grid. Returns only ``enabled=True`` pairs
    in PAIRS-dict insertion order (Python 3.7+ guarantees order on dict
    iteration). Markets are emitted in fixed DE → US → UK order.

    Sprint 28.05.2026 (Studio-Kennzahl): liefert zusaetzlich pro Pair
    eine Live-Kennzahl (``posts_count_this_week``) und den Timestamp
    des juengsten persistierten Briefs (``last_generated_at``). Drei
    Aggregat-Queries fuer ALLE Pairs zusammen — kein N+1:

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
    """
    week_start = _iso_week_start_utc()

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
                sa.or_(
                    sa.and_(
                        Post.published_at.is_not(None),
                        Post.published_at >= week_start,
                    ),
                    sa.and_(
                        Post.published_at.is_(None),
                        Post.detected_at >= week_start,
                    ),
                )
            ).group_by(Post.channel_id)
        ).all()
        posts_per_channel = {cid: count for cid, count in post_rows}

    # Bauteil 3: MAX(generated_at) pro pair_key, eine Query. Liefert
    # auch Pairs, die heute disabled sind — wir filtern unten ueber
    # enabled_pairs, das Dict-Lookup ist O(1).
    last_gen_rows = session.exec(
        select(
            InsightReportRow.pair_key,
            sa.func.max(InsightReportRow.generated_at),
        ).group_by(InsightReportRow.pair_key)
    ).all()
    last_gen_by_pair = {pkey: ts for pkey, ts in last_gen_rows}

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
        items.append(
            PairInfo(
                pair_key=pair_key,
                display_name=pair_def.get("display_name") or pair_key,
                markets=_markets_for_pair(pair_def),
                frequency_label=INSIGHT_FREQUENCY_LABEL,
                enabled=True,
                posts_count_this_week=int(posts_count),
                last_generated_at=last_gen_by_pair.get(pair_key),
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
            "(Last-Write-Wins). Hat keine Wirkung bei dry_run=true."
        ),
    ),
    session: Session = Depends(get_session),
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
    try:
        if dry_run:
            # Dry-Run-Pfad ist unverändert — weder Cache-Lookup noch
            # Persistenz. Nützlich für Prompt-Iteration.
            return generate_weekly_report(
                session,
                pair,
                window_days=window_days,
                dry_run=True,
            )
        return generate_and_persist_report(
            session,
            pair,
            window_days=window_days,
            force=force,
        )
    except AnthropicAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AnthropicRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AnthropicAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------- Segment-Roundup-Read (Master-Plan-Schritt-3b) -----------------


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
