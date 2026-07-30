"""
test_autocast.py — A/B comparison: fp32 (autocast OFF) vs fp16 (autocast ON)

Generates the same prompt at two durations (15s short-path + 60s chunked-path)
under each precision mode, then prints a side-by-side timing and VRAM table.
Output WAV files are named clearly so you can compare them by ear.

Test order: fp32 first (cold system = honest fp32 baseline), fp16 second.
Any speedup observed for fp16 despite a slightly warmer system is genuine.

Usage:
    python test_autocast.py
"""

import shutil
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pipeline.generate_song as gs

PROMPT    = "slow calm piano, grainy, old timer"
DURATIONS = [15, 60]
OUT_DIR   = ROOT / "pipeline" / "output"

# ─────────────────────────────────────────────────────────────────────────────


def _vram() -> tuple[float, float]:
    """Returns (allocated_GB, reserved_GB). Both 0 if no CUDA."""
    if not torch.cuda.is_available():
        return 0.0, 0.0
    return (
        torch.cuda.memory_allocated(0) / 1024 ** 3,
        torch.cuda.memory_reserved(0)  / 1024 ** 3,
    )


def run_one(label: str, use_ac: bool, duration: int) -> dict:
    gs._USE_AUTOCAST = use_ac
    precision = "fp16 autocast" if use_ac else "fp32      "

    print(f"\n{'-' * 64}")
    print(f"  {label}  |  precision={precision.strip()}  |  duration={duration}s")
    print(f"{'-' * 64}\n")

    alloc_before, resv_before = _vram()
    t0 = time.perf_counter()

    wav = gs.generate_song_from_text(PROMPT, duration=duration, model="medium")

    elapsed = time.perf_counter() - t0
    alloc_after, resv_after = _vram()

    # Copy to a stable, named path for side-by-side listening
    tag  = "fp16" if use_ac else "fp32"
    dest = OUT_DIR / f"autocast_test_{tag}_{duration}s.wav"
    shutil.copy(wav, dest)

    print(f"\n  --- {label} result ---")
    print(f"     elapsed : {elapsed:.1f}s  ({elapsed / duration:.2f}x real-time)")
    print(f"     VRAM alloc : {alloc_before:.2f} -> {alloc_after:.2f} GB")
    print(f"     VRAM resv  : {resv_before:.2f} -> {resv_after:.2f} GB")
    print(f"     saved   : {dest.name}")

    return {
        "label":        label,
        "precision":    precision.strip(),
        "duration":     duration,
        "elapsed":      elapsed,
        "rate":         elapsed / duration,
        "resv_before":  resv_before,
        "resv_after":   resv_after,
        "dest":         dest,
    }


def print_summary(results: list[dict]) -> None:
    print("\n" + "=" * 72)
    print("  AUTOCAST A/B SUMMARY")
    print("=" * 72)
    print(f"  {'Precision':<16} {'Dur':>4}  {'Elapsed':>8}  {'Rate':>6}  {'VRAM resv (after)':>17}")
    print(f"  {'-'*16} {'-'*4}  {'-'*8}  {'-'*6}  {'-'*17}")
    for r in results:
        print(f"  {r['precision']:<16} {r['duration']:>4}s  "
              f"{r['elapsed']:>7.1f}s  {r['rate']:>5.2f}x  "
              f"{r['resv_after']:>8.2f} GB")
    print()

    for dur in DURATIONS:
        fp32_r = next((r for r in results if r["duration"] == dur and r["precision"] == "fp32"), None)
        fp16_r = next((r for r in results if r["duration"] == dur and r["precision"] == "fp16 autocast"), None)
        if not fp32_r or not fp16_r:
            continue
        speedup    = (fp32_r["elapsed"] - fp16_r["elapsed"]) / fp32_r["elapsed"] * 100
        vram_delta = fp32_r["resv_after"] - fp16_r["resv_after"]
        direction  = "FASTER" if speedup > 0 else "SLOWER"
        print(f"  [{dur}s] fp16 vs fp32: {abs(speedup):.1f}% {direction}  |  "
              f"VRAM delta = {vram_delta:+.2f} GB reserved")
        print(f"         fp32={fp32_r['elapsed']:.1f}s   fp16={fp16_r['elapsed']:.1f}s")

    print()
    print("  Listen to compare quality (open in any audio player):")
    seen = set()
    for r in results:
        if r["dest"].name not in seen:
            seen.add(r["dest"].name)
            print(f"    pipeline/output/{r['dest'].name}")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    if not torch.cuda.is_available():
        print("WARNING: CUDA not available — autocast will be a no-op, test not meaningful.")

    results = []
    for dur in DURATIONS:
        # fp32 first (coldest system → honest fp32 baseline)
        results.append(run_one("fp32", use_ac=False, duration=dur))
        # fp16 second (slightly warmer, so any win is real)
        results.append(run_one("fp16", use_ac=True,  duration=dur))

    # Restore the module default so subsequent imports see the right value
    gs._USE_AUTOCAST = True

    print_summary(results)
