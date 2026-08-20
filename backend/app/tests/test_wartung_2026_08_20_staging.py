"""Staging-Fundament, 20.08.2026 — die zwei Bausteine, die tragen muessen.

1. **Richtungs-Sicherung des Refresh-Skripts.** ``scripts/
   staging_refresh.py`` kopiert die Prod-DB nach Staging. Die einzige
   Katastrophe, die es anrichten koennte, ist die umgekehrte Richtung:
   DROP SCHEMA auf der Produktion. ``pruefe_richtung`` ist die Sicherung
   davor — sie ist hier in jedem Ablehnungsfall festgehalten, denn eine
   Sicherung, die nicht getestet ist, ist eine Behauptung.

2. **Feature-Flag-Geruest.** Trailer-Intelligence wird auf main hinter
   ``FEATURE_TRAILER_INTELLIGENCE_ENABLED`` entwickelt; das Frontend
   liest den Zustand aus ``GET /api/health`` → ``features``. Prod (Flag
   aus) und Staging (Flag an) fahren denselben Build.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import feature_flags
from app.main import app

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKRIPT = _REPO_ROOT / "scripts" / "staging_refresh.py"


def _refresh_modul():
    """Laedt das Skript per Dateipfad — gleicher Weg und gleicher Grund
    wie ``_diag_modul`` in test_wartung_2026_08_20.py: ``scripts`` als
    Modulname ist von ``backend/scripts`` besetzt."""
    spec = importlib.util.spec_from_file_location("_staging_refresh", _SKRIPT)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


PROD = "postgresql://cr:geheim@prod-db.railway.internal:5432/creative_radar"
STAGING = "postgresql://cr:geheim@staging-db.railway.internal:5432/creative_radar"


# ---------------------------------------------------------------------
# 1 — Richtungs-Sicherung
# ---------------------------------------------------------------------


def test_richtige_richtung_geht_durch():
    _refresh_modul().pruefe_richtung(PROD, STAGING, "staging-db.railway.internal")


@pytest.mark.parametrize(
    "quelle, ziel, ziel_host, warum",
    [
        ("", STAGING, "staging-db", "Quelle fehlt"),
        (PROD, "", "staging-db", "Ziel fehlt"),
        (PROD, STAGING, "", "ziel-host leer"),
        (PROD, STAGING, "   ", "ziel-host nur Leerraum"),
        (PROD, PROD, "prod-db", "Quelle und Ziel identisch"),
        (PROD, STAGING, "prod-db.railway.internal", "ziel-host steht nicht im Ziel"),
        # Der heimtueckische Fall: ein zu unspezifischer Host, der in
        # BEIDEN URLs vorkommt, unterscheidet nichts — und genau dann
        # darf das Skript nicht so tun, als waere alles gut.
        (PROD, STAGING, "railway.internal", "ziel-host trifft auch die Quelle"),
        (PROD, STAGING, "creative_radar", "DB-Name trifft beide Seiten"),
    ],
)
def test_unklare_richtung_bricht_ab(quelle, ziel, ziel_host, warum):
    modul = _refresh_modul()
    with pytest.raises(modul.RichtungsFehler):
        modul.pruefe_richtung(quelle, ziel, ziel_host)


def test_refresh_prueft_die_richtung_vor_jedem_seiteneffekt():
    """Auch mit ``--ausfuehren`` faellt ein vertauschter Aufruf an der
    Sicherung — nicht erst an fehlendem pg_dump oder an der Verbindung.
    (pg_dump existiert in dieser Testumgebung nicht: kaeme der Aufruf an
    der Sicherung vorbei, endete er als SystemExit, nicht als
    RichtungsFehler — der Fehlertyp belegt die Reihenfolge.)"""
    modul = _refresh_modul()
    with pytest.raises(modul.RichtungsFehler):
        modul.refresh(PROD, PROD, "prod-db", ausfuehren=True)


def test_auch_die_trockenuebung_prueft_die_richtung():
    """Die Trockenuebung existiert, damit man dem echten Lauf trauen
    kann. Eine Trockenuebung, die bei vertauschten URLs "wuerde kopieren"
    meldet, rechtfertigt Vertrauen, das es nicht gibt — die Sicherung
    laeuft deshalb VOR der ausfuehren-Weiche, nicht dahinter. (Genau
    diese Verschiebung hat eine Mutation probiert und ueberlebt, bevor
    dieser Test existierte.)"""
    modul = _refresh_modul()
    with pytest.raises(modul.RichtungsFehler):
        modul.refresh(PROD, PROD, "prod-db", ausfuehren=False)


def test_trockenuebung_ist_der_standard_und_tut_nichts(capsys):
    """Ohne ``--ausfuehren`` wird nur beschrieben, was passieren wuerde.
    Kein pg_dump-Aufruf, kein DB-Kontakt — belegt dadurch, dass der
    Durchlauf in einer Umgebung OHNE pg_dump und OHNE erreichbare Hosts
    sauber durchlaeuft."""
    _refresh_modul().refresh(PROD, STAGING, "staging-db.railway.internal", ausfuehren=False)
    ausgabe = capsys.readouterr().out
    assert "Trockenuebung" in ausgabe
    assert "--ausfuehren" in ausgabe


# ---------------------------------------------------------------------
# 2 — Feature-Flag-Geruest
# ---------------------------------------------------------------------


def test_flag_ist_per_default_aus(monkeypatch):
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    assert feature_flags.is_trailer_intelligence_enabled() is False


@pytest.mark.parametrize(
    "wert, erwartet",
    [("true", True), ("TRUE", True), ("false", False), ("1", False), ("ja", False)],
)
def test_flag_liest_nur_true(wert, erwartet, monkeypatch):
    """Gleiche defensive Lesart wie die drei bestehenden Flags: alles
    ausser "true" (case-insensitive) ist aus."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", wert)
    assert feature_flags.is_trailer_intelligence_enabled() is erwartet


def test_health_meldet_den_flag_zustand(monkeypatch):
    """Der Weg, auf dem das Frontend den Zustand erfaehrt. Derselbe
    Build zeigt auf Staging das Panel und auf Prod nichts — also muss
    die Antwort mit der Umgebung kippen, nicht mit dem Build."""
    client = TestClient(app)

    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    features = client.get("/api/health").json()["features"]
    assert features["trailer_intelligence"] is False

    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    features = client.get("/api/health").json()["features"]
    assert features["trailer_intelligence"] is True
