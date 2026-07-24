"""
GET /status/{id}

Returns the current job_status for a file/job id.
When status is 'done', includes the converted_key, prompt, and duration.
"""

from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.jobs import get_progress, get_queue_depth

router = APIRouter()


@router.get("/status/{file_id}")
def get_status(file_id: str):
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, input_type, job_status, converted_key,
                      prompt, duration, created_at, expires_at
                 FROM files WHERE id=?""",
            (file_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    result = {
        "id": row["id"],
        "input_type": row["input_type"],
        "status": row["job_status"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }

    if row["job_status"] == "queued":
        result["queue_depth"] = get_queue_depth()
    elif row["job_status"] == "processing":
        result["progress"] = get_progress(file_id)
    elif row["job_status"] == "done":
        result["converted_key"] = row["converted_key"]
        result["prompt"] = row["prompt"]
        result["duration"] = row["duration"]

    return result
