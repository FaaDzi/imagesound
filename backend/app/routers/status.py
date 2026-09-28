"""
GET /status/{id}

Returns the current job_status for a file/job id.
When status is 'done', includes the converted_key, prompt, and duration.

GET /jobs/active

The caller's own jobs that are still queued or running. This is what lets a
page that lost its in-memory state (a phone that discarded the tab, a reload,
a different device) find out that a generation or MIDI conversion is still
going, instead of offering the button again and inviting a double request.
"""

from fastapi import APIRouter, HTTPException, Request

from app.access import current_user, owner_filter

from app.database import get_connection
from app.jobs import get_progress, get_queue_depth, get_stage

router = APIRouter()


@router.get("/status/{file_id}")
def get_status(file_id: str, request: Request):
    clause, params = owner_filter(request)
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, input_type, job_status, converted_key,
                      prompt, duration, created_at, expires_at
                 FROM files WHERE id=?""" + clause,
            (file_id, *params),
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

    _add_live_fields(result, row["job_status"], file_id)
    if row["job_status"] == "done":
        result["converted_key"] = row["converted_key"]
        result["prompt"] = row["prompt"]
        result["duration"] = row["duration"]

    return result


_IN_FLIGHT = ("queued", "processing", "loading_model")


def _add_live_fields(result: dict, status: str, file_id: str) -> None:
    if status == "queued":
        result["queue_depth"] = get_queue_depth()
    elif status == "processing":
        result["progress"] = get_progress(file_id)
        stage = get_stage(file_id)
        if stage:
            result["stage"] = stage


@router.get("/jobs/active")
def get_active_jobs(request: Request):
    # Always the caller's own jobs, admin included: this drives "you already
    # have one running" in the UI, and someone else's job isn't that.
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, input_type, job_status, source_file_id, created_at
                 FROM files
                WHERE owner_id=? AND job_status IN (?, ?, ?)
             ORDER BY created_at""",
            (current_user(request)["id"], *_IN_FLIGHT),
        ).fetchall()

    jobs = []
    for row in rows:
        job = {
            "id": row["id"],
            "input_type": row["input_type"],
            "status": row["job_status"],
            "source_file_id": row["source_file_id"],
            "created_at": row["created_at"],
        }
        _add_live_fields(job, row["job_status"], row["id"])
        jobs.append(job)
    return {"jobs": jobs}
