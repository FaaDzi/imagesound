"""
Runs one generation inside a model's own worker process.

Model-agnostic: it only speaks the protocol in pipeline/workers/protocol.py.
One process per generation means the model's VRAM is returned to the OS the
moment the song is done (no allocator fragmentation, no leaks across jobs),
and a cancel or timeout is just killing the process tree -- unlike an
in-process model, which can't be interrupted mid-generation.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from pipeline.models import PROJECT_ROOT, ModelSpec
from pipeline.workers.protocol import EVENT_PREFIX

log = logging.getLogger(__name__)

_PROGRESS_BAR = re.compile(r"\d+%\|")   # tqdm noise -- not worth a log line each
_TAIL_LINES = 40                        # kept for the error message if the worker dies


class WorkerError(RuntimeError):
    """The worker failed, crashed, or timed out."""


class WorkerCancelled(WorkerError):
    """The caller's cancel_check() asked us to stop."""


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.kill()


def run_worker(
    spec: ModelSpec,
    request: dict,
    work_dir: Path,
    on_status=None,
    on_progress=None,
    cancel_check=None,
) -> dict:
    """Run `spec`'s worker for one request and return its result event.

    on_status(state: str) / on_progress(fraction: float) are called from the
    calling thread as events arrive. cancel_check() -> bool is polled about
    once a second from a watchdog thread; when it returns True the worker is
    killed and WorkerCancelled is raised.
    """
    w = spec.worker
    python = spec.resolve(w["python"])
    script = spec.resolve(w["script"])
    cwd = spec.resolve(w["cwd"]) if w.get("cwd") else PROJECT_ROOT

    work_dir.mkdir(parents=True, exist_ok=True)
    # The worker's scratch space lives inside the project (never the system
    # drive) and is deleted here once the worker is gone. A cancelled or timed
    # out worker is killed outright, so it can't be trusted to clean up itself.
    scratch = Path(tempfile.mkdtemp(dir=work_dir, prefix="scratch_"))

    env = os.environ.copy()
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                "TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch)})
    # Manifest env values are project-relative paths (weights dir, HF cache).
    for key, rel in w.get("env", {}).items():
        env[key] = str(spec.resolve(rel))

    payload = {"model_id": spec.id, "args": w.get("args", {}), **request}
    fd, req_path = tempfile.mkstemp(dir=work_dir, prefix="req_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    proc = None
    stop_reason: list[str] = []          # filled by the watchdog: "cancelled" | "timeout"
    done = threading.Event()
    tail: deque = deque(maxlen=_TAIL_LINES)
    result: "dict | None" = None
    error_msg: "str | None" = None

    try:
        creationflags = getattr(subprocess, "HIGH_PRIORITY_CLASS", 0) if sys.platform == "win32" else 0
        log.info("[runner] %s: launching worker", spec.id)
        proc = subprocess.Popen(
            [str(python), str(script), "--request", req_path],
            cwd=str(cwd), env=env, creationflags=creationflags,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )

        def _watchdog() -> None:
            deadline = time.monotonic() + float(w.get("timeout_sec", 1800))
            while not done.wait(1.0):
                if time.monotonic() > deadline:
                    stop_reason.append("timeout")
                elif cancel_check is not None:
                    try:
                        if cancel_check():
                            stop_reason.append("cancelled")
                    except Exception:
                        log.exception("[runner] cancel_check raised -- ignoring")
                if stop_reason:
                    _kill_tree(proc)
                    return

        threading.Thread(target=_watchdog, daemon=True, name=f"runner-watchdog-{spec.id}").start()

        for line in proc.stdout:
            line = line.rstrip()
            if not line.startswith(EVENT_PREFIX):
                if line and not _PROGRESS_BAR.search(line):
                    tail.append(line)
                    # Model libraries are chatty; surface only what needs attention.
                    # Full output is one `logging.DEBUG` away.
                    noteworthy = " | WARNING " in line or " | ERROR " in line or "Traceback" in line
                    log.log(logging.WARNING if noteworthy else logging.DEBUG, "[%s] %s", spec.id, line)
                continue
            try:
                ev = json.loads(line[len(EVENT_PREFIX):])
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            if kind == "status" and on_status:
                on_status(ev["state"])
            elif kind == "progress" and on_progress:
                on_progress(float(ev["fraction"]))
            elif kind == "result":
                result = ev
            elif kind == "error":
                error_msg = ev.get("message")

        proc.wait()
    finally:
        done.set()
        if proc is not None:
            _kill_tree(proc)
        try:
            os.unlink(req_path)
        except OSError:
            pass
        shutil.rmtree(scratch, ignore_errors=True)

    if stop_reason and stop_reason[0] == "cancelled":
        raise WorkerCancelled(f"{spec.id} generation cancelled.")
    if stop_reason and stop_reason[0] == "timeout":
        raise WorkerError(f"{spec.id} timed out after {w.get('timeout_sec', 1800)}s.")
    if proc.returncode != 0 or result is None:
        detail = error_msg or (tail[-1] if tail else "no output")
        raise WorkerError(f"{spec.id} worker failed (exit {proc.returncode}): {detail}")
    return result
