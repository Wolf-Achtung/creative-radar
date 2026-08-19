"""Wächter aus dem Wartungsdurchgang vom 19.08.2026.

Drei Gruppen, drei verschiedene Anlässe:

1. **Klartext-Login-Codes im Prod-Log.** ``api/user_auth.py`` schrieb den
   angeforderten Anmeldecode ins Log, sobald ``DISABLE_EMAILS`` gesetzt
   war — abgesichert nur durch einen Kommentar, der behauptete, in
   Production sei der Schalter ohnehin aus. ``services/mailer.py``
   schützt dieselbe Stelle mit einer echten ``app_env``-Prüfung.

2. **Stille Leer-Antwort der Text-Analyse.** ``services/creative_ai.py``
   verwandelte eine unlesbare Modellantwort in einen Platzhalter-Text
   mit ``ReviewStatus.NEW`` — ohne eine einzige Logzeile. Der
   Schwester-Pfad ``visual_analysis`` erkennt genau diesen Zustand seit
   W4 und meldet ihn als ``vision_empty``.

3. **Der ungetestete SMTP-Zweig.** ``EMAIL_PROVIDER`` steht auf
   ``resend``; der SMTP-Pfad läuft im Normalbetrieb nie und hatte
   keinen einzigen Test. Genau solche Zweige verrotten unbemerkt — er
   ist der Rückfallweg, wenn Resend ausfällt.

Kein Test hier fasst Produktionsverhalten an. Sie halten fest, was
gilt, damit ein Rückbau auffällt.
"""
from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.database as database_module
from app.config import settings
from app.database import get_session
from app.main import app
from app.models import AppUser
from app.models.entities import ReviewStatus
from app.services import creative_ai, mailer


# ---------------------------------------------------------------------
# 1 — Klartext-Codes gehören nicht in ein Production-Log
# ---------------------------------------------------------------------


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def sent_mails(monkeypatch: pytest.MonkeyPatch):
    """Faengt ``send_mail`` ab — der echte Versand ist hier nicht das Thema."""
    mails: list[dict] = []

    async def _fake_send(to: str, subject: str, text: str, html=None):
        mails.append({"to": to, "subject": subject, "text": text})

    monkeypatch.setattr("app.api.user_auth.send_mail", _fake_send)
    return mails


@pytest.fixture
def client(db, sent_mails, monkeypatch: pytest.MonkeyPatch):
    """Muster aus ``test_user_auth_flow.py`` — echter HTTP-Stack, damit
    der Test an der echten Route hängt und nicht an einer Nachbildung."""
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "user_auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "user_session_secret", "user-test-secret", raising=False)
    monkeypatch.setattr(settings, "disable_emails", True, raising=False)
    monkeypatch.setattr(database_module, "engine", db, raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _add_user(engine, email: str) -> None:
    with Session(engine) as session:
        session.add(AppUser(email=email, is_active=True))
        session.commit()


def _request_code_logs(client, engine, app_env: str, sent_mails, caplog, monkeypatch):
    monkeypatch.setattr(settings, "app_env", app_env, raising=False)
    _add_user(engine, "wolf@example.com")
    with caplog.at_level(logging.INFO, logger="app.api.user_auth"):
        antwort = client.post(
            "/api/auth/request-code", json={"email": "wolf@example.com"}
        )
    assert antwort.status_code == 200, antwort.text
    code = re.search(r"\b(\d{6})\b", sent_mails[-1]["text"]).group(1)
    return code, [r.getMessage() for r in caplog.records]


def test_login_code_landet_nicht_im_production_log(
    client, db, sent_mails, caplog, monkeypatch
):
    """Der Kern. ``DISABLE_EMAILS=true`` in Production — etwa während
    einer Störung beim Mail-Provider — darf keinen Klartext-Code in ein
    Log schreiben, das bei Dritten liegt."""
    code, meldungen = _request_code_logs(
        client, db, "production", sent_mails, caplog, monkeypatch
    )
    assert not any(code in m for m in meldungen), (
        f"Der Anmeldecode {code} steht im Klartext im Production-Log."
    )


def test_login_code_bleibt_lokal_sichtbar(
    client, db, sent_mails, caplog, monkeypatch
):
    """Die Gegenprobe: ohne sie wäre der Test oben auch grün, wenn die
    Logzeile ganz verschwände — und der lokale Login damit unbenutzbar,
    weil der Code ohne Mail-Provider dann nirgends mehr auftaucht."""
    code, meldungen = _request_code_logs(
        client, db, "development", sent_mails, caplog, monkeypatch
    )
    assert any(code in m for m in meldungen), (
        "Ohne Mail-Provider ist der Code lokal sonst nicht erreichbar."
    )


def test_quelltext_prueft_app_env_und_verlaesst_sich_nicht_auf_prosa():
    """Der eigentliche Fehler war, dass die Zusicherung im Kommentar
    stand statt in der Bedingung. Dieser Test liest die Bedingung."""
    import ast
    import inspect
    from app.api import user_auth

    quelle = inspect.getsource(user_auth.request_code)
    baum = ast.parse(quelle.strip())
    bedingungen = [
        ast.unparse(k.test)
        for k in ast.walk(baum)
        if isinstance(k, ast.If)
    ]
    treffer = [b for b in bedingungen if "disable_emails" in b]
    assert treffer, "Der DISABLE_EMAILS-Zweig ist verschwunden."
    for bedingung in treffer:
        assert "app_env" in bedingung, (
            f"{bedingung!r} prüft app_env nicht. Ein Kommentar, der "
            f"behauptet 'in Production ist der Schalter ohnehin aus', "
            f"ist keine Prüfung — mailer.py macht es richtig."
        )


def test_mailer_schuetzt_dieselbe_stelle_weiterhin():
    """Der Bezugspunkt der Korrektur. Fällt dieser Test, ist nicht
    user_auth kaputt, sondern das Vorbild verschwunden."""
    import ast
    import inspect

    quelle = inspect.getsource(mailer.send_mail)
    baum = ast.parse(quelle.strip())
    bedingungen = [
        ast.unparse(k.test) for k in ast.walk(baum) if isinstance(k, ast.If)
    ]
    assert any("app_env" in b for b in bedingungen), (
        "mailer.send_mail prüft app_env nicht mehr — dann steht die "
        "gleiche Frage in user_auth.py auch neu zur Debatte."
    )


# ---------------------------------------------------------------------
# 2 — Eine unlesbare Modellantwort muss sichtbar werden
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "rohtext, warum",
    [
        ("Tut mir leid, das kann ich nicht.", "Fließtext statt JSON"),
        ("{}", "leeres Objekt"),
        ("", "gar keine Antwort"),
        ('{"ai_summary_de": "   "}', "nur Leerraum in den Textfeldern"),
        ('{"confidence_score": 0.9}', "nur Beiwerk, kein einziger Text"),
    ],
)
def test_unlesbare_antwort_wird_geloggt(rohtext, warum, caplog):
    """Vorher passierte an dieser Stelle nichts — kein Log, kein
    Zähler, nichts. Der Fehlschlag war aus den Daten nur zu erkennen,
    wenn jemand den deutschen Platzhaltersatz als solchen erkannte."""
    with caplog.at_level(logging.WARNING, logger="app.services.creative_ai"):
        ergebnis = creative_ai._shape_response(rohtext)

    treffer = [
        r for r in caplog.records if "creative-ai-empty-response" in r.getMessage()
    ]
    assert treffer, f"{warum}: kein WARNING — der Fehlschlag bleibt unsichtbar."
    # Das Verhalten selbst ist bewusst unverändert (Produktentscheidung,
    # siehe Wartungsbericht): der Platzhalter geht weiterhin durch.
    assert ergebnis["ai_summary_de"] == "Keine belastbare Zusammenfassung erzeugt."
    assert ergebnis["review_status"] == ReviewStatus.NEW


@pytest.mark.parametrize(
    "rohtext",
    [
        '{"ai_summary_de": "Ein Teaser mit hartem Schnitt."}',
        '{"ai_trend_notes": "Wiederkehrendes Motiv."}',
        '{"ai_summary_en": "A teaser."}',
    ],
)
def test_brauchbare_antwort_loggt_nicht(rohtext, caplog):
    """Gegenprobe: ein Wächter, der immer feuert, ist kein Wächter."""
    with caplog.at_level(logging.WARNING, logger="app.services.creative_ai"):
        creative_ai._shape_response(rohtext)
    assert not [
        r for r in caplog.records if "creative-ai-empty-response" in r.getMessage()
    ], "Fehlalarm auf einer verwertbaren Antwort."


def test_erkennung_deckt_sich_mit_dem_vision_pfad():
    """``visual_analysis`` beantwortet dieselbe Frage seit W4. Beide
    Erkennungen sollen sich gleich verhalten, sonst driften sie."""
    from app.services import visual_analysis

    assert creative_ai._text_data_is_empty({}) is True
    assert visual_analysis._vision_data_is_empty({}) is True
    assert creative_ai._text_data_is_empty({"confidence_score": 0.9}) is True
    assert visual_analysis._vision_data_is_empty({"confidence": 0.9}) is True
    assert creative_ai._text_data_is_empty({"ai_summary_de": "Text"}) is False
    assert visual_analysis._vision_data_is_empty({"visual_summary_de": "Text"}) is False


# ---------------------------------------------------------------------
# 3 — Der Rückfallweg SMTP
# ---------------------------------------------------------------------


async def test_smtp_ohne_host_meldet_klaren_fehler():
    """Fehlkonfiguration muss als ``MailerError`` herauskommen (die
    Route übersetzt das in ein 503) — nicht als AttributeError."""
    with patch.object(settings, "smtp_host", None):
        with pytest.raises(mailer.MailerError, match="SMTP_HOST"):
            await mailer._send_smtp(to="a@b.de", subject="s", text="t", html=None)


async def test_smtp_ohne_absender_meldet_klaren_fehler():
    with patch.object(settings, "smtp_host", "mail.example.com"), patch.object(
        settings, "mail_from", None
    ), patch.object(settings, "smtp_user", None):
        with pytest.raises(mailer.MailerError, match="MAIL_FROM/SMTP_USER"):
            await mailer._send_smtp(to="a@b.de", subject="s", text="t", html=None)


async def test_smtp_versendet_mit_starttls_und_login():
    """Der Pfad, der im Normalbetrieb nie läuft — einmal ganz durch."""
    smtp_instanz = MagicMock()
    smtp_kontext = MagicMock()
    smtp_kontext.__enter__ = MagicMock(return_value=smtp_instanz)
    smtp_kontext.__exit__ = MagicMock(return_value=False)

    with patch.object(settings, "smtp_host", "mail.example.com"), patch.object(
        settings, "smtp_port", 587
    ), patch.object(settings, "smtp_starttls", True), patch.object(
        settings, "smtp_user", "nutzer"
    ), patch.object(
        settings, "smtp_password", "geheim"
    ), patch.object(
        settings, "mail_from", "no-reply@creative-radar.de"
    ), patch(
        "smtplib.SMTP", return_value=smtp_kontext
    ):
        await mailer._send_smtp(
            to="wolf@example.com", subject="Ihr Code", text="424242", html=None
        )

    smtp_instanz.starttls.assert_called_once()
    smtp_instanz.login.assert_called_once_with("nutzer", "geheim")
    smtp_instanz.sendmail.assert_called_once()
    absender, empfaenger, roh = smtp_instanz.sendmail.call_args[0]
    assert absender == "no-reply@creative-radar.de"
    assert empfaenger == ["wolf@example.com"]
    assert "Ihr Code" in roh


async def test_smtp_fehler_wird_zu_mailer_error():
    """``smtplib``-Ausnahmen dürfen nicht roh nach oben durchschlagen —
    die Route kennt nur ``MailerError``."""
    import smtplib

    with patch.object(settings, "smtp_host", "mail.example.com"), patch.object(
        settings, "mail_from", "no-reply@creative-radar.de"
    ), patch("smtplib.SMTP", side_effect=smtplib.SMTPException("Verbindung abgelehnt")):
        with pytest.raises(mailer.MailerError, match="SMTP error"):
            await mailer._send_smtp(
                to="a@b.de", subject="s", text="t", html=None
            )


async def test_unvollstaendige_resend_config_faellt_auf_smtp_zurueck():
    """Fehlt ``RESEND_API_KEY`` oder ``MAIL_FROM``, geht der Versand
    über SMTP — und scheitert dort hörbar, wenn auch das nicht steht.
    Wichtig ist: er meldet nicht Erfolg."""
    with patch.object(settings, "disable_emails", False), patch.object(
        settings, "email_provider", "resend"
    ), patch.object(settings, "resend_api_key", None), patch.object(
        settings, "smtp_host", None
    ):
        with pytest.raises(mailer.MailerError, match="SMTP_HOST"):
            await mailer.send_mail(to="a@b.de", subject="s", text="t")


async def test_kill_switch_meldet_erfolg_ohne_zu_senden():
    """``DISABLE_EMAILS`` ist als "Aufrufer sieht Erfolg" dokumentiert.
    Festhalten, damit die Zusage nicht unbemerkt kippt."""
    with patch.object(settings, "disable_emails", True), patch.object(
        settings, "app_env", "development"
    ), patch.object(mailer, "_send_smtp") as smtp, patch.object(
        mailer, "_send_resend"
    ) as resend:
        await mailer.send_mail(to="a@b.de", subject="s", text="t")
    smtp.assert_not_called()
    resend.assert_not_called()


# ---------------------------------------------------------------------
# 4 — Der ENV-Vertrag
# ---------------------------------------------------------------------
#
# ``.env.example`` ist die einzige Liste der Variablen, die dieses
# Projekt kennt — sie ist die Vorlage für Railway und die erste Stelle,
# an der jemand nachsieht. Beim Durchgang am 19.08. fehlte dort
# ``USER_SESSION_TTL_SECONDS``: gelesen wurde die Variable, gesetzt hat
# sie niemand, also lief die Session-Dauer still auf dem Default. Solche
# Lücken melden sich nie von selbst.
#
# Diese Prüfung ist bewusst nur eine Buchhaltung über zwei Textdateien.
# Sie kann nicht sehen, was in Railway wirklich gesetzt ist — das bleibt
# eine Frage an Wolf.


def _felder_aus_config() -> set[str]:
    import ast
    from pathlib import Path

    quelle = Path(__file__).resolve().parents[1] / "config.py"
    baum = ast.parse(quelle.read_text(encoding="utf-8"))
    felder: set[str] = set()
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.ClassDef) or knoten.name != "Settings":
            continue
        for eintrag in knoten.body:
            if isinstance(eintrag, ast.AnnAssign) and isinstance(
                eintrag.target, ast.Name
            ):
                if eintrag.target.id != "model_config":
                    felder.add(eintrag.target.id.upper())
    return felder


def _schluessel_aus_env_beispiel() -> set[str]:
    from pathlib import Path

    pfad = Path(__file__).resolve().parents[3] / ".env.example"
    if not pfad.exists():  # pragma: no cover — Repo-Layout-Wechsel
        pytest.skip(".env.example nicht gefunden")
    return {
        treffer.group(1)
        for zeile in pfad.read_text(encoding="utf-8").splitlines()
        if (treffer := re.match(r"^([A-Z][A-Z0-9_]*)=", zeile))
    }


def test_jede_einstellung_steht_in_env_example():
    """Gelesen, aber nirgends dokumentiert = läuft still auf dem Default."""
    fehlend = _felder_aus_config() - _schluessel_aus_env_beispiel()
    assert not fehlend, (
        f"In config.py gelesen, in .env.example nicht dokumentiert: "
        f"{sorted(fehlend)}. Wer die Variable nicht kennt, setzt sie nicht — "
        f"und der Default entscheidet still."
    )


def test_env_example_erfindet_keine_einstellungen():
    """Die Gegenrichtung: dokumentiert, aber von niemandem gelesen =
    wirkungslose Konfiguration. Wer sie in Railway setzt, glaubt etwas
    zu steuern und steuert nichts.

    Variablen, die per ``os.environ`` statt über ``Settings`` gelesen
    werden, sind hier ausgenommen — sie stehen zu Recht in der Vorlage.
    Gescannt wird ``app/`` UND ``scripts/``: ``SEED_DEV_ON_DEPLOY`` und
    ``SEED_DEV_PAIRS`` liest der Deploy-Bootstrap (``scripts/
    db_bootstrap.py``, Railway ``preDeployCommand``), nicht die
    Anwendung. Ohne den zweiten Ordner meldet diese Prüfung sie
    fälschlich als wirkungslos.
    """
    import re as _re
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    direkt: set[str] = set()
    for wurzel in (backend / "app", backend / "scripts"):
        if not wurzel.exists():  # pragma: no cover — Layout-Wechsel
            continue
        for pfad in wurzel.rglob("*.py"):
            if "tests" in pfad.parts:
                continue
            for name in _re.findall(
                r'os\.(?:environ\.get|getenv)\(\s*"([A-Z0-9_]+)"',
                pfad.read_text(encoding="utf-8"),
            ):
                direkt.add(name)

    ueberzaehlig = _schluessel_aus_env_beispiel() - _felder_aus_config() - direkt
    assert not ueberzaehlig, (
        f"In .env.example dokumentiert, aber im Code nirgends gelesen: "
        f"{sorted(ueberzaehlig)}. Das sieht aus wie Konfiguration und ist keine."
    )
