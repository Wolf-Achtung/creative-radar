#!/usr/bin/env python3
"""Erzeugt Kalibrier-Clips mit exakt bekannter Schnittliste (ffmpeg).

Trailer-Intelligence Stufe 5, Plan B, Abschnitt 3: bevor ein Mensch
echte Trailer tippt oder ein Detektor echte Dateien schneidet, wird
beides an Clips gemessen, deren Schnittliste *bekannt* ist. Dieses
Skript erzeugt solche Clips — farbige Flaechen, jede Einstellung eine
andere Farbe — samt Wahrheits-JSON im Format, das
``app.services.annotation_eval.parse_truth`` liest.

Ehrlichkeitshalber: Farbwechsel sind leichter zu erkennen als echte
Filmschnitte, fuer Auge wie Detektor. Die gemessene Latenz und ihr
Jitter uebertragen sich brauchbar, die Trefferquote ist eine
Obergrenze. Wer es haerter will, senkt --min-shot.

Drei Profile:
  gleichmaessig   alle Einstellungen aehnlich lang
  beschleunigend  letztes Drittel deutlich schneller als das erste —
                  die Form, die echte Trailer haben (rhythm_ratio < 1)
  gemischt        beides gemischt, je Clip zufaellig

Beispiel:
  python scripts/make_calibration_clips.py --out kalibrierung --clips 5
  # -> kalibrierung/clip_01.mp4 + kalibrierung/clip_01.wahrheit.json ...

Benoetigt ffmpeg im PATH. Laeuft lokal (Mac reicht), nicht auf Railway.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

TRUTH_SCHEMA = "tap-truth/1"

# Kraeftige, klar unterscheidbare Farben; Nachbarn sind nie gleich,
# weil zyklisch durchgegangen wird.
COLORS = [
    "0xC0392B", "0x2980B9", "0x27AE60", "0xF1C40F",
    "0x8E44AD", "0xE67E22", "0x16A085", "0x2C3E50",
]


def _shot_lengths(
    rng: random.Random,
    profile: str,
    target_seconds: float,
    min_shot: float,
    max_shot: float,
) -> list[float]:
    lengths: list[float] = []
    total = 0.0
    while total < target_seconds:
        progress = total / target_seconds
        if profile == "beschleunigend":
            # Vom oberen Ende der Spanne zum unteren: hinten schneller.
            hi = max_shot - (max_shot - min_shot) * 0.7 * progress
            lo = min_shot
        else:
            hi, lo = max_shot, min_shot
        lengths.append(round(rng.uniform(lo, hi), 2))
        total += lengths[-1]
    return lengths


def _build_clip(
    out_dir: Path,
    index: int,
    lengths: list[float],
    size: str,
    fps: int,
) -> tuple[Path, Path]:
    clip = out_dir / f"clip_{index:02d}.mp4"
    truth = out_dir / f"clip_{index:02d}.wahrheit.json"

    inputs: list[str] = []
    for i, dur in enumerate(lengths):
        color = COLORS[i % len(COLORS)]
        inputs += ["-f", "lavfi", "-t", f"{dur}", "-i",
                   f"color=c={color}:s={size}:r={fps}"]
    n = len(lengths)
    filtergraph = (
        "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", filtergraph,
        "-map", "[v]", "-pix_fmt", "yuv420p", str(clip),
    ]
    subprocess.run(cmd, check=True)

    cuts: list[float] = []
    t = 0.0
    for dur in lengths[:-1]:
        t += dur
        cuts.append(round(t, 2))
    duration = round(sum(lengths), 2)
    truth.write_text(
        json.dumps(
            {
                "schema": TRUTH_SCHEMA,
                "video": clip.name,
                "duration_seconds": duration,
                "cuts": cuts,
                "shot_count": n,
            },
            indent=2,
        )
    )
    return clip, truth


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="Zielverzeichnis")
    parser.add_argument("--clips", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument(
        "--profile",
        choices=("gleichmaessig", "beschleunigend", "gemischt"),
        default="gemischt",
    )
    parser.add_argument("--dauer", type=float, default=30.0,
                        help="Ziel-Laufzeit je Clip in Sekunden")
    parser.add_argument("--min-shot", type=float, default=0.8)
    parser.add_argument("--max-shot", type=float, default=3.0)
    parser.add_argument("--size", default="640x360")
    parser.add_argument("--fps", type=int, default=25)
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print(
            "ffmpeg nicht gefunden. Auf dem Mac: brew install ffmpeg",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    for i in range(1, args.clips + 1):
        profile = (
            rng.choice(("gleichmaessig", "beschleunigend"))
            if args.profile == "gemischt"
            else args.profile
        )
        lengths = _shot_lengths(
            rng, profile, args.dauer, args.min_shot, args.max_shot
        )
        clip, truth = _build_clip(out_dir, i, lengths, args.size, args.fps)
        print(
            f"{clip.name}: {len(lengths)} Einstellungen, "
            f"{sum(lengths):.1f}s, Profil {profile} -> {truth.name}"
        )
    print(
        f"\nFertig. Kalibrieren: Clips im Annotator (Modus 'Lokale Datei') "
        f"tippen, dann je Clip:\n"
        f"  python scripts/evaluate_annotations.py calibrate "
        f"--annotation IHRE.json --truth {out_dir}/clip_01.wahrheit.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
