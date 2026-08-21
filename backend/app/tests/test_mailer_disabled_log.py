"""DISABLE_EMAILS-Rettungsleine (21.08.2026): der Log-Eintrag mit dem
Login-Code muss EINZEILIG sein. Railways Log-Shipper schneidet Records
an der ersten Newline ab — der Code stand sonst genau hinter dem
Schnitt und Staging-Login war unmoeglich (Wolf-Screenshot 21.08.)."""
from __future__ import annotations

import logging

import pytest

from app.config import settings
from app.services import mailer


@pytest.mark.anyio
async def test_disabled_body_log_ist_einzeilig_mit_code(monkeypatch, caplog):
    monkeypatch.setattr(settings, "disable_emails", True, raising=False)
    monkeypatch.setattr(settings, "app_env", "staging", raising=False)

    with caplog.at_level(logging.INFO, logger="app.services.mailer"):
        await mailer.send_mail(
            to="wolf@x.test",
            subject="Ihr Anmeldecode",
            text="Ihr persönlicher Anmeldecode für Creative Radar lautet:\n\n123456\n\nGültig 10 Minuten.",
        )

    zeilen = [
        r.getMessage() for r in caplog.records
        if "mailer.disabled.body" in r.getMessage()
    ]
    assert zeilen, "disabled-body-Log fehlt"
    assert "123456" in zeilen[0], "der Code muss im Log stehen"
    assert "\n" not in zeilen[0], (
        "Der Log-Record darf keine Newline tragen — der Shipper schneidet "
        "dort ab und der Code ist weg."
    )
