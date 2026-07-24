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
