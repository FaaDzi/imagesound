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
    created_at    TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user'
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
        "ALTER TABLE files ADD COLUMN model_id TEXT",
        "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # column already exists


def _seed_default_user(conn: sqlite3.Connection) -> None:
    """Seed exactly one default admin (test/admin1234) if `users` is empty.

    Never overwrites an existing row -- once seeded, or once the password is
    ever changed some other way, this is a permanent no-op. Idempotent by
    design: safe to call on every startup.
    """
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count > 0:
        return
    password_hash = bcrypt.hashpw(_DEFAULT_PASSWORD.encode(), bcrypt.gensalt()).decode()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, created_at, role) VALUES (?, ?, ?, ?, 'admin')",
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
        _ensure_admin_owns_legacy(conn)
        _fail_orphaned_jobs(conn)


def _fail_orphaned_jobs(conn: sqlite3.Connection) -> None:
    """Rows still in flight at startup belong to a queue that died with the
    last process: nothing will ever pick them up. Left alone, GET /jobs/active
    would report them as running forever and the UI would lock its Generate /
    Convert buttons waiting on them."""
    n = conn.execute(
        "UPDATE files SET job_status='failed' "
        "WHERE job_status IN ('queued', 'processing', 'loading_model')"
    ).rowcount
    conn.commit()
    if n:
        print(f"  [startup] marked {n} job(s) left over from the last run as failed", flush=True)


def _ensure_admin_owns_legacy(conn: sqlite3.Connection) -> None:
    """Upgrade path from the single-login era.

    Before roles existed there was one account and no song had an owner. Make
    the oldest account the admin if nobody is one yet, and hand every ownerless
    song to the admin -- otherwise they'd be invisible to everyone once the
    library started filtering by owner. Both steps are no-ops after the first run.
    """
    has_admin = conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
    if not has_admin:
        conn.execute(
            "UPDATE users SET role='admin' WHERE id=(SELECT id FROM users ORDER BY created_at LIMIT 1)"
        )
    admin = conn.execute(
        "SELECT id FROM users WHERE role='admin' ORDER BY created_at LIMIT 1"
    ).fetchone()
    if admin:
        conn.execute("UPDATE files SET owner_id=? WHERE owner_id IS NULL", (admin["id"],))
    conn.commit()


def original_in_use(conn: sqlite3.Connection, original_key: str, except_id: str) -> bool:
    """Whether another row still points at this uploaded file.

    Generating again from an image whose song was saved creates a new row that
    shares the upload's file, so deleting one row's original can pull the image
    out from under a saved song."""
    return conn.execute(
        "SELECT 1 FROM files WHERE original_key=? AND id<>? LIMIT 1", (original_key, except_id)
    ).fetchone() is not None


def drop_cancelled(conn: sqlite3.Connection, file_id: str) -> None:
    """Clean up the row of a generation that was cancelled.

    An image's first generation runs on the upload's own row, so deleting it
    would delete the upload: the image stays on disk but every later Generate
    for it answers 404. Image rows are therefore kept and marked cancelled --
    /generate re-queues them -- while text and remix rows, which exist only
    for their job, are deleted."""
    row = conn.execute("SELECT input_type FROM files WHERE id=?", (file_id,)).fetchone()
    if row is not None and row["input_type"] == "image":
        conn.execute("UPDATE files SET job_status='cancelled', converted_key=NULL WHERE id=?", (file_id,))
    else:
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()


def get_user(user_id: str) -> "sqlite3.Row | None":
    """The account behind a session, or None if it no longer exists."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, username, role FROM users WHERE id=?", (user_id,)
        ).fetchone()
