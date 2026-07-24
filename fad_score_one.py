"""Score a single generated song for the backend, printing JSON to stdout.

Must be run with the ISOLATED .venv-fad interpreter -- see check_fad.py's
module docstring for why fadtk can never share an environment with the main
app. Not meant to be run by hand for spot-checking -- use check_fad.py for
that; this script is invoked by backend/app/jobs.py after each generation.

Usage:
    .venv-fad\\Scripts\\python.exe fad_score_one.py <wav_path> --prompt "<prompt text>"

Prints exactly one JSON line to stdout and sets the exit code accordingly:
    {"score": 103.49, "bucket": "jazz", "verdict": "satisfactory"}   (exit 0)
    {"error": "<description>"}                                       (exit 1)
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import soundfile as sf
import torch
import torchaudio

# See check_fad.py's module docstring for why this monkeypatch exists (this
# venv's torchaudio routes load/save through torchcodec, which needs FFmpeg
# shared libraries not installed on this machine). Must stay at module level,
# outside any function -- Windows' spawned multiprocessing workers re-run
# this file's top-level code but skip the __main__ guard below, so a patch
# placed inside main() would silently not apply in worker processes.
def _soundfile_load(path, *args, **kwargs):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(data.T).contiguous(), sr


_SUBTYPE_BY_BITS = {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}


def _soundfile_save(path, src, sample_rate, encoding=None, bits_per_sample=None, **kwargs):
    data = src.detach().cpu().numpy().T
    sf.write(str(path), data, int(sample_rate), subtype=_SUBTYPE_BY_BITS.get(bits_per_sample, "PCM_16"))


torchaudio.load = _soundfile_load
torchaudio.save = _soundfile_save

from fadtk.model_loader import EncodecEmbModel
from fadtk.fad import FrechetAudioDistance
from fadtk.fad_batch import cache_embedding_files

from fad_common import classify_genre


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav_path")
    parser.add_argument("--prompt", default="")
    args = parser.parse_args()

    wav_path = Path(args.wav_path).resolve()
    if not wav_path.is_file():
        print(json.dumps({"error": f"file not found: {wav_path}"}))
        return 1

    bucket, ceiling = classify_genre(args.prompt)

    try:
        with tempfile.TemporaryDirectory(prefix="fad_score_one_") as tmp:
            eval_dir = Path(tmp)
            staged = eval_dir / wav_path.name
            try:
                os.link(wav_path, staged)
            except OSError:
                shutil.copy2(wav_path, staged)

            model = EncodecEmbModel("24k")
            cache_embedding_files(eval_dir, model, workers=1)

            fad = FrechetAudioDistance(model, audio_load_worker=1, load_model=False)
            csv_out = eval_dir / "scores.csv"
            fad.score_individual("fma_pop", eval_dir, csv_out)

            score = None
            for line in csv_out.read_text().splitlines():
                if not line.strip():
                    continue
                path_str, score_str = line.rsplit(",", 1)
                if Path(path_str).name == staged.name:
                    score = float(score_str)
                    break

            if score is None:
                print(json.dumps({"error": "no score produced"}))
                return 1

            verdict = "satisfactory" if score <= ceiling else "unsatisfactory"
            print(json.dumps({"score": score, "bucket": bucket, "verdict": verdict}))
            return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
