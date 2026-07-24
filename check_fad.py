"""Standalone FAD (Frechet Audio Distance) checker for generated songs.

Must be run with the ISOLATED .venv-fad interpreter, never the main .venv --
fadtk requires torch>=2.3, which conflicts with the main app's pinned
torch==2.1.0 (needed by audiocraft/xformers). The two can never share one
environment.

Usage (from repo root):
    .venv-fad\\Scripts\\python.exe check_fad.py pipeline/output/Minecraft.wav
    .venv-fad\\Scripts\\python.exe check_fad.py pipeline/output/*.wav

    # With a genre verdict (satisfactory/unsatisfactory against a bucket ceiling):
    .venv-fad\\Scripts\\python.exe check_fad.py song.wav --prompt "187 BPM psytrance"
    .venv-fad\\Scripts\\python.exe check_fad.py song.wav --genre ambient

Files can come from anywhere; each is hardlinked into a local .fad_eval/
staging folder (created next to this script) where embeddings get cached,
so re-checking a song you've already scored is fast on later runs.

Lower score = closer to the FMA-Pop reference distribution. This is a
STYLE-DISTANCE metric, not an absolute quality score -- a song that's far
from pop stylistically (chiptune, ambient, sparse melody) can score high
without actually sounding bad. That's why the verdict below is genre-bucketed
rather than a single global cutoff.

GENRE BUCKETS -- first pass, calibrated from a handful of hand-labeled songs
(see project conversation history). Expect to retune ceilings as more songs
get labeled; these are a starting point, not settled numbers:
    dense     (EDM, rock, rhythm-game, psytrance, techno, ...)  ceiling ~150
    jazz      (multi-instrument: piano, drums, clapping, funk, ...) ceiling ~170
    ambient   (sparse, "absence of information": drone, koto, calm, ...) ceiling ~300
    unclassified (prompt didn't match any bucket keyword) ceiling ~200, flagged
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

import soundfile as sf
import torch
import torchaudio

# This venv's torchaudio (2.11+) routes torchaudio.load/save through
# torchcodec, which needs real FFmpeg shared libraries not installed on this
# machine. Our files are plain WAV, so bypass that dispatcher entirely via
# soundfile. Must stay at module level (not inside main()) so Windows'
# spawned worker processes -- which re-run this file's top-level code under
# runpy but skip the __main__ guard -- pick up the patch too.
def _soundfile_load(path, *args, **kwargs):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(data.T).contiguous(), sr  # (channels, frames)


_SUBTYPE_BY_BITS = {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}


def _soundfile_save(path, src, sample_rate, encoding=None, bits_per_sample=None, **kwargs):
    data = src.detach().cpu().numpy().T  # (channels, frames) -> (frames, channels)
    sf.write(str(path), data, int(sample_rate), subtype=_SUBTYPE_BY_BITS.get(bits_per_sample, "PCM_16"))


torchaudio.load = _soundfile_load
torchaudio.save = _soundfile_save

from fadtk.model_loader import EncodecEmbModel
from fadtk.fad import FrechetAudioDistance
from fadtk.fad_batch import cache_embedding_files


# fadtk globs its target directory for "*.*" with no audio-extension filter,
# so it must never point directly at a folder that can contain non-audio
# files (pipeline/output/ has a .gitkeep, which matches that glob and blows
# up trying to decode it). Stage requested files into a dedicated folder via
# hardlinks instead -- free (same drive, no copy) and keeps embeddings cached
# across runs for songs you check more than once.
CACHE_DIR = Path(__file__).resolve().parent / ".fad_eval"

from fad_common import GENRE_BUCKETS, DEFAULT_CEILING, classify_genre


def _stage(target: Path) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    link = CACHE_DIR / target.name
    if link.exists():
        return
    try:
        os.link(target, link)
    except OSError:
        shutil.copy2(target, link)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="Audio file(s) to score")
    parser.add_argument("--prompt", help="Prompt text used to auto-classify the genre bucket (applies to all files given)")
    parser.add_argument("--genre", choices=list(GENRE_BUCKETS) + ["unclassified"], help="Force a genre bucket instead of classifying from --prompt")
    args = parser.parse_args()

    targets = [Path(p).resolve() for p in args.files]
    for t in targets:
        if not t.exists():
            print(f"File not found: {t}")
            sys.exit(1)
        _stage(t)

    bucket = ceiling = None
    if args.genre:
        bucket = args.genre
        ceiling = GENRE_BUCKETS.get(args.genre, (DEFAULT_CEILING, []))[0]
    elif args.prompt:
        bucket, ceiling = classify_genre(args.prompt)

    eval_dir = CACHE_DIR

    model = EncodecEmbModel("24k")
    print(f"Caching embeddings in {eval_dir} using {model.name} (only new/uncached files are computed)...")
    cache_embedding_files(eval_dir, model, workers=1)

    fad = FrechetAudioDistance(model, audio_load_worker=1, load_model=False)
    # Must live OUTSIDE eval_dir -- fadtk's own glob has no audio-extension
    # filter, so a leftover CSV from a prior run inside eval_dir gets picked
    # up as a "file to score" on the next run and blows up trying to decode it.
    csv_out = Path(__file__).resolve().parent / "fad_scores.csv"
    csv_out.unlink(missing_ok=True)
    fad.score_individual("fma_pop", eval_dir, csv_out)

    scores = {}
    for line in csv_out.read_text().splitlines():
        if not line.strip():
            continue
        path_str, score_str = line.rsplit(",", 1)
        scores[Path(path_str).name] = float(score_str)

    print("\nFAD scores (lower = closer to FMA-Pop reference; style-distance, not a quality verdict on its own):")
    for t in targets:
        score = scores.get(t.name)
        if score is None:
            print(f"  {t.name}: (no score computed -- check for errors above)")
            continue
        if bucket is None:
            print(f"  {t.name}: {score:.2f}")
        else:
            verdict = "SATISFACTORY" if score <= ceiling else "UNSATISFACTORY"
            flag = "  [bucket guessed, no clear genre keyword match]" if bucket == "unclassified" else ""
            print(f"  {t.name}: {score:.2f}  -- {verdict} (bucket={bucket}, ceiling={ceiling}){flag}")


if __name__ == "__main__":
    main()
