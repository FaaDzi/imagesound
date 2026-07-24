"""
POST /convert?format=<fmt>

Accepts raw audio bytes (WAV) from the client, converts them to the
requested format via PyAV, and returns the converted audio as a download.

Used for client-side rendered audio (Web Audio effects baked in) that
needs server-side format conversion. Source bytes are never persisted.
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.config import RATE_LIMIT_UPLOAD
from app.convert import ALLOWED_FORMATS, content_type_for, convert_audio, conversion_available
from app.limiter import limiter
from app.services.storage import validate_type

router = APIRouter()

_MAX_BYTES = 50 * 1024 * 1024  # 50 MB — covers a 2-min stereo 44.1 kHz WAV


async def _read_capped_body(request: Request, max_bytes: int) -> bytes:
    """Read the request body incrementally, aborting as soon as it exceeds
    max_bytes — unlike request.body(), which buffers the entire payload into
    memory before any size check can run."""
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            mb = max_bytes // (1024 * 1024)
            raise HTTPException(status_code=413, detail=f"Audio payload too large (max {mb} MB).")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/convert")
@limiter.limit(RATE_LIMIT_UPLOAD)
async def convert_client_audio(
    request: Request,
    format: str = Query(default="mp3"),
):
    fmt = format.lower().strip()
    if fmt not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Format '{fmt}' not supported. Allowed: {sorted(ALLOWED_FORMATS)}",
        )

    data = await _read_capped_body(request, _MAX_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="Empty request body.")

    validate_type(data)

    # WAV passthrough — no conversion needed.
    if fmt == "wav":
        return Response(
            content=data,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="processed.wav"'},
        )

    if not conversion_available():
        raise HTTPException(
            status_code=503,
            detail="PyAV not installed — only WAV is available for processed audio.",
        )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
        tmp_src = Path(f.name)

    try:
        tmp_out = convert_audio(tmp_src, fmt)
    except (ValueError, RuntimeError) as exc:
        tmp_src.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(exc))

    tmp_src.unlink(missing_ok=True)

    return FileResponse(
        path=tmp_out,
        media_type=content_type_for(fmt),
        filename=f"processed.{fmt}",
        background=BackgroundTask(tmp_out.unlink, missing_ok=True),
    )
