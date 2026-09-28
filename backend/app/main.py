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
from app.config import ensure_storage_dirs, FRONTEND_ORIGINS, SESSION_HTTPS_ONLY, SESSION_SECRET_KEY
from app.convert import conversion_available
from app.database import get_user, init_db
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
from app.routers.models import router as models_router
from app.routers.save import router as save_router
from app.routers.status import router as status_router
from app.routers.upload import router as upload_router

log = logging.getLogger(__name__)

# Ensure project root is on sys.path (needed by jobs.py to import the pipeline package).
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

    from pipeline.models import list_models
    for spec in list_models():
        ok, reason = spec.availability()
        print(f"  [startup] model {spec.id}: {'ready' if ok else 'UNAVAILABLE - ' + reason}", flush=True)

    print(f"  [startup] Backend ready. PID={os.getpid()} threads={threading.active_count()}", flush=True)
    print(f"  [startup] If generation is slow after a restart, open Task Manager and confirm", flush=True)
    print(f"  [startup] no OTHER python.exe processes are holding GPU memory (kill them first).", flush=True)
    log.info("Backend ready. PID=%d — each model loads in its own worker process per generation.", os.getpid())

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
    # /library and /image/* used to be public too. With a shared public
    # account, each role only sees its own songs, so both need a login now.
    return (request.url.path, request.method) in _PUBLIC_EXACT


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    if _is_public(request):
        return await call_next(request)
    # Re-read the account on every request rather than trusting the role in
    # the cookie: a renamed or deleted account, or a changed role, takes
    # effect immediately. Sessions from before roles existed carry no
    # user_id and simply have to log in again.
    user_id = request.session.get("user_id")
    user = get_user(user_id) if user_id else None
    if user is None:
        request.session.clear()
        return JSONResponse({"error": "not logged in"}, status_code=401)
    request.state.user = dict(user)
    return await call_next(request)


# Signed-cookie session (see config.py's SESSION_SECRET_KEY). same_site="lax"
# works correctly for cross-port localhost dev (cookies aren't port-scoped).
# Starlette only adds the Secure flag when https_only=True -- it cannot see
# the tunnel's HTTPS, since requests arrive from Vite's proxy over plain HTTP.
# run.py --tunnel sets SESSION_HTTPS_ONLY=1 so the cookie is never sent over
# a plain-HTTP request to the public hostname. Browsers treat localhost as a
# secure context, so local access keeps working either way.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
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
app.include_router(models_router)
app.include_router(save_router)
app.include_router(status_router)
app.include_router(audio_router)
app.include_router(download_router)
app.include_router(convert_router)
app.include_router(midi_router)


@app.get("/health")
def health():
    # Read cross-origin by the public status page (a separate GitHub Pages
    # site) to decide whether to redirect. Opened to any origin for this one
    # route only -- it carries no data and no credentials, so the app-wide
    # credentialed CORS list above stays locked to the real frontend.
    return JSONResponse(
        {"status": "ok"},
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )
