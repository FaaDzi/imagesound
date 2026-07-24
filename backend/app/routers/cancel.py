"""
POST /cancel/{id}

Cancel a queued or in-flight generation.

Queued / loading_model / processing:
  Sets job_status='cancelled'. The worker checks this on dequeue (queued case)
  or after generation finishes (processing case) and discards any produced output.

Done:
  Job finished before cancel arrived — honor the explicit cancel by deleting the
  files and the DB row. The user pressed cancel; we don't keep the output.

Failed / Cancelled:
  Already terminal — returns 409.
"""

from fastapi import APIRouter, HTTPException

from app.config import DIR_CONVERTED, DIR_ORIGINALS
from app.database import get_connection

router = APIRouter()

_TERMINAL = frozenset({"failed", "cancelled"})


@router.post("/cancel/{file_id}", status_code=200)
def cancel_job(file_id: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_status, original_key, converted_key FROM files WHERE id=?",
            (file_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        status = row["job_status"]

        if status in _TERMINAL:
            raise HTTPException(
                status_code=409,
                detail=f"Job already in terminal state: {status}",
            )

        if status == "done":
            # Race: job completed just before cancel arrived. Honor the cancel — delete everything.
            for directory, key in [
                (DIR_ORIGINALS, row["original_key"]),
                (DIR_CONVERTED, row["converted_key"]),
            ]:
                if key:
                    (directory / key).unlink(missing_ok=True)
            conn.execute("DELETE FROM files WHERE id=?", (file_id,))
            conn.commit()
            return {"id": file_id, "cancelled": True}

        # queued / loading_model / processing:
        # Mark cancelled; the worker handles cleanup when it reaches this job.
        conn.execute(
            "UPDATE files SET job_status='cancelled' WHERE id=?",
            (file_id,),
        )
        conn.commit()

    return {"id": file_id, "cancelled": True}
