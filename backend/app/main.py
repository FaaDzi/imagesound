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

# --- Middleware registration order matters here. -----------------------
# Starlette's app.add_middleware() (and the @app.middleware("http")
# decorator, which calls it) prepends to the middleware list, so the LAST
# middleware registered ends up OUTERMOST and runs FIRST on the way in.
# We need, on the way in: CORS first (so preflight + credential headers are
# handled/attached regardless of what happens downstream, including on the
# auth-gate's own 401s) -> SessionMiddleware next (so request.session
# exists) -> auth_gate last (so it can safely read request.session).
# That means they must be *registered* in the opposite order: auth_gate
# first, then SessionMiddleware, then CORSMiddleware.
#
# (Registering them in the file order the brief's prose lists them --
# CORS, then Session, then auth_gate as the last decorator in the file --
# makes auth_gate the outermost layer instead, so it runs BEFORE
# SessionMiddleware has attached request.session and raises
# `AssertionError: SessionMiddleware must be installed to access
# request.session` on every request, a 500 instead of the intended 401.
# Confirmed live during Task 2 verification; fixed by the registration
# order below.)

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

# CORS — use FRONTEND_ORIGINS from config; override at runtime via ALLOWED_ORIGINS env var.
# allow_credentials=True is required so the browser sends the session cookie
# on cross-port requests from the frontend -- this is only valid because
# allow_origins is never "*" (the CORS spec forbids combining a wildcard
# origin with credentials). Registered last (outermost) so its headers are
# attached to every response, including the auth-gate's direct 401s.
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", ",".join(FRONTEND_ORIGINS)).split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
