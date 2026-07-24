import sqlite3
from pathlib import Path

from app.config import DATABASE_PATH

_CREATE_FILES_TABLE = """
CREATE TABLE IF NOT EXISTS files (
    id             TEXT PRIMARY KEY,
    owner_id       TEXT,
    input_type     TEXT,
    original_key   TEXT,
    converted_key  TEXT,
    prompt         TEXT,
    output_format  TEXT,
    duration       REAL,
    job_status     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    saved          INTEGER NOT NULL DEFAULT 0,
    source_file_id TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with Row factory enabled.

    WAL mode lets readers (e.g. /status polling) proceed without blocking
    behind a writer (the worker thread, cleanup sweep), and busy_timeout makes
    any remaining lock contention retry for 5s instead of raising
    'database is locked' immediately.
    """
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _migrate_db(conn: sqlite3.Connection) -> None:
    """Apply any schema migrations that may be missing on an existing DB."""
    for stmt in [
        "ALTER TABLE files ADD COLUMN saved INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE files ADD COLUMN source_file_id TEXT",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # column already exists


def init_db() -> None:
    """Create the database file and all tables if they don't exist."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute(_CREATE_FILES_TABLE)
        conn.commit()
        _migrate_db(conn)
