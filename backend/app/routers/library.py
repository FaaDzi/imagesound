"""
GET /library

Returns all non-expired, completed songs ordered newest-first.
Includes both saved and still-alive unsaved songs; the frontend
shows the saved/temporary distinction via the `saved` and `expires_at` fields.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.access import owner_filter

from app.database import get_connection

router = APIRouter()


@router.get("/library")
def get_library(request: Request):
    clause, params = owner_filter(request)
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, input_type, prompt, duration, saved, expires_at, created_at,
                      output_format, source_file_id, fad_verdict, model_id
                 FROM files
                WHERE job_status = 'done'
                  AND expires_at > ?""" + clause + """
                ORDER BY created_at DESC""",
            (now_iso, *params),
        ).fetchall()

    return [
        {
            "id":             row["id"],
            "input_type":     row["input_type"],
            "prompt":         row["prompt"],
            "duration":       row["duration"],
            "saved":          bool(row["saved"]),
            "expires_at":     row["expires_at"],
            "created_at":     row["created_at"],
            "output_format":  row["output_format"],
            "source_file_id": row["source_file_id"],
            "fad_verdict":    row["fad_verdict"],
            "model_id":       row["model_id"],
        }
        for row in rows
    ]
