"""Transcribe audio with an MT3-family model. Runs in third_party/mt3/.venv.

Kept as its own script because mt3-infer needs torch 2.14 while the main
environment is pinned to 2.1 for Demucs and Basic Pitch -- the same reason the
music models each get their own venv (see pipeline/runner.py).

Unlike the Demucs + Basic Pitch path this reads the *mix*: MT3 is trained to
transcribe several instruments at once, so separating first would only feed it
artifacts.

    third_party/mt3/.venv/Scripts/python.exe scripts/mt3_transcribe.py \
        <audio.wav> <out.mid> [model]
"""
import os
import sys
from pathlib import Path

# Checkpoints are project data, so they belong on the project drive rather than
# in a home-directory cache. Set before importing mt3_infer, which reads it.
os.environ.setdefault(
    "MT3_CHECKPOINT_DIR",
    str(Path(__file__).resolve().parent.parent / "third_party" / "mt3" / "checkpoints"),
)

import librosa  # noqa: E402
import mt3_infer  # noqa: E402

MODEL_SR = 16000


def main() -> None:
    audio_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    model = sys.argv[3] if len(sys.argv) > 3 else "mr_mt3"

    y, _ = librosa.load(str(audio_path), sr=MODEL_SR, mono=True)
    midi = mt3_infer.transcribe(y, model=model, sr=MODEL_SR, device="cpu")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(str(out_path))

    tracks = len(midi.tracks)
    notes = sum(1 for t in midi.tracks for m in t if m.type == "note_on" and m.velocity > 0)
    print(f"{model}: {notes} notes across {tracks} track(s) -> {out_path}")


if __name__ == "__main__":
    main()
