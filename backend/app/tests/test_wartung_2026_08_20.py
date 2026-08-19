"""Wächter aus dem Nachgang zum Wartungsdurchgang, 20.08.2026.

Drei Themen:

1. **Fund C.** Ein Aufruf ohne verwertbare Antwort trägt nicht länger die
   Kennzeichen eines gelungenen (``ReviewStatus.NEW``, Confidence 0.5).

2. **Sonnet-Preis.** Anthropic hat die zum 01.09.2026 geplante Erhöhung
   auf $3/$15 abgesagt; $2/$10 ist die Standard-Rate. Die Config trug bis
   heute den nie eingetretenen Zukunftspreis.

3. **Citation-Auswertung.** Die Rechenkerne von
   ``scripts/diag_citation_rate.py`` — die Zahl, auf der die
   Cutover-Entscheidung beruhen soll, darf nicht ungeprüft sein.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.entities import AssetType, ReviewStatus
from app.services import creative_ai

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKRIPT = _REPO_ROOT / "scripts" / "diag_citation_rate.py"


def _diag_modul():
    """Laedt ``scripts/diag_citation_rate.py`` ueber den Dateipfad.

    Ein schlichtes ``import scripts.diag_citation_rate`` greift ins
    Leere: ``backend/scripts`` ist ein eigenes, importierbares Package
    gleichen Namens, und ``pythonpath = .`` in pytest.ini macht
    ``backend/`` zur Wurzel — der Name ``scripts`` ist also bereits
    vergeben. Die anderen Root-Skript-Tests umgehen das per Subprozess;
    hier sollen die reinen Rechenfunktionen direkt geprueft werden, also
    laedt der Test die Datei am Package-System vorbei.
    """
    spec = importlib.util.spec_from_file_location("_diag_citation_rate", _SKRIPT)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


# ---------------------------------------------------------------------
# 1 — Fund C: ein Nicht-Ergebnis sieht nicht mehr aus wie ein Ergebnis
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "rohtext, warum",
    [
        ("Tut mir leid, das kann ich nicht.", "Fließtext statt JSON"),
        ("{}", "leeres Objekt"),
        ("", "gar keine Antwort"),
        ('{"ai_summary_de": "   "}', "nur Leerraum"),
        ('{"confidence_score": 0.9}', "nur Beiwerk, kein Text"),
    ],
)
def test_leere_antwort_wird_als_zu_pruefen_gefuehrt(rohtext, warum):
    """Der Kern von Fund C. Vorher: ``NEW`` + 0.5 — von einer echten
    Analyse nur am deutschen Platzhaltersatz zu unterscheiden."""
    ergebnis = creative_ai._shape_response(rohtext)
    assert ergebnis["review_status"] == ReviewStatus.NEEDS_REVIEW, (
        f"{warum}: geht weiterhin als fertige Analyse durch."
    )
    assert ergebnis["confidence_score"] == 0.2, (
        f"{warum}: {ergebnis['confidence_score']} behauptet Zuversicht, die "
        f"es nicht gibt."
    )


@pytest.mark.parametrize(
    "rohtext, erwartete_confidence",
    [
        ('{"ai_summary_de": "Ein Teaser mit hartem Schnitt.", "confidence_score": 0.8}', 0.8),
        ('{"ai_trend_notes": "Wiederkehrendes Motiv."}', 0.5),
    ],
)
def test_brauchbare_antwort_bleibt_unveraendert(rohtext, erwartete_confidence):
    """Gegenprobe. Die Änderung darf ausschließlich den Leerfall treffen —
    eine verwertbare Antwort ohne ``confidence_score`` behält den alten
    Helfer-Default 0.5."""
    ergebnis = creative_ai._shape_response(rohtext)
    assert ergebnis["review_status"] == ReviewStatus.NEW
    assert ergebnis["confidence_score"] == erwartete_confidence


def test_leerfall_und_fehlender_schluessel_antworten_gleich():
    """``_unconfigured_response`` (kein API-Key) und der Leerfall sind
    dasselbe Problem aus Sicht des Menschen vor der Prüfliste: es liegt
    keine Analyse vor. Beide sollen es gleich sagen."""
    ohne_key = creative_ai._unconfigured_response(AssetType.UNKNOWN)
    leer = creative_ai._shape_response("kein JSON")
    assert ohne_key["review_status"] == leer["review_status"] == ReviewStatus.NEEDS_REVIEW
    assert ohne_key["confidence_score"] == leer["confidence_score"] == 0.2


def test_die_pruefliste_verliert_nichts():
    """Der Grund, warum diese Änderung risikoarm ist: das Frontend zählt
    ``new`` UND ``needs_review`` als offene Prüfung. Umetikettieren
    versteckt also nichts.

    Der Test liest das Frontend, statt es zu behaupten — fällt jemand
    dort auf reines ``=== 'new'`` zurück, verschwinden die Fehlschläge
    lautlos aus der Liste, und dieser Test fällt.
    """
    quelle = (_REPO_ROOT / "frontend" / "src" / "AdminApp.jsx").read_text(
        encoding="utf-8"
    )
    zeilen = [z for z in quelle.splitlines() if "openReview" in z and "filter" in z]
    assert zeilen, "Die openReview-Zählung ist verschwunden oder umbenannt."
    for zeile in zeilen:
        assert "needs_review" in zeile, (
            f"{zeile.strip()!r} zählt needs_review nicht mehr mit — dann "
            f"verschwinden fehlgeschlagene Analysen aus der Prüfliste."
        )


# ---------------------------------------------------------------------
# 2 — Der Sonnet-Preis
# ---------------------------------------------------------------------


def test_sonnet_preis_ist_die_standard_rate():
    """Anthropic hat die Erhöhung zum 01.09.2026 abgesagt; $2/$10 pro
    MTok ist die Standard-Rate (Preisseite, abgerufen 20.08.2026).

    Die Config trug bis heute 0.003/0.015 — den Zukunftspreis, der nie
    eingetreten ist. Cost-Logs überschätzten den Sonnet-Anteil um 50 %.
    """
    assert settings.anthropic_sonnet_input_per_1k_usd == 0.002
    assert settings.anthropic_sonnet_output_per_1k_usd == 0.010


def test_preise_entsprechen_der_anbieter_liste():
    """Alle drei Modell-Preise gegen die Anbieter-Liste vom 20.08.2026,
    in der Einheit, die ``cost_log`` benutzt (USD pro 1k Token)."""
    erwartet = {
        "haiku": (0.001, 0.005),    # $1 / $5 pro MTok
        "sonnet": (0.002, 0.010),   # $2 / $10 pro MTok
        "opus": (0.005, 0.025),     # $5 / $25 pro MTok
    }
    for stufe, (ein, aus) in erwartet.items():
        assert getattr(settings, f"anthropic_{stufe}_input_per_1k_usd") == ein, stufe
        assert getattr(settings, f"anthropic_{stufe}_output_per_1k_usd") == aus, stufe


def test_kein_hinweis_mehr_auf_die_abgesagte_erhoehung():
    """Der alte Kommentar kündigte eine Standard-Rate ab 01.09.2026 an.
    Wer ihn stehen lässt, setzt den Preis beim nächsten Durchgang wieder
    hoch."""
    quelle = (Path(__file__).resolve().parents[1] / "config.py").read_text(
        encoding="utf-8"
    )
    block = quelle[quelle.find("anthropic_sonnet_input_per_1k_usd") - 1200:]
    block = block[: block.find("anthropic_opus_model")]
    assert "abgesagt" in block or "will not occur" in block, (
        "Im Sonnet-Preisblock steht nicht, dass die Erhöhung abgesagt "
        "wurde — der nächste Leser hebt den Preis wieder an."
    )


# ---------------------------------------------------------------------
# 3 — Die Citation-Auswertung
# ---------------------------------------------------------------------


def _zeile(pair_key: str, jahr: int, woche: int, llm_output: dict, aggregation: dict):
    return SimpleNamespace(
        pair_key=pair_key,
        iso_year=jahr,
        iso_week=woche,
        llm_output=llm_output,
        aggregation=aggregation,
    )


@pytest.fixture
def auswerten():
    return _diag_modul().auswerten


def _minimal_agg(post_urls: list[str]) -> dict:
    """Eine echte ``PairAggregation``, serialisiert wie in der DB.

    Bewusst ueber die echten Pydantic-Modelle statt ueber ein
    handgeschriebenes dict: die gespeicherten Blobs entstehen per
    ``model_dump``, und ein von Hand nachgebautes dict wuerde bei der
    naechsten Schema-Aenderung still danebenliegen — genau der Fehler,
    den diese Auswertung nicht machen darf.
    """
    from app.schemas.insights import ChannelStats, PairAggregation, TitleCoverage, TopPost

    def _kanal(markt: str) -> ChannelStats:
        return ChannelStats(
            handle=f"@{markt.lower()}", market=markt, channel_id=None,
            channel_found=True, posts_count=len(post_urls), assets_count=0,
            coverage_pct=0.0, top_hashtags=[], avg_caption_length=0.0,
            avg_duration_seconds=0.0, duration_buckets={}, avg_engagement=0.0,
            top_posts=[
                TopPost(
                    post_url=u, caption_excerpt="", duration_seconds=0.0,
                    engagement_sum=0, likes=0, comments=0, shares=0, saves=0, views=0,
                )
                for u in post_urls
            ],
        )

    agg = PairAggregation(
        pair_key="p", pair_label="P", platform="instagram", window_days=30,
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-08-15T00:00:00+00:00",
        iso_week=33, iso_year=2026,
        de_channel=_kanal("DE"), us_channel=_kanal("US"),
        cross_market_matches=[],
        title_coverage=TitleCoverage(
            titles_in_both_markets=[], de_only_titles=[], us_only_titles=[],
            de_assets_with_title=0, de_assets_total=0,
            us_assets_with_title=0, us_assets_total=0, overall_coverage_pct=0.0,
        ),
        notes=[],
    )
    return agg.model_dump(mode="json")


def _bericht_mit_zitaten(ids: list[str]) -> dict:
    """Ein echter ``LLMReport``, serialisiert wie in der DB. Die Zitate
    haengen an genau einer Sektion (``trends[0]``), damit die Zaehlung
    eindeutig zuzuordnen ist."""
    from app.schemas.insights import CrossMarketInsight, LLMReport, Trend

    bericht = LLMReport(
        headline="H", tldr="T",
        trends=[Trend(
            name="n", evidence="e", implication_for_creation="i",
            cited_post_ids=ids,
        )],
        actions=[], risks=[], data_caveats=["d"],
        cross_market_insight=CrossMarketInsight(
            de_vs_us="a", transfer_opportunity="b", cited_post_ids=[],
        ),
    )
    return bericht.model_dump(mode="json")


def test_saubere_zitate_ergeben_null_prozent(auswerten):
    agg = _minimal_agg(["https://example.com/p1"])
    zeilen = [_zeile("p", 2026, 33, _bericht_mit_zitaten(["https://example.com/p1"]), agg)]
    e = auswerten(zeilen)
    assert e["zitiert_gesamt"] == 1
    assert e["fehlend_gesamt"] == 0
    assert e["rate_prozent"] == 0.0
    assert e["briefs_mit_fehler"] == 0


def test_erfundene_id_wird_gezaehlt(auswerten):
    agg = _minimal_agg(["https://example.com/p1"])
    zeilen = [
        _zeile("p", 2026, 33, _bericht_mit_zitaten(["https://example.com/erfunden"]), agg)
    ]
    e = auswerten(zeilen)
    assert e["fehlend_gesamt"] == 1
    assert e["rate_prozent"] == 100.0
    assert e["briefs_mit_fehler"] == 1


def test_rate_rechnet_ueber_ids_nicht_ueber_briefs(auswerten):
    """Das Kriterium lautet "Falsch-Zitat-Rate", nicht "Anteil
    fehlerhafter Briefs". Ein Brief mit 1 Fehler unter 100 Zitaten ist
    1 %, nicht 100 %."""
    agg = _minimal_agg([f"https://example.com/p{i}" for i in range(99)])
    ids = [f"https://example.com/p{i}" for i in range(99)] + ["https://example.com/x"]
    e = auswerten([_zeile("p", 2026, 33, _bericht_mit_zitaten(ids), agg)])
    assert e["zitiert_gesamt"] == 100
    assert e["fehlend_gesamt"] == 1
    assert e["rate_prozent"] == pytest.approx(1.0)
    assert e["briefs_mit_fehler"] == 1


def test_unlesbare_altzeile_bricht_nicht_ab(auswerten):
    """Ältere Briefs können Blobs tragen, die das heutige Schema nicht
    mehr validiert. Die sollen gezählt und übersprungen werden — nicht
    die ganze Auswertung kippen."""
    agg = _minimal_agg(["https://example.com/p1"])
    zeilen = [
        _zeile("kaputt", 2026, 30, {"voellig": "anders"}, {"auch": "anders"}),
        _zeile("gut", 2026, 33, _bericht_mit_zitaten(["https://example.com/p1"]), agg),
    ]
    e = auswerten(zeilen)
    assert len(e["unlesbar"]) == 1
    assert e["briefs_gesamt"] == 1
    assert e["rate_prozent"] == 0.0


def test_brief_ohne_zitate_verzerrt_die_rate_nicht(auswerten):
    """Leere ``cited_post_ids`` sind kein Falsch-Zitat. Sie dürfen die
    Rate weder heben noch senken — nur separat gezählt werden."""
    agg = _minimal_agg(["https://example.com/p1"])
    zeilen = [
        _zeile("leer", 2026, 32, _bericht_mit_zitaten([]), agg),
        _zeile("gut", 2026, 33, _bericht_mit_zitaten(["https://example.com/p1"]), agg),
    ]
    e = auswerten(zeilen)
    assert e["briefs_ohne_zitat"] == 1
    assert e["zitiert_gesamt"] == 1
    assert e["rate_prozent"] == 0.0


def test_ohne_jedes_zitat_kein_urteil(auswerten):
    """Wenn nirgends zitiert wird, ist die Rate undefiniert. Das Skript
    darf dann nicht "0 % — alles gut" melden."""
    _urteil = _diag_modul()._urteil

    agg = _minimal_agg(["https://example.com/p1"])
    e = auswerten([_zeile("leer", 2026, 33, _bericht_mit_zitaten([]), agg)])
    assert e["rate_prozent"] is None
    assert "KEIN URTEIL" in _urteil(e)


def test_urteil_haengt_an_der_schwelle_aus_der_config():
    """Die 2 %-Schwelle stammt aus dem Cutover-Kriterium in config.py.
    Ober- und unterhalb muss das Urteil kippen."""
    modul = _diag_modul()
    SCHWELLE_PROZENT, _urteil = modul.SCHWELLE_PROZENT, modul._urteil

    assert SCHWELLE_PROZENT == 2.0
    knapp_drunter = {
        "rate_prozent": 1.9, "briefs_gesamt": 10, "briefs_mit_fehler": 1,
        "briefs_ohne_zitat": 0,
    }
    knapp_drueber = {**knapp_drunter, "rate_prozent": 2.1}
    assert "ERFUELLT" in _urteil(knapp_drunter)
    assert "NICHT erfuellt" in _urteil(knapp_drueber)


def test_skript_schreibt_nichts():
    """Ein Diagnose-Skript, das gegen die Produktions-DB läuft, muss
    read-only sein. Der Test liest den Syntaxbaum statt der Prosa."""
    quelle = (_REPO_ROOT / "scripts" / "diag_citation_rate.py").read_text(
        encoding="utf-8"
    )
    verboten = ("session.add", "session.commit", "session.delete", "exec_driver_sql")
    baum = ast.parse(quelle)
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Call):
            gerendert = ast.unparse(knoten.func)
            for muster in verboten:
                assert muster not in gerendert, (
                    f"Zeile {knoten.lineno}: {gerendert} — das Skript soll "
                    f"ausschliesslich lesen."
                )


def test_auswertung_benutzt_den_echten_validator():
    """Der Wert des Skripts hängt daran, dass es DENSELBEN Allow-Set-Bau
    und dieselbe ID-Sammlung benutzt wie der Cron-Lauf. Eine Kopie würde
    mit der Zeit auseinanderlaufen und eine falsche Zahl liefern."""
    quelle = inspect.getsource(_diag_modul().auswerten)
    assert "_build_citation_allow_set" in quelle
    assert "_collect_cited_ids" in quelle
    assert "from app.services.insight_engine import" in quelle
