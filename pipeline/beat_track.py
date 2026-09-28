"""
Optional rhythm analysis via Beat This (https://github.com/CPJKU/beat_this).

`analyse()` returns tempo *and* time signature, or None when the optional
.venv-beat environment isn't installed — callers are expected to fall back to
librosa, which gives a tempo but no meter. Nothing here imports torch, so the
backend pays nothing for this module existing.

Why a subprocess: beat_this needs torch >= 2.14, while the main environment
pins torch 2.1.0+cu121 for Demucs. Installing it directly would upgrade torch
underneath the MIDI pipeline, so it lives in its own virtualenv and is spoken
to over stdout, exactly like the music models in pipeline/models.json.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_PYTHON = _ROOT / ".venv-beat" / "Scripts" / "python.exe"
_WORKER = _ROOT / "pipeline" / "workers" / "beat_worker.py"
_TIMEOUT_SEC = 300

# Below this, the bar lengths disagreed enough that the meter is a coin toss;
# 4/4 is the safer assumption than a confidently wrong 5/4.
MIN_METER_AGREEMENT = 0.6


def available() -> bool:
    return _PYTHON.is_file() and _WORKER.is_file()


def analyse(audio_path: Path) -> "dict | None":
    """Return {'bpm', 'beats_per_bar', ...} for `audio_path`, or None.

    Never raises: rhythm analysis is an enhancement, and a conversion that
    works with a librosa tempo is better than one that fails outright.
    """
    if not available():
        log.info("[beat] .venv-beat not installed — falling back to librosa")
        return None

    env = {**os.environ,
           "TORCH_HOME": str(_ROOT / ".torch-cache"),
           "HF_HOME": str(_ROOT / ".hf-cache"),
           "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [str(_PYTHON), str(_WORKER), str(audio_path)],
            cwd=str(_ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, OSError):
        log.exception("[beat] analysis failed to run — falling back")
        return None

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        log.warning("[beat] worker exited %d: %s", proc.returncode,
                    tail[-1] if tail else "no output")
        return None

    # The worker's JSON is the last stdout line; torch and friends print above it.
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("meter_agreement", 0.0) < MIN_METER_AGREEMENT:
            log.info("[beat] meter agreement %.2f too low — assuming 4/4",
                     data["meter_agreement"])
            data["beats_per_bar"] = 4
        log.info("[beat] %.1f BPM, %d/4 (%d beats, %d downbeats, agreement %.2f)",
                 data["bpm"], data["beats_per_bar"], data["beats"],
                 data["downbeats"], data["meter_agreement"])
        return data

    log.warning("[beat] worker produced no JSON — falling back")
    return None


if __name__ == "__main__":  # manual check: python -m pipeline.beat_track <wav>
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(analyse(Path(sys.argv[1])), indent=2))
