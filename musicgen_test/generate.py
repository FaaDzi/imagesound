"""
MusicGen smoke-test — Phase C
Isolated script: no FastAPI, no database, no backend integration.
Purpose: confirm the model loads and generates audio on this machine's GPU.
"""

import time
from pathlib import Path

# ── 1. Device check (before loading the model, so we bail early on CPU) ──────

import torch

print()
print("=" * 64)
print("  MusicGen Phase-C Test")
print("=" * 64)

if torch.cuda.is_available():
    DEVICE = "cuda"
    GPU_NAME = torch.cuda.get_device_name(0)
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    print(f"  [GPU]  {GPU_NAME}  ({vram_gb:.1f} GB VRAM)")
else:
    DEVICE = "cpu"
    GPU_NAME = None
    print("  [WARN] CUDA not available — running on CPU.")
    print("         Generation will be extremely slow (~10–30× slower).")
    print("         Check your torch install: torch.cuda.is_available() is False.")

print()

# ── 2. Load model (times the download + load separately) ─────────────────────

MODEL_NAME = "facebook/musicgen-medium"

print(f"  Loading {MODEL_NAME} ...")
print("  First run will download ~3 GB of weights — this can take several minutes.")
print("  Subsequent runs use the local Hugging Face cache.")
print()

t_load_start = time.perf_counter()

from audiocraft.models import MusicGen  # noqa: E402 — import after torch device check

model = MusicGen.get_pretrained(MODEL_NAME)
model.set_generation_params(duration=8)  # 8 s — short for a fast first test

t_load_end = time.perf_counter()
LOAD_TIME = t_load_end - t_load_start
print(f"  Model ready in {LOAD_TIME:.1f}s")
print()

# ── 3. Prompts ────────────────────────────────────────────────────────────────

PROMPTS = [
    "upbeat electronic with a driving beat and punchy synth bass",
    "calm ambient piano with soft pads and gentle reverb",
]

# ── 4. Generate ───────────────────────────────────────────────────────────────

print(f"  Generating {len(PROMPTS)} clips × 8 s each ...")
for i, p in enumerate(PROMPTS):
    print(f"    [{i}] {p}")
print()

t_gen_start = time.perf_counter()
wavs = model.generate(PROMPTS)
t_gen_end = time.perf_counter()
GEN_TIME = t_gen_end - t_gen_start

print(f"  Generation done in {GEN_TIME:.1f}s  "
      f"({GEN_TIME / len(PROMPTS):.1f}s per clip, "
      f"real-time factor {GEN_TIME / (8 * len(PROMPTS)):.2f}×)")
print()

# ── 5. Save ───────────────────────────────────────────────────────────────────

from audiocraft.data.audio import audio_write  # noqa: E402

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

sample_rate = model.sample_rate

for i, wav in enumerate(wavs):
    stem = str(OUTPUT_DIR / f"output_{i}")
    # audio_write appends .wav automatically; strategy="loudness" normalises peak level
    audio_write(stem, wav.cpu(), sample_rate, strategy="loudness", loudness_compressor=True)
    print(f"  Saved: output/output_{i}.wav")

# ── 6. Summary (the two things you asked me to flag) ─────────────────────────

print()
print("=" * 64)
print("  RESULTS TO CHECK")
print("-" * 64)
if DEVICE == "cuda":
    print(f"  Device   : GPU  ✓  {GPU_NAME}")
else:
    print("  Device   : CPU  ✗  (torch has no CUDA — see install notes)")
print(f"  Load time: {LOAD_TIME:.1f}s")
print(f"  Gen time : {GEN_TIME:.1f}s for {len(PROMPTS)} clips × 8 s audio")
print(f"             = {GEN_TIME / len(PROMPTS):.1f}s per clip")
print(f"             = {GEN_TIME / (8 * len(PROMPTS)):.2f}× real-time ratio")
print("=" * 64)
print()
