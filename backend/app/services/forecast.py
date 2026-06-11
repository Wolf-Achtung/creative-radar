"""V3 Sprint 7 — ER-Prognose pro Markt: lineare Regression über die
Engagement-Rate-Zeitreihe (Sprint 6) + eine sachliche LLM-Einordnung.

Architektur-Trennung (Wolf-Festlegung):
- Die Regression RECHNET (dependency-freies Least-Squares, kein numpy/scipy).
- Die LLM ORDNET NUR EIN — sie bekommt die Regressions-AUSGABE + die ER-Reihe,
  nicht die Rohdaten zum Selbst-Rechnen.

NUR ER, kein Views-Forecast (Views sind zu spitzenlastig für Extrapolation).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from sqlmodel import Session

from app.models.entities import ErForecastEinordnung
from app.services.anthropic_client import (
    AnthropicAPIError,
    is_anthropic_configured,
    messages_create_text,
)
from app.services.cost_log import record_anthropic_call
from app.services.insight_engine import OPUS_MODEL_ALIAS
from app.services.market_timeline import (
    TIMELINE_MARKET_VALUES,
    compute_market_timeline,
    iso_week_monday,
)

logger = logging.getLogger(__name__)

# Mindestens 3 valide Punkte für eine sinnvolle Linie (Wolf-Festlegung).
MIN_POINTS = 3
# Steigungs-Schwelle, unter der wir "stabil" statt steigend/fallend melden.
_FLAT_SLOPE_EPS = 1e-6

# Ehrlichkeits-Gate (#252, Wolf-Freigabe 11.06.2026): unterhalb dieser
# Schwellen zeigt die ÖFFENTLICHE Sicht keine Prognose — eine Trendlinie
# mit R² < 0.5 ist mehrheitlich Rauschen ("Zahlenraten mit falscher
# Autorität"), und bei n < 5 ist R² selbst kaum aussagekräftig (drei
# Punkte ergeben fast immer hohes R²; Syy=0 liefert künstlich 1.0, siehe
# _linear_regression). Der Admin-Pfad bleibt ungegated (apply_gate=False)
# und sieht weiterhin alle Werte. Messlauf 11.06.: die R²-Verteilung ist
# bimodal (0.00–0.21 Rauschen vs. 0.56–0.92 echte Trends) — 0.5 trennt
# exakt entlang dieser Lücke. Keine Hysterese zum Start (nachrüstbar,
# falls Gruppen um die Schwelle flackern).
FORECAST_GATE_MIN_R2 = 0.5
FORECAST_GATE_MIN_POINTS = 5


def _apply_honesty_gate(result: dict) -> dict:
    """Mappt ein ``_forecast_one_market``-Ergebnis auf die öffentliche
    Sicht: ``status='ok'`` unterhalb der Gate-Schwellen wird zu
    ``'too_volatile'`` — ``n_points`` und ``r2`` bleiben im Payload
    (Transparenz), aber ``forecast_er``/``slope``/``direction`` werden
    ENTZOGEN, damit das Frontend eine ungedeckte Zahl strukturell gar
    nicht rendern kann. ``insufficient_data`` passiert unverändert."""
    if result.get("status") != "ok":
        return result
    if (
        result["r2"] >= FORECAST_GATE_MIN_R2
        and result["n_points"] >= FORECAST_GATE_MIN_POINTS
    ):
        return result
    return {
        "status": "too_volatile",
        "n_points": result["n_points"],
        "r2": result["r2"],
    }


def _linear_regression(points: list[tuple[float, float]]) -> dict:
    """Least-Squares-Fit y = slope*x + intercept über ``points`` = [(x, y)].

    Liefert ``slope``, ``intercept`` und ``r2`` (Bestimmtheitsmaß). Edge-sicher:
    - Bei flacher Linie (alle y gleich, Syy=0) ist das Modell exakt → r2 = 1.0.
    - Distinkte x sind hier garantiert (Achsen-Indizes), Sxx > 0 für n>=2.
    Kein NaN/Infinity.
    """
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    syy = sum((y - mean_y) ** 2 for _, y in points)

    slope = sxy / sxx if sxx > 0 else 0.0
    intercept = mean_y - slope * mean_x
    if syy <= 0:
        # Keine Streuung in y → flache Linie, perfekt erklärt.
        r2 = 1.0
    else:
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
        r2 = 1.0 - ss_res / syy
    # Numerische Rundungs-Drift einklammern.
    r2 = max(0.0, min(1.0, r2))
    return {"slope": slope, "intercept": intercept, "r2": r2}


def _forecast_one_market(points: list[dict]) -> dict:
    """Regression über die ER-Werte EINES Markts. ``points`` ist die
    achsen-positionsgleiche Liste aus ``compute_market_timeline`` (jeder Punkt
    mit ``er`` = float|None). x = Achsen-Index, damit Lücken als Abstand zählen;
    er=null-Wochen werden ausgeschlossen (nicht als 0 eingerechnet)."""
    valid = [(i, p["er"]) for i, p in enumerate(points) if p["er"] is not None]
    if len(valid) < MIN_POINTS:
        return {"status": "insufficient_data", "n_points": len(valid)}

    fit = _linear_regression([(float(i), float(er)) for i, er in valid])
    forecast_x = float(len(points))  # nächste KW = Position nach dem letzten Achsen-Punkt
    raw = fit["slope"] * forecast_x + fit["intercept"]
    forecast_er = max(0.0, raw)  # ER ist eine Rate >= 0; negative Extrapolation auf 0 begrenzt

    slope = fit["slope"]
    if slope > _FLAT_SLOPE_EPS:
        direction = "steigend"
    elif slope < -_FLAT_SLOPE_EPS:
        direction = "fallend"
    else:
        direction = "stabil"

    return {
        "status": "ok",
        "n_points": len(valid),
        "forecast_er": forecast_er,
        "r2": fit["r2"],
        "slope": slope,
        "direction": direction,
    }


def _next_week(axis: list[tuple[int, int]]) -> Optional[tuple[int, int]]:
    if not axis:
        return None
    nxt = iso_week_monday(*axis[-1]) + timedelta(days=7)
    iso = nxt.isocalendar()
    return (iso.year, iso.week)


_EINORDNUNG_SYSTEM = """\
Du bist Analyst bei Trailerhaus. Du ordnest eine bereits berechnete
Engagement-Rate-Prognose sachlich ein — du rechnest NICHT selbst, du
interpretierst die dir gelieferten Regressions-Zahlen.

BERICHTSTON:
- Sachlich berichten, nicht werten. Keine Dramatisierung, keine Formeln wie
  "wird sicher steigen". Eine Prognose ist unsicher; benenne das.
- Zahlen ausschreiben (zum Beispiel 8,4 Prozent), keine Abkürzungen.
- Ländernamen oder Kürzel (Deutschland/DE, die USA/US, Großbritannien/UK)
  beide erlaubt.
- Ganze, ruhige Sätze, kein Stakkato.

PFLICHT-INHALT:
- Die Richtung der Prognose je Markt (steigend/fallend/stabil) mit dem
  Prognosewert.
- Eine explizite Nennung der dünnen Datenbasis (nur wenige Wochen) und dass
  die Güte (Bestimmtheitsmaß) die Verlässlichkeit einordnet — niedrige Güte
  heißt: wenig belastbar.
- Keine Übertreibung. Maximal 4-5 Sätze gesamt."""


def _build_einordnung_prompt(
    pair_label: str, n_axis_weeks: int, per_market: dict, next_week: Optional[tuple[int, int]]
) -> str:
    lines = [
        f"Pair: {pair_label}.",
        f"Datenbasis: {n_axis_weeks} Wochen auf der Zeitachse (dünn).",
    ]
    if next_week:
        lines.append(f"Prognose-Zielwoche: KW {next_week[1]}/{next_week[0]}.")
    lines.append("")
    lines.append("Regressions-Ausgabe je Markt (bereits berechnet — nur einordnen):")
    for m in TIMELINE_MARKET_VALUES:
        r = per_market.get(m, {})
        if r.get("status") == "too_volatile":
            # Ehrlichkeits-Gate (#252): Der Prompt bekommt die GEGATETE
            # Sicht — für diesen Markt existiert kein Prognosewert, den
            # der Text ausplaudern könnte. Nur R²/n als Begründung.
            lines.append(
                f"- {m}: Wochenwerte zu schwankend für eine Prognose "
                f"(Bestimmtheitsmaß R² {r.get('r2', 0.0):.2f}, "
                f"{r.get('n_points', 0)} valide Wochen) — keine Prognose. "
                f"Benenne das als 'keine belastbare Aussage möglich', nenne KEINE Zahl."
            )
            continue
        if r.get("status") != "ok":
            lines.append(f"- {m}: zu wenig Daten ({r.get('n_points', 0)} valide Wochen).")
            continue
        lines.append(
            f"- {m}: Prognose Engagement-Rate {r['forecast_er'] * 100:.1f} Prozent, "
            f"Richtung {r['direction']}, Bestimmtheitsmaß R² {r['r2']:.2f}, "
            f"{r['n_points']} valide Wochen."
        )
    lines.append("")
    lines.append(
        "Schreibe eine kurze, sachliche Einordnung dieser Prognose (4-5 Sätze), "
        "die Richtung und Prognosewert je Markt nennt, die dünne Datenbasis und "
        "die Unsicherheit explizit benennt und nichts übertreibt."
    )
    return "\n".join(lines)


def _load_cached_einordnung(
    session: Session, pair_key: str, next_week: tuple[int, int]
) -> Optional[str]:
    row = session.get(ErForecastEinordnung, (pair_key, next_week[0], next_week[1]))
    return row.einordnung if row else None


def _persist_einordnung(
    session: Session, pair_key: str, next_week: tuple[int, int], text: str
) -> None:
    """First-write-wins pro (pair, Ziel-Woche). Ein paralleler Zweitschreiber
    (zwei gleichzeitige Cache-Misses) verliert das Insert-Rennen — dann hat
    er denselben Text-Zweck bereits erfüllt; der IntegrityError wird
    geschluckt statt den Forecast-Response zu 500en."""
    if session.get(ErForecastEinordnung, (pair_key, next_week[0], next_week[1])):
        return
    session.add(ErForecastEinordnung(
        pair_key=pair_key,
        iso_year=next_week[0],
        iso_week=next_week[1],
        einordnung=text,
        model=OPUS_MODEL_ALIAS,
    ))
    try:
        session.commit()
    except Exception:  # noqa: BLE001 — Insert-Race, Row existiert bereits
        session.rollback()


def generate_er_forecast(
    session: Session,
    pair_key: str,
    pair_def: dict,
    *,
    weeks: Optional[int] = None,
    apply_gate: bool = False,
) -> dict:
    """Vollständige ER-Prognose für ein Pair: pro Markt Regression über die
    ER-Zeitreihe + eine gemeinsame LLM-Einordnung.

    ``apply_gate`` (#252 Ehrlichkeits-Gate): ``True`` = öffentliche Sicht,
    Märkte unter den Gate-Schwellen (R² < 0.5 oder n < 5) kommen als
    ``status='too_volatile'`` OHNE Prognosewert zurück. ``False`` (Default,
    Admin-Pfad) = ungegatete Roh-Sicht mit allen Werten. Die LLM-Einordnung
    wird IMMER aus der gegateten Sicht gebaut — sie ist public-safe und darf
    keine Zahl ausplaudern, die die öffentliche UI verschweigt (und sie wird
    pro (pair, ziel_woche) gecacht und von beiden Pfaden geteilt).

    Rückgabe (plain dict; der Endpoint gießt es in die Pydantic-Antwort)::

        {
          "pair_key": str,
          "n_axis_weeks": int,
          "next_week": {"iso_year","iso_week"} | None,
          "markets": {"DE": {...}, "US": {...}, "UK": {...}},   # je _forecast_one_market
          "einordnung": str | None,                              # None wenn LLM aus/fehlerhaft
        }
    """
    timeline = compute_market_timeline(session, pair_key, pair_def, weeks=weeks)
    axis = timeline["weeks"]
    per_market_raw = {
        m: _forecast_one_market(timeline["markets"].get(m, []))
        for m in TIMELINE_MARKET_VALUES
    }
    per_market_gated = {m: _apply_honesty_gate(r) for m, r in per_market_raw.items()}
    per_market = per_market_gated if apply_gate else per_market_raw
    next_week = _next_week(axis)

    # Split-Cache (#252): Regression oben ist gratis und immer live; nur die
    # Einordnung kostet. Cache-Key = (pair, Ziel-Woche der Prognose) —
    # Cache-Hit liest, Cache-Miss generiert EINEN Opus-Call und persistiert.
    einordnung: Optional[str] = None
    einordnung_source: Optional[str] = None  # "cache" | "generated" | None
    any_ok = any(r.get("status") == "ok" for r in per_market_raw.values())
    if any_ok and next_week is not None:
        einordnung = _load_cached_einordnung(session, pair_key, next_week)
        if einordnung is not None:
            einordnung_source = "cache"
        elif is_anthropic_configured():
            pair_label = pair_def.get("label") or pair_def.get("display_name") or pair_key
            prompt = _build_einordnung_prompt(pair_label, len(axis), per_market_gated, next_week)
            try:
                msg = messages_create_text(
                    model=OPUS_MODEL_ALIAS,
                    system=_EINORDNUNG_SYSTEM,
                    user_message=prompt,
                    max_tokens=400,
                )
                usage = getattr(msg, "usage", None)
                if usage is not None:
                    # Costlog-Lücke geschlossen (#252): der Forecast-Call war
                    # vorher für den F0.7-Anthropic-Cap unsichtbar.
                    record_anthropic_call(
                        usage,
                        model=OPUS_MODEL_ALIAS,
                        operation="er_forecast",
                        meta={
                            "pair_key": pair_key,
                            "iso_year": next_week[0],
                            "iso_week": next_week[1],
                        },
                    )
                parts = [
                    getattr(block, "text", "")
                    for block in (getattr(msg, "content", None) or [])
                ]
                text = "".join(parts).strip()
                if text:
                    einordnung = text
                    einordnung_source = "generated"
                    _persist_einordnung(session, pair_key, next_week, text)
            except AnthropicAPIError as exc:
                # Best-effort: die Regression steht auch ohne Einordnung. Nicht 500en.
                logger.warning("er-forecast einordnung failed for pair=%s: %s", pair_key, exc)
                einordnung = None

    return {
        "pair_key": pair_key,
        "n_axis_weeks": len(axis),
        "next_week": (
            {"iso_year": next_week[0], "iso_week": next_week[1]} if next_week else None
        ),
        "markets": per_market,
        "einordnung": einordnung,
        "einordnung_source": einordnung_source,
    }
