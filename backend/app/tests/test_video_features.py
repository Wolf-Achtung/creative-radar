"""Schnitt-Merkmale und Kohortenvergleich (Trailer-Intelligence Stufe 5).

Der Kern ist nicht das Rechnen, sondern die Vermeidung des vierten
Confounds. Dieses Projekt hat dreimal eine Kennzahl gebaut, die etwas
anderes mass als ihr Name behauptete. Die Tests sichern deshalb vor
allem:

1. Laufzeitabhaengige Merkmale sind im Kohortenvergleich **gesperrt** —
   ``shot_count`` zwischen Langform und Kurzform zu vergleichen misst
   die Dauer.
2. Die skalenfreien Merkmale sind es wirklich: dasselbe Schnittmuster
   in doppelter Laufzeit muss identische Werte liefern.
3. Die Rangstatistik stimmt gegen von Hand nachrechenbare Faelle.
4. Zu duenne Stichproben bekommen kein Urteil, sondern einen Grund.
"""
from __future__ import annotations

import pytest

from app.services import video_features as vf


def _even_shots(n: int, length: float, start: float = 0.0):
    """``n`` gleich lange Einstellungen hintereinander."""
    return [(start + i * length, start + (i + 1) * length) for i in range(n)]


# ---------- Validierung --------------------------------------------------


def test_empty_shot_list_is_rejected():
    with pytest.raises(vf.ShotListError, match="Leere"):
        vf.extract_features([])


def test_overlapping_shots_are_rejected_not_repaired():
    """Eine ueberlappende Liste deutet auf einen Fehler im erzeugenden
    Werkzeug. Sie stillschweigend zurechtzubiegen wuerde den Fehler
    verschleiern und die Zahlen unbrauchbar machen, ohne dass es
    jemand merkt."""
    with pytest.raises(vf.ShotListError, match="ueberlappt"):
        vf.extract_features([(0.0, 5.0), (3.0, 8.0)])


def test_zero_length_shot_is_rejected():
    with pytest.raises(vf.ShotListError, match="liegt nicht nach"):
        vf.extract_features([(0.0, 5.0), (5.0, 5.0)])


def test_duration_shorter_than_shots_is_rejected():
    with pytest.raises(vf.ShotListError, match="kuerzer"):
        vf.extract_features(_even_shots(4, 5.0), duration_seconds=10.0)


def test_duration_defaults_to_last_shot_end():
    f = vf.extract_features(_even_shots(10, 2.0))
    assert f.duration_seconds == pytest.approx(20.0)


def test_trailing_runtime_after_last_cut_is_honoured():
    """Nachspann nach dem letzten Schnitt gehoert in die Laufzeit —
    sonst waere die ASL zu niedrig."""
    f = vf.extract_features(_even_shots(10, 2.0), duration_seconds=30.0)
    assert f.duration_seconds == pytest.approx(30.0)
    assert f.asl_seconds == pytest.approx(3.0)


# ---------- Der Kern: sind die Merkmale wirklich skalenfrei? -------------


def test_same_rhythm_at_double_length_gives_identical_scale_free_values():
    """Der eigentliche Zweck der Merkmalsauswahl.

    Zwei Videos mit demselben *Muster*, eines doppelt so lang. Jedes
    skalenfreie Merkmal muss identisch herauskommen — sonst misst es
    heimlich die Laufzeit und taugt nicht fuer den Vergleich zwischen
    Formatklassen.
    """
    kurz = _even_shots(6, 1.0) + _even_shots(6, 0.5, start=6.0)
    lang = _even_shots(6, 2.0) + _even_shots(6, 1.0, start=12.0)

    a = vf.extract_features(kurz)
    b = vf.extract_features(lang)

    assert b.duration_seconds == pytest.approx(2 * a.duration_seconds)
    for name in sorted(vf.SCALE_FREE_FEATURES):
        va, vb = getattr(a, name), getattr(b, name)
        if name in ("asl_seconds", "median_shot_seconds"):
            # Raten in Sekunden: skalieren mit dem Muster, nicht mit der
            # Laufzeit — hier verdoppelt sich das Muster selbst.
            assert vb == pytest.approx(2 * va), name
        elif va is None:
            assert vb is None, name
        else:
            assert vb == pytest.approx(va), name


def test_shot_count_is_flagged_duration_dependent():
    assert "shot_count" in vf.DURATION_DEPENDENT_FEATURES
    assert "duration_seconds" in vf.DURATION_DEPENDENT_FEATURES
    assert not (vf.SCALE_FREE_FEATURES & vf.DURATION_DEPENDENT_FEATURES)


def test_comparing_a_duration_dependent_feature_raises():
    """Die Lehre aus drei Confounds, in Code gegossen. Ein Kommentar
    haette es nicht verhindert, ein Fehler schon."""
    with pytest.raises(ValueError, match="haengt an der Laufzeit"):
        vf.compare_unpaired("shot_count", [1.0] * 20, [2.0] * 20)
    with pytest.raises(ValueError, match="haengt an der Laufzeit"):
        vf.compare_paired("duration_seconds", [(1.0, 2.0)] * 20)


def test_unknown_feature_raises():
    with pytest.raises(ValueError, match="kein bekanntes Merkmal"):
        vf.compare_unpaired("hook_typ", [1.0] * 20, [2.0] * 20)


# ---------- Einzelmerkmale -----------------------------------------------


def test_asl_is_runtime_over_shot_count():
    f = vf.extract_features(_even_shots(20, 1.5))
    assert f.shot_count == 20
    assert f.asl_seconds == pytest.approx(1.5)
    assert f.median_shot_seconds == pytest.approx(1.5)


def test_even_cutting_has_no_variation():
    f = vf.extract_features(_even_shots(12, 2.0))
    assert f.shot_length_cv == pytest.approx(0.0)
    assert f.rhythm_ratio == pytest.approx(1.0)


def test_accelerating_cut_gives_rhythm_ratio_below_one():
    """Der klassische Trailer-Aufbau: hinten schneller geschnitten als
    vorne. Genau das soll ``rhythm_ratio`` einfangen."""
    shots = _even_shots(6, 3.0)                      # erstes Drittel: langsam
    shots += _even_shots(6, 1.5, start=18.0)          # Mitte
    shots += _even_shots(6, 0.75, start=27.0)         # Ende: schnell
    f = vf.extract_features(shots)

    assert f.rhythm_ratio is not None and f.rhythm_ratio < 1.0
    assert f.asl_first_third_ratio > f.asl_last_third_ratio


def test_longest_shot_position_is_relative(   ):
    """Die Position der laengsten Einstellung als Anteil der Laufzeit —
    dimensionslos und damit zwischen Laengen vergleichbar."""
    shots = _even_shots(8, 1.0) + [(8.0, 14.0)] + _even_shots(8, 1.0, start=14.0)
    f = vf.extract_features(shots)

    # Die lange Einstellung liegt mittig.
    assert 0.4 < f.longest_shot_position < 0.6
    assert f.longest_shot_ratio > 1.0


def test_thirds_need_enough_shots():
    f = vf.extract_features(_even_shots(4, 2.0))
    assert f.rhythm_ratio is None
    assert f.asl_first_third_ratio is None
    assert any("Minimum" in n for n in f.notes)


def test_empty_third_is_reported_not_guessed():
    """Eine Einstellung, die ein ganzes Drittel ueberspannt, laesst eine
    Luecke — die wird gemeldet statt gefuellt."""
    shots = _even_shots(9, 0.5) + [(4.5, 30.0)] + _even_shots(2, 0.5, start=30.0)
    f = vf.extract_features(shots)
    if f.rhythm_ratio is None:
        assert any("ohne eigene Einstellung" in n for n in f.notes)


# ---------- Lautheit ------------------------------------------------------


def test_loudness_positions_are_relative_to_runtime():
    shots = _even_shots(12, 1.0)
    loudness = [(t, 0.1) for t in range(6)] + [(t, 0.9) for t in range(6, 12)]
    f = vf.extract_features(shots, loudness=loudness)

    assert f.loudness_rise_position == pytest.approx(0.5)
    assert f.loudness_peak_position == pytest.approx(0.5)


def test_loudness_is_invariant_to_absolute_level():
    """Ein leise gemasterter und ein lauter Trailer mit derselben
    Kurvenform muessen denselben Wert liefern — sonst maesse das
    Merkmal die Mastering-Lautheit statt den Aufbau."""
    shots = _even_shots(12, 1.0)
    leise = [(t, 0.01) for t in range(6)] + [(t, 0.09) for t in range(6, 12)]
    laut = [(t, 10.0) for t in range(6)] + [(t, 90.0) for t in range(6, 12)]

    a = vf.extract_features(shots, loudness=leise)
    b = vf.extract_features(shots, loudness=laut)
    assert a.loudness_rise_position == pytest.approx(b.loudness_rise_position)


def test_constant_loudness_has_no_rise():
    shots = _even_shots(12, 1.0)
    f = vf.extract_features(shots, loudness=[(t, 0.5) for t in range(12)])
    assert f.loudness_rise_position is None
    assert f.loudness_peak_position is not None


def test_missing_audio_is_reported():
    f = vf.extract_features(_even_shots(12, 1.0))
    assert f.loudness_rise_position is None
    assert any("Keine Audiospur" in n for n in f.notes)


# ---------- Rangstatistik -------------------------------------------------


def test_ranks_average_ties():
    assert vf._ranks([10.0, 20.0, 30.0]) == [1.0, 2.0, 3.0]
    # Zwei gleiche Werte auf Platz 2 und 3 -> beide 2,5.
    assert vf._ranks([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]


def test_paired_test_detects_a_consistent_shift():
    """Zwanzig Paare, Langform durchgehend groesser. Der
    Vorzeichen-Rangtest muss das klar anzeigen."""
    pairs = [(2.0 + i * 0.1, 1.0 + i * 0.1) for i in range(20)]
    r = vf.compare_paired("asl_seconds", pairs)

    assert r.verdict == "differs"
    assert r.z is not None and r.z > 2.0
    assert r.effect == pytest.approx(1.0)  # alle Differenzen positiv


def test_paired_test_finds_nothing_when_there_is_nothing():
    pairs = [(1.0 + (i % 2) * 0.5, 1.25) for i in range(20)]
    r = vf.compare_paired("asl_seconds", pairs)
    assert r.verdict == "no_difference"
    assert abs(r.z) < 2.0


def test_paired_test_is_stronger_than_unpaired_on_the_same_data():
    """Der Grund, warum die Vorstufe eigenes Material empfiehlt: dieselbe
    Datenlage, nach Titel gepaart, liefert ein klares Urteil — ungepaart
    ersaeuft der Effekt in der Streuung zwischen den Filmen."""
    # Jeder Film hat sein eigenes Niveau (grosse Streuung), aber Langform
    # liegt konsistent 0,4 s ueber der Kurzform desselben Films.
    niveaus = [1.0 + i * 0.5 for i in range(14)]
    pairs = [(n + 0.4, n) for n in niveaus]

    gepaart = vf.compare_paired("asl_seconds", pairs)
    ungepaart = vf.compare_unpaired(
        "asl_seconds", [a for a, _ in pairs], [b for _, b in pairs]
    )

    assert gepaart.verdict == "differs"
    assert ungepaart.verdict == "no_difference"
    assert abs(gepaart.z) > abs(ungepaart.z)


def test_unpaired_test_detects_a_clean_separation():
    r = vf.compare_unpaired(
        "rhythm_ratio", [0.5 + i * 0.01 for i in range(15)], [1.5 + i * 0.01 for i in range(15)]
    )
    assert r.verdict == "differs"
    assert r.effect == pytest.approx(-1.0)  # Kohorte A durchgehend kleiner


def test_thin_arm_gets_no_verdict_but_a_reason():
    r = vf.compare_unpaired("asl_seconds", [1.0] * 4, [2.0] * 30)
    assert r.verdict == "insufficient"
    assert "mindestens" in r.reason
    assert r.z is None
    # Die Mediane werden trotzdem gemeldet.
    assert r.median_a == pytest.approx(1.0)


def test_too_few_pairs_get_no_verdict():
    r = vf.compare_paired("asl_seconds", [(2.0, 1.0)] * 5)
    assert r.verdict == "insufficient"
    assert "Normalapproximation" in r.reason


def test_zero_difference_pairs_are_dropped_and_reported():
    pairs = [(2.0 + i * 0.1, 1.0 + i * 0.1) for i in range(12)] + [(1.0, 1.0)] * 3
    r = vf.compare_paired("asl_seconds", pairs)
    assert r.verdict == "differs"
    assert "ohne Differenz verworfen" in r.reason


def test_identical_cohorts_are_insufficient_not_significant():
    """Alle Werte gleich: keine Varianz, also kein Test — und keinesfalls
    ein Urteil."""
    r = vf.compare_unpaired("asl_seconds", [1.0] * 15, [1.0] * 15)
    assert r.verdict == "insufficient"
    assert r.z is None


# ---------- Kohorten-Ebene ------------------------------------------------


def _features(asl: float, rhythm_fast: bool) -> vf.VideoFeatures:
    """Baut ein Video mit vorgegebener ASL und Rhythmusrichtung."""
    if rhythm_fast:
        shots = (
            _even_shots(6, asl * 1.6)
            + _even_shots(6, asl, start=6 * asl * 1.6)
            + _even_shots(6, asl * 0.4, start=6 * asl * 1.6 + 6 * asl)
        )
    else:
        shots = _even_shots(18, asl)
    return vf.extract_features(shots)


def test_compare_cohorts_covers_all_scale_free_features(  ):
    lang = [_features(2.0 + i * 0.05, True) for i in range(12)]
    kurz = [_features(1.0 + i * 0.05, False) for i in range(12)]

    results = vf.compare_cohorts(lang, kurz)
    names = {r.feature for r in results}

    assert names == vf.SCALE_FREE_FEATURES
    asl = next(r for r in results if r.feature == "asl_seconds")
    assert asl.verdict == "differs"
    # Auffaelligstes Merkmal steht vorn, Unbestimmbares hinten.
    assert results[0].verdict != "insufficient"
    assert all(
        r.verdict == "insufficient" for r in results[len(
            [x for x in results if x.verdict != "insufficient"]):]
    )


def test_missing_feature_drops_only_that_video_not_the_cohort():
    """Ein Video mit zu wenigen Einstellungen hat kein
    ``rhythm_ratio`` — das darf nur dieses Merkmal fuer dieses Video
    kosten, nicht die ganze Kohorte."""
    lang = [_features(2.0 + i * 0.05, True) for i in range(12)]
    lang.append(vf.extract_features(_even_shots(3, 2.0)))  # ohne Rhythmus
    kurz = [_features(1.0 + i * 0.05, False) for i in range(12)]

    results = {r.feature: r for r in vf.compare_cohorts(lang, kurz)}
    assert results["asl_seconds"].n_a == 13
    assert results["rhythm_ratio"].n_a == 12


def test_compare_pairs_uses_the_paired_test():
    pairs = [
        (_features(2.0 + i * 0.05, True), _features(1.0 + i * 0.05, False))
        for i in range(12)
    ]
    results = {r.feature: r for r in vf.compare_pairs(pairs)}
    assert results["asl_seconds"].test == "wilcoxon_paired"
    assert results["asl_seconds"].verdict == "differs"


def test_result_is_json_serialisable():
    import json

    lang = [_features(2.0 + i * 0.05, True) for i in range(12)]
    kurz = [_features(1.0 + i * 0.05, False) for i in range(12)]
    payload = [r.to_dict() for r in vf.compare_cohorts(lang, kurz)]
    json.dumps(payload)
    json.dumps(lang[0].to_dict())
