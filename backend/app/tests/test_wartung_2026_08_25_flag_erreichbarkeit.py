"""Wartung 25.08.2026 — ein Flag, das im Frontend nirgends ankommt.

Wolfs Befund aus der Staging-Abnahme von #429: Er hat
``FEATURE_KATALOG_NACHLADEN_ENABLED`` gesetzt, Railway neu gestartet —
und stand dann vor ``{"detail":"Missing Bearer token"}``. Es gab
schlicht keinen Weg zu dem Feature. Der Endpoint existierte, das Flag
stand auf ``true``, ``/api/health`` meldete es brav, und trotzdem war
nichts zu bedienen: kein Knopf im Admin-Bereich.

Alle bestehenden Waechter waren gruen. Sie pruefen die Kette bis zum
health-Schluessel — und hoeren genau dort auf. Ob irgendjemand diesen
Schluessel je liest, prueft keiner. Ein Flag ohne Leser ist wirkungslose
Konfiguration, dieselbe Fehlerklasse wie eine ENV-Variable, die in
keiner Codezeile vorkommt (``test_env_example_erfindet_keine_
einstellungen``, 19.08.).

Dieser Test schliesst das letzte Glied: jeder ``features``-Schluessel
aus ``/api/health`` muss im Frontend ausgewertet werden. Umgekehrt darf
das Frontend keinen Schluessel abfragen, den das Backend nicht liefert
— ein Tippfehler dort ist stumm, ``undefined`` ist falsy und das
Feature bleibt fuer immer unsichtbar.

``vertrag``-Marker: der Test liest ueber die Backend/Frontend-Grenze
(siehe ``test_wartung_vertrag_marker.py``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.health import health

pytestmark = pytest.mark.vertrag

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"

# ``features.<name>`` oder ``features?.<name>`` — die zwei Formen, in
# denen das Frontend die Flags heute liest.
_ZUGRIFF = re.compile(r"features(?:\?)?\.([a-z_][a-z0-9_]*)\b")


def _frontend_quellen() -> list[Path]:
    """Produktionsquellen ohne Tests: ein Flag, das nur in einer
    Testdatei vorkommt, ist im Betrieb genauso unerreichbar."""
    return [
        p
        for p in [*_FRONTEND_SRC.rglob("*.jsx"), *_FRONTEND_SRC.rglob("*.js")]
        if ".test." not in p.name
    ]


def _im_frontend_gelesen() -> dict[str, list[str]]:
    gelesen: dict[str, list[str]] = {}
    for pfad in _frontend_quellen():
        for name in _ZUGRIFF.findall(pfad.read_text(encoding="utf-8")):
            gelesen.setdefault(name, []).append(pfad.name)
    return gelesen


def test_frontend_quellen_sind_ueberhaupt_auffindbar():
    """Schutz gegen einen leer laufenden Waechter: verschiebt jemand das
    Frontend, wuerden beide Richtungen unten trivial bestehen."""
    quellen = _frontend_quellen()
    assert len(quellen) >= 10, (
        f"nur {len(quellen)} Frontend-Quelldateien unter {_FRONTEND_SRC} "
        "gefunden — der Pfad stimmt nicht mehr."
    )
    assert _im_frontend_gelesen(), (
        "kein einziger features-Zugriff gefunden — das Zugriffsmuster "
        f"({_ZUGRIFF.pattern}) passt nicht mehr auf den Code."
    )


def test_jedes_health_flag_wird_im_frontend_ausgewertet():
    """Die Richtung, die am 25.08. gefehlt hat: geliefert, aber
    ungelesen. Das Feature ist dann nicht ausschaltbar-versteckt,
    sondern unerreichbar — auch mit gesetztem Flag."""
    geliefert = set(health()["features"])
    gelesen = _im_frontend_gelesen()
    ungelesen = sorted(geliefert - set(gelesen))
    assert not ungelesen, (
        f"/api/health liefert {ungelesen}, aber keine Frontend-Quelle "
        "liest den Schluessel. Das Flag laesst sich setzen, ohne dass "
        "irgendwo etwas erscheint — genau Wolfs Fall vom 25.08.2026. "
        "Entweder das Feature bekommt einen Bedienweg, oder der "
        "health-Schluessel gehoert weg."
    )


def test_frontend_liest_keinen_schluessel_den_es_nicht_gibt():
    """Die Gegenrichtung: ``features.katalog_nachlade`` (Tippfehler) ist
    ``undefined``, also falsy — der Block bleibt fuer immer leer, ohne
    dass irgendetwas rot wird."""
    geliefert = set(health()["features"])
    gelesen = _im_frontend_gelesen()
    erfunden = sorted(set(gelesen) - geliefert)
    assert not erfunden, (
        "Frontend liest features-Schluessel, die /api/health nicht "
        f"liefert: { {k: sorted(set(gelesen[k])) for k in erfunden} }. "
        "undefined ist falsy — der Block bliebe stumm unsichtbar."
    )
