"""
POST /generate

Accepts an image file id (from /upload) OR a raw text prompt, plus an
optional duration.  Enqueues the job and returns 202 immediately — the
caller should poll GET /status/{id} for progress.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import DIR_CONVERTED, DIR_ORIGINALS, MAX_DURATION_SECONDS, RATE_LIMIT_GENERATE, SMALL_MODEL_AVAILABLE, UNSAVED_EXPIRY_SECONDS
from app.database import get_connection
from app.jobs import Job, enqueue
from app.limiter import limiter

router = APIRouter()


class GenerateRequest(BaseModel):
    id: str | None = None
    melody_source_id: str | None = None
    prompt: str | None = None
    duration: int = Field(default=8, ge=1, le=MAX_DURATION_SECONDS)
    model: Literal["medium", "small"] = "medium"
    arc_preset: Literal["steady", "gentle_build", "rise_and_settle", "calm_energetic"] = "steady"
    arc_segments: list[int] | None = Field(default=None)
    filter_mode: Literal["raw", "filtered"] = "filtered"


def _insert_queued_file_row(
    conn,
    file_id: str,
    input_type: str,
    prompt: str | None,
    duration: float,
    created_at: str,
    expires_at: str,
    original_key: str | None = None,
    source_file_id: str | None = None,
) -> None:
    """Insert a new 'files' row for a freshly queued generation job.

    Shared by the melody/image/text paths below — they only ever differ in
    input_type/original_key/source_file_id. converted_key/output_format start
    NULL (set once the job completes), job_status starts 'queued', saved
    starts 0.
    """
    conn.execute(
        """INSERT INTO files
               (id, owner_id, input_type, original_key, converted_key,
                prompt, output_format, duration, job_status, created_at, expires_at,
                saved, source_file_id)
           VALUES (?, NULL, ?, ?, NULL, ?, NULL, ?, 'queued', ?, ?, 0, ?)""",
        (file_id, input_type, original_key, prompt, duration, created_at, expires_at, source_file_id),
    )


def _base_job_kwargs(req: GenerateRequest) -> dict:
    """Job fields shared by every generation path — duration/arc/filter options."""
    return {
        "duration": req.duration,
        "arc_preset": req.arc_preset,
        "arc_segments": req.arc_segments,
        "filter_mode": req.filter_mode,
    }


@router.post("/generate", status_code=202)
@limiter.limit(RATE_LIMIT_GENERATE)
def generate(request: Request, req: GenerateRequest):
    if req.melody_source_id is None and req.model == "small" and not SMALL_MODEL_AVAILABLE:
        raise HTTPException(
            status_code=422,
            detail="The small model is not available yet. Select 'medium' to generate.",
        )
    if req.melody_source_id is not None:
        # --- Melody-conditioned path: reference audio + prompt -> new song ---
        if req.id is not None:
            raise HTTPException(
                status_code=400,
                detail="'melody_source_id' and 'id' are mutually exclusive — provide either "
                       "an image to generate from or a melody reference, not both.",
            )
        if not (req.prompt and req.prompt.strip()):
            raise HTTPException(
                status_code=400,
                detail="Describe the target style — melody conditioning needs a text prompt "
                       "alongside the reference audio.",
            )
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, input_type, output_format, converted_key, original_key "
                "FROM files WHERE id=?",
                (req.melody_source_id,),
            ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Melody reference file not found.")
        if row["output_format"] == "midi":
            raise HTTPException(
                status_code=400,
                detail="MIDI entries can't be used as a melody reference — pick an audio "
                       "or song entry instead.",
            )

        if row["converted_key"]:
            melody_wav_path = DIR_CONVERTED / row["converted_key"]
        elif row["input_type"] == "audio" and row["original_key"]:
            melody_wav_path = DIR_ORIGINALS / row["original_key"]
        else:
            raise HTTPException(status_code=400, detail="Melody reference has no usable audio yet.")

        if not melody_wav_path.is_file():
            raise HTTPException(status_code=404, detail="Melody reference file missing from storage.")

        prompt = req.prompt.strip()
        file_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=UNSAVED_EXPIRY_SECONDS)

        with get_connection() as conn:
            _insert_queued_file_row(
                conn, file_id, "audio", prompt, float(req.duration),
                now.isoformat(), expires_at.isoformat(),
                source_file_id=req.melody_source_id,
            )
            conn.commit()

        job = Job(
            file_id=file_id,
            input_type="audio",
            source=str(melody_wav_path),
            melody_source=str(melody_wav_path),
            prompt=prompt,
            model="melody",
            source_file_id=req.melody_source_id,
            **_base_job_kwargs(req),
        )

    elif req.id is not None:
        # --- Image path: look up the uploaded file ---
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, input_type, original_key FROM files WHERE id=?",
                (req.id,),
            ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="File not found.")
        if row["input_type"] != "image":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File '{req.id}' has input_type='{row['input_type']}'. "
                    "Pass 'prompt' instead of 'id' for text-only generation."
                ),
            )

        source = str(DIR_ORIGINALS / row["original_key"])

        # Use a pre-computed prompt if provided (skips Gemini in the worker).
        provided_prompt = req.prompt.strip() if req.prompt and req.prompt.strip() else None

        now = datetime.now(timezone.utc)
        unsaved_expires = (now + timedelta(seconds=UNSAVED_EXPIRY_SECONDS)).isoformat()
        with get_connection() as conn:
            saved_row = conn.execute(
                "SELECT saved FROM files WHERE id=?", (row["id"],)
            ).fetchone()
            if saved_row and saved_row["saved"]:
                # Previous result is saved in the library — don't overwrite it.
                # Create a fresh record for this generation so both coexist.
                file_id = str(uuid.uuid4())
                _insert_queued_file_row(
                    conn, file_id, "image", provided_prompt, float(req.duration),
                    now.isoformat(), unsaved_expires,
                    original_key=row["original_key"],
                )
            else:
                # Unsaved — update in place (user hasn't kept this result).
                file_id = row["id"]
                conn.execute(
                    "UPDATE files SET job_status='queued', duration=?, saved=0, expires_at=? WHERE id=?",
                    (float(req.duration), unsaved_expires, file_id),
                )
            conn.commit()

        job = Job(
            file_id=file_id,
            input_type="image",
            source=source,
            prompt=provided_prompt,
            model=req.model,
            **_base_job_kwargs(req),
        )

    elif req.prompt and req.prompt.strip():
        # --- Text path: create a new DB row (no uploaded file) ---
        file_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=UNSAVED_EXPIRY_SECONDS)

        with get_connection() as conn:
            _insert_queued_file_row(
                conn, file_id, "text", req.prompt.strip(), float(req.duration),
                now.isoformat(), expires_at.isoformat(),
            )
            conn.commit()

        job = Job(
            file_id=file_id,
            input_type="text",
            source=req.prompt.strip(),
            model=req.model,
            **_base_job_kwargs(req),
        )

    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'id' (image file from /upload) or 'prompt' (text-only).",
        )

    enqueue(job)

    return JSONResponse(
        status_code=202,
        content={
            "id": file_id,
            "status": "queued",
            "message": f"Job queued. Poll GET /status/{file_id} for progress.",
        },
    )
