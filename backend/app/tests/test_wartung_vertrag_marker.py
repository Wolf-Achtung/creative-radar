"""Wartung 20.08.2026 — der Wächter über den ``vertrag``-Marker.

Der Anlass, konkret: #366 (reiner Frontend-Lint-Durchgang) hat auf
``main`` einen Backend-Test gebrochen — ``backend-tests.yml`` triggert
nur auf ``backend/**`` und hat den PR grün durchgewunken. Der gebrochene
Test las ``AdminApp.jsx``. Solche Tests sind Absicht: sie prüfen
Zusagen, die beide Seiten betreffen (Fehlertexte, Status-Labels,
Filter-Defaults). Aber sie brauchen einen CI-Lauf, der auch bei
Frontend-Änderungen feuert.

Die Konstruktion, die das leistet:

1. Grenzüberschreitende Tests tragen ``@pytest.mark.vertrag``.
2. ``vertrag-tests.yml`` führt ``pytest -m vertrag`` aus und triggert
   auf ``backend/**`` UND ``frontend/**`` — ohne Postgres, in Sekunden.
3. Diese Datei erzwingt beides. Denn wer den nächsten
   grenzüberschreitenden Test schreibt, denkt nicht an den Pfad-Filter
   — genau daran ist es beim letzten Mal gescheitert. Der Wächter
   denkt daran.

Erkennung über den AST, nicht über grep: das Wort „frontend" steht in
vielen Kommentaren und Docstrings harmlos herum. Zählen soll nur das
String-Literal ``"frontend"`` im Code — so wird der Pfad gebaut
(``_REPO_ROOT / "frontend" / ...``). Helfer werden transitiv verfolgt:
ein Test, der einen frontend-lesenden Helfer ruft, liest selbst über
die Grenze.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND = _TESTS_DIR.parents[1]
_REPO_ROOT = _BACKEND.parent


def _grenz_tests_ohne_marker(pfad: Path) -> list[str]:
    """Testfunktionen in ``pfad``, die (auch über Helfer) das Literal
    ``"frontend"`` benutzen und den ``vertrag``-Marker nicht tragen."""
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    funktionen = {
        knoten.name: knoten
        for knoten in baum.body
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def liest_direkt(fn: ast.AST) -> bool:
        return any(
            isinstance(k, ast.Constant) and k.value == "frontend"
            for k in ast.walk(fn)
        )

    def gerufene_namen(fn: ast.AST) -> set[str]:
        return {
            k.func.id
            for k in ast.walk(fn)
            if isinstance(k, ast.Call) and isinstance(k.func, ast.Name)
        }

    # Fixpunkt: erst die direkten Leser, dann jeder, der einen Leser ruft.
    grenz = {name for name, fn in funktionen.items() if liest_direkt(fn)}
    veraendert = True
    while veraendert:
        veraendert = False
        for name, fn in funktionen.items():
            if name not in grenz and gerufene_namen(fn) & grenz:
                grenz.add(name)
                veraendert = True

    def hat_marker(fn) -> bool:
        for deko in fn.decorator_list:
            if (
                isinstance(deko, ast.Attribute)
                and deko.attr == "vertrag"
                and isinstance(deko.value, ast.Attribute)
                and deko.value.attr == "mark"
            ):
                return True
        return False

    return [
        f"{pfad.name}::{name}"
        for name in sorted(grenz)
        if name.startswith("test_") and not hat_marker(funktionen[name])
    ]


def test_jeder_grenz_test_traegt_den_vertrag_marker():
    fehlend: list[str] = []
    for pfad in sorted(_TESTS_DIR.glob("test_*.py")):
        if pfad.name == Path(__file__).name:
            continue
        fehlend.extend(_grenz_tests_ohne_marker(pfad))
    assert not fehlend, (
        f"Diese Tests lesen frontend/-Quelldateien, tragen aber kein "
        f"@pytest.mark.vertrag: {fehlend}. Ohne den Marker laufen sie "
        f"bei Frontend-Änderungen nicht — genau so hat #366 einen "
        f"grünen PR gemergt, der einen Backend-Test auf main brach."
    )


def test_der_marker_ist_registriert():
    """Ein unregistrierter Marker ist nur eine Warnung — und
    ``pytest -m vertrag`` würde mit einem Tippfehler still 0 Tests
    ausführen. Die Registrierung in pytest.ini ist Teil des Vertrags."""
    ini = (_BACKEND / "pytest.ini").read_text(encoding="utf-8")
    assert "vertrag:" in ini, "pytest.ini registriert den vertrag-Marker nicht mehr."


# Diese beiden Prüfungen lesen .github/ — selbst ausserhalb von
# backend/, deshalb tragen sie den Marker, den sie bewachen. So läuft
# der Wächter auch, wenn jemand NUR den Workflow ändert (der Workflow
# listet seine eigene Datei in den Pfad-Triggern).
@pytest.mark.vertrag
def test_der_vertrag_workflow_fuehrt_die_markierten_tests_aus():
    workflow = _REPO_ROOT / ".github" / "workflows" / "vertrag-tests.yml"
    assert workflow.exists(), (
        "vertrag-tests.yml fehlt — die markierten Tests laufen dann bei "
        "Frontend-Änderungen nirgends."
    )
    inhalt = workflow.read_text(encoding="utf-8")
    # Ausdruecklich die run-Zeile, nicht der blosse String: die Datei
    # ERWAEHNT ``-m vertrag`` auch in Kommentaren, und genau daran ist
    # die erste Fassung dieser Pruefung gescheitert (Mutation entfernte
    # den Filter aus der run-Zeile, der Test blieb gruen).
    run_zeilen = [
        z.strip() for z in inhalt.splitlines() if z.strip().startswith("run: pytest")
    ]
    assert run_zeilen, "Keine ``run: pytest``-Zeile in vertrag-tests.yml."
    assert any("-m vertrag" in z for z in run_zeilen), (
        f"Der Workflow führt pytest ohne ``-m vertrag`` aus: {run_zeilen}. "
        f"Dann läuft dort die volle Suite ohne Postgres — oder nach dem "
        f"nächsten Umbau gar nichts Gezieltes mehr."
    )


@pytest.mark.vertrag
def test_der_vertrag_workflow_triggert_auf_beiden_seiten():
    inhalt = (
        _REPO_ROOT / ".github" / "workflows" / "vertrag-tests.yml"
    ).read_text(encoding="utf-8")
    for pfad in ('"backend/**"', '"frontend/**"'):
        assert inhalt.count(pfad) >= 2, (
            f"{pfad} fehlt in den Triggern (pull_request UND push) von "
            f"vertrag-tests.yml. Der ganze Zweck des Jobs ist, bei "
            f"Änderungen BEIDER Seiten zu laufen."
        )
