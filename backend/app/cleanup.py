"""
cleanup.py — removes expired files from disk and the database.

Runs once at startup (to catch stale files from previous runs), then
every 24 hours in a daemon thread.  Uses its own SQLite connection so
it is safe to call from a background thread.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from app.config import DIR_CONVERTED, DIR_MIDI, DIR_ORIGINALS
from app.database import get_connection

log = logging.getLogger(__name__)

_INTERVAL = 24 * 60 * 60  # 24 hours


def run_cleanup() -> None:
    """Delete files and DB rows whose expires_at is in the past."""
    conn = get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT id, original_key, converted_key, output_format FROM files WHERE expires_at < ?",
            (now_iso,),
        ).fetchall()

        if not rows:
            log.info("[cleanup] No expired files.")
            return

        log.info("[cleanup] %d expired row(s) to remove.", len(rows))

        for row in rows:
            converted_dir = DIR_MIDI if row["output_format"] == "midi" else DIR_CONVERTED
            paths_to_delete = [
                (DIR_ORIGINALS, row["original_key"]),
                (converted_dir, row["converted_key"]),
            ]
            for directory, key in paths_to_delete:
                if key:
                    path = directory / key
                    try:
                        path.unlink(missing_ok=True)
                        log.info("[cleanup] Deleted %s", path)
                    except Exception:
                        log.exception("[cleanup] Could not delete %s", path)

            # MIDI entries also have a sonified preview WAV.
            if row["output_format"] == "midi":
                preview = DIR_MIDI / f"{row['id']}_preview.wav"
                try:
                    preview.unlink(missing_ok=True)
                except Exception:
                    log.exception("[cleanup] Could not delete %s", preview)

            conn.execute("DELETE FROM files WHERE id=?", (row["id"],))

        conn.commit()
        log.info("[cleanup] Removed %d expired row(s) from DB.", len(rows))

    except Exception:
        log.exception("[cleanup] Cleanup run failed.")
    finally:
        conn.close()


def _scheduler_loop() -> None:
    while True:
        time.sleep(_INTERVAL)
        run_cleanup()


def start_cleanup_scheduler() -> None:
    """Run cleanup once immediately, then every 24 h in a daemon thread."""
    run_cleanup()
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="cleanup-scheduler")
    t.start()
    log.info("[cleanup] Scheduler started (interval: 24h).")
