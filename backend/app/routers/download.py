"""
GET /download/{id}?format=<fmt>

Converts the stored WAV to the requested format on demand and serves it
as an attachment download.  WAV is served directly from storage (no
conversion).  All other formats are converted via ffmpeg and the temp
file is deleted immediately after the response is sent.

Supported formats: wav, mp3, flac, m4a, ogg.
"""

import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.access import owner_filter
from app.config import DIR_CONVERTED, DIR_MIDI
from app.convert import ALLOWED_FORMATS, content_type_for, convert_audio, conversion_available
from app.database import get_connection

router = APIRouter()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _filename_slug(prompt: str | None, file_id: str) -> str:
    """Derive a download filename slug from the prompt or fall back to the job id."""
    if prompt:
        slug = _SLUG_RE.sub("_", prompt[:40].strip().lower()).strip("_")
        if slug:
            return slug
    return file_id[:8]


@router.get("/download/{file_id}")
def download(
    file_id: str,
    request: Request,
    format: str = Query(default="wav", description="Target audio format"),
):
    fmt = format.lower().strip()
    clause, params = owner_filter(request)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, job_status, converted_key, prompt, output_format FROM files WHERE id=?" + clause,
            (file_id, *params),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["job_status"] != "done" or not row["converted_key"]:
        raise HTTPException(
            status_code=404,
            detail=f"Not ready (status: {row['job_status']}). Poll /status/{file_id}.",
        )

    slug = _filename_slug(row["prompt"], file_id)

    # ── MIDI entries: serve the .mid file from DIR_MIDI ──────────────────
    if row["output_format"] == "midi":
        if fmt not in ("midi", "mid"):
            raise HTTPException(
                status_code=422,
                detail="MIDI entries can only be downloaded as format=midi.",
            )
        midi_path = DIR_MIDI / row["converted_key"]
        if not midi_path.is_file():
            raise HTTPException(status_code=404, detail="MIDI file missing from storage.")
        return FileResponse(
            path=midi_path,
            media_type="audio/midi",
            filename=f"{slug}.mid",
        )

    # ── Audio entries: existing format conversion logic ───────────────────
    if fmt not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Format '{fmt}' is not supported. "
                f"Allowed values: {sorted(ALLOWED_FORMATS)}"
            ),
        )

    src_path = DIR_CONVERTED / row["converted_key"]
    if not src_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file missing from storage.")

    if fmt == "wav":
        return FileResponse(
            path=src_path,
            media_type="audio/wav",
            filename=f"{slug}.wav",
        )

    if not conversion_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Audio conversion library (PyAV) is not installed. "
                "Only WAV download is available."
            ),
        )

    try:
        tmp_path = convert_audio(src_path, fmt)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return FileResponse(
        path=tmp_path,
        media_type=content_type_for(fmt),
        filename=f"{slug}.{fmt}",
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )
