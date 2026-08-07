"""Tests fuer Stufe 5, Plan B — Tap-Annotationen und Kalibrierung.

Die drei methodischen Behauptungen aus dem Plan-B-Dokument (Abschnitt
2.3) stehen hier als Tests, nicht nur als Prosa:
1. Konstante Latenz aendert die Einstellungslaengen nicht.
2. Verpasste Schnitte ziehen die getippte ASL nach oben (konservativ).
3. Die Kalibrierung misst Latenz und Jitter tatsaechlich.
"""
from __future__ import annotations

import pytest

import app.services.annotation_eval as ae
import app.services.video_features as vf


def _truth(n: int, asl: float) -> tuple[list[float], float]:
    """n+1 gleich lange Einstellungen -> n innere Schnitte."""
    duration = (n + 1) * asl
    return [round(asl * (i + 1), 4) for i in range(n)], duration


# ---------- taps_to_shots ----------


def test_taps_become_boundaries_with_implicit_start_and_end():
    shots = ae.taps_to_shots([2.0, 5.0], duration_seconds=9.0)
    assert shots == [(0.0, 2.0), (2.0, 5.0), (5.0, 9.0)]


def test_no_taps_means_a_single_shot():
    assert ae.taps_to_shots([], duration_seconds=30.0) == [(0.0, 30.0)]


def test_unsorted_taps_are_sorted_not_rejected():
    # Tippen liefert der Konstruktion nach aufsteigende Zeiten; ein
    # unsortiertes JSON (Handbearbeitung) ist trotzdem eindeutig.
    shots = ae.taps_to_shots([5.0, 2.0], duration_seconds=9.0)
    assert shots == [(0.0, 2.0), (2.0, 5.0), (5.0, 9.0)]


def test_double_triggers_are_merged():
    shots = ae.taps_to_shots([2.0, 2.05, 5.0], duration_seconds=9.0)
    assert len(shots) == 3  # der 2.05er ist Tastenprellen


def test_taps_on_the_edges_are_dropped():
    shots = ae.taps_to_shots([0.05, 4.0, 8.97], duration_seconds=9.0)
    assert shots == [(0.0, 4.0), (4.0, 9.0)]


def test_result_always_satisfies_extract_features():
    shots = ae.taps_to_shots(
        [3.0, 1.0, 1.02, 0.1, 28.9], duration_seconds=29.0
    )
    feats = vf.extract_features(shots, duration_seconds=29.0)
    assert feats.shot_count == len(shots)


def test_nonpositive_duration_is_hard_error():
    with pytest.raises(ae.AnnotationError):
        ae.taps_to_shots([1.0], duration_seconds=0.0)


# ---------- Kalibrierung ----------


def test_perfect_tapping_scores_perfectly():
    cuts, duration = _truth(10, 2.0)
    report = ae.evaluate_against_truth(cuts, cuts, duration_seconds=duration)
    assert report.recall == 1.0
    assert report.precision == 1.0
    assert report.latency_mean_seconds == pytest.approx(0.0)
    assert report.asl_error_ratio == pytest.approx(1.0)


def test_constant_latency_is_measured_but_lengths_stay_unchanged():
    """Behauptung 1 aus Plan B: konstante Verspaetung ist harmlos."""
    cuts, duration = _truth(10, 2.0)
    late = [c + 0.2 for c in cuts]

    report = ae.evaluate_against_truth(cuts, late, duration_seconds=duration)
    assert report.recall == 1.0
    assert report.latency_mean_seconds == pytest.approx(0.2)
    assert report.latency_std_seconds == pytest.approx(0.0)

    # Und die Einstellungslaengen? Bis auf erste und letzte identisch.
    true_shots = ae.taps_to_shots(cuts, duration_seconds=duration)
    tapped_shots = ae.taps_to_shots(late, duration_seconds=duration)
    true_lengths = [e - s for s, e in true_shots][1:-1]
    tapped_lengths = [e - s for s, e in tapped_shots][1:-1]
    assert tapped_lengths == pytest.approx(true_lengths)


def test_missed_cuts_inflate_tapped_asl_the_conservative_direction():
    """Behauptung 2: Untertippen macht die ASL zu gross, nie zu klein."""
    cuts, duration = _truth(12, 1.5)
    tapped = cuts[::2]  # jeder zweite Schnitt verpasst
    report = ae.evaluate_against_truth(cuts, tapped, duration_seconds=duration)
    assert report.recall == pytest.approx(0.5)
    assert report.asl_error_ratio > 1.0


def test_jitter_shows_up_in_the_std_not_the_mean():
    """Behauptung 3: Streuung und Mittel sind getrennte Messwerte."""
    cuts, duration = _truth(8, 2.5)
    jittered = [c + (0.15 if i % 2 else -0.15) for i, c in enumerate(cuts)]
    report = ae.evaluate_against_truth(
        cuts, jittered, duration_seconds=duration
    )
    assert report.latency_mean_seconds == pytest.approx(0.0, abs=1e-9)
    assert report.latency_std_seconds == pytest.approx(0.15)


def test_extra_taps_cost_precision_not_recall():
    cuts, duration = _truth(6, 3.0)
    tapped = sorted(cuts + [1.4, 10.1])  # zwei Phantom-Schnitte
    report = ae.evaluate_against_truth(cuts, tapped, duration_seconds=duration)
    assert report.recall == 1.0
    assert report.precision == pytest.approx(6 / 8)


def test_matching_is_one_to_one():
    # Zwei Taps nahe demselben wahren Schnitt: nur einer darf zaehlen.
    report = ae.evaluate_against_truth(
        [5.0], [4.9, 5.1], duration_seconds=10.0
    )
    assert report.n_matched == 1
    assert report.precision == pytest.approx(0.5)


# ---------- Annotation -> VideoFeatures ----------


def _annotation_data(**over) -> dict:
    data = {
        "schema": ae.ANNOTATION_SCHEMA,
        "video": "abc123def45",
        "format_class": "langform",
        "pair_key": "titel-x",
        "duration_seconds": 100.0,
        "taps": [round(2.0 * (i + 1), 2) for i in range(49)],
        "music_entry": 12.0,
        "flags": {"ist_trailer": True, "ist_cutdown": True},
    }
    data.update(over)
    return data


def test_parse_rejects_wrong_schema_and_missing_fields():
    with pytest.raises(ae.AnnotationError):
        ae.parse_annotation({"schema": "anders/9"})
    with pytest.raises(ae.AnnotationError):
        ae.parse_annotation(_annotation_data(pair_key=""))
    with pytest.raises(ae.AnnotationError):
        ae.parse_annotation(_annotation_data(format_class="mittelform"))


def test_music_entry_becomes_a_relative_position():
    ann = ae.parse_annotation(_annotation_data())
    feats = ae.annotation_to_features(ann)
    assert feats.music_entry_position == pytest.approx(0.12)
    assert feats.shot_count == 50


def test_extract_features_never_sets_music_entry_itself():
    feats = vf.extract_features([(0.0, 1.0), (1.0, 2.0)])
    assert feats.music_entry_position is None


def test_music_entry_is_scale_free_and_comparable():
    assert "music_entry_position" in vf.SCALE_FREE_FEATURES
    pairs = [(0.10 + i * 0.01, 0.30 + i * 0.01) for i in range(12)]
    result = vf.compare_paired("music_entry_position", pairs)
    assert result.verdict == "differs"


# ---------- Paar-Bildung ----------


def test_pairs_are_built_and_cutdown_split_is_respected():
    anns = [
        ae.parse_annotation(_annotation_data(pair_key="a")),
        ae.parse_annotation(_annotation_data(
            pair_key="a", format_class="kurzform", duration_seconds=40.0,
            taps=[round(1.0 * (i + 1), 2) for i in range(39)],
        )),
        ae.parse_annotation(_annotation_data(pair_key="b")),
        ae.parse_annotation(_annotation_data(
            pair_key="b", format_class="kurzform", duration_seconds=40.0,
            taps=[5.0, 10.0, 20.0],
            flags={"ist_trailer": True, "ist_cutdown": False},
        )),
    ]
    result = ae.build_pairs(anns)
    assert len(result.cutdown_pairs) == 1
    assert len(result.other_pairs) == 1
    assert result.skipped == []


def test_non_trailers_and_incomplete_pairs_are_named_not_hidden():
    anns = [
        ae.parse_annotation(_annotation_data(
            pair_key="c", flags={"ist_trailer": False},
        )),
        ae.parse_annotation(_annotation_data(pair_key="d")),  # ohne Kurzform
    ]
    result = ae.build_pairs(anns)
    assert result.cutdown_pairs == []
    assert len(result.skipped) == 2


def test_duplicate_annotations_of_one_side_are_a_named_error():
    anns = [
        ae.parse_annotation(_annotation_data(pair_key="e")),
        ae.parse_annotation(_annotation_data(pair_key="e")),
        ae.parse_annotation(_annotation_data(
            pair_key="e", format_class="kurzform", duration_seconds=40.0,
            taps=[5.0, 10.0],
        )),
    ]
    result = ae.build_pairs(anns)
    assert result.cutdown_pairs == []
    assert any("2 Langform" in msg for msg in result.skipped)


# ---------- Wahrheits-JSON ----------


def test_parse_truth_roundtrip():
    cuts, duration = ae.parse_truth({
        "schema": ae.TRUTH_SCHEMA,
        "duration_seconds": 30.0,
        "cuts": [10.0, 5.0, 20.0],
    })
    assert cuts == [5.0, 10.0, 20.0]
    assert duration == 30.0


def test_parse_truth_rejects_missing_duration():
    with pytest.raises(ae.AnnotationError):
        ae.parse_truth({"schema": ae.TRUTH_SCHEMA, "cuts": [1.0]})
