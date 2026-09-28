"""
jobs.py — in-process job queue with a single background worker thread.

The worker pulls Job items off _queue one at a time, runs the generation
pipeline (which launches the chosen model's own worker process -- see
pipeline/runner.py), moves the output WAV into storage/converted, and writes
the result back to the database.  Exceptions never crash the worker loop.
"""

import json
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.config import DIR_CONVERTED, DIR_MIDI
from app.database import drop_cancelled, get_connection

log = logging.getLogger(__name__)

# Add project root to sys.path so `from pipeline.generate_song import ...` works.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

def _score_song(wav_path: Path, prompt: str | None) -> tuple[float | None, str | None]:
    """Score a finished song for quality via the isolated .venv-fad environment.

    Returns (score, verdict) -- both None if scoring failed for any reason.
    Must never raise: a scoring failure must never fail the generation job.
    """
    venv_python = _PROJECT_ROOT / ".venv-fad" / "Scripts" / "python.exe"
    script = _PROJECT_ROOT / "fad_score_one.py"
    try:
        result = subprocess.run(
            [str(venv_python), str(script), str(wav_path), "--prompt", prompt or ""],
            capture_output=True, text=True, timeout=90,
        )
        data = json.loads(result.stdout.strip().splitlines()[-1])
        if "error" in data:
            log.warning("[jobs] FAD scoring failed for %s: %s", wav_path.name, data["error"])
            return None, None
        return data["score"], data["verdict"]
    except Exception:
        log.exception("[jobs] FAD scoring crashed for %s", wav_path.name)
        return None, None


_queue: queue.Queue = queue.Queue()


def _is_cancelled(file_id: str) -> bool:
    """True if the job was cancelled or its row deleted (e.g. discarded).
    Called from the runner's watchdog thread, so it opens its own connection
    -- SQLite connections must stay on the thread that created them."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT job_status FROM files WHERE id=?", (file_id,)).fetchone()
        return row is None or row["job_status"] == "cancelled"
    finally:
        conn.close()


@dataclass
class Job:
    file_id: str
    input_type: Literal["image", "text", "midi", "audio"]
    source: str      # absolute image/wav path (image/midi/audio jobs) or text prompt (text jobs)
    duration: int
    prompt: str | None = None          # pre-computed prompt; if set for image jobs, skips Gemini
    model: str = ""                    # model id from pipeline/models.json (unused by midi jobs)
    reference_mode: str | None = None  # audio jobs: how `source` conditions the model ("cover" | "style" ...)
    options: dict = field(default_factory=dict)  # per-model options, already validated against the manifest
    source_file_id: str | None = None  # for MIDI/audio jobs: the entry this was derived from
    lyrics: str | None = None          # sung when options["vocals"] == "lyrics"; None = drafted


def enqueue(job: Job) -> None:
    """Push a job onto the queue.  Returns immediately."""
    _queue.put(job)
    log.info("[queue] job %s enqueued (%s, %ds) — queue depth now %d",
             job.file_id, job.input_type, job.duration, _queue.qsize())


def get_queue_depth() -> int:
    """Return the number of jobs currently waiting in the queue (not counting the one in flight)."""
    return _queue.qsize()


# Tracks whether the worker is actively processing a job right now. Separate
# from _queue.qsize(), which only counts jobs still WAITING -- it excludes
# the one job the worker has already dequeued and is running, so qsize()
# alone can read 0 while a generation is still in flight.
#
# This backs a simple "one generation at a time" rule for /generate: with
# only a single shared login for the whole app (see auth), two people using
# a shared tunnel link at the same time previously had no coordination at
# all -- both requests would just enqueue, and if they targeted the same
# unsaved image, the second one to finish would silently overwrite the
# first's result in the database. Rejecting the second request outright
# (see routers/generate.py) is simpler and more honest than letting it
# queue silently.
_active_lock = threading.Lock()
_active = False
_active_job_id: "str | None" = None  # which job the worker is running, for the admin's "stop it"


def is_active() -> bool:
    """True if the worker is currently processing a job (not just queued)."""
    with _active_lock:
        return _active


def _set_active(value: bool, job_id: "str | None" = None) -> None:
    global _active, _active_job_id
    with _active_lock:
        _active = value
        _active_job_id = job_id if value else None


def active_job_id() -> "str | None":
    """The file id of the job the worker is running right now, if any."""
    with _active_lock:
        return _active_job_id


def has_work_in_progress() -> bool:
    """True if a generation is running OR waiting behind one that is."""
    return is_active() or get_queue_depth() > 0


# In-memory generation-progress store — no DB writes, since a model's
# progress callback can fire many times a second and this is purely
# ephemeral, poll-friendly state (never needed after a job finishes).
_progress_lock = threading.Lock()
_progress: dict[str, float] = {}
# What the job is doing right now, in words ("Separating instruments"), for
# jobs whose fraction alone would leave the user guessing -- MIDI conversion
# runs several quite different steps. Absent for plain generation.
_stage: dict[str, str] = {}


def set_progress(file_id: str, fraction: float, stage: "str | None" = None) -> None:
    with _progress_lock:
        _progress[file_id] = max(0.0, min(1.0, fraction))
        if stage is not None:
            _stage[file_id] = stage


def get_progress(file_id: str) -> "float | None":
    with _progress_lock:
        return _progress.get(file_id)


def get_stage(file_id: str) -> "str | None":
    with _progress_lock:
        return _stage.get(file_id)


def clear_progress(file_id: str) -> None:
    with _progress_lock:
        _progress.pop(file_id, None)
        _stage.pop(file_id, None)


def _run_worker() -> None:
    """
    Runs forever in a daemon thread.  Pulls one Job at a time, processes
    it, updates the DB.  Any exception marks the job 'failed' and continues
    to the next job.

    Status transitions (normal):
      queued → processing → loading_model → processing → done
      (the first 'processing' covers the Gemini image->prompt step; the
       model's worker process then reports loading_model and processing)

    Cancel transitions:
      queued     → cancelled  (worker skips immediately on dequeue)
      processing → cancelled  (the runner kills the model's worker process
                               within ~1s; the DB row is then discarded)
    """
    from pipeline.generate_song import generate_song
    from pipeline.models import ModelError, get_model
    from pipeline.runner import WorkerCancelled

    log.info("[worker] Worker thread started.")

    while True:
        log.info("[worker] Waiting for next job (queue depth=%d)...", _queue.qsize())
        job: Job = _queue.get()
        log.info("[worker] Picked up job %s (%s, %ds) — queue depth now %d",
                 job.file_id, job.input_type, job.duration, _queue.qsize())
        conn = get_connection()
        _set_active(True, job.file_id)

        try:
            # --- Pre-start cancellation check ---
            # /cancel may have set job_status='cancelled' while the job sat in the queue.
            pre = conn.execute(
                "SELECT job_status FROM files WHERE id=?", (job.file_id,)
            ).fetchone()
            if pre is None or pre["job_status"] == "cancelled":
                log.info("[jobs] Job %s cancelled before start — skipping", job.file_id)
                continue  # finally still runs: conn.close() + task_done()

            # --- MIDI conversion (Basic Pitch) — no generation model needed ---
            if job.input_type == "midi":
                conn.execute(
                    "UPDATE files SET job_status='processing' WHERE id=?", (job.file_id,)
                )
                conn.commit()
                from pipeline.midi_convert import convert_to_midi as _midi_convert
                midi_dest = DIR_MIDI / f"{job.file_id}.mid"
                set_progress(job.file_id, 0.0, "Starting")
                note_count = _midi_convert(
                    Path(job.source), midi_dest,
                    on_progress=lambda frac, stage: set_progress(job.file_id, frac, stage),
                )
                conn.execute(
                    "UPDATE files SET job_status='done', converted_key=? WHERE id=?",
                    (midi_dest.name, job.file_id),
                )
                conn.commit()
                log.info("[jobs] MIDI job %s done → %s (%d notes)",
                         job.file_id, midi_dest.name, note_count)
                continue  # finally executes (conn.close + task_done), then next job

            conn.execute("UPDATE files SET job_status='processing' WHERE id=?", (job.file_id,))
            conn.commit()

            # The model's worker process reports loading_model, then
            # processing, as it starts up -- mirror that into the DB so the
            # frontend can say "warming up" instead of looking frozen.
            def _on_status(state: str) -> None:
                try:
                    conn.execute("UPDATE files SET job_status=? WHERE id=?", (state, job.file_id))
                    conn.commit()
                except Exception:
                    log.exception("[jobs] Could not set status %r for %s", state, job.file_id)

            def _on_progress(frac: float) -> None:
                set_progress(job.file_id, frac)

            log.info("[worker] job %s — generating (type=%s, duration=%ds, model=%s)",
                     job.file_id, job.input_type, job.duration, job.model)

            # What the job's `source` means depends on its type: an image to
            # describe, a reference song to condition on, or the prompt itself.
            gen_kwargs: dict = {}
            if job.input_type == "image":
                gen_kwargs = {"image_path": job.source, "prompt": job.prompt}
            elif job.input_type == "audio":
                gen_kwargs = {"prompt": job.prompt, "reference_path": job.source,
                              "reference_mode": job.reference_mode}
            else:
                gen_kwargs = {"prompt": job.source}

            try:
                wav_path, prompt_used = generate_song(
                    model_id=job.model, duration=job.duration, options=job.options,
                    lyrics=job.lyrics,
                    on_status=_on_status, on_progress=_on_progress,
                    cancel_check=lambda: _is_cancelled(job.file_id),
                    **gen_kwargs,
                )
            except WorkerCancelled:
                # The runner already killed the model process. Same cleanup as
                # a cancel that lands after generation finishes (below).
                drop_cancelled(conn, job.file_id)
                log.info("[jobs] Job %s cancelled mid-flight — worker killed", job.file_id)
                continue
            except ModelError as e:
                log.error("[jobs] Job %s rejected: %s", job.file_id, e)
                raise

            log.info("[worker] job %s — generation returned: wav=%s", job.file_id, wav_path.name)

            # --- Post-generation cancellation check ---
            # /cancel may have landed in the instant between the last watchdog
            # poll and the worker finishing. Discard the WAV and the DB row.
            post = conn.execute(
                "SELECT job_status FROM files WHERE id=?", (job.file_id,)
            ).fetchone()
            if post is None or post["job_status"] == "cancelled":
                wav_path.unlink(missing_ok=True)
                if post is not None:
                    drop_cancelled(conn, job.file_id)
                log.info("[jobs] Job %s cancelled mid-flight — output discarded", job.file_id)
                continue  # finally still runs

            # Move the WAV from pipeline/output/ into storage/converted/{file_id}.wav
            dest = DIR_CONVERTED / f"{job.file_id}.wav"
            shutil.move(str(wav_path), str(dest))
            converted_key = dest.name

            fad_score, fad_verdict = (
                _score_song(dest, prompt_used) if get_model(job.model).quality_scoring else (None, None)
            )

            conn.execute(
                """UPDATE files
                      SET job_status='done', converted_key=?, prompt=?, duration=?,
                          fad_score=?, fad_verdict=?, model_id=?
                    WHERE id=?""",
                (converted_key, prompt_used, float(job.duration), fad_score, fad_verdict,
                 job.model, job.file_id),
            )
            conn.commit()
            log.info("[jobs] Job %s done -> %s (fad_verdict=%s)", job.file_id, converted_key, fad_verdict)

        except Exception:
            log.exception("[worker] job %s FAILED — exception caught, worker will continue", job.file_id)
            try:
                conn.execute(
                    "UPDATE files SET job_status='failed' WHERE id=?",
                    (job.file_id,),
                )
                conn.commit()
            except Exception:
                log.exception("[jobs] Could not mark job %s failed in DB", job.file_id)
        finally:
            _set_active(False)
            clear_progress(job.file_id)
            conn.close()
            _queue.task_done()
            log.info("[worker] job %s task_done — returning to queue.get()", job.file_id)


def _run_heartbeat() -> None:
    """
    Daemon thread: log a liveness line every 30s.

    If the backend process is running but /health stops responding, heartbeats
    reveal whether the process is frozen (heartbeats stop) or just the request
    path is (heartbeats continue).
    """
    while True:
        time.sleep(30)
        try:
            log.info("[heartbeat] backend alive, generating=%s, queue_depth=%d",
                     is_active(), get_queue_depth())
        except Exception:
            log.exception("[heartbeat] Unexpected error")


def start_worker() -> None:
    """Spawn the single background worker thread.  Call once at startup."""
    t = threading.Thread(target=_run_worker, daemon=True, name="job-worker")
    t.start()
    log.info("[jobs] Worker thread launched: %s", t.name)


def start_heartbeat() -> None:
    """Spawn the background heartbeat thread.  Call once at startup."""
    t = threading.Thread(target=_run_heartbeat, daemon=True, name="heartbeat")
    t.start()
    log.info("[heartbeat] Thread launched: %s", t.name)
