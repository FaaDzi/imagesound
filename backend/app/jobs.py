"""
jobs.py — in-process job queue with a single background worker thread.

The worker pulls Job items off _queue one at a time, runs the Phase E
pipeline, moves the output WAV into storage/converted, and writes the
result back to the database.  Exceptions never crash the worker loop.
"""

import json
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import DIR_CONVERTED, DIR_MIDI
from app.database import get_connection

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


@dataclass
class Job:
    file_id: str
    input_type: Literal["image", "text", "midi", "audio"]
    source: str      # absolute image/wav path (image/midi/audio jobs) or text prompt (text jobs)
    duration: int
    prompt: str | None = None          # pre-computed prompt; if set for image jobs, skips Gemini
    model: str = "medium"              # MusicGen variant: "medium" | "small" | "melody"
    arc_preset: str = "steady"         # named energy arc (overridden by arc_segments when set)
    arc_segments: list[int] | None = None  # custom per-chunk intensities 0-100; takes priority
    source_file_id: str | None = None  # for MIDI/audio jobs: the entry this was derived from
    melody_source: str | None = None   # absolute wav path for melody-conditioned ("audio") jobs
    filter_mode: str = "filtered"      # "raw" | "filtered" — postfilter applied server-side after generation


def enqueue(job: Job) -> None:
    """Push a job onto the queue.  Returns immediately."""
    _queue.put(job)
    log.info("[queue] job %s enqueued (%s, %ds) — queue depth now %d",
             job.file_id, job.input_type, job.duration, _queue.qsize())


def get_queue_depth() -> int:
    """Return the number of jobs currently waiting in the queue (not counting the one in flight)."""
    return _queue.qsize()


# In-memory generation-progress store — no DB writes, since audiocraft's
# per-token callback can fire many times a second and this is purely
# ephemeral, poll-friendly state (never needed after a job finishes).
_progress_lock = threading.Lock()
_progress: dict[str, float] = {}


def set_progress(file_id: str, fraction: float) -> None:
    with _progress_lock:
        _progress[file_id] = fraction


def get_progress(file_id: str) -> "float | None":
    with _progress_lock:
        return _progress.get(file_id)


def clear_progress(file_id: str) -> None:
    with _progress_lock:
        _progress.pop(file_id, None)


def _run_worker() -> None:
    """
    Runs forever in a daemon thread.  Pulls one Job at a time, processes
    it, updates the DB.  Any exception marks the job 'failed' and continues
    to the next job.

    Status transitions (normal):
      queued → loading_model → processing → done  (model was unloaded before job)
      queued → processing    → done               (model was already in VRAM)

    Cancel transitions:
      queued     → cancelled  (worker skips immediately on dequeue)
      processing → cancelled  (worker discards wav + DB row after generation finishes)
    """
    from pipeline.generate_song import (
        generate_song_from_audio_melody,
        generate_song_from_image,
        generate_song_from_text,
        get_model_manager,
    )

    log.info("[worker] Worker thread started.")

    while True:
        log.info("[worker] Waiting for next job (queue depth=%d)...", _queue.qsize())
        job: Job = _queue.get()
        log.info("[worker] Picked up job %s (%s, %ds) — queue depth now %d",
                 job.file_id, job.input_type, job.duration, _queue.qsize())
        conn = get_connection()

        try:
            # --- Pre-start cancellation check ---
            # /cancel may have set job_status='cancelled' while the job sat in the queue.
            pre = conn.execute(
                "SELECT job_status FROM files WHERE id=?", (job.file_id,)
            ).fetchone()
            if pre is None or pre["job_status"] == "cancelled":
                log.info("[jobs] Job %s cancelled before start — skipping", job.file_id)
                continue  # finally still runs: conn.close() + task_done()

            # --- MIDI conversion (Basic Pitch) — no GPU/MusicGen needed ---
            if job.input_type == "midi":
                conn.execute(
                    "UPDATE files SET job_status='processing' WHERE id=?", (job.file_id,)
                )
                conn.commit()
                from pipeline.midi_convert import convert_to_midi as _midi_convert
                midi_dest = DIR_MIDI / f"{job.file_id}.mid"
                note_count = _midi_convert(Path(job.source), midi_dest)
                conn.execute(
                    "UPDATE files SET job_status='done', converted_key=? WHERE id=?",
                    (midi_dest.name, job.file_id),
                )
                conn.commit()
                log.info("[jobs] MIDI job %s done → %s (%d notes)",
                         job.file_id, midi_dest.name, note_count)
                continue  # finally executes (conn.close + task_done), then next job

            manager = get_model_manager()

            # If model isn't resident, show loading_model so the frontend can
            # display "warming up" instead of looking frozen.
            initial_status = "loading_model" if not manager.is_loaded else "processing"
            conn.execute(
                "UPDATE files SET job_status=? WHERE id=?",
                (initial_status, job.file_id),
            )
            conn.commit()

            # Called from inside the generation lock once the model is loaded
            # but before inference starts — transitions loading_model → processing.
            def _on_model_ready():
                log.info("[worker] job %s on_model_ready fired — setting status='processing'", job.file_id)
                try:
                    conn.execute(
                        "UPDATE files SET job_status='processing' WHERE id=?",
                        (job.file_id,),
                    )
                    conn.commit()
                    log.info("[worker] job %s status='processing' committed to DB", job.file_id)
                except Exception:
                    log.exception("[jobs] Could not transition %s to 'processing'", job.file_id)

            # Only pass the callback when we actually entered loading_model; if
            # the model was already loaded the status is already 'processing'.
            on_ready = _on_model_ready if initial_status == "loading_model" else None

            # Fired repeatedly (once per MusicGen autoregressive step) during
            # generation — passed unconditionally, regardless of whether the
            # model needed to load first.
            def _on_progress(frac: float) -> None:
                set_progress(job.file_id, frac)

            log.info("[worker] job %s — calling generation function (type=%s, duration=%ds, model=%s)",
                     job.file_id, job.input_type, job.duration, job.model)
            if job.input_type == "image":
                if job.prompt:
                    # Prompt already computed by /describe — skip Gemini, run MusicGen only.
                    wav_path = generate_song_from_text(
                        job.prompt, duration=job.duration, model=job.model,
                        on_model_ready=on_ready, arc_preset=job.arc_preset,
                        arc_segments=job.arc_segments, filter_mode=job.filter_mode,
                        on_progress=_on_progress,
                    )
                    prompt_used = job.prompt
                else:
                    wav_path, prompt_used = generate_song_from_image(
                        Path(job.source), duration=job.duration, model=job.model,
                        on_model_ready=on_ready, arc_preset=job.arc_preset,
                        arc_segments=job.arc_segments, filter_mode=job.filter_mode,
                        on_progress=_on_progress,
                    )
            elif job.input_type == "audio":
                wav_path = generate_song_from_audio_melody(
                    job.melody_source, job.prompt, duration=job.duration,
                    on_model_ready=on_ready, arc_preset=job.arc_preset,
                    arc_segments=job.arc_segments, filter_mode=job.filter_mode,
                    on_progress=_on_progress,
                )
                prompt_used = job.prompt
            else:
                wav_path = generate_song_from_text(
                    job.source, duration=job.duration, model=job.model,
                    on_model_ready=on_ready, arc_preset=job.arc_preset,
                    arc_segments=job.arc_segments, filter_mode=job.filter_mode,
                    on_progress=_on_progress,
                )
                prompt_used = job.source

            log.info("[worker] job %s — generation returned: wav=%s", job.file_id, wav_path.name if wav_path else None)
            try:
                import torch as _torch
                if _torch.cuda.is_available():
                    _alloc = _torch.cuda.memory_allocated(0) / 1024 ** 3
                    _resv  = _torch.cuda.memory_reserved(0)  / 1024 ** 3
                    log.info("[worker] job %s post-gen VRAM: alloc=%.2f GB, reserved=%.2f GB",
                             job.file_id, _alloc, _resv)
            except Exception:
                pass

            # --- Post-generation cancellation check ---
            # /cancel may have arrived while MusicGen was running (can't interrupt it).
            # The WAV exists in pipeline/output/ — discard it and the DB row entirely.
            post = conn.execute(
                "SELECT job_status FROM files WHERE id=?", (job.file_id,)
            ).fetchone()
            if post is None or post["job_status"] == "cancelled":
                wav_path.unlink(missing_ok=True)
                if post is not None:
                    conn.execute("DELETE FROM files WHERE id=?", (job.file_id,))
                    conn.commit()
                log.info("[jobs] Job %s cancelled mid-flight — output discarded", job.file_id)
                continue  # finally still runs

            # Move the WAV from pipeline/output/ into storage/converted/{file_id}.wav
            dest = DIR_CONVERTED / f"{job.file_id}.wav"
            shutil.move(str(wav_path), str(dest))
            converted_key = dest.name

            fad_score, fad_verdict = _score_song(dest, prompt_used)

            conn.execute(
                """UPDATE files
                      SET job_status='done', converted_key=?, prompt=?, duration=?,
                          fad_score=?, fad_verdict=?
                    WHERE id=?""",
                (converted_key, prompt_used, float(job.duration), fad_score, fad_verdict, job.file_id),
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
            clear_progress(job.file_id)
            conn.close()
            _queue.task_done()
            log.info("[worker] job %s task_done — returning to queue.get()", job.file_id)


def _run_heartbeat() -> None:
    """
    Daemon thread: log a liveness line every 30s.

    If the backend process is running but /health stops responding, heartbeats
    will reveal whether the event loop is live (heartbeats continue) or the
    whole process is frozen (heartbeats stop).  If the idle-checker deadlocks
    but the process is still alive, heartbeats keep printing while [unload]
    logs go silent at the deadlock point — those two facts together name the culprit.
    """
    from pipeline.generate_song import get_model_manager

    while True:
        time.sleep(30)
        try:
            loaded = get_model_manager().is_loaded
            log.info("[heartbeat] backend alive, model_loaded=%s", loaded)
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
