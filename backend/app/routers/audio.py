"""
GET /audio/{id}

Serves the generated WAV with HTTP Range-request support so browsers
can seek/scrub without waiting for a full download.

The DB is always checked first — files are never exposed via a public
static folder.  Adding an owner_id check later is a one-line addition
to the WHERE clause (see the TODO comment below).
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.config import DIR_CONVERTED
from app.database import get_connection

router = APIRouter()

_CHUNK = 256 * 1024  # 256 KB read chunks for streaming


@router.get("/audio/{file_id}")
def get_audio(file_id: str, request: Request):
    # DB gate — only serve files that finished successfully.
    # TODO: add `AND owner_id=?` here once auth exists.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, job_status, converted_key FROM files WHERE id=?",
            (file_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["job_status"] != "done" or not row["converted_key"]:
        raise HTTPException(
            status_code=404,
            detail=f"Audio not ready (status: {row['job_status']}). Poll /status/{file_id}.",
        )

    file_path = DIR_CONVERTED / row["converted_key"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file missing from storage.")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range")

    if range_header:
        # Parse "bytes=start-end" — single range only (multi-range not needed for audio).
        try:
            raw = range_header.replace("bytes=", "").strip()
            start_str, end_str = raw.split("-", 1)
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
        except (ValueError, AttributeError):
            raise HTTPException(status_code=416, detail="Invalid Range header.")

        if start >= file_size or start > end:
            raise HTTPException(
                status_code=416,
                detail="Range Not Satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        end = min(end, file_size - 1)
        length = end - start + 1

        with open(file_path, "rb") as f:
            f.seek(start)
            data = f.read(length)

        return Response(
            content=data,
            status_code=206,
            media_type="audio/wav",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
                "Content-Disposition": f'inline; filename="{file_id}.wav"',
            },
        )

    # Full file — stream in chunks so large files don't sit in memory.
    def _stream():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _stream(),
        status_code=200,
        media_type="audio/wav",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Disposition": f'inline; filename="{file_id}.wav"',
        },
    )
