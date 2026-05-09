"""Sprint 7 — TitelImFokus.verdict Voice-2.5-Vokabular + Backwards-Compat.

The ``verdict`` field went from a free string ("trägt"/"zerläuft"/
"ausbaufähig"/"sitzt"/"zweischneidig") to a strict
``Optional[VerdictEnum]`` with three Voice-2.5 values
("funktioniert" / "kommt nicht an" / "noch ausbaufähig"). A
``mode='before'`` field-validator normalises the five legacy values
onto the three new ones so persisted briefs from Sprint 1-6 still
re-hydrate cleanly via ``model_validate``.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.insights import TitelImFokus, VerdictEnum


def _kwargs(**overrides):
    base = {
        "titel": "Mortal Kombat II",
        "markt": "DE",
        "format_typ": "Kino-Reminder",
        "kennzahl": "22s, 11.000 Reaktionen",
    }
    base.update(overrides)
    return base


# ---------- Sprint 7 — neue Werte werden akzeptiert ----------------------


def test_verdict_accepts_funktioniert():
    item = TitelImFokus(**_kwargs(verdict="funktioniert"))
    assert item.verdict == VerdictEnum.FUNKTIONIERT


def test_verdict_accepts_kommt_nicht_an():
    item = TitelImFokus(**_kwargs(verdict="kommt nicht an"))
    assert item.verdict == VerdictEnum.KOMMT_NICHT_AN


def test_verdict_accepts_noch_ausbaufaehig():
    item = TitelImFokus(**_kwargs(verdict="noch ausbaufähig"))
    assert item.verdict == VerdictEnum.NOCH_AUSBAUFAEHIG


def test_verdict_accepts_none():
    """``verdict`` ist Optional — None bleibt None und wird nicht in
    eine Enum gezwungen."""
    item = TitelImFokus(**_kwargs(verdict=None))
    assert item.verdict is None


# ---------- Backwards-Compat: alte Werte werden normalisiert ------------


def test_verdict_normalizes_traegt_to_funktioniert():
    item = TitelImFokus(**_kwargs(verdict="trägt"))
    assert item.verdict == VerdictEnum.FUNKTIONIERT


def test_verdict_normalizes_sitzt_to_funktioniert():
    item = TitelImFokus(**_kwargs(verdict="sitzt"))
    assert item.verdict == VerdictEnum.FUNKTIONIERT


def test_verdict_normalizes_zerlaeuft_to_kommt_nicht_an():
    item = TitelImFokus(**_kwargs(verdict="zerläuft"))
    assert item.verdict == VerdictEnum.KOMMT_NICHT_AN


def test_verdict_normalizes_ausbaufaehig_to_noch_ausbaufaehig():
    item = TitelImFokus(**_kwargs(verdict="ausbaufähig"))
    assert item.verdict == VerdictEnum.NOCH_AUSBAUFAEHIG


def test_verdict_normalizes_zweischneidig_to_noch_ausbaufaehig():
    item = TitelImFokus(**_kwargs(verdict="zweischneidig"))
    assert item.verdict == VerdictEnum.NOCH_AUSBAUFAEHIG


# ---------- Unbekannte Werte werfen sauber Validation-Error -------------


def test_verdict_rejects_unknown_value():
    """Ein neuer freier String, der nicht im Backwards-Compat-Mapping
    UND nicht in der Enum steht, soll laut auffallen — keine
    Silent-Through. Wenn der LLM ``erfolgreich`` würfelt, wollen wir
    das beim Re-Hydrate sehen, nicht still beerdigen."""
    with pytest.raises(ValidationError):
        TitelImFokus(**_kwargs(verdict="erfolgreich"))


def test_verdict_round_trip_legacy_brief():
    """Persistierter Sprint-1-6-Brief (verdict='trägt') re-hydratisiert
    auf den Voice-2.5-Wert. Garantiert, dass alte Cache-Einträge im
    insight_report-Table nicht beim Frontend-Load platzen."""
    legacy_payload = _kwargs(verdict="trägt")
    item = TitelImFokus.model_validate(legacy_payload)
    assert item.verdict == VerdictEnum.FUNKTIONIERT
    # Weiteres Re-Validate nach JSON-Roundtrip → bleibt funktioniert.
    rehydrated = TitelImFokus.model_validate_json(item.model_dump_json())
    assert rehydrated.verdict == VerdictEnum.FUNKTIONIERT
