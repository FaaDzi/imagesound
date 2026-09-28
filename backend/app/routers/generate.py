"""
POST /generate

Accepts an image file id (from /upload), a raw text prompt, or an existing
song to use as a reference, plus a model id (see GET /models) and duration.
Enqueues the job and returns 202 immediately — the caller should poll
GET /status/{id} for progress.

Nothing here knows about any particular music model: what a model supports
(duration range, reference modes, options) comes from pipeline/models.json.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.access import current_user, is_admin, max_duration_for, owner_filter
from app.config import DIR_CONVERTED, DIR_ORIGINALS, LOW_RAM_WARN_GB, MAX_DURATION_SECONDS, RATE_LIMIT_GENERATE, UNSAVED_EXPIRY_SECONDS
from app.database import get_connection
from app.jobs import Job, active_job_id, enqueue, has_work_in_progress
from app.limiter import limiter
from app.sysmem import available_ram_gb
from pipeline.models import ModelError, default_model_id, get_model

router = APIRouter()


class GenerateRequest(BaseModel):
    id: str | None = None                # uploaded image to turn into a song
    reference_id: str | None = None      # existing song/audio to condition on
    reference_mode: str | None = None    # how to use it ("cover", "style", ...) — model-declared
    prompt: str | None = None
    duration: int = Field(default=30, ge=1, le=MAX_DURATION_SECONDS)
    model: str | None = None             # model id from GET /models; omitted -> the default
    options: dict[str, Any] = Field(default_factory=dict)  # per-model options, validated server-side
    lyrics: str | None = Field(default=None, max_length=4000)  # with options.vocals="lyrics"; empty = drafted


def _insert_queued_file_row(
    conn,
    file_id: str,
    owner_id: str,
    input_type: str,
    prompt: str | None,
    duration: float,
    created_at: str,
    expires_at: str,
    original_key: str | None = None,
    source_file_id: str | None = None,
) -> None:
    """Insert a new 'files' row for a freshly queued generation job.

    Shared by the reference/image/text paths below — they only ever differ in
    input_type/original_key/source_file_id. converted_key/output_format start
    NULL (set once the job completes), job_status starts 'queued', saved
    starts 0.
    """
    conn.execute(
        """INSERT INTO files
               (id, owner_id, input_type, original_key, converted_key,
                prompt, output_format, duration, job_status, created_at, expires_at,
                saved, source_file_id)
           VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, 'queued', ?, ?, 0, ?)""",
        (file_id, owner_id, input_type, original_key, prompt, duration, created_at, expires_at, source_file_id),
    )


def _busy_response(request: Request) -> JSONResponse:
    """409 while another generation runs. The admin also gets the running
    job's id, so the UI can offer to stop it (POST /cancel/{id}) -- admin
    priority means being able to take the GPU back, not jumping a queue."""
    body: dict[str, Any] = {
        "detail": "Someone is currently generating a song -- please wait for it to finish, "
                  "then try again.",
    }
    # Ask the worker, not the DB: uploads sit in 'queued' until generated, so
    # a status query can't tell a waiting upload from the job holding the GPU.
    job_id = active_job_id()
    if is_admin(request) and job_id:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT f.id, u.role FROM files f LEFT JOIN users u ON u.id = f.owner_id WHERE f.id=?",
                (job_id,),
            ).fetchone()
        if row is not None:
            body["active_job_id"] = row["id"]
            body["detail"] = (
                "A User visitor is generating a song right now."
                if row["role"] != "admin"
                else "You already have a song generating (another tab?)."
            )
    return JSONResponse(status_code=409, content=body)


@router.post("/generate", status_code=202)
@limiter.limit(RATE_LIMIT_GENERATE)
def generate(request: Request, req: GenerateRequest):
    # One GPU, one generation at a time. With a shared public account,
    # "someone else" here really is another person. Reject outright rather
    # than silently queueing: queueing let two requests targeting the same
    # unsaved image silently overwrite each other's result in the database.
    if has_work_in_progress():
        return _busy_response(request)

    owner_id = current_user(request)["id"]
    clause, params = owner_filter(request)

    cap = max_duration_for(request)
    if cap is not None and req.duration > cap:
        raise HTTPException(
            status_code=422,
            detail=f"This account can generate songs up to {cap}s long (requested {req.duration}s).",
        )

    # Validate against the chosen model's declared capabilities before any DB
    # write, so a bad request never leaves an orphaned 'queued' row behind.
    try:
        spec = get_model(req.model or default_model_id())
        if not is_admin(request):
            # Options the manifest marks admin_only (test knobs such as vocal
            # tone) are hidden from this account by GET /models; asking for
            # them anyway is refused.
            locked = [o["label"] for o in spec.options if o.get("admin_only")
                      and o["key"] in req.options and req.options[o["key"]] != o.get("default")]
            if locked:
                raise HTTPException(status_code=403,
                                    detail=f"{', '.join(locked)} is not available on this account.")
        available, reason = spec.availability()
        if not available:
            raise ModelError(f"{spec.label} is not available: {reason}")
        reference_mode = None
        if req.reference_id is not None:
            if not spec.reference_modes:
                raise ModelError(f"{spec.label} can't use a reference song.")
            reference_mode = req.reference_mode or spec.reference_modes[0]
        options = spec.validate_request(req.duration, reference_mode, req.options)
    except ModelError as e:
        raise HTTPException(status_code=422, detail=str(e))

    lyrics = req.lyrics.strip() if req.lyrics and options.get("vocals") == "lyrics" else None
    job_common = {"duration": req.duration, "model": spec.id, "options": options, "lyrics": lyrics}

    if req.reference_id is not None:
        # --- Reference path: existing song + text prompt -> new song ---
        if req.id is not None:
            raise HTTPException(
                status_code=400,
                detail="'reference_id' and 'id' are mutually exclusive — provide either "
                       "an image to generate from or a reference song, not both.",
            )
        if not (req.prompt and req.prompt.strip()):
            raise HTTPException(
                status_code=400,
                detail="Describe the target style — a reference song needs a text prompt "
                       "alongside it.",
            )
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, input_type, output_format, converted_key, original_key "
                "FROM files WHERE id=?" + clause,
                (req.reference_id, *params),
            ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Reference file not found.")
        if row["output_format"] == "midi":
            raise HTTPException(
                status_code=400,
                detail="MIDI entries can't be used as a reference — pick an audio "
                       "or song entry instead.",
            )

        if row["converted_key"]:
            reference_path = DIR_CONVERTED / row["converted_key"]
        elif row["input_type"] == "audio" and row["original_key"]:
            reference_path = DIR_ORIGINALS / row["original_key"]
        else:
            raise HTTPException(status_code=400, detail="Reference has no usable audio yet.")

        if not reference_path.is_file():
            raise HTTPException(status_code=404, detail="Reference file missing from storage.")

        prompt = req.prompt.strip()
        file_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=UNSAVED_EXPIRY_SECONDS)

        with get_connection() as conn:
            _insert_queued_file_row(
                conn, file_id, owner_id, "audio", prompt, float(req.duration),
                now.isoformat(), expires_at.isoformat(),
                source_file_id=req.reference_id,
            )
            conn.commit()

        job = Job(
            file_id=file_id,
            input_type="audio",
            source=str(reference_path),
            prompt=prompt,
            reference_mode=reference_mode,
            source_file_id=req.reference_id,
            **job_common,
        )

    elif req.id is not None:
        # --- Image path: look up the uploaded file ---
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, input_type, original_key FROM files WHERE id=?" + clause,
                (req.id, *params),
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
                    conn, file_id, owner_id, "image", provided_prompt, float(req.duration),
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
            **job_common,
        )

    elif req.prompt and req.prompt.strip():
        # --- Text path: create a new DB row (no uploaded file) ---
        file_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=UNSAVED_EXPIRY_SECONDS)

        with get_connection() as conn:
            _insert_queued_file_row(
                conn, file_id, owner_id, "text", req.prompt.strip(), float(req.duration),
                now.isoformat(), expires_at.isoformat(),
            )
            conn.commit()

        job = Job(
            file_id=file_id,
            input_type="text",
            source=req.prompt.strip(),
            **job_common,
        )

    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'id' (image file from /upload) or 'prompt' (text-only).",
        )

    enqueue(job)

    content = {
        "id": file_id,
        "status": "queued",
        "message": f"Job queued. Poll GET /status/{file_id} for progress.",
    }
    warning = _low_ram_warning(request)
    if warning:
        content["warning"] = warning
    return JSONResponse(status_code=202, content=content)


def _low_ram_warning(request: Request) -> str | None:
    """A soft heads-up when the host is short on RAM -- never a refusal.

    Printed in the server terminal for the person at the laptop, the only one
    who can free memory, and returned to the requester in role-appropriate
    words: a User visitor can't close apps on someone else's laptop.
    """
    free_gb = available_ram_gb()
    if free_gb is None or free_gb >= LOW_RAM_WARN_GB:
        return None
    print(
        f"\n  [WARNING] Low memory: {free_gb:.1f} GB RAM free (want {LOW_RAM_WARN_GB:.0f}+ GB). "
        "Loading the model may stall this laptop for a bit.\n"
        "  [WARNING] Close apps you don't need (browser tabs, Steam, Discord, ...) for a smoother run.\n",
        flush=True,
    )
    if is_admin(request):
        return (f"The laptop only has {free_gb:.1f} GB of RAM free. Close some apps "
                "for a smoother generation -- it may stall briefly while the model loads.")
    return "The server is under heavy load right now, so this may take a little longer than usual."
