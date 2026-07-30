"""
Standalone generation diagnostic — runs Tests 1, 2, and 3 directly
against the pipeline without FastAPI or the browser.

Usage:
    python run_gen_tests.py [1|2|3]   # run only that test
    python run_gen_tests.py            # run all three in order

Each test prints step-by-step timing.  If a test hangs the output
shows exactly which step went silent.
"""

import sys
import time
from pathlib import Path

# Make sure the project root is on the path so pipeline/ is importable.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline.generate_song import generate_song_from_text

PROMPT = "soft piano melody, gentle strings, calm and introspective, 70 bpm"


def _separator(label: str) -> None:
    print(f"\n{'=' * 64}", flush=True)
    print(f"  {label}", flush=True)
    print(f"{'=' * 64}", flush=True)


def test1():
    """TEST 1 — Plain short song (8s, no arc, no chunking)."""
    _separator("TEST 1 — 8s plain text song (no arc)")
    t = time.perf_counter()
    wav = generate_song_from_text(PROMPT, duration=8, model="medium")
    elapsed = time.perf_counter() - t
    _separator(f"TEST 1 PASSED in {elapsed:.1f}s  ->  {wav.name}")
    return True


def test2():
    """TEST 2 — Long song, steady arc (tests chunking without custom arc)."""
    _separator("TEST 2 — 90s song, steady arc (no custom segments)")
    t = time.perf_counter()
    wav = generate_song_from_text(
        PROMPT, duration=90, model="medium", arc_preset="steady"
    )
    elapsed = time.perf_counter() - t
    _separator(f"TEST 2 PASSED in {elapsed:.1f}s  ->  {wav.name}")
    return True


def test3():
    """TEST 3 — 90s song, 3-segment CUSTOM arc (reproduces the hang)."""
    _separator("TEST 3 — 90s song, custom 3-segment arc [50, 70, 90]")
    t = time.perf_counter()
    wav = generate_song_from_text(
        PROMPT,
        duration=90,
        model="medium",
        arc_segments=[50, 70, 90],
    )
    elapsed = time.perf_counter() - t
    _separator(f"TEST 3 PASSED in {elapsed:.1f}s  ->  {wav.name}")
    return True


TESTS = [test1, test2, test3]

if __name__ == "__main__":
    if len(sys.argv) > 1:
        idx = int(sys.argv[1]) - 1
        TESTS[idx]()
    else:
        for fn in TESTS:
            try:
                fn()
            except Exception as exc:
                print(f"\n  !! TEST FAILED: {exc}", flush=True)
                sys.exit(1)
    print("\nAll requested tests completed.", flush=True)
