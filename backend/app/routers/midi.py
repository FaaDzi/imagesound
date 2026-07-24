"""
POST /midi/convert/{id}

Queues a Basic Pitch audio-to-MIDI conversion job for an existing, completed
audio entry.  Returns 202 immediately with the new MIDI entry's id; the caller
should poll GET /status/{id} for progress and GET /download/{id}?format=midi
once done.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.config import DIR_CONVERTED, DIR_MIDI, RATE_LIMIT_MIDI, UNSAVED_EXPIRY_SECONDS
from app.database import get_connection
from app.jobs import Job, enqueue
from app.limiter import limiter

router = APIRouter()


@router.get("/midi/preview/{file_id}")
def get_midi_preview(file_id: str):
    """Serve the sonified WAV preview for a completed MIDI entry."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_status, output_format FROM files WHERE id=?",
            (file_id,),
        ).fetchone()

    if row is None or row["output_format"] != "midi":
        raise HTTPException(status_code=404, detail="MIDI entry not found.")
    if row["job_status"] != "done":
        raise HTTPException(status_code=409, detail="MIDI conversion not complete yet.")

    preview_path = DIR_MIDI / f"{file_id}_preview.wav"
    if not preview_path.is_file():
        raise HTTPException(status_code=404, detail="Preview WAV not available for this entry.")

    return FileResponse(path=str(preview_path), media_type="audio/wav")


@router.post("/midi/convert/{file_id}", status_code=202)
@limiter.limit(RATE_LIMIT_MIDI)
def convert_to_midi(request: Request, file_id: str):
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, job_status, converted_key, prompt, duration, output_format
                 FROM files WHERE id=?""",
            (file_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    if row["output_format"] == "midi":
        raise HTTPException(status_code=400, detail="Cannot convert a MIDI entry to MIDI.")
    if row["job_status"] != "done" or not row["converted_key"]:
        raise HTTPException(
            status_code=400,
            detail=f"Source audio is not ready (status: {row['job_status']}). "
                   "Wait until generation is complete.",
        )

    wav_path = DIR_CONVERTED / row["converted_key"]
    if not wav_path.is_file():
        raise HTTPException(status_code=404, detail="Source audio file missing from storage.")

    midi_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=UNSAVED_EXPIRY_SECONDS)).isoformat()

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO files
                   (id, owner_id, input_type, original_key, converted_key,
                    prompt, output_format, duration, job_status,
                    created_at, expires_at, saved, source_file_id)
               VALUES (?, NULL, 'midi', NULL, NULL, ?, 'midi', ?, 'queued', ?, ?, 0, ?)""",
            (midi_id, row["prompt"], row["duration"],
             now.isoformat(), expires_at, file_id),
        )
        conn.commit()

    job = Job(
        file_id=midi_id,
        input_type="midi",
        source=str(wav_path),
        duration=0,
        source_file_id=file_id,
    )
    enqueue(job)

    return JSONResponse(
        status_code=202,
        content={
            "id": midi_id,
            "status": "queued",
            "message": f"MIDI conversion queued. Poll GET /status/{midi_id} for progress.",
        },
    )
