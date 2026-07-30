"""
prefetch_melody.py — One-time pre-download of facebook/musicgen-melody.
Run once while on WiFi. Does NOT change the pipeline, UI, or generation logic.
Melody is cached for future use only.
"""

import gc
import os
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import torch

print("=" * 60)
print("PRE-FETCHING: facebook/musicgen-melody")
print("~3 GB download — do NOT interrupt.")
print("=" * 60)

if torch.cuda.is_available():
    gpu = torch.cuda.get_device_name(0)
    total_vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    print(f"\n[device] GPU : {gpu}")
    print(f"[device] VRAM: {total_vram:.1f} GB total")
else:
    print("\n[device] CPU only — CUDA not available")

from audiocraft.models import MusicGen

print("\n[download] Calling MusicGen.get_pretrained('facebook/musicgen-melody') ...")
print("[download] First run downloads ~3 GB — this may take several minutes ...")
t0 = time.perf_counter()
model = MusicGen.get_pretrained("facebook/musicgen-melody")
elapsed = time.perf_counter() - t0
print(f"[download] Complete — {elapsed:.1f}s")

# VRAM after load
if torch.cuda.is_available():
    vram_alloc = torch.cuda.memory_allocated() / 1024 ** 3
    vram_res   = torch.cuda.memory_reserved() / 1024 ** 3
    device     = next(model.lm.parameters()).device
    print(f"\n[vram] Device      : {device}")
    print(f"[vram] Allocated   : {vram_alloc:.2f} GB")
    print(f"[vram] Reserved    : {vram_res:.2f} GB")

# Smoke test — 5-second text-only generation (no melody input needed)
print("\n[test] Generating 5s text-only clip to confirm the model functions ...")
model.set_generation_params(duration=5)
t1 = time.perf_counter()
wav = model.generate(["calm ambient piano"])
gen_elapsed = time.perf_counter() - t1
print(f"[test] OK — output shape={tuple(wav.shape)}  took {gen_elapsed:.1f}s")

if torch.cuda.is_available():
    vram_after = torch.cuda.memory_allocated() / 1024 ** 3
    print(f"[vram] After generation: {vram_after:.2f} GB allocated")

# HuggingFace cache report
hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
hub_dir = hf_home / "hub"
print(f"\n[cache] HuggingFace cache : {hf_home}")

if hub_dir.exists():
    entries = sorted(hub_dir.iterdir())
    total_bytes = 0
    for d in entries:
        if d.is_dir():
            sz = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            total_bytes += sz
            print(f"[cache]   {d.name:<50s}  {sz / 1024**3:.2f} GB")
    print(f"[cache] Total: {total_bytes / 1024**3:.2f} GB across {len(entries)} model(s)")
else:
    print("[cache] hub/ subdirectory not found — check HF_HOME env var")

# Free VRAM
del model
gc.collect()
if torch.cuda.is_available():
    torch.cuda.empty_cache()
    print(f"\n[cleanup] VRAM after unload: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

print("\n[done] facebook/musicgen-melody is cached and ready for future use.")
print("[done] App pipeline is unchanged — melody is cached only, not integrated.")
