import inspect
from sqlmodel import SQLModel, Session, create_engine, select

from app.models.entities import Asset, Channel, Post, Title, TitleCandidate
from app.services import title_rematch as title_rematch_module
from app.services.title_rematch import rematch_unassigned_assets


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_rematch_assigns_safe_whitelist_match():
    with _session() as session:
        title = Title(title_original="Euphoria", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/post-1", caption="Official Trailer: Euphoria")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Trailer zu Euphoria")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        refreshed = session.get(Asset, asset.id)

        assert summary.checked == 1
        assert summary.auto_matched == 1
        assert summary.candidates_created == 0
        assert summary.still_unmatched == 0
        assert refreshed is not None
        assert refreshed.title_id == title.id


def test_rematch_does_not_create_candidate_for_guess_only_match():
    """Sprint 28.05.2026 (Variante D): wenn der Whitelist-Matcher
    KEINEN Treffer findet (``match.source == "none"``) und nur ein
    6-Token-Guess als ``suggested_title`` produziert, wird KEIN
    Candidate angelegt — das war der Hauptproduzent des Rauschens
    ("Rivals" fuer UEFA-Posts).

    Vorher (vor Variante D) hat dieser Test ``candidates_created == 1``
    erwartet; das ALTE Verhalten ist absichtlich abgeschaltet.
    ``still_unmatched`` zaehlt weiter, sodass die Summary trotzdem
    sichtbar macht, dass Assets ohne Title-Zuordnung herumliegen.
    """
    with _session() as session:
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/post-2", caption="Unknown preview teaser")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Kein bekannter Titel")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        candidates = session.exec(select(TitleCandidate)).all()

        assert summary.checked == 1
        assert summary.auto_matched == 0
        assert summary.candidates_created == 0
        assert summary.still_unmatched == 1
        assert candidates == []


def test_rematch_creates_candidate_for_fuzzy_whitelist_match():
    """Sprint 28.05.2026 (Variante D, Gegenprobe): echte Whitelist-
    Matches mit Confidence < 0.95 bleiben als Candidate erhalten — das
    sind legitime Review-Faelle (Fuzzy-Hit, Brand-Whitelist,
    ambiguous-Multi-Match). Hier provoziert eine Caption mit einem
    Tippfehler im Whitelist-Titel den Fuzzy-Pfad
    (``SequenceMatcher.ratio > 0.72`` aber < 0.95, sodass
    ``is_safe_auto_match`` NICHT greift)."""
    with _session() as session:
        title = Title(title_original="Drawn to You", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        # 1-Buchstabe-Drop ("Drawn to Yu") liegt bei SequenceMatcher.ratio
        # ~0.91 — ueber der 0.72-Fuzzy-Schwelle, unter der 0.95-Safe-Bar.
        post = Post(
            channel_id=channel.id,
            post_url="https://example.com/post-fuzzy",
            caption="Drawn to Yu",
        )
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(
            post_id=post.id, title_id=None,
            ai_summary_de="drawn to yu",
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        candidates = session.exec(select(TitleCandidate)).all()

        # Kein safe-auto-Match (Confidence < 0.95), aber der Fuzzy-
        # Match liefert einen Whitelist-Treffer → Candidate wird
        # angelegt.
        assert summary.checked == 1
        assert summary.auto_matched == 0
        assert summary.candidates_created == 1
        assert len(candidates) == 1
        assert candidates[0].asset_id == asset.id


def test_rematch_open_fallback_for_ambiguous_no_release():
    """Variante D Teil 3 + Candidate-Insert-Filter-Sprint: matcht der Text
    zwei gleich spezifische Titel derselben Franchise ohne Release-Datum,
    löst weder Spezifität noch Zeit eindeutig auf → KEINE Zuweisung
    (title_id bleibt None).

    Verhaltensänderung (Candidate-Insert-Filter): ``source=="ambiguous"``
    zählt jetzt als Rausch-Quelle ohne Auto-Match-Nutzen und erzeugt im
    Auto-Pfad (rematch, ``skip_if_guess_only=True``) KEINEN Candidate mehr.
    Vorher legte dieser Fall einen OPEN-Kandidaten zur Review an; das war
    eine der unterdrückten Rausch-Quellen. ``still_unmatched`` zählt weiter,
    sodass die Summary das unzugeordnete Asset weiterhin sichtbar macht."""
    with _session() as session:
        a = Title(title_original="Alpha One", franchise="Saga", aliases=["Saga Collection"], active=True)
        b = Title(title_original="Alpha Two", franchise="Saga", aliases=["Saga Collection"], active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(a)
        session.add(b)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/post-ambig",
                    caption="Behind the Saga Collection shoot")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Saga Collection feature")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        refreshed = session.get(Asset, asset.id)
        candidates = session.exec(select(TitleCandidate)).all()

        assert summary.auto_matched == 0
        assert refreshed is not None
        assert refreshed.title_id is None          # im Zweifel NICHTS zuweisen
        assert summary.still_unmatched == 1
        assert summary.candidates_created == 0     # ambiguous → kein Candidate mehr
        assert candidates == []


def test_rematch_batches_commits_for_many_auto_matches(monkeypatch):
    """Sprint 10g: with ``commit_batch_size=10`` and 25 matched assets we
    expect 3 batched commits inside the loop (2 full batches × 10 + 1
    flush of the remaining 5 at the end). All 25 assets must be assigned.
    """
    with _session() as session:
        title = Title(title_original="Euphoria", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)
        session.refresh(title)

        asset_ids = []
        for i in range(25):
            post = Post(
                channel_id=channel.id,
                post_url=f"https://example.com/p-{i}",
                caption="Official Trailer: Euphoria",
            )
            session.add(post)
            session.commit()
            session.refresh(post)
            asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Euphoria trailer")
            session.add(asset)
            session.commit()
            session.refresh(asset)
            asset_ids.append(asset.id)

        commit_calls = {"n": 0}
        original_commit = Session.commit

        def counting_commit(self):
            commit_calls["n"] += 1
            return original_commit(self)

        monkeypatch.setattr(Session, "commit", counting_commit)

        summary = rematch_unassigned_assets(session, commit_batch_size=10)

        assert summary.checked == 25
        assert summary.auto_matched == 25
        assert summary.still_unmatched == 0

        for aid in asset_ids:
            refreshed = session.get(Asset, aid)
            assert refreshed.title_id == title.id

        # At least 3 batched commits inside rematch_unassigned_assets
        # (10, 10, 5 flush). ``resolve_open_candidates_for_asset`` skips
        # commits when no open candidate exists, so it doesn't inflate the
        # count for this scenario.
        assert commit_calls["n"] >= 3


def test_rematch_reads_vision_description_field():
    """Recall-Fix Post-#277: ein Titel, der NUR in asset.vision_description steht
    (Sprint-5.3.1-Vision-Output), war fuer den Matcher unsichtbar. Jetzt wird das
    Feld gelesen — ein Multi-Token-Titel dort matcht safe."""
    with _session() as session:
        title = Title(title_original="Inside Out 2", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/vd-1", caption=None)
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None,
                      vision_description="A clip from Inside Out 2 with the emotions")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        refreshed = session.get(Asset, asset.id)

        assert summary.auto_matched == 1
        assert refreshed.title_id == title.id


def test_rematch_zero_budget_breaks_immediately_partial():
    """Soft-Deadline (Cron-Run 16421771): mit aufgebrauchtem Budget bricht
    die Schleife VOR dem ersten Asset ab — nichts verarbeitet, ``partial``
    gesetzt, ``remaining`` = kompletter Bestand, keine Zuweisungen."""
    with _session() as session:
        title = Title(title_original="Euphoria", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        for i in range(3):
            post = Post(channel_id=channel.id, post_url=f"https://example.com/zb-{i}",
                        caption="Official Trailer: Euphoria")
            session.add(post)
            session.commit()
            session.refresh(post)
            session.add(Asset(post_id=post.id, title_id=None, ai_summary_de="Euphoria trailer"))
            session.commit()

        summary = rematch_unassigned_assets(session, time_budget_seconds=0)

        assert summary.partial is True
        assert summary.checked == 0
        assert summary.remaining == 3
        assert summary.auto_matched == 0
        unassigned = session.exec(select(Asset).where(Asset.title_id == None)).all()  # noqa: E711
        assert len(unassigned) == 3


def test_rematch_budget_midway_commits_partial_progress(monkeypatch):
    """Soft-Deadline mitten im Lauf: Fake-Uhr laesst das Budget nach 2 von 4
    Assets ablaufen. Die 2 verarbeiteten Auto-Matches sind COMMITTET (der
    Flush nach der Schleife greift auch im Partial-Fall), die 2 restlichen
    bleiben unzugeordnet und werden als ``remaining`` gemeldet."""
    with _session() as session:
        title = Title(title_original="Euphoria", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        for i in range(4):
            post = Post(channel_id=channel.id, post_url=f"https://example.com/mb-{i}",
                        caption="Official Trailer: Euphoria")
            session.add(post)
            session.commit()
            session.refresh(post)
            session.add(Asset(post_id=post.id, title_id=None, ai_summary_de="Euphoria trailer"))
            session.commit()

        # Fake-Uhr, die pro ASSET tickt, nicht pro monotonic()-Aufruf.
        #
        # Frueher lief sie an der Aufrufzahl: jeder ``monotonic()`` +1. Damit
        # kodierte der Test, WIE OFT die Funktion auf die Uhr sieht — und fiel,
        # sobald sie am 24.08.2026 eine Zeitmessung dazubekam, obwohl sich am
        # Budget-Verhalten nichts geaenderte hatte. Der Takt haengt jetzt am
        # Matcher-Aufruf, also an genau einem pro Asset; zusaetzliche Messungen
        # innerhalb eines Assets verschieben nichts mehr.
        #
        # Nur das ``time``-Attribut IM Modul-Namespace patchen (SimpleNamespace),
        # nicht das globale stdlib-``time`` — sonst wuerden fremde
        # ``monotonic``-Aufrufer die Ticks mitverbrauchen.
        from types import SimpleNamespace

        takt = {"asset": 0}
        echter_matcher = title_rematch_module.find_best_title_match

        def _matcher_mit_takt(*args, **kwargs):
            takt["asset"] += 1
            return echter_matcher(*args, **kwargs)

        monkeypatch.setattr(
            title_rematch_module, "find_best_title_match", _matcher_mit_takt
        )
        monkeypatch.setattr(
            title_rematch_module, "time",
            SimpleNamespace(monotonic=lambda: float(takt["asset"])),
        )

        # Budget 2.0: die Checks vor Asset 0 und 1 sehen 0.0 und 1.0 und lassen
        # durch, der Check vor Asset 2 sieht 2.0 und bricht ab.
        summary = rematch_unassigned_assets(session, time_budget_seconds=2.0)

        assert summary.partial is True
        assert summary.checked == 2
        assert summary.remaining == 2
        assert summary.auto_matched == 2
        assigned = session.exec(select(Asset).where(Asset.title_id != None)).all()  # noqa: E711
        assert len(assigned) == 2


def test_rematch_without_budget_reports_not_partial():
    """Default-Pfad (kein Budget, z.B. manueller /api/titles/rematch-assets):
    ``partial`` bleibt False, ``remaining`` 0 — Verhalten unveraendert."""
    with _session() as session:
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/nb-1",
                    caption="just some random caption")
        session.add(post)
        session.commit()
        session.refresh(post)
        session.add(Asset(post_id=post.id, title_id=None, ai_summary_de="generic"))
        session.commit()

        summary = rematch_unassigned_assets(session)

        assert summary.partial is False
        assert summary.remaining == 0
        assert summary.checked == 1


def test_rematch_large_batch_is_fast_and_correct():
    """Perf regression guard (post-#280): a large catalog + many unmatched assets
    must finish quickly. The candidate path re-runs the matcher per asset; without
    the batch-cached bundle/indices it reloaded all titles per asset (~1 asset/s,
    cron-untauglich). Here most assets hit the candidate path (generic text) and a
    couple auto-match a real title."""
    import time
    with _session() as session:
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(channel)
        # Large catalog
        for i in range(1500):
            session.add(Title(title_original=f"Catalogtitle{i}xyz", active=True))
        session.add(Title(title_original="Mortal Kombat II", active=True))
        session.commit()
        session.refresh(channel)

        # 2 clearly-matching assets + 120 non-matching (candidate/unmatched) assets
        match_ids = []
        for i in range(2):
            p = Post(channel_id=channel.id, post_url=f"https://example.com/m-{i}",
                     caption="New look at Mortal Kombat II today")
            session.add(p); session.commit(); session.refresh(p)
            a = Asset(post_id=p.id, title_id=None, ai_summary_de="Mortal Kombat II clip")
            session.add(a); session.commit(); session.refresh(a)
            match_ids.append(a.id)
        for i in range(120):
            p = Post(channel_id=channel.id, post_url=f"https://example.com/u-{i}",
                     caption="just some random everyday caption without any title")
            session.add(p); session.commit(); session.refresh(p)
            a = Asset(post_id=p.id, title_id=None, ai_summary_de="generic description here")
            session.add(a); session.commit(); session.refresh(a)

        started = time.monotonic()
        summary = rematch_unassigned_assets(session, commit_batch_size=50)
        elapsed = time.monotonic() - started

        assert summary.checked == 122
        assert summary.auto_matched == 2
        for mid in match_ids:
            assert session.get(Asset, mid).title_id is not None
        # Batch-cached: comfortably fast. The pre-fix per-asset bundle reload over
        # 1500 titles x 122 assets would be far slower.
        assert elapsed < 15.0, f"rematch too slow ({elapsed:.2f}s) — per-asset reload regressed?"


# --- Zeitmessung (24.08.2026) -------------------------------------------
#
# Die Stage schaffte im Montagslauf 781 Assets in 28 Minuten — zwei
# Sekunden pro Asset, obwohl Bundle und Indizes nur einmal pro Lauf
# gebaut werden. Den Deckel anzuheben behandelt das Symptom; diese
# Messung trennt die drei Verdächtigen, damit die nächste Entscheidung
# auf Zahlen steht.


def test_summary_weist_die_zeit_nach_abschnitten_aus():
    with _session() as session:
        title = Title(title_original="Euphoria", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)
        post = Post(channel_id=channel.id, post_url="https://example.com/zeit-1",
                    caption="Official Trailer: Euphoria")
        session.add(post)
        session.commit()
        session.refresh(post)
        session.add(Asset(post_id=post.id, title_id=None, ai_summary_de="Euphoria trailer"))
        session.commit()

        summary = rematch_unassigned_assets(session)

        d = summary.to_dict()
        for feld in ("setup_seconds", "match_seconds", "candidate_seconds",
                     "commit_seconds"):
            assert feld in d, f"{feld} fehlt im Summary"
            assert isinstance(d[feld], float)
            assert d[feld] >= 0.0
        assert d["assets_pro_sekunde"] is not None, (
            "Bei mindestens einem geprueften Asset muss eine Rate stehen — "
            "sie ist die Zahl, an der der naechste Deckel haengt."
        )


def test_ohne_assets_bleibt_die_rate_none():
    """Keine Messung ist etwas anderes als 'null pro Sekunde'. Eine 0.0
    hier wuerde im Log wie ein Totalausfall aussehen."""
    with _session() as session:
        summary = rematch_unassigned_assets(session)

        assert summary.checked == 0
        assert summary.to_dict()["assets_pro_sekunde"] is None


def test_setup_zeit_zaehlt_den_index_aufbau_mit():
    """``setup_seconds`` wird ab Funktionsstart gemessen, nicht ab dem
    ersten Asset — der Index-Aufbau ueber 47k Titel ist der erste
    Verdaechtige und darf nicht unsichtbar bleiben."""
    quelle = inspect.getsource(rematch_unassigned_assets)
    vor_schleife = quelle.split("for index, asset in enumerate")[0]

    assert "summary.setup_seconds = time.monotonic() - started" in vor_schleife, (
        "Die Setup-Messung muss VOR der Schleife stehen und ab ``started`` "
        "rechnen, sonst zaehlt sie den Bundle-/Index-Aufbau nicht mit."
    )


# --- Rematch-Merker (31.08.2026) --------------------------------------
#
# Vorher lud jeder Lauf ALLE titellosen Assets neueste-zuerst und brach
# nach dem Zeitbudget ab: die vorderen ~1.200 wurden jede Woche neu
# geprueft, die hinteren 2.639 nie erreicht. Der Stempel macht daraus
# eine Rotation — nie geprueft zuerst, danach am laengsten nicht
# geprueft, und jeder Check rueckt das Asset ans Ende.


def _titelloses_asset(session, channel, *, slug, created_at=None, last_rematch_at=None):
    from datetime import datetime, timezone

    post = Post(
        channel_id=channel.id,
        post_url=f"https://example.com/{slug}",
        caption="Unknown preview teaser",
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Kein Titel")
    if created_at is not None:
        asset.created_at = created_at
    if last_rematch_at is not None:
        asset.last_rematch_at = last_rematch_at
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def test_rematch_stempelt_jedes_gepruefte_asset():
    with _session() as session:
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(channel)
        session.commit()
        session.refresh(channel)
        asset = _titelloses_asset(session, channel, slug="stempel")

        rematch_unassigned_assets(session)

        refreshed = session.get(Asset, asset.id)
        assert refreshed.last_rematch_at is not None, (
            "Auch ein erfolgloser Check zaehlt als geprueft — sonst "
            "steht derselbe hoffnungslose Fall naechste Woche wieder vorn."
        )


def test_rematch_rotation_nie_gepruefte_zuerst_dann_die_aeltesten():
    from datetime import datetime, timedelta, timezone

    basis = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with _session() as session:
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(channel)
        session.commit()
        session.refresh(channel)

        # Letzte Woche geprueft — muss ans Ende.
        vorwoche = _titelloses_asset(
            session, channel, slug="vorwoche",
            created_at=basis + timedelta(days=20),
            last_rematch_at=basis + timedelta(days=24),
        )
        # Vor drei Wochen geprueft — kommt vor der Vorwoche dran.
        alt_geprueft = _titelloses_asset(
            session, channel, slug="alt-geprueft",
            created_at=basis + timedelta(days=1),
            last_rematch_at=basis + timedelta(days=10),
        )
        # Nie geprueft: neuestes zuerst (frische Kampagnen speisen die
        # Montags-Queue), aeltere nie-gepruefte danach.
        nie_alt = _titelloses_asset(
            session, channel, slug="nie-alt", created_at=basis,
        )
        nie_neu = _titelloses_asset(
            session, channel, slug="nie-neu",
            created_at=basis + timedelta(days=25),
        )

        # Deckel 2: nur die beiden NIE gepr. passen in dieses Fenster.
        summary = rematch_unassigned_assets(session, time_budget_seconds=None)
        assert summary.checked == 4

        stempel = {
            a_id: session.get(Asset, a_id).last_rematch_at
            for a_id in [vorwoche.id, alt_geprueft.id, nie_alt.id, nie_neu.id]
        }
        # Reihenfolge ueber die Stempel-Zeitpunkte belegen: nie-neu vor
        # nie-alt vor alt-geprueft vor vorwoche.
        assert stempel[nie_neu.id] <= stempel[nie_alt.id] <= stempel[alt_geprueft.id] <= stempel[vorwoche.id]


def test_rematch_rotation_wird_in_der_query_sortiert():
    """Quelltext-Waechter: die Rotation haengt an der ORDER-BY-Klausel —
    ``nulls_first`` fuer die Nie-Gepruefte-Gruppe, dann Alter des
    Stempels. Ein ``order_by(created_at.desc())`` allein waere der alte
    fest stehende Kopf."""
    quelle = inspect.getsource(rematch_unassigned_assets)
    assert "last_rematch_at.asc().nulls_first()" in quelle
    assert "created_at.desc()" in quelle
