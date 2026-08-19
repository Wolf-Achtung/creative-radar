"""Wartung 20.08.2026 — Vision-Durchsatz: Preis, Deckel, Zeitbudget.

Anlass
------
Die beiden Vision-Deckel im Cron (``cron_vision_max_assets_per_run`` = 50,
``cron_vision_backlog_max_assets_per_run`` = 200) waren an einem
Vision-Preis kalibriert, der aus der gpt-4o-mini-Zeit stammte und nie
nachgezogen wurde: ``_VISION_COST_USD_PER_CALL = 0.015``, im Kommentar
ausdruecklich als "gpt-4o-mini Vision pricing ballpark" ausgewiesen.
Gemessen am costlog kostet ein Call auf gpt-5.4-mini ~$0,0027 — Faktor
5,6 daneben.

Die Folge war kein Kostenfehler, sondern ein stiller Datenverlust: bei
~680 neuen Assets pro Woche liessen 50 + 200 rund zwei Drittel liegen,
und Instagram-CDN-Links laufen nach 24-48h ab. Was ein Lauf nicht
anfasst, ist danach oft nicht mehr analysierbar. Gemessen ueber 90 Tage:
3.228 Assets ``pending``, 3.023 ``analyzed``, 2.338 ``fetch_failed``.

Was hier geprueft wird
----------------------
1. Der Preis-Wert ist korrigiert und die Rechnung im Summary haengt an
   der Konstante, nicht an einer zweiten Kopie der Zahl.
2. Die Deckel sind angehoben — mit dem Grund als Testtext, damit ein
   spaeteres Absenken eine bewusste Entscheidung ist und kein Versehen.
3. Das neue ZEITbudget haelt: ohne es waere das Anheben der Deckel
   fahrlaessig, weil ein Stueckzahl-Deckel nichts ueber Laufzeit sagt
   (genau der Fehler aus dem Vorfall 10.08.2026 in der Post-Analyse).
   Beide Vision-Stages teilen sich EINEN Zeitpunkt.
"""
from __future__ import annotations

import time as _echte_zeit
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.api import cron as cron_module
from app.config import Settings
from app.models.entities import Asset, CronRun
from app.services import asset_screenshot_persistence as persistence_mod

# Die Cron-Fixtures liegen bewusst nicht in conftest.py (dieses Projekt
# haelt sie je Testmodul). Statt sie hier ein zweites Mal zu bauen —
# zwei Kopien laufen auseinander — werden sie importiert. pytest loest
# Fixtures ueber den Modul-Namensraum auf, der Import genuegt.
from app.tests.test_cron_sync import (  # noqa: F401  (Fixture-Import)
    _ig_item,
    _make_vision_stub,
    _seed_ig_channel,
    _stub_capture,
    _stub_capture_async,
    client_with_auth,
    db,
)


# ---------------------------------------------------------------------------
# 1. Der Preis
# ---------------------------------------------------------------------------


def test_vision_kosten_konstante_ist_nicht_mehr_der_gpt_4o_mini_wert():
    """Der Naeherungswert im Cron-Summary muss zum aktuellen Modell passen.

    Gemessen am costlog (20.08.2026): 1.409 ``vision_call``-Zeilen auf
    gpt-5.4-mini, 379,73 Cent gesamt => $0,002695 pro Call.

    Die untere Schranke steht hier, damit niemand versehentlich auf 0
    faellt (dann meldet das Summary immer $0 und der Kosten-Blick geht
    verloren); die obere haelt den alten gpt-4o-mini-Wert draussen.
    """
    wert = cron_module._VISION_COST_USD_PER_CALL
    assert wert != 0.015, (
        "0.015 ist der gpt-4o-mini-Wert aus dem Sprint-Beta-Plan — er hat "
        "die beiden Vision-Deckel fuenfmal zu eng gerechnet."
    )
    assert 0.0005 <= wert <= 0.006, (
        f"{wert} liegt ausserhalb der am costlog gemessenen Groessenordnung "
        "(~$0,0027/Call auf gpt-5.4-mini). Beim Modellwechsel neu messen — "
        "nicht schaetzen."
    )


def test_summary_rechnet_kosten_ueber_die_konstante(db):
    """attempted * Konstante — keine eingefrorene zweite Zahl."""
    kanal = _seed_ig_channel(db, handle="netflixde")
    basis = datetime.now(timezone.utc) - timedelta(days=5)
    for i in range(3):
        _pending_asset(db, kanal, slug=f"preis-{i}", created_at=basis + timedelta(hours=i))

    protokoll: list = []
    with patch("app.api.cron.analyze_asset_visual", side_effect=_make_vision_stub(protokoll)):
        with Session(db) as session:
            ergebnis = cron_module._run_vision_backlog(
                session, backlog_cap=3, exclude_ids=[]
            )

    assert ergebnis["attempted"] == 3
    assert ergebnis["estimated_cost_usd"] == round(
        3 * cron_module._VISION_COST_USD_PER_CALL, 4
    )


# ---------------------------------------------------------------------------
# 2. Die Deckel
# ---------------------------------------------------------------------------


# Gemessener Zufluss: ~680 neue Assets pro Woche ueber alle Kanaele.
# Ein Deckel unterhalb dieser Zahl heisst: der Stapel waechst schneller,
# als er abgearbeitet wird — und verfaellt dabei.
_WOECHENTLICHER_ZUFLUSS = 680


@pytest.mark.parametrize(
    "feld",
    ["cron_vision_max_assets_per_run", "cron_vision_backlog_max_assets_per_run"],
)
def test_vision_deckel_decken_den_woechentlichen_zufluss(feld):
    """Entscheidungs-Protokoll, kein Naturgesetz.

    Wird ein Deckel bewusst wieder gesenkt, gehoert der Grund hierher —
    dann faellt dieser Test, und das ist der Punkt. Er soll verhindern,
    dass ein Wert *unbemerkt* zurueckrutscht, so wie er zwischen dem
    Modellwechsel und dem 20.08.2026 unbemerkt zu klein blieb.
    """
    # Bewusst der eingecheckte DEFAULT, nicht ``settings.<feld>``: eine
    # ENV-Belegung in der Testumgebung soll dieses Protokoll nicht
    # uebertoenen — geprueft wird, was im Code steht.
    wert = Settings.model_fields[feld].default
    assert wert >= _WOECHENTLICHER_ZUFLUSS, (
        f"{feld}={wert} liegt unter dem gemessenen Wochen-Zufluss von "
        f"~{_WOECHENTLICHER_ZUFLUSS} Assets. Dann waechst der pending-Stapel "
        "schneller, als er geleert wird, und die CDN-Links darin verfallen "
        "binnen 24-48h. Gegen Laufzeit schuetzt VISION_STAGE_TIMEOUT_SECONDS, "
        "nicht dieser Deckel."
    )


# ---------------------------------------------------------------------------
# 3. Das Zeitbudget — Helfer
# ---------------------------------------------------------------------------


def test_zeitbudget_default_ohne_env(monkeypatch):
    monkeypatch.delenv("VISION_STAGE_TIMEOUT_SECONDS", raising=False)
    assert cron_module._vision_stage_budget_seconds() == 1800


def test_zeitbudget_liest_env(monkeypatch):
    monkeypatch.setenv("VISION_STAGE_TIMEOUT_SECONDS", "600")
    assert cron_module._vision_stage_budget_seconds() == 600


def test_zeitbudget_unlesbar_faellt_auf_default(monkeypatch, caplog):
    """Ein Tippfehler in Railway darf die Stage nicht abschalten.

    Der stille Weg waere: unlesbar -> 0 -> kein Budget, oder unlesbar ->
    Exception -> Cron tot. Beides waere schlechter als der Default, und
    beides waere unsichtbar. Deshalb Default + Warnung.
    """
    monkeypatch.setenv("VISION_STAGE_TIMEOUT_SECONDS", "eine halbe stunde")
    with caplog.at_level("WARNING"):
        assert cron_module._vision_stage_budget_seconds() == 1800
    assert any("cron-vision-budget-unparsable" in s.message for s in caplog.records)


def test_zeitbudget_null_ist_erlaubt_und_heisst_kein_budget(monkeypatch):
    """Rueckfallweg auf das alte Verhalten ohne Code-Aenderung.

    Anders als die uebrigen Stage-Timeouts (die ``max(1, ...)`` erzwingen)
    darf dieser Wert 0 sein: steht das Budget je im Weg, laesst sich per
    ENV auf 'nur Stueckzahl begrenzt' zurueckschalten.
    """
    monkeypatch.setenv("VISION_STAGE_TIMEOUT_SECONDS", "0")
    assert cron_module._vision_stage_budget_seconds() == 0


# ---------------------------------------------------------------------------
# 3b. Das Zeitbudget — Schleife
# ---------------------------------------------------------------------------


class _ZeitSchalter:
    """Ersetzt ``time`` NUR innerhalb von ``app.api.cron``.

    Ein globales Patchen von ``time.monotonic`` wuerde jede Bibliothek im
    Prozess mit treffen. Dieser Schalter reicht alles ausser ``monotonic``
    an das echte Modul weiter, sodass nur die geprueften Stellen eine
    kontrollierte Uhr sehen.
    """

    def __init__(self, start: float = 1000.0):
        self._jetzt = start

    def monotonic(self) -> float:
        return self._jetzt

    def vorspulen(self, sekunden: float) -> None:
        self._jetzt += sekunden

    def __getattr__(self, name):
        return getattr(_echte_zeit, name)


def _pending_asset(db, channel, *, slug: str, created_at: datetime):
    from app.models.entities import Asset as AssetModel, Post

    with Session(db) as session:
        post = Post(
            channel_id=channel.id,
            platform="instagram",
            post_url=f"https://www.instagram.com/p/{slug}/",
            created_at=created_at,
        )
        session.add(post)
        session.commit()
        session.refresh(post)
        asset = AssetModel(
            post_id=post.id,
            visual_analysis_status="pending",
            created_at=created_at,
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset.id


def _vier_pending(db):
    kanal = _seed_ig_channel(db, handle="netflixde")
    basis = datetime.now(timezone.utc) - timedelta(days=10)
    return [
        _pending_asset(db, kanal, slug=f"budget-{i}", created_at=basis + timedelta(hours=i))
        for i in range(4)
    ]


def _stub_der_zeit_verbraucht(protokoll: list, uhr: _ZeitSchalter, sekunden: float):
    echt = _make_vision_stub(protokoll)

    def fake(session, asset):
        ergebnis = echt(session, asset)
        uhr.vorspulen(sekunden)
        return ergebnis

    return fake


def test_ohne_deadline_laeuft_der_ganze_stapel(db):
    ids = _vier_pending(db)
    protokoll: list = []
    with patch("app.api.cron.analyze_asset_visual", side_effect=_make_vision_stub(protokoll)):
        with Session(db) as session:
            zaehler = cron_module._vision_process_ids(session, ids, deadline=None)

    assert zaehler["attempted"] == 4
    assert zaehler["skipped_budget"] == 0


def test_abgelaufene_deadline_faengt_gar_nicht_erst_an(db):
    """Der Zeitpunkt wird VOR jedem Asset geprueft, nicht danach.

    Ist das Budget beim Eintritt schon leer — etwa weil die frische Stage
    es aufgebraucht hat — darf der Backlog-Drain keinen einzigen Call
    mehr starten. Sonst waere das geteilte Budget nur eine Behauptung.
    """
    ids = _vier_pending(db)
    protokoll: list = []
    uhr = _ZeitSchalter()
    with patch.object(cron_module, "time", uhr), \
         patch("app.api.cron.analyze_asset_visual", side_effect=_make_vision_stub(protokoll)):
        with Session(db) as session:
            zaehler = cron_module._vision_process_ids(
                session, ids, deadline=uhr.monotonic() - 1
            )

    assert protokoll == []
    assert zaehler["attempted"] == 0
    assert zaehler["skipped_budget"] == 4


def test_deadline_bricht_mitten_im_stapel_ab_und_zaehlt_den_rest(db, caplog):
    """Kernzusage: was nicht mehr in die Zeit passt, bleibt SICHTBAR liegen.

    Uhr steht auf 1000, jeder Call kostet 10s, Budget bis 1025:
    Asset 0 (1000), 1 (1010), 2 (1020) starten noch, Asset 3 (1030) nicht
    mehr. Der Rest landet als ``skipped_budget`` im Summary — die
    Alternative waere ein stiller Abbruch, den niemand im Cron-Report
    sieht.
    """
    ids = _vier_pending(db)
    protokoll: list = []
    uhr = _ZeitSchalter(start=1000.0)
    with patch.object(cron_module, "time", uhr), \
         patch(
             "app.api.cron.analyze_asset_visual",
             side_effect=_stub_der_zeit_verbraucht(protokoll, uhr, 10.0),
         ), \
         caplog.at_level("WARNING"):
        with Session(db) as session:
            zaehler = cron_module._vision_process_ids(session, ids, deadline=1025.0)

    assert len(protokoll) == 3
    assert zaehler["attempted"] == 3
    assert zaehler["succeeded"] == 3
    assert zaehler["skipped_budget"] == 1
    assert any("cron-vision-budget-exhausted" in s.message for s in caplog.records)

    # Das ueberzaehlige Asset bleibt pending — es ist Arbeit fuer den
    # naechsten Lauf, kein Fehler.
    with Session(db) as session:
        offen = list(session.exec(
            select(Asset).where(Asset.visual_analysis_status == "pending")
        ).all())
    assert len(offen) == 1


def test_skipped_budget_steht_immer_im_summary(db):
    """Auch bei 0 — Dashboard-Logik soll nicht gegen fehlende Keys
    verteidigen muessen (gleiche Regel wie bei den Rematch-Zaehlern)."""
    ids = _vier_pending(db)
    protokoll: list = []
    with patch("app.api.cron.analyze_asset_visual", side_effect=_make_vision_stub(protokoll)):
        with Session(db) as session:
            frisch = cron_module._run_vision_after_sync(session, ids[:2], cap=2)
            backlog = cron_module._run_vision_backlog(
                session, backlog_cap=5, exclude_ids=ids[:2]
            )

    assert frisch["skipped_budget"] == 0
    assert backlog["skipped_budget"] == 0


# ---------------------------------------------------------------------------
# 3c. Das Zeitbudget — Verdrahtung im Cron-Lauf
# ---------------------------------------------------------------------------


def _cron_lauf_mit_gefangenen_stages(client_with_auth, monkeypatch, *, budget_env: str | None):
    """Faehrt einen echten Cron-Lauf, ersetzt aber beide Vision-Stages
    durch Attrappen, die ihre ``deadline``-Zusage protokollieren."""
    gefangen: dict = {}

    def falsche_frische(session, asset_ids, cap, *, deadline=None):
        gefangen["frisch"] = deadline
        return {"attempted": 0, "skipped_budget": 0}

    def falscher_backlog(session, backlog_cap, *, exclude_ids, deadline=None):
        gefangen["backlog"] = deadline
        return {"enabled": True, "attempted": 0, "skipped_budget": 0}

    if budget_env is None:
        monkeypatch.delenv("VISION_STAGE_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("VISION_STAGE_TIMEOUT_SECONDS", budget_env)

    items = [_ig_item(f"wiring-{i}") for i in range(2)]
    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=items), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=_stub_capture), \
         patch.object(persistence_mod, "capture_asset_screenshot_async",
                      side_effect=_stub_capture_async), \
         patch("app.api.cron._run_vision_after_sync", side_effect=falsche_frische), \
         patch("app.api.cron._run_vision_backlog", side_effect=falscher_backlog):
        antwort = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )
    assert antwort.status_code == 202, antwort.text
    return gefangen, antwort.json()["run_id"]


def test_beide_vision_stages_bekommen_denselben_zeitpunkt(
    client_with_auth, db, monkeypatch
):
    """Ein Budget fuer beide, nicht eines je Stage.

    Bekaeme jede Stage ihr eigenes Budget, waere die Summe doppelt so
    gross wie der konfigurierte Wert — und der Schutz vor dem
    Gesamt-Timeout waere nur halb so gut wie gedacht.
    """
    _seed_ig_channel(db, handle="netflixde")
    gefangen, run_id = _cron_lauf_mit_gefangenen_stages(
        client_with_auth, monkeypatch, budget_env="600"
    )

    assert gefangen["frisch"] is not None
    assert gefangen["frisch"] == gefangen["backlog"]

    with Session(db) as session:
        lauf = session.get(CronRun, uuid4().__class__(run_id))
        assert lauf.summary_json["vision_budget_seconds"] == 600


def test_budget_null_schaltet_die_zeitgrenze_ab(client_with_auth, db, monkeypatch):
    """Der dokumentierte Rueckfallweg muss bis in die Stages durchschlagen."""
    _seed_ig_channel(db, handle="netflixde")
    gefangen, run_id = _cron_lauf_mit_gefangenen_stages(
        client_with_auth, monkeypatch, budget_env="0"
    )

    assert gefangen["frisch"] is None
    assert gefangen["backlog"] is None

    with Session(db) as session:
        lauf = session.get(CronRun, uuid4().__class__(run_id))
        assert lauf.summary_json["vision_budget_seconds"] == 0
