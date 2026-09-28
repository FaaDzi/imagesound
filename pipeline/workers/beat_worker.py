"""
Beat / downbeat / meter analysis worker (Beat This, CPJKU).

Runs inside .venv-beat -- NOT the backend's environment. beat_this requires a
much newer torch than the main venv pins, so it gets its own virtualenv for
the same reason each music model does (see pipeline/models.json).

Prints one JSON object on stdout:
    {"bpm": float, "beats_per_bar": int, "beats": int, "downbeats": int,
     "meter_agreement": float}

Deliberately CPU-only: analysing a song takes well under a second, and
keeping it off the GPU means it can run while a generation is in flight.
"""

import argparse
import collections
import json
import sys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--checkpoint", default="final0")
    args = ap.parse_args()

    import numpy as np
    from beat_this.inference import File2Beats

    beats, downbeats = File2Beats(
        checkpoint_path=args.checkpoint, device="cpu", dbn=False
    )(args.audio)

    if len(beats) < 2:
        raise SystemExit("not enough beats detected")

    bpm = 60.0 / float(np.median(np.diff(beats)))

    # The time signature is how many beats sit between one downbeat and the
    # next. Count every bar and take the most common answer; first and last
    # bars are routinely partial, so a bare majority is normal and the
    # agreement figure lets the caller decide whether to trust it.
    per_bar = [
        int(np.sum((beats >= downbeats[i] - 1e-6) & (beats < downbeats[i + 1] - 1e-6)))
        for i in range(len(downbeats) - 1)
    ]
    beats_per_bar, agreement = 4, 0.0
    if per_bar:
        common, count = collections.Counter(per_bar).most_common(1)[0]
        agreement = count / len(per_bar)
        if common in (2, 3, 4, 5, 6, 7):
            beats_per_bar = common

    json.dump({
        "bpm": round(bpm, 2),
        "beats_per_bar": beats_per_bar,
        "beats": int(len(beats)),
        "downbeats": int(len(downbeats)),
        "meter_agreement": round(agreement, 3),
    }, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
