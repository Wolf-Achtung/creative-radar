import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session

from app.database import get_session

logger = logging.getLogger(__name__)
from app.schemas.insights import InsightReport, PairInfo, PairsResponse
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    AnthropicRateLimitError,
)
from app.services.insight_engine import (
    INSIGHT_FREQUENCY_LABEL,
    MARKETS_DISPLAY_ORDER,
    PAIRS,
    generate_and_persist_report,
    generate_weekly_report,
)
from app.services.insights import build_overview

router = APIRouter(prefix="/api/insights", tags=["insights"])

# Separate router so the URL is ``/api/pairs`` instead of nested under
# ``/api/insights``. Same module to keep the PAIRS import single-source.
pairs_router = APIRouter(prefix="/api", tags=["pairs"])


def _enabled_pair_keys() -> list[str]:
    return sorted(k for k, v in PAIRS.items() if v.get("enabled", True))


def _markets_for_pair(pair_def: dict) -> list[str]:
    """Return the pair's surface market codes in DE → US → UK display order.

    Primary source: the explicit ``markets`` field on the PAIRS entry —
    the curated set of markets the LLM brief actually covers. This is
    what the landing-page card grid promises, and it stays decoupled
    from the channel-pool so the cron can keep harvesting UK channels
    without the surface claiming the brief covers them. When B2 brings
    a market into the brief output, the pair's ``markets`` field flips
    to include it.

    Fallback: if a pair has no ``markets`` field (future pairs added
    before the X1 convention catches up), derive from the channel-pool
    union, matching the pre-X1 behaviour.

    Markets the pair does not cover are skipped — Lionsgate emits
    ``["US"]`` (X1) instead of ``["US", "UK"]`` even though the UK
    channels exist in the pool.
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


@pairs_router.get("/pairs", response_model=PairsResponse)
def pairs() -> PairsResponse:
    """List enabled pairs with Frontend-ready metadata.

    Drives the landing-page card grid. Returns only ``enabled=True`` pairs
    in PAIRS-dict insertion order (Python 3.7+ guarantees order on dict
    iteration). Markets are emitted in fixed DE → US → UK order.
    """
    items: list[PairInfo] = []
    for pair_key, pair_def in PAIRS.items():
        if not pair_def.get("enabled", False):
            continue
        items.append(
            PairInfo(
                pair_key=pair_key,
                display_name=pair_def.get("display_name") or pair_key,
                markets=_markets_for_pair(pair_def),
                frequency_label=INSIGHT_FREQUENCY_LABEL,
                enabled=True,
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
