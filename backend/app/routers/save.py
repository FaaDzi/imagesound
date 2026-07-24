"""
POST /save/{id}    — mark a generated song as permanently saved (7-day expiry).
POST /discard/{id} — delete a generated song immediately (files + DB row).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.config import DIR_CONVERTED, DIR_MIDI, DIR_ORIGINALS
from app.database import get_connection

router = APIRouter()


@router.post("/save/{file_id}", status_code=200)
def save_file(file_id: str):
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=7)).isoformat()

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE files SET saved=1, expires_at=? WHERE id=? AND job_status='done'",
            (expires_at, file_id),
        )
        if cursor.rowcount == 0:
            row = conn.execute("SELECT id FROM files WHERE id=?", (file_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Song not found.")
            raise HTTPException(status_code=400, detail="Song is not ready yet (generation not complete).")
        conn.commit()

    return {"id": file_id, "saved": True, "expires_at": expires_at}


@router.post("/discard/{file_id}", status_code=200)
def discard_file(file_id: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT original_key, converted_key, output_format FROM files WHERE id=?",
            (file_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Song not found.")

        # MIDI entries store their .mid in DIR_MIDI, not DIR_CONVERTED.
        converted_dir = DIR_MIDI if row["output_format"] == "midi" else DIR_CONVERTED

        for directory, key in [
            (DIR_ORIGINALS, row["original_key"]),
            (converted_dir, row["converted_key"]),
        ]:
            if key:
                (directory / key).unlink(missing_ok=True)

        # MIDI entries also have a sonified preview WAV alongside the .mid file.
        if row["output_format"] == "midi":
            (DIR_MIDI / f"{file_id}_preview.wav").unlink(missing_ok=True)

        conn.execute("DELETE FROM files WHERE id=?", (file_id,))
        conn.commit()

    return {"id": file_id, "discarded": True}
