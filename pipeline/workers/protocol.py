"""
Worker protocol shared by every model worker (see pipeline/runner.py).

A worker is a standalone script run inside its OWN virtualenv -- that isolation
is what lets models with conflicting dependencies (different torch, different
transformers, ...) coexist. This module is deliberately stdlib-only so a worker
can import it from any venv.

Contract
--------
  argv        --request <path-to-request.json>
  request     {"model_id", "args", "prompt", "duration", "output_path",
               "reference": {"path", "mode"} | null, "options": {...}}
  stdout      any number of ordinary lines (library noise is fine) plus
              event lines:  @@EVENT {"event": ..., ...}
                {"event": "status",   "state": "loading_model" | "processing"}
                {"event": "progress", "fraction": 0.0-1.0}
                {"event": "result",   "path": ..., "sample_rate": int, "meta": {...}}
                {"event": "error",    "message": ...}
  exit code   0 on success (after a result event), non-zero otherwise

The worker writes its final audio to request["output_path"] as a WAV file.
"""

import json
import sys

EVENT_PREFIX = "@@EVENT "


def emit(event: str, **fields) -> None:
    sys.stdout.write(EVENT_PREFIX + json.dumps({"event": event, **fields}) + "\n")
    sys.stdout.flush()


def emit_status(state: str) -> None:
    emit("status", state=state)


def emit_progress(fraction: float) -> None:
    emit("progress", fraction=max(0.0, min(1.0, float(fraction))))


def load_request() -> dict:
    if len(sys.argv) != 3 or sys.argv[1] != "--request":
        raise SystemExit("usage: <worker> --request <request.json>")
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        return json.load(f)
