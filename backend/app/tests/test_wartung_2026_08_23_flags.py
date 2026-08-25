"""Arbeitsregel Feature-Flags (23.08.2026) — Wolfs Freigabe-Modell.

Jedes neue Feature kommt hinter ein Flag (Schema
``FEATURE_<NAME>_ENABLED``, Default aus): in Staging an, in Production
erst nach Wolfs Abnahme. Diese Tests nageln die Mechanik fest:

- Alle Flags defaulten auf AUS — ein frisches Deployment zeigt nichts
  Unfreigegebenes.
- ``/api/health -> features`` kennt jedes UI-relevante Flag — das
  Frontend blendet ausschliesslich darueber ein; fehlt ein Schluessel,
  ist das Feature nirgends sichtbar, auch nicht in Staging.

Die Flag-Liste wird **nicht von Hand gepflegt**, sondern aus dem Modul
selbst gelesen. Der erste Zuschnitt (23.08.2026) trug eine Handliste —
und das naechste Flag (``FEATURE_KATALOG_NACHLADEN_ENABLED``, 25.08.)
stand prompt nicht darin: eine Mutation des Defaults auf ``"true"``
ueberlebte den Waechter unbemerkt. Seitdem entdeckt der Test die Helfer
per Introspektion. Ein neues Flag ist damit ab der ersten Zeile
mitgeprueft, und ein Flag ohne health-Schluessel muss ausdruecklich als
Cron-only deklariert werden — Vergessen faellt auf, statt durchzurutschen.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.api.health import health
from app.core import feature_flags as ff

# Flags ohne UI: sie schalten reine Cron-Bloecke, das Frontend hat
# nichts einzublenden. Wer hier eintraegt, entscheidet bewusst gegen
# einen health-Schluessel — alles andere muss in health stehen.
_NUR_CRON = {
    "is_segment_roundups_enabled",
    "is_cutter_weekly_enabled",
    "is_designer_weekly_enabled",
}


def _entdecke_flags() -> dict[str, tuple]:
    """Liest ``(Helfer, Env-Var)`` aus dem Modul statt aus einer Liste.

    Der Env-Var-Name wird aus dem Quelltext des Helfers gezogen, nicht
    aus dem Funktionsnamen abgeleitet: so faellt auch ein Helfer auf,
    der auf die falsche Variable liest.
    """
    gefunden: dict[str, tuple] = {}
    for name, helper in vars(ff).items():
        if not (name.startswith("is_") and name.endswith("_enabled")):
            continue
        if not inspect.isfunction(helper):
            continue
        quelle = inspect.getsource(helper)
        treffer = re.findall(r'os\.getenv\(\s*"(FEATURE_[A-Z0-9_]+)"', quelle)
        assert len(treffer) == 1, (
            f"{name} liest {treffer or 'keine'} FEATURE_-Variable — "
            "erwartet ist genau eine, sonst greift der Waechter daneben."
        )
        gefunden[name] = (helper, treffer[0])
    return gefunden


_FLAGS = _entdecke_flags()


def test_es_gibt_ueberhaupt_flags_zu_pruefen():
    """Schutz gegen einen still leer laufenden Waechter: wenn die
    Introspektion nichts findet, faellt jeder parametrisierte Test
    ersatzlos weg und der Testlauf bleibt trotzdem gruen."""
    assert len(_FLAGS) >= 8, (
        f"nur {len(_FLAGS)} Flags entdeckt — die Introspektion greift "
        "nicht mehr (Umbenennung im Modul?)."
    )


@pytest.mark.parametrize("helper_name", sorted(_FLAGS))
def test_flags_defaulten_auf_aus_und_schalten_per_env(helper_name, monkeypatch):
    helper, env_var = _FLAGS[helper_name]

    monkeypatch.delenv(env_var, raising=False)
    assert helper() is False, f"{env_var} muss ohne Setzung AUS sein — Default ist Production."

    monkeypatch.setenv(env_var, "true")
    assert helper() is True

    monkeypatch.setenv(env_var, "TRUE")
    assert helper() is True, "case-insensitive wie die Bestands-Flags"

    monkeypatch.setenv(env_var, "1")
    assert helper() is False, "Nur 'true' schaltet — alles andere defensiv AUS."


def test_health_kennt_jedes_ui_relevante_flag(monkeypatch):
    """Das Frontend blendet NUR ueber /api/health -> features ein.
    Fehlt hier ein Schluessel, ist das Feature auch in Staging
    unsichtbar — das Flag waere dann wirkungslos statt gestuft."""
    for _, env_var in _FLAGS.values():
        monkeypatch.delenv(env_var, raising=False)

    features = health()["features"]

    erwartet = {
        name[len("is_"):-len("_enabled")]
        for name in _FLAGS
        if name not in _NUR_CRON
    }
    assert erwartet <= set(features), (
        f"features fehlt: {sorted(erwartet - set(features))} — das "
        "Frontend kann diese Flags dann nirgends einblenden. Wer bewusst "
        "kein UI will, traegt den Helfer in _NUR_CRON ein."
    )
    for name in erwartet:
        assert features[name] is False, "Default ist AUS (Production-Sicht)."


def test_nur_cron_liste_verweist_auf_existierende_helfer():
    """Ein Tippfehler in ``_NUR_CRON`` wuerde ein UI-Flag stillschweigend
    von der health-Pruefung ausnehmen — ohne dass etwas rot wird."""
    unbekannt = sorted(set(_NUR_CRON) - set(_FLAGS))
    assert not unbekannt, (
        f"_NUR_CRON nennt Helfer, die es nicht (mehr) gibt: {unbekannt}"
    )
