#!/usr/bin/env python3
"""Wertet Tap-Along-Annotationen aus (Plan B, Stufe 5).

Zwei Unterbefehle:

**calibrate** — eine Annotation gegen die bekannte Schnittliste eines
Kalibrier-Clips halten (aus ``make_calibration_clips.py``):

  python scripts/evaluate_annotations.py calibrate \\
      --annotation meine_annotation.json \\
      --truth kalibrierung/clip_01.wahrheit.json

**pairs** — ein Verzeichnis voller Annotations-Exporte nach
``pair_key`` zu (Langform, Kurzform)-Paaren gruppieren und
``compare_pairs`` rechnen:

  python scripts/evaluate_annotations.py pairs --dir annotationen/

Echte Cutdowns und eigenstaendige Kurzformate werden getrennt
ausgewertet (Plan B, Abschnitt 2.4) — beides in einen Topf zu werfen
waere der naechste selbstgebaute Confound.

Braucht keine Datenbank. Laeuft aus backend/:
  cd backend && python scripts/evaluate_annotations.py ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Wie measure_system_prompt_tokens.py: laeuft auch als
# ``python scripts/evaluate_annotations.py`` aus backend/, nicht nur
# als ``python -m scripts.evaluate_annotations``.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.annotation_eval import (  # noqa: E402
    ANNOTATION_SCHEMA,
    Annotation,
    AnnotationError,
    build_pairs,
    evaluate_against_truth,
    parse_annotation,
    parse_truth,
    summarize_pair_medians,
)
from app.services.video_features import (  # noqa: E402
    MIN_PAIRS_FOR_TEST,
    ComparisonResult,
    compare_pairs,
)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationError(f"{path}: nicht lesbar ({exc}).") from exc


def _cmd_calibrate(args: argparse.Namespace) -> int:
    ann = parse_annotation(_load_json(Path(args.annotation)))
    cuts, duration = parse_truth(_load_json(Path(args.truth)))
    report = evaluate_against_truth(
        cuts, list(ann.taps), duration_seconds=duration
    )
    print(f"Kalibrierung: {args.annotation} gegen {args.truth}")
    if ann.annotator:
        print(f"Person: {ann.annotator}")
    if ann.playback_rate:
        print(f"Abspielrate: {ann.playback_rate}x")
    print()
    d = report.to_dict()
    print(f"  wahre Schnitte     {d['n_true_cuts']:>6}")
    print(f"  getippt            {d['n_taps']:>6}")
    print(f"  zugeordnet         {d['n_matched']:>6}")
    print(f"  Trefferquote       {d['recall']:>6.1%}")
    print(f"  Praezision         {d['precision']:>6.1%}")
    if d["latency_mean_seconds"] is not None:
        print(f"  Latenz (Mittel)    {d['latency_mean_seconds']:>6.3f} s"
              f"   (positiv = zu spaet; harmlos, kuerzt sich)")
    if d["latency_std_seconds"] is not None:
        print(f"  Latenz (Streuung)  {d['latency_std_seconds']:>6.3f} s"
              f"   (der eigentliche Stoerfaktor)")
    print(f"  ASL wahr/getippt   {d['asl_true_seconds']:.2f} / "
          f"{d['asl_tapped_seconds']:.2f} s  "
          f"(Verhaeltnis {d['asl_error_ratio']:.3f}; ueber 1 = "
          f"Schnitte verpasst, konservative Richtung)")
    for note in d.get("notes", []):
        print(f"  Hinweis: {note}")
    return 0


def _print_comparisons(title: str, results: list[ComparisonResult]) -> None:
    print(f"\n{title}")
    print(f"  {'Merkmal':<24} {'n':>4} {'Median L':>9} {'Median K':>9} "
          f"{'Effekt':>7} {'z':>6}  Urteil")
    for r in results:
        d = r.to_dict()
        print(
            f"  {d['feature']:<24} {d['n_a']:>4} "
            f"{d['median_a'] if d['median_a'] is not None else '—':>9} "
            f"{d['median_b'] if d['median_b'] is not None else '—':>9} "
            f"{d['effect'] if d['effect'] is not None else '—':>7} "
            f"{d['z'] if d['z'] is not None else '—':>6}  {d['verdict']}"
            + (f"  ({d['reason']})" if d.get("reason") else "")
        )


def _cmd_pairs(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    annotations: list[Annotation] = []
    errors: list[str] = []
    for path in sorted(directory.glob("*.json")):
        data = _load_json(path)
        if data.get("schema") != ANNOTATION_SCHEMA:
            continue  # Wahrheits-JSONs u. ae. liegen oft im selben Ordner
        try:
            annotations.append(parse_annotation(data))
        except AnnotationError as exc:
            errors.append(f"{path.name}: {exc}")

    if errors:
        print("Nicht auswertbar:")
        for e in errors:
            print(f"  {e}")
    if not annotations:
        print(f"Keine Annotationen mit Schema {ANNOTATION_SCHEMA} in "
              f"{directory}.")
        return 1

    result = build_pairs(annotations)
    print(f"{len(annotations)} Annotationen -> "
          f"{len(result.cutdown_pairs)} Cutdown-Paare, "
          f"{len(result.other_pairs)} Paare mit eigenstaendiger Kurzform.")
    for msg in result.skipped:
        print(f"  uebersprungen: {msg}")

    if result.cutdown_pairs:
        _print_comparisons(
            f"Kern-PoC — echte Cutdowns ({len(result.cutdown_pairs)} Paare, "
            f"Wilcoxon, |z| >= 2.0):",
            compare_pairs(result.cutdown_pairs),
        )
        lang_med, kurz_med = summarize_pair_medians(
            result.cutdown_pairs, "asl_seconds"
        )
        if lang_med is not None and kurz_med is not None:
            print(f"\n  Zur Einordnung: mittlere Einstellungslaenge "
                  f"Langform {lang_med:.2f} s, Kurzform {kurz_med:.2f} s.")
    if result.other_pairs:
        if len(result.other_pairs) >= MIN_PAIRS_FOR_TEST:
            _print_comparisons(
                f"Eigenstaendige Kurzformate ({len(result.other_pairs)} "
                f"Paare):",
                compare_pairs(result.other_pairs),
            )
        else:
            print(f"\nEigenstaendige Kurzformate: {len(result.other_pairs)} "
                  f"Paare — unter dem Minimum von {MIN_PAIRS_FOR_TEST}, "
                  f"kein Test, nur gezaehlt.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    cal = sub.add_parser("calibrate", help="Annotation gegen Wahrheit halten")
    cal.add_argument("--annotation", required=True)
    cal.add_argument("--truth", required=True)
    cal.set_defaults(func=_cmd_calibrate)

    pairs = sub.add_parser("pairs", help="Paar-Vergleich ueber ein Verzeichnis")
    pairs.add_argument("--dir", required=True)
    pairs.set_defaults(func=_cmd_pairs)

    args = parser.parse_args()
    try:
        return args.func(args)
    except AnnotationError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
