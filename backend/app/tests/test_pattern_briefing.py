"""Pattern-Briefing — Text-Bausteine aus dem Muster-Bericht (Stufe 1,
Schritt 3, 20.08.2026).

Die Arbeitsteilung ist der Kern und deshalb das Test-Gerüst: die
Code-Prüfung entscheidet, was ein Muster ist und was als Beleg zählt —
das LLM formuliert nur. Getestet werden entlang der Kette:

1. ``build_pattern_evidence`` — nur belastbare Genre-Zellen, stärkstes
   Signal zuerst, gedeckelt; je Zelle die Top-Lift-Posts mit Caption
   und URL als Beleg-Material.
2. Leerlauf — keine Muster → KEIN LLM-Call, ``model="none"``,
   deterministischer Caveat, Row wird trotzdem persistiert.
3. LLM-Pfad — Schema-Validierung plus Citation-Pflicht: ein Baustein,
   dessen ``cited_post_ids`` nicht aus den mitgegebenen Beispiel-URLs
   stammen, fliegt raus und wird gezählt.
4. Cron-Block — Flag-Gate, Budget-Re-Check, Cache-Hit, force.
5. Endpoints — Nutzer-GET hinter dem TI-Flag (503/404/200), die
   Admin-Wege bewusst UNGEGATET (Wolf reviewt in Production, bevor
   das Flag fällt).

Fixtures spiegeln ``test_title_genres.py``: messbare Views sind
Pflicht, sonst wirft ``build_lift_context`` die Posts raus, bevor
irgendein Muster sie sieht.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import (
    Asset,
    Channel,
    Market,
    PatternBriefing as PatternBriefingRow,
    Post,
    Title,
)
from app.schemas.insights import (
    PatternBriefingLLMReport,
    PatternTextBaustein,
)
from app.services import pattern_briefing as pb
from app.services.anthropic_client import JsonRetryResult

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    with Session(engine) as s:
        yield s


def _channel(session: Session) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        handle=f"handle{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _post(
    session: Session,
    channel: Channel,
    *,
    views: int = 1000,
    likes: int = 10,
    caption: str = "x",
) -> Post:
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption=caption,
        detected_at=NOW - timedelta(days=1),
        visible_views=views,
        visible_likes=likes,
        visible_comments=0,
        visible_bookmarks=0,
        raw_payload={},
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _titel_mit_asset(session: Session, post: Post, *, genres: list[str]) -> Title:
    titel = Title(title_original=f"Titel-{uuid4().hex[:6]}", genres=genres)
    session.add(titel)
    session.commit()
    session.refresh(titel)
    session.add(Asset(post_id=post.id, title_id=titel.id))
    session.commit()
    return titel


def _seed_genre(
    session: Session,
    genre: str,
    *,
    channels: int = 3,
    posts_per_channel: int = 4,
    breakout_likes: int = 200,
    caption: str = "Der Countdown läuft. #kino",
) -> None:
    """Eine belastbare Genre-Zelle: >= 3 Kanäle à >= 4 Posts (Kanal-
    Baseline-Minimum), je Kanal ein Ausreißer-Post mit ``breakout_likes``
    (Lift >> 2), damit die Korpus-Breakout-Quote in (0, 1) liegt und der
    z-Test überhaupt rechnet."""
    for _ in range(channels):
        ch = _channel(session)
        posts = [
            _post(session, ch, likes=10, caption=caption)
            for _ in range(posts_per_channel - 1)
        ]
        posts.append(
            _post(session, ch, likes=breakout_likes, caption=caption)
        )
        for p in posts:
            _titel_mit_asset(session, p, genres=[genre])


# ---------------------------------------------------------------------
# 1 — Evidence: was die Code-Prüfung freigibt
# ---------------------------------------------------------------------


def test_evidence_enthaelt_nur_belastbare_genre_zellen(session):
    _seed_genre(session, "Romance")
    # Western: 3 Posts auf 1 Kanal — unter Stichproben- und Kanal-Minimum.
    ch = _channel(session)
    for _ in range(4):
        p = _post(session, ch, likes=10)
        _titel_mit_asset(session, p, genres=["Western"])

    evidence = pb.build_pattern_evidence(session, window_days=30, now=NOW)

    werte = [c.value for c in evidence.patterns]
    assert "Romance" in werte
    assert "Western" not in werte, (
        "Eine insufficient-Zelle darf nie zum LLM — sonst formuliert es "
        "Empfehlungen aus einer Stichprobe, die die Code-Prüfung "
        "ausdrücklich als nicht belastbar markiert hat."
    )


def test_evidence_beispiele_sind_die_staerksten_posts_mit_beleg_material(session):
    _seed_genre(session, "Horror", caption="Wer schaut das allein? #horror")

    evidence = pb.build_pattern_evidence(session, window_days=30, now=NOW)

    zelle = next(c for c in evidence.patterns if c.value == "Horror")
    assert 1 <= len(zelle.examples) <= pb.EXAMPLES_PER_PATTERN
    # Absteigend nach Lift — die Ausreißer zuerst, von denen soll das
    # LLM die Hook-Mechanik lernen.
    lifts = [ex.lift for ex in zelle.examples]
    assert lifts == sorted(lifts, reverse=True)
    assert lifts[0] >= 2.0
    top = zelle.examples[0]
    assert top.post_url.startswith("https://")
    assert "Wer schaut das allein?" in top.caption
    assert top.channel_handle != "?"


def test_evidence_sortiert_staerkstes_signal_zuerst_und_deckelt(session):
    # Zwei Genres mit gegensätzlichem Signal: Comedy hat Ausreißer,
    # Drama keinen einzigen → z(Comedy) > z(Drama).
    _seed_genre(session, "Comedy")
    for _ in range(3):
        ch = _channel(session)
        for _ in range(4):
            p = _post(session, ch, likes=10)
            _titel_mit_asset(session, p, genres=["Drama"])

    evidence = pb.build_pattern_evidence(session, window_days=30, now=NOW)
    werte = [c.value for c in evidence.patterns]
    assert werte.index("Comedy") < werte.index("Drama"), (
        "Sortierung muss breakout_z-absteigend sein — gekappt wird das "
        "schwächste Signal, nie das stärkste."
    )

    gedeckelt = pb.build_pattern_evidence(
        session, window_days=30, now=NOW, max_patterns=1
    )
    assert [c.value for c in gedeckelt.patterns] == ["Comedy"]


# ---------------------------------------------------------------------
# 2 — Leerlauf: kein Muster, kein LLM-Call, ehrliche Row
# ---------------------------------------------------------------------


def test_leerlauf_persistiert_ohne_llm_call(session, monkeypatch):
    def _explodiert(**kwargs):
        raise AssertionError("Im Leerlauf darf kein LLM-Call passieren.")

    monkeypatch.setattr(pb, "call_with_json_retry", _explodiert)
    # Kein API-Key nötig — genau das ist der Punkt: der Leerlauf ist bis
    # zum ersten Title-Sync mit Genres der Normalfall und kostet nichts.
    monkeypatch.setattr(pb, "is_anthropic_configured", lambda: False)

    report = pb.generate_and_persist_pattern_briefing(
        session, window_days=30, now=NOW
    )

    assert report.model == "none"
    assert report.llm_output is not None
    assert report.llm_output.bausteine == []
    assert any(
        "Kein belastbares Genre-Muster" in c
        for c in report.llm_output.data_caveats
    )
    row = session.get(
        PatternBriefingRow, (pb.BRIEFING_MODE_GENRE, 2026, 34)
    )
    assert row is not None
    assert row.model == "none"


# ---------------------------------------------------------------------
# 3 — Citation-Pflicht: unbelegte Bausteine fliegen raus
# ---------------------------------------------------------------------


def _baustein(cited: list[str], muster: str = "Romance auf TikTok") -> dict:
    return {
        "muster": muster,
        "begruendung": "25,0 % Breakout-Quote bei erwarteten 25,0 %.",
        "hooks_de": ["Noch 3 Tage."],
        "hooks_en": ["3 days left."],
        "captions_de": ["[TITEL] — ab Donnerstag im Kino."],
        "captions_en": ["[TITLE] — in theaters Thursday."],
        "hashtags": ["kino"],
        "cited_post_ids": cited,
    }


def test_citation_pruefung_verwirft_unbelegte_bausteine(session, monkeypatch):
    _seed_genre(session, "Romance")
    evidence = pb.build_pattern_evidence(session, window_days=30, now=NOW)
    echte_url = evidence.patterns[0].examples[0].post_url

    fake = JsonRetryResult(
        parsed={
            "bausteine": [
                _baustein([echte_url]),
                _baustein(
                    ["https://erfunden.test/p/123"], muster="Erfundenes Muster"
                ),
            ],
            "data_caveats": [],
        },
        call_attempts=[(SimpleNamespace(usage=None), "raw")],
        parse_path="strict",
    )
    monkeypatch.setattr(pb, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(pb, "call_with_json_retry", lambda **kw: fake)

    report = pb.generate_and_persist_pattern_briefing(
        session, window_days=30, now=NOW
    )

    assert report.llm_output is not None
    muster = [b.muster for b in report.llm_output.bausteine]
    assert muster == ["Romance auf TikTok"], (
        "Der Baustein mit erfundener Beleg-URL muss KOMPLETT raus — eine "
        "Empfehlung, deren Beleg nicht existiert, ist keine Empfehlung "
        "mit Schönheitsfehler."
    )
    assert report.citation_dropped == 1
    row = session.get(
        PatternBriefingRow, (pb.BRIEFING_MODE_GENRE, 2026, 34)
    )
    assert row.citation_dropped == 1
    assert len(row.llm_output["bausteine"]) == 1


def test_validator_behaelt_caveats_wenn_er_verwirft():
    report = PatternBriefingLLMReport(
        bausteine=[
            PatternTextBaustein.model_validate(_baustein(["https://a.test/1"]))
        ],
        data_caveats=["x"],
    )
    evidence = pb.PatternBriefingEvidence(
        mode="genre", iso_year=2026, iso_week=34, window_days=30,
        window_start=NOW, window_end=NOW, posts_with_baseline=0,
        channels_covered=0, genre_coverage=0.0, baseline_breakout_rate=0.0,
        patterns=[],
    )
    bereinigt, dropped = pb.validate_baustein_citations(report, evidence)
    assert dropped == 1
    assert bereinigt.bausteine == []
    assert bereinigt.data_caveats == ["x"]


def test_schema_fail_persistiert_evidence_mit_raw_text(session, monkeypatch):
    _seed_genre(session, "Romance")
    fake = JsonRetryResult(
        parsed={"voellig": "falsches schema", "bausteine": "kein array"},
        call_attempts=[(SimpleNamespace(usage=None), "DER ROHE TEXT")],
        parse_path="strict",
    )
    monkeypatch.setattr(pb, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(pb, "call_with_json_retry", lambda **kw: fake)

    report = pb.generate_and_persist_pattern_briefing(
        session, window_days=30, now=NOW
    )

    assert report.llm_output is None
    assert report.raw_llm_text == "DER ROHE TEXT"
    row = session.get(
        PatternBriefingRow, (pb.BRIEFING_MODE_GENRE, 2026, 34)
    )
    assert row is not None, (
        "Auch bei Schema-Fail wird persistiert — die Evidence ist das "
        "Audit-Produkt (Cutter-Weekly-Konvention)."
    )
    assert row.llm_output is None
    assert row.raw_llm_text == "DER ROHE TEXT"


def test_persistenz_ist_last_write_wins(session, monkeypatch):
    monkeypatch.setattr(pb, "is_anthropic_configured", lambda: False)
    pb.generate_and_persist_pattern_briefing(session, window_days=30, now=NOW)
    pb.generate_and_persist_pattern_briefing(session, window_days=60, now=NOW)

    rows = session.exec(select(PatternBriefingRow)).all()
    assert len(rows) == 1
    assert rows[0].window_days == 60


# ---------------------------------------------------------------------
# 4 — Cron-Block: Gates in der richtigen Reihenfolge
# ---------------------------------------------------------------------


def test_cron_block_ohne_flag_tut_nichts(session, monkeypatch):
    from app.api.cron import _run_pattern_briefing_after_designer_weekly

    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    monkeypatch.setattr(
        pb, "generate_and_persist_pattern_briefing",
        lambda *a, **kw: pytest.fail("Flag aus — es darf nichts generiert werden."),
    )

    summary = _run_pattern_briefing_after_designer_weekly(session)

    assert summary["skipped"] is True
    assert summary["reason"] == "feature_flag_off"
    assert summary["enabled"] is False


def test_cron_block_respektiert_den_anthropic_deckel(session, monkeypatch):
    import app.api.cron as cron_mod

    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    cap = SimpleNamespace(
        hard_cap_exceeded=True,
        enforced=True,
        spent_usd_cents=10100,
        budget_usd_cents=10000,
        pct_used=1.01,
        to_dict=lambda: {"hard_cap_exceeded": True},
    )
    monkeypatch.setattr(
        cron_mod, "compute_anthropic_monthly_spend", lambda s: cap
    )
    monkeypatch.setattr(
        pb, "generate_and_persist_pattern_briefing",
        lambda *a, **kw: pytest.fail("Deckel gerissen — kein LLM-Call erlaubt."),
    )

    summary = cron_mod._run_pattern_briefing_after_designer_weekly(session)

    assert summary["skipped"] is True
    assert summary["reason"] == "anthropic_budget_exceeded"


def _cap_frei():
    return SimpleNamespace(
        hard_cap_exceeded=False, enforced=True,
        spent_usd_cents=0, budget_usd_cents=10000, pct_used=0.0,
        to_dict=lambda: {},
    )


def test_cron_block_cache_hit_verhindert_zweiten_lauf(session, monkeypatch):
    import app.api.cron as cron_mod

    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    monkeypatch.setattr(
        cron_mod, "compute_anthropic_monthly_spend", lambda s: _cap_frei()
    )
    brief_now = NOW
    iso = brief_now.isocalendar()
    session.add(PatternBriefingRow(
        mode=pb.BRIEFING_MODE_GENRE,
        iso_year=iso.year, iso_week=iso.week, window_days=90,
        evidence={}, model="none",
    ))
    session.commit()
    monkeypatch.setattr(
        pb, "generate_and_persist_pattern_briefing",
        lambda *a, **kw: pytest.fail("Cache-Hit — kein zweiter Lauf."),
    )

    summary = cron_mod._run_pattern_briefing_after_designer_weekly(
        session, brief_now=brief_now
    )
    assert summary["skipped_cache_hit"] == 1

    # force=True überspringt den Cache-Check (Last-Write-Wins).
    aufgerufen = {}

    def _fake_generate(s, *, now=None):
        aufgerufen["now"] = now
        return SimpleNamespace(
            model="none", llm_output=None, citation_dropped=0,
            cost_usd_estimate=None,
        )

    monkeypatch.setattr(
        pb, "generate_and_persist_pattern_briefing", _fake_generate
    )
    summary = cron_mod._run_pattern_briefing_after_designer_weekly(
        session, brief_now=brief_now, force=True
    )
    assert summary["generated"] == 1
    assert aufgerufen["now"] == brief_now


def test_cron_block_isoliert_fehler(session, monkeypatch):
    import app.api.cron as cron_mod

    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    monkeypatch.setattr(
        cron_mod, "compute_anthropic_monthly_spend", lambda s: _cap_frei()
    )

    def _kaputt(s, *, now=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        pb, "generate_and_persist_pattern_briefing", _kaputt
    )

    summary = cron_mod._run_pattern_briefing_after_designer_weekly(session)
    assert summary["failed"] == 1
    assert summary["error"]["error_class"] == "RuntimeError"


# ---------------------------------------------------------------------
# 5 — Endpoints: Nutzer-GET gegatet, Admin-Wege bewusst nicht
# ---------------------------------------------------------------------


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "user_auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_nutzer_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    antwort = client.get("/api/insights/pattern-briefing")
    assert antwort.status_code == 503
    assert "FEATURE_TRAILER_INTELLIGENCE_ENABLED" in antwort.json()["detail"]


def test_nutzer_endpoint_404_ohne_row_und_200_mit_row(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    assert client.get("/api/insights/pattern-briefing").status_code == 404

    session.add(PatternBriefingRow(
        mode="genre", iso_year=2026, iso_week=33, window_days=90,
        evidence={"geheim": "review-material"},
        llm_output={"bausteine": [], "data_caveats": ["leer"]},
        model="none", citation_dropped=0,
    ))
    session.add(PatternBriefingRow(
        mode="genre", iso_year=2026, iso_week=34, window_days=90,
        evidence={"geheim": "review-material"},
        llm_output={"bausteine": [_baustein(["https://x.test/p/1"])],
                    "data_caveats": []},
        model="opus-alias", citation_dropped=0,
    ))
    session.commit()

    antwort = client.get("/api/insights/pattern-briefing")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert (daten["iso_year"], daten["iso_week"]) == (2026, 34), (
        "Es muss die jüngste Woche kommen, nicht die erste."
    )
    assert daten["llm_output"]["bausteine"][0]["muster"] == "Romance auf TikTok"
    assert "evidence" not in daten, (
        "Der Evidence-Blob ist Review-Material für den Admin-Endpoint — "
        "er gehört nicht auf den Nutzer-Wire."
    )


def test_admin_wege_sind_bewusst_ungegatet(client, monkeypatch):
    """Wolf reviewt in Production, BEVOR das Flag fällt: Staging hat
    keinen Anthropic-Key, echte Briefings entstehen nur in Production.
    Ein flag-gegateter Admin-Weg würde genau den Review-Lauf verhindern,
    für den er existiert."""
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    assert client.get("/api/admin/pattern-briefing/latest").status_code != 503
    # Leere DB → Leerlauf-Pfad, kein Anthropic-Key nötig: 200 mit model=none.
    antwort = client.post("/api/admin/pattern-briefing/generate")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["model"] == "none"
    assert daten["llm_output_present"] is True

    danach = client.get("/api/admin/pattern-briefing/latest")
    assert danach.status_code == 200
    assert "evidence" in danach.json()


# ---------------------------------------------------------------------
# 6 — Vertrag über die Grenze: Frontend liest denselben Endpoint
# ---------------------------------------------------------------------


@pytest.mark.vertrag
def test_frontend_client_zeigt_auf_den_pattern_briefing_endpoint(
    monkeypatch: pytest.MonkeyPatch,
):
    """PatternsBlock lädt die Text-Bausteine über
    ``endpoints.insightPatternBriefing`` → ``/api/insights/pattern-briefing``.
    Wird der Pfad auf einer Seite umbenannt, bleiben beide Seiten für
    sich grün (der Frontend-Test mockt den Client) — und die Sektion
    verschwindet still. Dieselbe Fehlerklasse wie beim Feature-Key."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    client_js = (repo_root / "frontend" / "src" / "api" / "client.js").read_text(
        encoding="utf-8"
    )
    assert "'/api/insights/pattern-briefing'" in client_js

    block = (repo_root / "frontend" / "src" / "PatternsBlock.jsx").read_text(
        encoding="utf-8"
    )
    assert "insightPatternBriefing" in block

    # Existenz-Beweis ueber einen echten Request statt Routen-Introspektion
    # (die Router sind gewrappt und tragen kein ``path``-Attribut): mit
    # Flag aus antwortet die Route 503 — ein 404 hiesse, der Pfad, auf
    # den client.js zeigt, existiert im Backend nicht mehr.
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "user_auth_enabled", False, raising=False)
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    antwort = TestClient(app).get("/api/insights/pattern-briefing")
    assert antwort.status_code == 503
