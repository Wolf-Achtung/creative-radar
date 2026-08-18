"""Waechter gegen Modell-Drift.

Anlass: Der Wechsel von Sonnet 4.6 auf Sonnet 5 hat das Verhalten des
Post-Analyzers geaendert, ohne dass eine Zeile Code sich geaendert
haette — Sonnet 5 denkt ohne ``thinking``-Parameter, Sonnet 4.6 nicht.
Denk-Tokens zaehlen gegen ``max_tokens`` und werden wie Ausgabe
abgerechnet. Aufgefallen ist es erst Wochen spaeter.

Diese Tests machen aus dieser Klasse von Fehlern eine laute statt einer
leisen. Sie pruefen kein Verhalten, sondern eine Zusage: *Fuer jedes
Modell, das wir einsetzen, ist entschieden und hingeschrieben, ob es
denken soll.* Wer ein Modell tauscht, muss die Tabelle unten anfassen —
und stolpert dabei ueber die Frage, die sonst niemand stellt.

Bewusst ohne Import der Anwendung: die Quelltexte werden geparst. Damit
laeuft die Datei ohne Datenbank, ohne Schluessel und in unter einer
Sekunde — Voraussetzung dafuer, dass sie taeglich laufen kann
(``.github/workflows/model-drift.yml``).

Quelle der Tabelle: Anthropic-Modelldokumentation, Stand 18.08.2026.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "services"
CONFIG = Path(__file__).resolve().parents[1] / "config.py"
WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "model-drift.yml"
)


# ---------- Was wir ueber die eingesetzten Modelle wissen ---------------
#
# denkt_per_default : laeuft ohne ``thinking``-Parameter adaptives Denken?
# kennt_effort      : akzeptiert das Modell ``output_config.effort``?
#
# Ein Modell, das per Default denkt, braucht an jedem Aufrufort eine
# ausdrueckliche Angabe — sonst entscheidet die Voreinstellung, und die
# wechselt mit dem Modell.
MODELL_TABELLE = {
    "claude-haiku-4-5": {"denkt_per_default": False, "kennt_effort": False},
    "claude-haiku-4-5-20251001": {"denkt_per_default": False, "kennt_effort": False},
    "claude-sonnet-4-6": {"denkt_per_default": False, "kennt_effort": True},
    "claude-sonnet-5": {"denkt_per_default": True, "kennt_effort": True},
    "claude-opus-4-6": {"denkt_per_default": False, "kennt_effort": True},
    "claude-opus-4-7": {"denkt_per_default": False, "kennt_effort": True},
    "claude-opus-4-8": {"denkt_per_default": False, "kennt_effort": True},
    "claude-opus-5": {"denkt_per_default": True, "kennt_effort": True},
    "claude-fable-5": {"denkt_per_default": True, "kennt_effort": True},
}

# Einstellungs-Feld -> welche Aufrufe im Post-Analyzer es benutzen.
MODELL_FELDER = (
    "anthropic_haiku_model",
    "anthropic_sonnet_model",
    "anthropic_opus_model",
)

# Wrapper, ueber die alle Anthropic-Aufrufe laufen.
WRAPPER = {"messages_create_text", "messages_create_vision", "messages_create_strict_json"}

# Untergrenze fuer ``max_tokens`` bei denkenden Modellen. Denken teilt
# sich das Limit mit der Antwort; unterhalb davon wird es eng.
MIN_TOKENS_BEI_DENKEN = 1000


# ---------- Quelltexte lesen, ohne die Anwendung zu starten ------------


def _konfigurierte_modelle() -> dict[str, str]:
    """Die Vorgabewerte der Modell-Felder aus ``config.py``."""
    baum = ast.parse(CONFIG.read_text(encoding="utf-8"))
    gefunden: dict[str, str] = {}
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.AnnAssign) or knoten.value is None:
            continue
        ziel = knoten.target
        if not isinstance(ziel, ast.Name) or ziel.id not in MODELL_FELDER:
            continue
        if isinstance(knoten.value, ast.Constant) and isinstance(knoten.value.value, str):
            gefunden[ziel.id] = knoten.value.value
    return gefunden


def _wrapper_aufrufe(datei: Path) -> list[dict]:
    """Jeder Anthropic-Aufruf einer Datei mit Modell-Feld und Argumenten."""
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    treffer: list[dict] = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        name = getattr(knoten.func, "id", None) or getattr(knoten.func, "attr", None)
        if name not in WRAPPER:
            continue
        argumente = {kw.arg for kw in knoten.keywords if kw.arg}
        feld = None
        for kw in knoten.keywords:
            # ``model=settings.anthropic_sonnet_model``
            if kw.arg == "model" and isinstance(kw.value, ast.Attribute):
                feld = kw.value.attr
        treffer.append(
            {
                "datei": datei.name,
                "zeile": knoten.lineno,
                "wrapper": name,
                "feld": feld,
                "argumente": argumente,
            }
        )
    return treffer


def _post_analyzer_aufrufe() -> list[dict]:
    return [
        a
        for a in _wrapper_aufrufe(SERVICES / "post_analyzer.py")
        if a["feld"] in MODELL_FELDER
    ]


# ---------- Die Zusagen ------------------------------------------------


def test_der_taegliche_lauf_bleibt_unabhaengig():
    """Diese Datei importiert die Anwendung bewusst nicht — sonst
    braeuchte der taegliche Lauf die vollen Abhaengigkeiten und waere
    keine Sekundensache mehr.

    Zwei Dinge halten das: der Verzicht auf Anwendungs-Importe hier, und
    ``--noconftest`` im Workflow. Ohne das Flag laedt pytest
    ``app/tests/conftest.py``, das die Anwendung importiert und FastAPI
    hereinzieht — genau daran ist der erste CI-Lauf gescheitert.
    """
    quelle = Path(__file__).read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    for knoten in ast.walk(baum):
        modul = None
        if isinstance(knoten, ast.ImportFrom):
            modul = knoten.module
        elif isinstance(knoten, ast.Import):
            modul = knoten.names[0].name
        assert not (modul or "").startswith("app"), (
            f"Zeile {knoten.lineno} importiert {modul!r}. Diese Datei muss "
            f"ohne die Anwendung auskommen."
        )

    if not WORKFLOW.exists():
        return
    # Nur die Aufrufzeile zaehlt. Im Kommentar darueber steht das Flag
    # ebenfalls — Prosa darf einen Test nicht gruen halten. (Genau in
    # diese Falle war die erste Fassung getappt.)
    aufrufe = [
        z.strip()
        for z in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if "pytest" in z
        and "test_model_defaults_drift" in z
        and not z.lstrip().startswith("#")
    ]
    assert aufrufe, "model-drift.yml ruft diese Datei gar nicht mehr auf"
    for zeile in aufrufe:
        assert "--noconftest" in zeile, (
            f"{zeile!r} laeuft ohne --noconftest — der Lauf wuerde "
            f"conftest.py laden und an fehlendem FastAPI scheitern."
        )


def test_jedes_eingesetzte_modell_steht_in_der_tabelle():
    """Ein getauschtes Modell soll hier stolpern, nicht in Produktion."""
    for feld, modell in _konfigurierte_modelle().items():
        assert modell in MODELL_TABELLE, (
            f"{feld} = {modell!r} ist unbekannt. Bevor dieses Modell in "
            f"Betrieb geht: klaeren, ob es ohne thinking-Parameter denkt "
            f"und ob es effort kennt — dann in MODELL_TABELLE eintragen. "
            f"Denk-Tokens teilen sich max_tokens mit der Antwort."
        )


def test_alle_drei_modellfelder_werden_gefunden():
    """Stellt sicher, dass der Parser nicht ins Leere greift, wenn
    config.py umgebaut wird — sonst waeren die Tests oben wertlos."""
    assert set(_konfigurierte_modelle()) == set(MODELL_FELDER)


def test_der_parser_findet_die_aufrufe_wirklich():
    """Dieselbe Absicherung fuer den zweiten Parser."""
    aufrufe = _post_analyzer_aufrufe()
    assert len(aufrufe) >= 5, f"nur {len(aufrufe)} Aufrufe gefunden"
    assert {a["feld"] for a in aufrufe} == {
        "anthropic_haiku_model",
        "anthropic_sonnet_model",
    }


def test_denkende_modelle_bekommen_eine_ausdrueckliche_angabe():
    """Der Kern. Wo ein Modell per Default denkt, steht die Absicht da."""
    konfiguriert = _konfigurierte_modelle()
    for aufruf in _post_analyzer_aufrufe():
        modell = konfiguriert[aufruf["feld"]]
        if not MODELL_TABELLE[modell]["denkt_per_default"]:
            continue
        ort = f"{aufruf['datei']}:{aufruf['zeile']}"
        assert "effort" in aufruf["argumente"], (
            f"{ort} ruft {modell} ohne effort. Dieses Modell denkt ohne "
            f"Angabe — die Tiefe waere dann geerbt, nicht entschieden."
        )
        assert "max_tokens" in aufruf["argumente"], (
            f"{ort} ruft {modell} ohne max_tokens. Der Vorgabewert des "
            f"Wrappers laesst dem Denken zu wenig Luft vor der Antwort."
        )


def test_modelle_ohne_effort_bekommen_keinen():
    """Haiku 4.5 kennt den Parameter nicht und quittiert ihn mit 400."""
    konfiguriert = _konfigurierte_modelle()
    for aufruf in _post_analyzer_aufrufe():
        modell = konfiguriert[aufruf["feld"]]
        if MODELL_TABELLE[modell]["kennt_effort"]:
            continue
        assert "effort" not in aufruf["argumente"], (
            f"{aufruf['datei']}:{aufruf['zeile']} schickt effort an "
            f"{modell} — das Modell kennt den Parameter nicht (400)."
        )


def test_grosszuegige_limits_wo_gedacht_wird():
    """``max_tokens`` ist ein Deckel, keine Reservierung — ungenutzter
    Raum kostet nichts. Knapp bemessen ist er trotzdem gefaehrlich."""
    quelle = (SERVICES / "post_analyzer.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    konstanten = {
        knoten.targets[0].id: knoten.value.value
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.Assign)
        and len(knoten.targets) == 1
        and isinstance(knoten.targets[0], ast.Name)
        and isinstance(knoten.value, ast.Constant)
        and isinstance(knoten.value.value, int)
    }
    for name in ("CLASSIFY_MAX_TOKENS", "VISION_MAX_TOKENS"):
        assert name in konstanten, f"{name} fehlt"
        assert konstanten[name] >= MIN_TOKENS_BEI_DENKEN, (
            f"{name} = {konstanten[name]} laesst dem Denken zu wenig Luft"
        )


@pytest.mark.parametrize(
    "muster, warum",
    [
        ("budget_tokens", "entfernt auf allen aktuellen Modellen — 400er"),
        ("output_format=", "abgeloest durch output_config.format"),
    ],
)
def test_keine_abgeloesten_parameter(muster: str, warum: str):
    """Patterns, die auf aktuellen Modellen einen 400er ausloesen."""
    treffer = [
        f"{p.name}:{i}"
        for p in SERVICES.glob("*.py")
        for i, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if muster in zeile and not zeile.lstrip().startswith("#")
    ]
    assert not treffer, f"{muster} ({warum}) gefunden in: {treffer}"


def test_keine_sampling_parameter_an_anthropic():
    """``temperature``/``top_p`` sind auf Opus 4.7+ und Sonnet 5
    entfernt. In ``visual_analysis`` steht ein ``temperature`` — das
    geht an OpenAI und ist dort in Ordnung. Dieser Test faellt, sobald
    eines davon in einer Datei mit Anthropic-Aufrufen auftaucht."""
    for pfad in SERVICES.glob("*.py"):
        quelle = pfad.read_text(encoding="utf-8")
        if not any(w in quelle for w in WRAPPER):
            continue
        baum = ast.parse(quelle)
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            name = getattr(knoten.func, "id", None) or getattr(knoten.func, "attr", None)
            if name not in WRAPPER:
                continue
            verboten = {"temperature", "top_p", "top_k"} & {
                kw.arg for kw in knoten.keywords if kw.arg
            }
            assert not verboten, (
                f"{pfad.name}:{knoten.lineno} schickt {verboten} an Anthropic — "
                f"auf Opus 4.7+ und Sonnet 5 ist das ein 400er."
            )


def test_textextraktion_nimmt_nicht_den_ersten_block():
    """Denkt ein Modell, steht ein ``thinking``-Block vorn. Ein blinder
    ``content[0]``-Zugriff liefert dann leeren Text und verschluckt die
    Antwort. Genau das war der Fehler vom 18.08."""
    # Ueber den Syntaxbaum, nicht ueber Textzeilen: in den Kommentaren
    # steht ``content[0]`` als Beschreibung des behobenen Fehlers, und
    # Prosa darf keinen Test ausloesen.
    for pfad in SERVICES.glob("*.py"):
        quelle = pfad.read_text(encoding="utf-8")
        if not any(w in quelle for w in WRAPPER):
            continue
        for knoten in ast.walk(ast.parse(quelle)):
            if not isinstance(knoten, ast.Subscript):
                continue
            behaelter = getattr(knoten.value, "attr", None) or getattr(
                knoten.value, "id", None
            )
            if behaelter != "content":
                continue
            index = knoten.slice
            assert not (
                isinstance(index, ast.Constant) and index.value == 0
            ), (
                f"{pfad.name}:{knoten.lineno} greift blind auf content[0] zu. "
                f"Ueber alle Bloecke iterieren und auf type == 'text' pruefen."
            )
