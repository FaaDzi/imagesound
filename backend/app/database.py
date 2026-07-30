import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import bcrypt

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

_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""

_DEFAULT_USERNAME = "test"
_DEFAULT_PASSWORD = "admin1234"


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
        "ALTER TABLE files ADD COLUMN fad_score REAL",
        "ALTER TABLE files ADD COLUMN fad_verdict TEXT",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # column already exists


def _seed_default_user(conn: sqlite3.Connection) -> None:
    """Seed exactly one default user (test/admin1234) if `users` is empty.

    Never overwrites an existing row -- once seeded, or once the password is
    ever changed some other way, this is a permanent no-op. Idempotent by
    design: safe to call on every startup.
    """
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count > 0:
        return
    password_hash = bcrypt.hashpw(_DEFAULT_PASSWORD.encode(), bcrypt.gensalt()).decode()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), _DEFAULT_USERNAME, password_hash, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def init_db() -> None:
    """Create the database file and all tables if they don't exist."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute(_CREATE_FILES_TABLE)
        conn.execute(_CREATE_USERS_TABLE)
        conn.commit()
        _migrate_db(conn)
        _seed_default_user(conn)
