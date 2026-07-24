"""
test_arc.py — Step 2 experiment: per-chunk prompt steering.

Generates a ~2-minute song (4 × 30s chunks) where each chunk is steered by a
distinct prompt while musically continuing from the previous chunk's audio.

This is purely an experiment to LISTEN to.  It decides whether per-chunk arc
presets are worth building in Step 3.  Not a user-facing feature.

Run:
    python pipeline/test_arc.py

Output WAV is saved to pipeline/output/arc_<id>.wav

What to listen for:
  - Does the song audibly progress calm → building → energetic → climax?
  - Do the seams (~30s, ~60s, ~90s) flow or feel jarring?
  - Compare to a single-prompt 120s gen: more coherent or less?
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.generate_song import generate_song_arc

_ARC_PROMPTS = [
    "upbeat danceable pop, bright synths, punchy drum machine groove, warm bass, lively and full",
    "energetic pop groove, fuller drums, added rhythmic synths and claps, building momentum, same bright instrumentation",
    "high-energy danceable pop, driving beat, layered synths and brass stabs, lively Latin percussion, full and rich",
    "peak energetic dance-pop climax, full driving groove, brass and percussion, powerful and danceable, warm and full",
]

if __name__ == "__main__":
    print("\n=== Step 2b: Substance-First Pop/Latin Arc Test ===")
    print(f"  4 chunks × 30s = 120s target")
    print(f"  Arc: full groove -> fuller -> brass/percussion -> climax\n")
    print(f"  Theory: starting with substance (drums, bass, groove) prevents")
    print(f"  later chunks from inventing shrill tones to fill emptiness.\n")

    t0 = time.perf_counter()
    wav = generate_song_arc(_ARC_PROMPTS, duration=120, model="medium")
    elapsed = time.perf_counter() - t0

    print(f"\n  Output : {wav}")
    print(f"  Wall   : {elapsed:.1f}s ({elapsed / 60:.1f} min)")
    print(f"\nListen for:")
    print(f"  - Screech/shrill tone in later chunks? (main test — should be absent)")
    print(f"  - Groove getting fuller/more energetic across chunks?")
    print(f"  - Smooth transitions at ~30s, ~60s, ~90s seams?")
    print(f"  - Better or worse than the sparse-start ambient arc?\n")
