# Login Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let anyone browse Home/Library/Player to see how ImageSound works, but require a login (one seeded user, `test`/`admin1234`) before any state-changing or content-serving action succeeds — enforced independently at the backend, not just hidden in the UI.

**Architecture:** A `users` table + bcrypt-hashed password backs a signed-cookie session (Starlette `SessionMiddleware`). A backend middleware allowlists a small set of public GET endpoints (library metadata, thumbnails, health, auth) and rejects everything else with 401 unless a valid session is present. The frontend never redirects away from a page — each page reads auth state itself and disables the relevant controls (native `disabled` + `title`), except Player, which gets a full opaque overlay over its functional area.

**Tech Stack:** FastAPI/Starlette (backend), `bcrypt` (password hashing), React/TypeScript + react-router-dom (frontend).

## Global Constraints

- Design spec (read for full rationale, especially the two revision notes and the middleware-ordering warning): `docs/superpowers/specs/2026-07-30-login-screen-design.md`
- One seeded user only: username `test`, password `admin1234`. Seeding must be idempotent — never overwrite an existing `users` row, including on every server restart.
- No route ever redirects an unauthenticated visitor away — Home, Library, and Player are all always reachable. Only specific actions are gated.
- Backend enforcement is authoritative. Every protected endpoint must independently reject an unauthenticated request (401), regardless of what the frontend shows — verify this with direct `curl`/no-cookie requests, not just by clicking through the UI.
- Public (no login required): `GET /library`, `GET /image/{id}`, `GET /health`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`. Everything else requires a valid session — deny-by-default.
- CORS preflight (`OPTIONS`) must always succeed regardless of auth state, or every cross-origin request from the frontend breaks. Verify this with a real cross-origin browser fetch, not just curl (curl doesn't enforce CORS/preflight the way a browser does).
- Passwords are never logged, never returned in any response, never stored anywhere but as a bcrypt hash in `users.password_hash`.
- No route-level gate, no registration UI, no password reset, no per-user file scoping, no roles — see the design spec's Non-goals for the full list.

---

### Task 1: `users` table + bcrypt + seed

**Files:**
- Modify: `backend/app/database.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: a `users` table (`id`, `username`, `password_hash`, `created_at`) with exactly one seeded row (`test` / bcrypt hash of `admin1234`) after `init_db()` runs — consumed by Task 2's `/auth/login`.

- [ ] **Step 1: Add `bcrypt` to requirements**

Current `backend/requirements.txt`:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
python-dotenv>=1.0.0
filetype>=1.2.0
slowapi>=0.1.9
pillow>=10.0.0
```

Replace with:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
python-dotenv>=1.0.0
filetype>=1.2.0
slowapi>=0.1.9
pillow>=10.0.0
bcrypt>=4.0.0
```

Install it into the main `.venv`: `.venv\Scripts\pip.exe install bcrypt>=4.0.0`.

- [ ] **Step 2: Add the `users` table, seed logic**

Current `backend/app/database.py`:
```python
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
        "ALTER TABLE files ADD COLUMN fad_score REAL",
        "ALTER TABLE files ADD COLUMN fad_verdict TEXT",
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
```

Replace with:
```python
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
```

- [ ] **Step 3: Verify (no server needed — direct DB check)**

```bash
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); from app.database import init_db, get_connection; init_db(); c = get_connection(); print(c.execute('SELECT username, password_hash FROM users').fetchall())"
```
Expected: exactly one row, `username='test'`, `password_hash` starting with `$2b$` (bcrypt's hash prefix).

Run the exact same command a second time (idempotency check):
```bash
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); from app.database import init_db, get_connection; init_db(); c = get_connection(); print(c.execute('SELECT COUNT(*) FROM users').fetchone())"
```
Expected: `(1,)` — still exactly one row, not two.

- [ ] **Step 4: Commit**

```bash
git add backend/app/database.py backend/requirements.txt
git commit -m "Add users table with idempotent seeded test account"
```

---

### Task 2: Session middleware, auth-gate middleware, CORS flip, auth router

**Files:**
- Create: `backend/app/routers/auth.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: `users` table from Task 1.
- Produces: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`; a global auth-gate that 401s any request to a non-public path without a valid session — consumed by every frontend task below (3-8) and by the never-fail assumption every protected router already relies on.

This task is one unit (not split further) because the router literally cannot function without the session middleware already installed — `request.session` doesn't exist until `SessionMiddleware` is wired up, so they must land and be verified together.

- [ ] **Step 1: Add `itsdangerous` (SessionMiddleware's signing dependency) and a login rate limit**

Current `backend/requirements.txt` (after Task 1):
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
python-dotenv>=1.0.0
filetype>=1.2.0
slowapi>=0.1.9
pillow>=10.0.0
bcrypt>=4.0.0
```

Replace with:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
python-dotenv>=1.0.0
filetype>=1.2.0
slowapi>=0.1.9
pillow>=10.0.0
bcrypt>=4.0.0
itsdangerous>=2.0.0
```

Install: `.venv\Scripts\pip.exe install itsdangerous>=2.0.0`.

Current `backend/app/config.py`:
```python
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

# Resolve the backend root regardless of where the process is launched from
BACKEND_ROOT = Path(__file__).resolve().parent.parent

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", str(BACKEND_ROOT / "storage")))

DIR_ORIGINALS = STORAGE_ROOT / "originals"
DIR_CONVERTED = STORAGE_ROOT / "converted"
DIR_MIDI      = STORAGE_ROOT / "midi"
DIR_TEMP      = STORAGE_ROOT / "temp"

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BACKEND_ROOT / "app.db")))

MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024  # 50 MB — a 3min stereo 44.1kHz WAV melody reference is ~30MB
MAX_DURATION_SECONDS: int = 180

# Rate limiting (requests per minute per client IP)
RATE_LIMIT_GENERATE: str = "10/minute"
RATE_LIMIT_DESCRIBE: str = "20/minute"
RATE_LIMIT_UPLOAD: str = "30/minute"
RATE_LIMIT_MIDI: str = "10/minute"

# CORS — list the frontend dev origin explicitly (never use "*" with credentials).
# Add the production/tunnel URL here or override via ALLOWED_ORIGINS env var.
FRONTEND_ORIGINS: list[str] = ["http://localhost:3000"]


# Model availability — set SMALL_MODEL_AVAILABLE to True once musicgen-small is downloaded.
# When False, any request specifying model="small" is rejected at the router with a clear
# error before touching the pipeline — nothing calls get_pretrained("facebook/musicgen-small").
SMALL_MODEL_AVAILABLE: bool = True

# How long an unsaved generated song is kept before cleanup sweeps it.
# A saved song always gets the standard 7-day window (set at save time).
UNSAVED_EXPIRY_SECONDS: int = int(os.getenv("UNSAVED_EXPIRY_SECONDS", str(6 * 3600)))


def ensure_storage_dirs() -> None:
    """Create storage directories if they don't already exist."""
    for directory in (DIR_ORIGINALS, DIR_CONVERTED, DIR_MIDI, DIR_TEMP):
        directory.mkdir(parents=True, exist_ok=True)
```

Add `RATE_LIMIT_LOGIN` alongside the other rate limits, and `SESSION_SECRET_KEY` alongside the other env-driven settings:

```python
# Rate limiting (requests per minute per client IP)
RATE_LIMIT_GENERATE: str = "10/minute"
RATE_LIMIT_DESCRIBE: str = "20/minute"
RATE_LIMIT_UPLOAD: str = "30/minute"
RATE_LIMIT_MIDI: str = "10/minute"
RATE_LIMIT_LOGIN: str = "10/minute"
```

and, near `UNSAVED_EXPIRY_SECONDS`:

```python
# Signs the session cookie (see main.py's SessionMiddleware). The fallback
# below is DEV-ONLY -- it must never be relied on outside local development,
# since anyone who knows it could forge a valid session cookie. Set a real
# random value via the SESSION_SECRET_KEY env var for anything beyond that.
SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "dev-only-insecure-secret-change-me")
```

- [ ] **Step 2: Create the auth router**

Create `backend/app/routers/auth.py`:
```python
"""
POST /auth/login, POST /auth/logout, GET /auth/me

Session-based auth backed by the single-row `users` table (see
database.py's _seed_default_user). Session data lives entirely in
Starlette's signed cookie (see main.py's SessionMiddleware) -- this router
never touches a session store, it only reads/writes request.session.
"""

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import RATE_LIMIT_LOGIN
from app.database import get_connection
from app.limiter import limiter

router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
@limiter.limit(RATE_LIMIT_LOGIN)
def login(request: Request, req: LoginRequest):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT username, password_hash FROM users WHERE username=?",
            (req.username,),
        ).fetchone()

    valid = row is not None and bcrypt.checkpw(req.password.encode(), row["password_hash"].encode())
    if not valid:
        # Generic message regardless of whether the username or the password
        # was wrong -- never reveal which.
        raise HTTPException(status_code=401, detail="invalid username or password")

    request.session["username"] = row["username"]
    return {"username": row["username"]}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/me")
def me(request: Request):
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="not logged in")
    return {"username": username}
```

- [ ] **Step 3: Wire SessionMiddleware, the auth-gate middleware, the CORS flip, and the new router into `main.py`**

Current `backend/app/main.py`:
```python
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.cleanup import start_cleanup_scheduler
from app.config import ensure_storage_dirs, FRONTEND_ORIGINS
from app.convert import conversion_available
from app.database import init_db
from app.jobs import start_heartbeat, start_worker
from app.limiter import limiter
from app.routers.audio import router as audio_router
from app.routers.cancel import router as cancel_router
from app.routers.convert import router as convert_router
from app.routers.midi import router as midi_router
from app.routers.describe import router as describe_router
from app.routers.download import router as download_router
from app.routers.generate import router as generate_router
from app.routers.image import router as image_router
from app.routers.library import router as library_router
from app.routers.save import router as save_router
from app.routers.status import router as status_router
from app.routers.upload import router as upload_router

log = logging.getLogger(__name__)

# Ensure project root is on sys.path (needed by jobs.py and _preload_musicgen).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_storage_dirs()
    init_db()

    start_worker()
    start_heartbeat()
    start_cleanup_scheduler()

    if conversion_available():
        print("  [startup] PyAV found — audio format conversion enabled (mp3/flac/m4a/ogg).", flush=True)
        log.info("PyAV found — /download?format=<fmt> conversion enabled.")
    else:
        print("  [startup] WARNING: PyAV not found. /download will only serve WAV.", flush=True)
        log.warning("PyAV not importable — format conversion unavailable. Run: pip install av")

    print(f"  [startup] Backend ready. PID={os.getpid()} threads={threading.active_count()}", flush=True)
    print(f"  [startup] If generation is slow after a restart, open Task Manager and confirm", flush=True)
    print(f"  [startup] no OTHER python.exe processes are holding GPU memory (kill them first).", flush=True)
    log.info("Backend ready. PID=%d — MusicGen loads on first generation request.", os.getpid())

    yield


app = FastAPI(title="ImageSound API", lifespan=lifespan)

# CORS — use FRONTEND_ORIGINS from config; override at runtime via ALLOWED_ORIGINS env var.
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", ",".join(FRONTEND_ORIGINS)).split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(upload_router)
app.include_router(describe_router)
app.include_router(generate_router)
app.include_router(cancel_router)
app.include_router(image_router)
app.include_router(library_router)
app.include_router(save_router)
app.include_router(status_router)
app.include_router(audio_router)
app.include_router(download_router)
app.include_router(convert_router)
app.include_router(midi_router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

Replace with:
```python
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app.cleanup import start_cleanup_scheduler
from app.config import ensure_storage_dirs, FRONTEND_ORIGINS, SESSION_SECRET_KEY
from app.convert import conversion_available
from app.database import init_db
from app.jobs import start_heartbeat, start_worker
from app.limiter import limiter
from app.routers.audio import router as audio_router
from app.routers.auth import router as auth_router
from app.routers.cancel import router as cancel_router
from app.routers.convert import router as convert_router
from app.routers.midi import router as midi_router
from app.routers.describe import router as describe_router
from app.routers.download import router as download_router
from app.routers.generate import router as generate_router
from app.routers.image import router as image_router
from app.routers.library import router as library_router
from app.routers.save import router as save_router
from app.routers.status import router as status_router
from app.routers.upload import router as upload_router

log = logging.getLogger(__name__)

# Ensure project root is on sys.path (needed by jobs.py and _preload_musicgen).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_storage_dirs()
    init_db()

    start_worker()
    start_heartbeat()
    start_cleanup_scheduler()

    if conversion_available():
        print("  [startup] PyAV found — audio format conversion enabled (mp3/flac/m4a/ogg).", flush=True)
        log.info("PyAV found — /download?format=<fmt> conversion enabled.")
    else:
        print("  [startup] WARNING: PyAV not found. /download will only serve WAV.", flush=True)
        log.warning("PyAV not importable — format conversion unavailable. Run: pip install av")

    print(f"  [startup] Backend ready. PID={os.getpid()} threads={threading.active_count()}", flush=True)
    print(f"  [startup] If generation is slow after a restart, open Task Manager and confirm", flush=True)
    print(f"  [startup] no OTHER python.exe processes are holding GPU memory (kill them first).", flush=True)
    log.info("Backend ready. PID=%d — MusicGen loads on first generation request.", os.getpid())

    yield


app = FastAPI(title="ImageSound API", lifespan=lifespan)

# CORS — use FRONTEND_ORIGINS from config; override at runtime via ALLOWED_ORIGINS env var.
# allow_credentials=True is required so the browser sends the session cookie
# on cross-port requests from the frontend -- this is only valid because
# allow_origins is never "*" (the CORS spec forbids combining a wildcard
# origin with credentials).
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", ",".join(FRONTEND_ORIGINS)).split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Signed-cookie session (see config.py's SESSION_SECRET_KEY). same_site="lax"
# works correctly for cross-port localhost dev (cookies aren't port-scoped);
# secure=True is applied automatically by Starlette whenever the request
# arrives over HTTPS, so this needs no branching for local HTTP vs. future
# HTTPS deployment.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    max_age=30 * 24 * 3600,  # 30 days
)

# Endpoints reachable without a session. Deny-by-default: anything NOT
# listed here requires a valid session, including any future new endpoint
# someone adds later -- see the design spec's "Enforcement" section for why
# each of these specifically is public.
_PUBLIC_EXACT: set[tuple[str, str]] = {
    ("/health", "GET"),
    ("/auth/login", "POST"),
    ("/auth/logout", "POST"),
    ("/auth/me", "GET"),
}


def _is_public(request: Request) -> bool:
    if request.method == "OPTIONS":
        # CORS preflight must always succeed regardless of auth state, or
        # every cross-origin request from the frontend breaks before it's
        # even sent for real. This check does not depend on middleware
        # registration order elsewhere in this file.
        return True
    if (request.url.path, request.method) in _PUBLIC_EXACT:
        return True
    if request.method == "GET" and request.url.path == "/library":
        return True
    if request.method == "GET" and request.url.path.startswith("/image/"):
        return True
    return False


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    if _is_public(request):
        return await call_next(request)
    if not request.session.get("username"):
        return JSONResponse({"error": "not logged in"}, status_code=401)
    return await call_next(request)


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(describe_router)
app.include_router(generate_router)
app.include_router(cancel_router)
app.include_router(image_router)
app.include_router(library_router)
app.include_router(save_router)
app.include_router(status_router)
app.include_router(audio_router)
app.include_router(download_router)
app.include_router(convert_router)
app.include_router(midi_router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Verify live — restart the app and check every case with real requests**

Restart via `python run.py` (stop then start again if already running — see `run.py`'s own start/stop-switch behavior).

Public endpoints work with no cookie:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/library
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
```
Expected: both `200`.

Protected endpoint rejects with no cookie:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/status/nonexistent-id
```
Expected: `401` (not the endpoint's own 404 — the auth gate must reject it before the route handler ever runs).

Login with wrong password:
```bash
curl -s -w "\n%{http_code}\n" -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d "{\"username\":\"test\",\"password\":\"wrong\"}"
```
Expected: `401`, body `{"detail":"invalid username or password"}`, no `Set-Cookie` header (check with `-i` if you want to see headers).

Login with correct credentials, then use the cookie:
```bash
curl -s -c cookies.txt -w "\n%{http_code}\n" -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d "{\"username\":\"test\",\"password\":\"admin1234\"}"
curl -s -b cookies.txt -w "\n%{http_code}\n" http://127.0.0.1:8000/auth/me
curl -s -b cookies.txt -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/status/nonexistent-id
```
Expected: login `200` with `{"username":"test"}`; `/auth/me` `200` with the same; `/status/nonexistent-id` now returns `404` (the route handler's own not-found, proving the auth gate let it through and the previous 401 really was the gate, not something else).

Logout invalidates the session:
```bash
curl -s -b cookies.txt -c cookies.txt -X POST http://127.0.0.1:8000/auth/logout
curl -s -b cookies.txt -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/auth/me
```
Expected: logout `200`; `/auth/me` now `401` again.

**Real cross-origin browser check (not just curl)** — with the frontend dev server also running (`npm run dev`, port 3000), open the browser devtools console at `http://localhost:3000` and run:
```js
fetch('http://127.0.0.1:8000/auth/login', {
  method: 'POST', credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'test', password: 'admin1234' }),
}).then(r => r.json()).then(console.log)
```
Expected: no CORS error in the console, and the logged object is `{username: "test"}`. This is the check that actually proves the CORS-credentials flip and the OPTIONS-preflight handling work together in a real browser, not just in curl (which never sends preflight).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/auth.py backend/app/main.py backend/app/config.py backend/requirements.txt
git commit -m "Add session-based login with a public-endpoint allowlist"
```

Report every curl/fetch command's actual output alongside expected, plus confirmation the browser console check showed no CORS error.

---

### Task 3: Frontend `AuthContext` + `api.ts` changes

**Files:**
- Create: `src/context/AuthContext.tsx`
- Modify: `src/api.ts`

**Interfaces:**
- Consumes: `GET /auth/me`, `POST /auth/login`, `POST /auth/logout` from Task 2.
- Produces: `useAuth() -> { username: string | null, loading: boolean, login(username, password), logout() }` — consumed by every task from here on (4-8).

- [ ] **Step 1: Replace `src/api.ts` in full**

Current `src/api.ts` (197 lines) — read the live file first to confirm it hasn't changed since this plan was written, then replace its entire contents with:

```typescript
import { triggerDownload } from './utils/audioUtils';

export const API_BASE = (import.meta.env.VITE_API_BASE ?? 'http://localhost:8000').replace(/\/$/, '');

export function audioUrl(id: string): string {
  return `${API_BASE}/audio/${encodeURIComponent(id)}`;
}

export function imageUrl(id: string): string {
  return `${API_BASE}/image/${encodeURIComponent(id)}`;
}

export const DOWNLOAD_FORMATS = ['mp3', 'wav', 'flac', 'm4a', 'ogg'] as const;
export type DownloadFormat = typeof DOWNLOAD_FORMATS[number];

// Shared by downloadSong/downloadMidi: prompt-derived filename, falling back to
// the id's first 8 chars when there's no prompt to slugify.
function slugFilename(prompt: string | null | undefined, id: string): string {
  return prompt ? prompt.slice(0, 40).replace(/[^a-z0-9]+/gi, '_').toLowerCase() : id.slice(0, 8);
}

export async function downloadSong(id: string, prompt?: string | null, format: DownloadFormat = 'mp3'): Promise<void> {
  const dlUrl = `${API_BASE}/download/${encodeURIComponent(id)}?format=${format}`;
  const res = await fetch(dlUrl, { credentials: 'include' });
  if (!res.ok) throw new Error('Download failed.');
  const blob = await res.blob();
  triggerDownload(blob, `${slugFilename(prompt, id)}.${format}`);
}

export interface UploadResult {
  id: string;
  input_type: 'image' | 'audio';
  original_key: string;
}

export async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/upload`, { method: 'POST', credentials: 'include', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Upload failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Upload failed.');
  }
  return res.json() as Promise<UploadResult>;
}

// ---------- Generation ----------

export type JobStatus = 'queued' | 'processing' | 'done' | 'failed';

export interface GenerateResult {
  id: string;
  status: 'queued';
}

export interface StatusResult {
  id: string;
  input_type: 'image' | 'audio' | 'text';
  status: JobStatus;
  created_at: string;
  expires_at: string;
  converted_key?: string;
  prompt?: string;
  duration?: number;
  queue_depth?: number;
  progress?: number;
}

export type ArcPreset = 'steady' | 'gentle_build' | 'rise_and_settle' | 'calm_energetic';

export async function generateSong(opts: {
  id?: string;
  melody_source_id?: string;
  prompt?: string;
  duration?: number;
  model?: 'medium' | 'small';
  arc_preset?: ArcPreset;
  arc_segments?: number[];
  filter_mode?: 'raw' | 'filtered';
}): Promise<GenerateResult> {
  const res = await fetch(`${API_BASE}/generate`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Generate failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Generate failed.');
  }
  return res.json() as Promise<GenerateResult>;
}

export async function getStatus(id: string): Promise<StatusResult> {
  const res = await fetch(`${API_BASE}/status/${encodeURIComponent(id)}`, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Status check failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Status check failed.');
  }
  return res.json() as Promise<StatusResult>;
}

// ---------- Library ----------

export interface LibraryItem {
  id: string;
  input_type: 'image' | 'audio' | 'text' | 'midi';
  prompt: string | null;
  duration: number | null;
  saved: boolean;
  expires_at: string;
  created_at: string;
  output_format: string | null;    // 'midi' for MIDI entries, null for audio entries
  source_file_id: string | null;   // for MIDI entries: the audio entry this was derived from
  fad_verdict: 'satisfactory' | 'unsatisfactory' | null;  // quality signal; null = not scored (pre-feature song, MIDI entry, or scoring failed)
}

export async function getLibrary(): Promise<LibraryItem[]> {
  const res = await fetch(`${API_BASE}/library`, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to load library.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Failed to load library.');
  }
  return res.json() as Promise<LibraryItem[]>;
}

// ---------- Cancel ----------

export async function cancelJob(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/cancel/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok && res.status !== 409) {
    // 409 = already in terminal state (failed/cancelled) — treat as no-op.
    const err = await res.json().catch(() => ({ detail: 'Cancel failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Cancel failed.');
  }
}

// ---------- Save / Discard ----------

export async function saveJob(id: string): Promise<{ id: string; saved: boolean; expires_at: string }> {
  const res = await fetch(`${API_BASE}/save/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Save failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Save failed.');
  }
  return res.json() as Promise<{ id: string; saved: boolean; expires_at: string }>;
}

export async function discardJob(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/discard/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Discard failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Discard failed.');
  }
}

// ---------- MIDI ----------

export async function convertToMidi(sourceId: string): Promise<{ id: string; status: string }> {
  const res = await fetch(`${API_BASE}/midi/convert/${encodeURIComponent(sourceId)}`, {
    method: 'POST',
    credentials: 'include',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'MIDI conversion failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'MIDI conversion failed.');
  }
  return res.json() as Promise<{ id: string; status: string }>;
}

export function midiPreviewUrl(id: string): string {
  return `${API_BASE}/midi/preview/${encodeURIComponent(id)}`;
}

export async function downloadMidi(id: string, prompt?: string | null): Promise<void> {
  const dlUrl = `${API_BASE}/download/${encodeURIComponent(id)}?format=midi`;
  const res = await fetch(dlUrl, { credentials: 'include' });
  if (!res.ok) throw new Error('MIDI download failed.');
  const blob = await res.blob();
  triggerDownload(blob, `${slugFilename(prompt, id)}.mid`);
}

// ---------- Describe ----------

export async function describeImage(id: string, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`${API_BASE}/describe`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Describe failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Describe failed.');
  }
  const data = (await res.json()) as { prompt: string };
  return data.prompt;
}

// ---------- Auth ----------

export interface MeResult {
  username: string;
}

export async function getMe(): Promise<MeResult> {
  const res = await fetch(`${API_BASE}/auth/me`, { credentials: 'include' });
  if (!res.ok) throw new Error('Not logged in.');
  return res.json() as Promise<MeResult>;
}

export async function loginRequest(username: string, password: string): Promise<MeResult> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Invalid username or password.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Invalid username or password.');
  }
  return res.json() as Promise<MeResult>;
}

export async function logoutRequest(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
}
```

(Every existing `fetch()` call gained `credentials: 'include'`; three new functions — `getMe`, `loginRequest`, `logoutRequest` — were added at the end. Nothing else changed: same exports, same types, same error-handling shape.)

- [ ] **Step 2: Create `src/context/AuthContext.tsx`**

```tsx
import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react';
import { getMe, loginRequest, logoutRequest } from '../api';

interface AuthContextValue {
  username: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then(res => setUsername(res.username))
      .catch(() => setUsername(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (u: string, p: string) => {
    const res = await loginRequest(u, p);
    setUsername(res.username);
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest();
    setUsername(null);
  }, []);

  return (
    <AuthContext.Provider value={{ username, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
```

- [ ] **Step 3: Verify**

```bash
npx tsc --noEmit
```
Expected: no errors (this task adds no consumers of `AuthProvider`/`useAuth` yet — that's Tasks 4-8 — so this step is a type-check only; there's nothing to browser-test until the provider is actually mounted).

- [ ] **Step 4: Commit**

```bash
git add src/api.ts src/context/AuthContext.tsx
git commit -m "Add AuthContext and credentialed fetch calls"
```

---

### Task 4: Login page, `/login` route, mount `AuthProvider`

**Files:**
- Create: `src/pages/Login.tsx`
- Modify: `src/App.tsx`

**Interfaces:**
- Consumes: `useAuth()` from Task 3.
- Produces: a reachable `/login` page that calls `login()` and, on success, navigates back to wherever the visitor came from (or `/` by default) — consumed by Task 5's nav link and by anyone redirected here manually.

- [ ] **Step 1: Create `src/pages/Login.tsx`**

This uses the same visual conventions already established elsewhere in this codebase (`brutal-input`/`brutal-btn` classes, CSS custom properties like `var(--accent)`, bordered `data-collider` boxes, `// SECTION` header style — see `src/pages/Home.tsx` for the exact same patterns). This is a functional baseline; **after wiring it up, invoke the Hallmark design skill on this file specifically** to give it a proper pass matching the app's "Brutalist Cyberpunk" system, per the design spec's "Visual design" section — don't skip that step, this raw version is intentionally plain.

```tsx
import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Lock, AlertCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? '/';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="container mx-auto px-4 md:px-8 py-16 flex-grow flex items-start justify-center">
      <div
        data-collider
        className="border-4 p-8 md:p-10 w-full max-w-md flex flex-col gap-6"
        style={{ borderColor: 'var(--accent)', backgroundColor: 'transparent' }}
      >
        <div className="flex items-center gap-3" style={{ color: 'var(--accent)' }}>
          <Lock className="w-8 h-8" strokeWidth={1} />
          <h2 className="text-xl font-bold uppercase tracking-widest">// SYS_LOGIN</h2>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs uppercase tracking-widest font-bold" style={{ color: 'var(--accent)' }}>
              Username
            </label>
            <input
              type="text"
              className="brutal-input w-full font-mono text-sm"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus
              autoComplete="username"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs uppercase tracking-widest font-bold" style={{ color: 'var(--accent)' }}>
              Password
            </label>
            <input
              type="password"
              className="brutal-input w-full font-mono text-sm"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>

          {error && (
            <div
              className="flex items-start gap-3 border-2 p-3"
              style={{
                borderColor: 'var(--accent-secondary)',
                color: 'var(--accent-secondary)',
                backgroundColor: 'color-mix(in oklch, var(--accent-secondary) 8%, transparent)',
              }}
            >
              <AlertCircle size={18} className="shrink-0 mt-0.5" />
              <p className="text-xs uppercase tracking-wide">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || !username || !password}
            className="brutal-btn flex items-center justify-center gap-2 disabled:opacity-30"
          >
            {submitting ? 'LOGGING IN...' : 'LOGIN'}
          </button>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Mount `AuthProvider` and add the `/login` route**

Current `src/App.tsx`:
```tsx
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { Layout } from './components/Layout';
import { Navigation } from './components/Navigation';
import { Home } from './pages/Home';
import { Player } from './pages/Player';
import { Library } from './pages/Library';
import StressBall from './components/StressBall';
import { InProgressProvider } from './context/InProgressContext';

export default function App() {
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('theme') || 'dark';
  });

  useEffect(() => {
    document.body.className = theme;
    localStorage.setItem('theme', theme);
  }, [theme]);

  return (
    <InProgressProvider>
      <Router>
        <Layout>
          <Navigation theme={theme} setTheme={setTheme} />
          <main className="flex-grow flex flex-col z-10 w-full relative">
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/player" element={<Player />} />
              <Route path="/library" element={<Library />} />
            </Routes>
          </main>
          <StressBall />
        </Layout>
      </Router>
    </InProgressProvider>
  );
}
```

Replace with:
```tsx
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { Layout } from './components/Layout';
import { Navigation } from './components/Navigation';
import { Home } from './pages/Home';
import { Player } from './pages/Player';
import { Library } from './pages/Library';
import { Login } from './pages/Login';
import StressBall from './components/StressBall';
import { InProgressProvider } from './context/InProgressContext';
import { AuthProvider } from './context/AuthContext';

export default function App() {
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('theme') || 'dark';
  });

  useEffect(() => {
    document.body.className = theme;
    localStorage.setItem('theme', theme);
  }, [theme]);

  return (
    <AuthProvider>
      <InProgressProvider>
        <Router>
          <Layout>
            <Navigation theme={theme} setTheme={setTheme} />
            <main className="flex-grow flex flex-col z-10 w-full relative">
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/player" element={<Player />} />
                <Route path="/library" element={<Library />} />
                <Route path="/login" element={<Login />} />
              </Routes>
            </main>
            <StressBall />
          </Layout>
        </Router>
      </InProgressProvider>
    </AuthProvider>
  );
}
```

- [ ] **Step 3: Invoke Hallmark on `src/pages/Login.tsx`**

Run the Hallmark design skill scoped to this one file (component-scope, not a full page redesign — the surrounding app's tokens/genre already exist and must be preserved, not reinvented). Confirm it still compiles (`npx tsc --noEmit`) and still calls `login()`/navigates on submit exactly as before — Hallmark should only touch markup/styling, not the auth logic.

- [ ] **Step 4: Verify live**

Start both servers (`python run.py`). In a real browser:
- Navigate to `http://localhost:3000/login` directly — confirm it renders (proving the route works without needing to come from a redirect, since nothing redirects here in this design).
- Submit wrong credentials — confirm the inline error appears with the backend's generic message.
- Submit `test`/`admin1234` — confirm it navigates away and no error is shown.
- Reload the page while logged in and confirm no error/flash (the `AuthProvider`'s initial `GET /auth/me` should resolve to logged-in state).

Take screenshots of: the empty login form, the error state, and the moment right after a successful login (whatever page it lands on).

- [ ] **Step 5: Commit**

```bash
git add src/pages/Login.tsx src/App.tsx
git commit -m "Add Login page and mount AuthProvider"
```

---

### Task 5: Navigation login/logout indicator

**Files:**
- Modify: `src/components/Navigation.tsx`

**Interfaces:**
- Consumes: `useAuth()` from Task 3.

- [ ] **Step 1: Add the persistent indicator**

Current `src/components/Navigation.tsx`:
```tsx
import { Link, useLocation } from 'react-router-dom';
import { Sun, Moon } from 'lucide-react';
import React from 'react';

const ROUTES = [
  { path: '/', flag: 'upload' },
  { path: '/player', flag: 'studio' },
  { path: '/library', flag: 'library' },
] as const;

// N8 Terminal-command nav — routes read as CLI flags on a single prompt line,
// with a blinking caret at the end. See design.md § Nav.
export function Navigation({ theme, setTheme }: { theme: string, setTheme: React.Dispatch<React.SetStateAction<string>> }) {
  const location = useLocation();
  const toggleTheme = () => setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));

  return (
    <header
      className="border-b-2 px-4 md:px-8 py-4 sticky top-0 z-50"
      style={{ backgroundColor: 'var(--bg)', borderBottomColor: 'var(--border-muted)' }}
    >
      <pre className="m-0 font-mono flex flex-wrap items-baseline gap-x-3 gap-y-1.5 text-sm md:text-base whitespace-pre-wrap">
        <span aria-hidden="true" style={{ color: 'var(--accent)' }}>{'>'}</span>

        <Link
          to="/"
          className="glitch font-bold tracking-widest uppercase"
          data-text="imagesound"
          style={{ color: 'var(--text-heading)' }}
        >
          imagesound
        </Link>

        {ROUTES.map(r => {
          const active = location.pathname === r.path;
          return (
            <Link
              key={r.path}
              to={r.path}
              style={{
                color: active ? 'var(--accent)' : 'var(--text-muted)',
                textDecoration: active ? 'underline' : 'none',
                textUnderlineOffset: '3px',
                transition: 'color var(--dur-micro) var(--ease-out)',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              --{r.flag}
            </Link>
          );
        })}

        <button
          onClick={toggleTheme}
          className="inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
          style={{ color: 'var(--text-muted)', transition: 'color var(--dur-micro) var(--ease-out)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          title="Toggle theme"
        >
          --theme:{theme}
          {theme === 'light' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        <span className="nav-term__caret" aria-hidden="true" style={{ color: 'var(--accent)' }}>▮</span>
      </pre>
    </header>
  );
}
```

Replace with:
```tsx
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Sun, Moon, LogIn, LogOut } from 'lucide-react';
import React from 'react';
import { useAuth } from '../context/AuthContext';

const ROUTES = [
  { path: '/', flag: 'upload' },
  { path: '/player', flag: 'studio' },
  { path: '/library', flag: 'library' },
] as const;

// N8 Terminal-command nav — routes read as CLI flags on a single prompt line,
// with a blinking caret at the end. See design.md § Nav.
export function Navigation({ theme, setTheme }: { theme: string, setTheme: React.Dispatch<React.SetStateAction<string>> }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { username, logout } = useAuth();
  const toggleTheme = () => setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <header
      className="border-b-2 px-4 md:px-8 py-4 sticky top-0 z-50"
      style={{ backgroundColor: 'var(--bg)', borderBottomColor: 'var(--border-muted)' }}
    >
      <pre className="m-0 font-mono flex flex-wrap items-baseline gap-x-3 gap-y-1.5 text-sm md:text-base whitespace-pre-wrap">
        <span aria-hidden="true" style={{ color: 'var(--accent)' }}>{'>'}</span>

        <Link
          to="/"
          className="glitch font-bold tracking-widest uppercase"
          data-text="imagesound"
          style={{ color: 'var(--text-heading)' }}
        >
          imagesound
        </Link>

        {ROUTES.map(r => {
          const active = location.pathname === r.path;
          return (
            <Link
              key={r.path}
              to={r.path}
              style={{
                color: active ? 'var(--accent)' : 'var(--text-muted)',
                textDecoration: active ? 'underline' : 'none',
                textUnderlineOffset: '3px',
                transition: 'color var(--dur-micro) var(--ease-out)',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              --{r.flag}
            </Link>
          );
        })}

        <button
          onClick={toggleTheme}
          className="inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
          style={{ color: 'var(--text-muted)', transition: 'color var(--dur-micro) var(--ease-out)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          title="Toggle theme"
        >
          --theme:{theme}
          {theme === 'light' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        {username ? (
          <button
            onClick={handleLogout}
            className="inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
            style={{ color: 'var(--text-muted)', transition: 'color var(--dur-micro) var(--ease-out)' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            title="Log out"
          >
            --user:{username}
            <LogOut size={14} />
          </button>
        ) : (
          <Link
            to="/login"
            className="inline-flex items-center gap-1"
            style={{ color: 'var(--accent-secondary)', textDecoration: 'none' }}
            onMouseEnter={e => { e.currentTarget.style.textDecoration = 'underline'; }}
            onMouseLeave={e => { e.currentTarget.style.textDecoration = 'none'; }}
            title="Log in"
          >
            --login
            <LogIn size={14} />
          </Link>
        )}

        <span className="nav-term__caret" aria-hidden="true" style={{ color: 'var(--accent)' }}>▮</span>
      </pre>
    </header>
  );
}
```

- [ ] **Step 2: Verify live**

With both servers running: confirm `--login` shows in the nav when logged out (every page, since `Navigation` is mounted once in `App.tsx` above the routed `<main>`); log in via `/login`; confirm the nav now shows `--user:test` instead; click it, confirm it logs out and navigates to `/login`, and the nav reverts to `--login`. Screenshot both states.

- [ ] **Step 3: Commit**

```bash
git add src/components/Navigation.tsx
git commit -m "Add login/logout indicator to Navigation"
```

---

### Task 6: Home page — disable inputs when logged out

**Files:**
- Modify: `src/pages/Home.tsx`

**Interfaces:**
- Consumes: `useAuth()` from Task 3.

- [ ] **Step 1: Add the auth check**

Add the import and the derived flag near the top of the component:

Current (`src/pages/Home.tsx:1-15`):
```tsx
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, Image as ImageIcon, Music, AlertCircle, AlertTriangle, Type } from 'lucide-react';
import { uploadFile } from '../api';
import { useInProgress } from '../context/InProgressContext';
import { Waveform } from '../components/Waveform';

export function Home() {
  const navigate = useNavigate();
  const { item, setItem, clearItem } = useInProgress();
  const [dragActive, setDragActive] = useState(false);
  const [inputType, setInputType] = useState<'image' | 'audio' | 'text'>('image');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [textInput, setTextInput] = useState('');
```

Replace with:
```tsx
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, Image as ImageIcon, Music, AlertCircle, AlertTriangle, Type } from 'lucide-react';
import { uploadFile } from '../api';
import { useInProgress } from '../context/InProgressContext';
import { useAuth } from '../context/AuthContext';
import { Waveform } from '../components/Waveform';

export function Home() {
  const navigate = useNavigate();
  const { item, setItem, clearItem } = useInProgress();
  const { username } = useAuth();
  const loggedIn = !!username;
  const [dragActive, setDragActive] = useState(false);
  const [inputType, setInputType] = useState<'image' | 'audio' | 'text'>('image');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [textInput, setTextInput] = useState('');
```

- [ ] **Step 2: Disable the text prompt textarea and submit button**

Current (`src/pages/Home.tsx`, inside the `{inputType === 'text' && (...)}` block):
```tsx
              <textarea
                className="brutal-input w-full font-mono text-sm resize-none"
                rows={4}
                maxLength={300}
                value={textInput}
                onChange={e => setTextInput(e.target.value)}
                onKeyDown={handleTextKeyDown}
                placeholder="Describe the music you want… e.g. 'slow ambient piano, rainy and calm'"
                autoFocus
              />

              <div className="flex items-center justify-between w-full gap-2">
                <span className="text-xs font-mono uppercase opacity-40" style={{ color: 'var(--accent)' }}>
                  {textInput.length}/300 · Enter=submit · Shift+Enter=newline
                </span>
                <button
                  onClick={handleTextSubmit}
                  disabled={!textInput.trim()}
                  className="brutal-btn flex items-center gap-2 disabled:opacity-30"
                >
                  PROCEED TO STUDIO
                </button>
              </div>
```

Replace with:
```tsx
              <textarea
                className="brutal-input w-full font-mono text-sm resize-none disabled:opacity-40"
                rows={4}
                maxLength={300}
                value={textInput}
                onChange={e => setTextInput(e.target.value)}
                onKeyDown={handleTextKeyDown}
                placeholder="Describe the music you want… e.g. 'slow ambient piano, rainy and calm'"
                autoFocus
                disabled={!loggedIn}
                title={!loggedIn ? 'Login required' : undefined}
              />

              <div className="flex items-center justify-between w-full gap-2">
                <span className="text-xs font-mono uppercase opacity-40" style={{ color: 'var(--accent)' }}>
                  {textInput.length}/300 · Enter=submit · Shift+Enter=newline
                </span>
                <button
                  onClick={handleTextSubmit}
                  disabled={!textInput.trim() || !loggedIn}
                  className="brutal-btn flex items-center gap-2 disabled:opacity-30"
                  title={!loggedIn ? 'Login required' : undefined}
                >
                  PROCEED TO STUDIO
                </button>
              </div>
```

- [ ] **Step 3: Disable the drag & drop zone**

Current (`src/pages/Home.tsx`, the drag & drop zone block):
```tsx
            <div
              data-collider
              className="relative border-4 border-dashed p-10 md:p-12 flex flex-col items-start justify-center text-left transition-colors duration-200"
              style={{
                borderColor: dragActive ? 'var(--accent-secondary)' : 'var(--accent)',
                backgroundColor: dragActive ? 'var(--bg-card)' : 'transparent',
                opacity: uploading ? 0.6 : 1,
                pointerEvents: uploading ? 'none' : 'auto',
              }}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
            >
              <input
                type="file"
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
                onChange={handleFileInput}
                accept={inputType === 'image' ? 'image/*' : 'audio/*'}
                disabled={uploading}
              />
```

Replace with:
```tsx
            <div
              data-collider
              className="relative border-4 border-dashed p-10 md:p-12 flex flex-col items-start justify-center text-left transition-colors duration-200"
              style={{
                borderColor: dragActive ? 'var(--accent-secondary)' : 'var(--accent)',
                backgroundColor: dragActive ? 'var(--bg-card)' : 'transparent',
                opacity: (uploading || !loggedIn) ? 0.6 : 1,
                pointerEvents: (uploading || !loggedIn) ? 'none' : 'auto',
              }}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              title={!loggedIn ? 'Login required' : undefined}
            >
              <input
                type="file"
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
                onChange={handleFileInput}
                accept={inputType === 'image' ? 'image/*' : 'audio/*'}
                disabled={uploading || !loggedIn}
              />
```

- [ ] **Step 4: Verify live**

Logged out: confirm the textarea and PROCEED TO STUDIO button are visibly grayed out and don't accept input/clicks (hovering shows the "Login required" tooltip); confirm the drop zone is similarly non-interactive. Log in, reload, confirm both are fully interactive again and a real text-prompt submission still navigates to `/player` as before. Screenshot both states.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Home.tsx
git commit -m "Disable Home page inputs when logged out"
```

---

### Task 7: Library page — disable all actions when logged out

**Files:**
- Modify: `src/pages/Library.tsx`

**Interfaces:**
- Consumes: `useAuth()` from Task 3.

**Scope note:** the design spec's wording only called out "Play and Download," but the live file has more actions per card than that (Play, Remix, format-select, Download/Convert, Save, Discard, plus the MIDI variants of Play/Download). All of them call backend endpoints that Task 2 protects, so all of them must be disabled here for consistency — otherwise a logged-out click would just fail with an unhandled 401. This is a direct, mechanical extension of the same approved design intent, not a new decision.

- [ ] **Step 1: Add the auth check**

Current (`src/pages/Library.tsx:29-34`):
```tsx
export function Library() {
  const navigate = useNavigate();
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [downloadFormats, setDownloadFormats] = useState<Record<string, DownloadFormat | 'midi'>>({});
```

Replace with:
```tsx
export function Library() {
  const navigate = useNavigate();
  const { username } = useAuth();
  const loggedIn = !!username;
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [downloadFormats, setDownloadFormats] = useState<Record<string, DownloadFormat | 'midi'>>({});
```

And add the import at the top (`src/pages/Library.tsx:1-4`):

Current:
```tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Trash2, Music, Database, Play, Pause, Download, Save, FileMusic, Shuffle, Check, AlertTriangle } from 'lucide-react';
import { getLibrary, discardJob, saveJob, audioUrl, imageUrl, downloadSong, convertToMidi, downloadMidi, midiPreviewUrl, LibraryItem, DOWNLOAD_FORMATS, DownloadFormat } from '../api';
```

Replace with:
```tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Trash2, Music, Database, Play, Pause, Download, Save, FileMusic, Shuffle, Check, AlertTriangle } from 'lucide-react';
import { getLibrary, discardJob, saveJob, audioUrl, imageUrl, downloadSong, convertToMidi, downloadMidi, midiPreviewUrl, LibraryItem, DOWNLOAD_FORMATS, DownloadFormat } from '../api';
import { useAuth } from '../context/AuthContext';
```

- [ ] **Step 2: Disable the MIDI-entry action buttons**

Current (`src/pages/Library.tsx`, the MIDI entry actions block):
```tsx
                    <>
                      {/* Play sonified WAV preview */}
                      <button
                        onClick={() => handlePlay(item.id, midiPreviewUrl(item.id))}
                        title={isThisPlaying && isAudioPlaying ? 'Pause MIDI preview' : 'Play MIDI preview (synth rendering)'}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        {isThisPlaying && isAudioPlaying ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying ? 'PAUSE' : 'PLAY'}
                      </button>
                      {/* Download MIDI */}
                      <button
                        onClick={(e) => handleDownloadMidi(item.id, item.prompt, e)}
                        title="Download MIDI file"
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                        style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)' }}
                        onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent-tertiary)'; e.currentTarget.style.color = 'var(--bg)'; }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-tertiary)'; }}
                      >
                        <Download size={12} /> MIDI
                      </button>
```

Replace with:
```tsx
                    <>
                      {/* Play sonified WAV preview */}
                      <button
                        onClick={() => handlePlay(item.id, midiPreviewUrl(item.id))}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : (isThisPlaying && isAudioPlaying ? 'Pause MIDI preview' : 'Play MIDI preview (synth rendering)')}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        {isThisPlaying && isAudioPlaying ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying ? 'PAUSE' : 'PLAY'}
                      </button>
                      {/* Download MIDI */}
                      <button
                        onClick={(e) => handleDownloadMidi(item.id, item.prompt, e)}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : 'Download MIDI file'}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent-tertiary)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-tertiary)'; }}
                      >
                        <Download size={12} /> MIDI
                      </button>
```

- [ ] **Step 3: Disable the audio-entry action buttons (Play, Remix, format select, Download/Convert)**

Current (`src/pages/Library.tsx`, the audio entry actions block):
```tsx
                    <>
                      {/* Play / Pause */}
                      <button
                        onClick={() => handlePlay(item.id, audioUrl(item.id))}
                        title={isThisPlaying && isAudioPlaying ? 'Pause' : 'Play'}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        {isThisPlaying && isAudioPlaying ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying ? 'PAUSE' : 'PLAY'}
                      </button>

                      {/* Remix: use this song's audio as a melody reference for a new generation */}
                      <button
                        onClick={(e) => handleRemix(item.id, item.prompt, e)}
                        title="Use as melody reference for a new song"
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        <Shuffle size={12} />
                        REMIX
                      </button>

                      {/* Format selector: audio formats then MIDI */}
                      {(() => {
                        const selectedFmt = downloadFormats[item.id] ?? 'mp3';
                        const isMidiSelected = selectedFmt === 'midi';
                        const btnColor = isMidiSelected
                          ? (midiState === 'failed' ? 'red' : 'var(--accent)')
                          : 'var(--accent-tertiary)';
                        return (
                          <>
                            <select
                              value={selectedFmt}
                              onChange={e => {
                                e.stopPropagation();
                                setDownloadFormats(prev => ({ ...prev, [item.id]: e.target.value as DownloadFormat | 'midi' }));
                              }}
                              className="border text-xs font-bold font-mono uppercase px-1 py-1 cursor-pointer"
                              style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)', backgroundColor: 'var(--bg)', outline: 'none' }}
                              title="Export format"
                            >
                              {DOWNLOAD_FORMATS.map(f => (
                                <option key={f} value={f}>{f.toUpperCase()}</option>
                              ))}
                              <option disabled>──────</option>
                              <option value="midi">MIDI</option>
                            </select>

                            {/* Single action button: Download (audio) or Convert (MIDI) */}
                            <button
                              onClick={(e) => isMidiSelected ? handleConvertToMidi(item.id, e) : handleDownload(item.id, item.prompt, e)}
                              disabled={isMidiSelected && midiState === 'converting'}
                              title={
                                isMidiSelected
                                  ? (midiState === 'failed' ? 'MIDI conversion failed — retry' : 'Convert to MIDI')
                                  : `Download ${selectedFmt.toUpperCase()}`
                              }
                              className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                              style={{
                                borderColor: btnColor,
                                color: btnColor,
                                opacity: isMidiSelected && midiState === 'converting' ? 0.5 : 1,
                                cursor: isMidiSelected && midiState === 'converting' ? 'not-allowed' : 'pointer',
                              }}
                              onMouseEnter={e => {
                                if (!(isMidiSelected && midiState === 'converting')) {
                                  e.currentTarget.style.backgroundColor = btnColor;
                                  e.currentTarget.style.color = 'var(--bg)';
                                }
                              }}
                              onMouseLeave={e => {
                                e.currentTarget.style.backgroundColor = 'transparent';
                                e.currentTarget.style.color = btnColor;
                              }}
                            >
                              {isMidiSelected ? <FileMusic size={12} /> : <Download size={12} />}
                              {isMidiSelected
                                ? (midiState === 'converting' ? 'CONVERTING…' : midiState === 'failed' ? 'CONVERT ✗' : 'CONVERT')
                                : 'DOWNLOAD'}
                            </button>
                          </>
                        );
                      })()}
                    </>
```

Replace with:
```tsx
                    <>
                      {/* Play / Pause */}
                      <button
                        onClick={() => handlePlay(item.id, audioUrl(item.id))}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : (isThisPlaying && isAudioPlaying ? 'Pause' : 'Play')}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        {isThisPlaying && isAudioPlaying ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying ? 'PAUSE' : 'PLAY'}
                      </button>

                      {/* Remix: use this song's audio as a melody reference for a new generation */}
                      <button
                        onClick={(e) => handleRemix(item.id, item.prompt, e)}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : 'Use as melody reference for a new song'}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        <Shuffle size={12} />
                        REMIX
                      </button>

                      {/* Format selector: audio formats then MIDI */}
                      {(() => {
                        const selectedFmt = downloadFormats[item.id] ?? 'mp3';
                        const isMidiSelected = selectedFmt === 'midi';
                        const btnColor = isMidiSelected
                          ? (midiState === 'failed' ? 'red' : 'var(--accent)')
                          : 'var(--accent-tertiary)';
                        return (
                          <>
                            <select
                              value={selectedFmt}
                              onChange={e => {
                                e.stopPropagation();
                                setDownloadFormats(prev => ({ ...prev, [item.id]: e.target.value as DownloadFormat | 'midi' }));
                              }}
                              disabled={!loggedIn}
                              className="border text-xs font-bold font-mono uppercase px-1 py-1 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                              style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)', backgroundColor: 'var(--bg)', outline: 'none' }}
                              title={!loggedIn ? 'Login required' : 'Export format'}
                            >
                              {DOWNLOAD_FORMATS.map(f => (
                                <option key={f} value={f}>{f.toUpperCase()}</option>
                              ))}
                              <option disabled>──────</option>
                              <option value="midi">MIDI</option>
                            </select>

                            {/* Single action button: Download (audio) or Convert (MIDI) */}
                            <button
                              onClick={(e) => isMidiSelected ? handleConvertToMidi(item.id, e) : handleDownload(item.id, item.prompt, e)}
                              disabled={!loggedIn || (isMidiSelected && midiState === 'converting')}
                              title={
                                !loggedIn
                                  ? 'Login required'
                                  : isMidiSelected
                                    ? (midiState === 'failed' ? 'MIDI conversion failed — retry' : 'Convert to MIDI')
                                    : `Download ${selectedFmt.toUpperCase()}`
                              }
                              className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                              style={{
                                borderColor: btnColor,
                                color: btnColor,
                                opacity: !loggedIn || (isMidiSelected && midiState === 'converting') ? 0.5 : 1,
                                cursor: !loggedIn || (isMidiSelected && midiState === 'converting') ? 'not-allowed' : 'pointer',
                              }}
                              onMouseEnter={e => {
                                if (loggedIn && !(isMidiSelected && midiState === 'converting')) {
                                  e.currentTarget.style.backgroundColor = btnColor;
                                  e.currentTarget.style.color = 'var(--bg)';
                                }
                              }}
                              onMouseLeave={e => {
                                e.currentTarget.style.backgroundColor = 'transparent';
                                e.currentTarget.style.color = btnColor;
                              }}
                            >
                              {isMidiSelected ? <FileMusic size={12} /> : <Download size={12} />}
                              {isMidiSelected
                                ? (midiState === 'converting' ? 'CONVERTING…' : midiState === 'failed' ? 'CONVERT ✗' : 'CONVERT')
                                : 'DOWNLOAD'}
                            </button>
                          </>
                        );
                      })()}
                    </>
```

- [ ] **Step 4: Disable Save and Discard**

Current (`src/pages/Library.tsx`, the footer's Save/Purge buttons):
```tsx
                  {/* Save (unsaved only) */}
                  {!item.saved && (
                    <button
                      onClick={(e) => handleSave(item.id, e)}
                      title="Save to library"
                      className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                      style={{ borderColor: 'var(--accent-secondary)', color: 'var(--accent-secondary)' }}
                      onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent-secondary)'; e.currentTarget.style.color = 'var(--bg)'; }}
                      onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-secondary)'; }}
                    >
                      <Save size={12} /> SAVE
                    </button>
                  )}

                  {/* Purge / Discard */}
                  <button
                    onClick={(e) => handleDiscard(item.id, e)}
                    title="Delete"
                    className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                    style={{ borderColor: 'red', color: 'red' }}
                    onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'red'; e.currentTarget.style.color = 'var(--bg)'; }}
                    onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'red'; }}
                  >
                    <Trash2 size={12} /> PURGE
                  </button>
```

Replace with:
```tsx
                  {/* Save (unsaved only) */}
                  {!item.saved && (
                    <button
                      onClick={(e) => handleSave(item.id, e)}
                      disabled={!loggedIn}
                      title={!loggedIn ? 'Login required' : 'Save to library'}
                      className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                      style={{ borderColor: 'var(--accent-secondary)', color: 'var(--accent-secondary)' }}
                      onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent-secondary)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                      onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-secondary)'; }}
                    >
                      <Save size={12} /> SAVE
                    </button>
                  )}

                  {/* Purge / Discard */}
                  <button
                    onClick={(e) => handleDiscard(item.id, e)}
                    disabled={!loggedIn}
                    title={!loggedIn ? 'Login required' : 'Delete'}
                    className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                    style={{ borderColor: 'red', color: 'red' }}
                    onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'red'; e.currentTarget.style.color = 'var(--bg)'; } }}
                    onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'red'; }}
                  >
                    <Trash2 size={12} /> PURGE
                  </button>
```

- [ ] **Step 5: Verify live**

Logged out: confirm every action button on every card (Play, Remix, format select, Download/Convert, Save, Discard, and the MIDI variants) is visibly grayed out, unclickable, and shows a "Login required" tooltip on hover — while the cards themselves (title, duration, thumbnail, `fad_verdict` badge) still render normally. Log in, reload, confirm every button is fully functional again (play a song, save one, discard one). Screenshot both states.

- [ ] **Step 6: Commit**

```bash
git add src/pages/Library.tsx
git commit -m "Disable Library card actions when logged out"
```

---

### Task 8: Player page — opaque overlay when logged out

**Files:**
- Modify: `src/pages/Player.tsx`

**Interfaces:**
- Consumes: `useAuth()` from Task 3.

- [ ] **Step 1: Add the import and auth check**

Current (`src/pages/Player.tsx:1-12`):
```tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { Play, Pause, FastForward, Rewind, AlertTriangle } from 'lucide-react';
import { describeImage, saveJob, discardJob, audioUrl, downloadSong, DownloadFormat } from '../api';
import { usePromptHistory } from '../hooks/usePromptHistory';
import { useGeneration } from '../hooks/useGeneration';
import { useInProgress } from '../context/InProgressContext';
import { useAudioEffects } from '../hooks/useAudioEffects';
import { SourcePreview } from '../components/player/SourcePreview';
import { ArcEditor, ARC_PRESETS, samplePreset } from '../components/player/ArcEditor';
import { EffectsPanel } from '../components/player/EffectsPanel';
import { GeneratePanel } from '../components/player/GeneratePanel';
```

Replace with:
```tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { Play, Pause, FastForward, Rewind, AlertTriangle, Lock } from 'lucide-react';
import { describeImage, saveJob, discardJob, audioUrl, downloadSong, DownloadFormat } from '../api';
import { usePromptHistory } from '../hooks/usePromptHistory';
import { useGeneration } from '../hooks/useGeneration';
import { useInProgress } from '../context/InProgressContext';
import { useAuth } from '../context/AuthContext';
import { useAudioEffects } from '../hooks/useAudioEffects';
import { SourcePreview } from '../components/player/SourcePreview';
import { ArcEditor, ARC_PRESETS, samplePreset } from '../components/player/ArcEditor';
import { EffectsPanel } from '../components/player/EffectsPanel';
import { GeneratePanel } from '../components/player/GeneratePanel';
```

Current (`src/pages/Player.tsx:41`):
```tsx
  const { item, setItem, updatePrompt, clearItem } = useInProgress();
```

Replace with:
```tsx
  const { item, setItem, updatePrompt, clearItem } = useInProgress();
  const { username } = useAuth();
```

- [ ] **Step 2: Wrap the functional grid in a `relative` container and add the overlay**

Current (`src/pages/Player.tsx`, right before the closing of the component's render, the grid wrapper):
```tsx
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

        {/* LEFT COL: ORIGINAL SOURCE */}
        <SourcePreview
          filename={filename}
          imageUrl={url}
          rawUrl={source?.url ?? null}
          fileId={fileId}
          isImage={isImage}
          isText={isText}
          isAudio={isAudio}
          mode={mode}
          textPreview={textPreview}
        />

        {/* RIGHT COL: VISUALIZER & CONTROLS */}
        <div className="col-span-1 lg:col-span-2 flex flex-col gap-8">
```

Replace with:
```tsx
      <div className="relative">
        {!username && (
          <div
            className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-4 text-center px-6"
            style={{ backgroundColor: 'rgba(0, 0, 0, 0.88)' }}
          >
            <Lock className="w-12 h-12" style={{ color: 'var(--accent)' }} strokeWidth={1} />
            <p className="text-lg font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>
              LOGIN REQUIRED
            </p>
            <p className="text-xs uppercase tracking-wide opacity-70 max-w-xs" style={{ color: 'var(--text-muted)' }}>
              The studio's generation controls are locked until you log in.
            </p>
            <Link to="/login" className="brutal-btn text-xs">
              GO TO LOGIN
            </Link>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

          {/* LEFT COL: ORIGINAL SOURCE */}
          <SourcePreview
            filename={filename}
            imageUrl={url}
            rawUrl={source?.url ?? null}
            fileId={fileId}
            isImage={isImage}
            isText={isText}
            isAudio={isAudio}
            mode={mode}
            textPreview={textPreview}
          />

          {/* RIGHT COL: VISUALIZER & CONTROLS */}
          <div className="col-span-1 lg:col-span-2 flex flex-col gap-8">
```

The closing tags at the end of the component need one more `</div>` to match the new wrapper. Current (`src/pages/Player.tsx`, the three closing divs right before the component's final `return` closes):
```tsx
            onGenerateAgain={() => { clearItem(); generation.reset(); }}
          />

        </div>
      </div>
    </div>
  );
}
```

Replace with:
```tsx
            onGenerateAgain={() => { clearItem(); generation.reset(); }}
          />

        </div>
      </div>
      </div>
    </div>
  );
}
```

(One new `</div>` added, closing the `relative` wrapper opened in this step — everything between the RIGHT COL div and these closing tags is untouched.)

- [ ] **Step 3: Verify live**

Logged out: navigate to `/player` (e.g. from Home after typing a prompt, or directly) and confirm the entire functional area (source preview, arc editor/waveform, playback controls, effects panel, generate panel) is covered by the opaque overlay with the lock icon and "GO TO LOGIN" button, while the page's own `// SYS_STUDIO` header stays visible above it. Click "GO TO LOGIN", confirm it navigates to `/login`. Log in, navigate back to `/player`, confirm the overlay is gone and a real generation still works end-to-end (reusing the same verification a visitor would do: type/upload a source, generate, play, save/discard).

Screenshot: Player logged-out (overlay visible) and Player logged-in (overlay gone, normal studio).

- [ ] **Step 4: Commit**

```bash
git add src/pages/Player.tsx
git commit -m "Add login-required overlay to Player's functional area"
```

---

## Self-Review Notes

- **Spec coverage:** every architecture piece from the design spec (users table, session middleware, auth-gate allowlist, CORS flip, auth router, AuthContext, Login page, Navigation indicator, Home/Library/Player per-page treatment) maps to a task above.
- **Ordering:** Task 1 before Task 2 (users table must exist before login can query it). Task 2 before Task 3 (frontend needs real endpoints to call). Task 3 before Tasks 4-8 (all of them consume `useAuth()`). Tasks 4-8 touch disjoint files and are otherwise independent of each other, but are listed in an order that surfaces `/login` (Task 4) and the nav indicator (Task 5) before the three pages that reference "login required" states (6-8), so a reviewer can actually click through to `/login` from the nav while verifying 6-8 live.
- **Scope correction applied during planning:** the design spec said "Play and Download buttons" for Library; Task 7 above covers every action button on a card (Play, Remix, format-select, Download/Convert, Save, Discard, MIDI Play/Download) since all of them hit backend endpoints Task 2 protects. Noted explicitly in Task 7 rather than silently expanding scope.
- **Type consistency:** `useAuth()`'s shape (`{ username, loading, login, logout }`) is used identically everywhere it's consumed (Login, Navigation, Home, Library, Player) — none of them destructure a field that Task 3's `AuthContext.tsx` doesn't actually produce.
- **No placeholders:** every step has literal, runnable code and stated expected output; the one deliberately-not-fully-specified piece is Task 4 Step 3's Hallmark pass, which is a design-skill invocation by nature, not something to pre-write here — the functional baseline it operates on is fully specified.
