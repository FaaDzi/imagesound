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
RATE_LIMIT_LOGIN: str = "10/minute"

# CORS — list the frontend dev origin explicitly (never use "*" with credentials).
# Override via ALLOWED_ORIGINS env var if needed. No change is needed here
# for remote tunnel access (python run.py --tunnel): only the frontend is
# tunneled, and Vite proxies /api/* to the backend same-origin, so the
# browser never makes a cross-origin request to the backend that CORS would
# need to allow.
FRONTEND_ORIGINS: list[str] = ["http://localhost:3000"]


# Model availability — set SMALL_MODEL_AVAILABLE to True once musicgen-small is downloaded.
# When False, any request specifying model="small" is rejected at the router with a clear
# error before touching the pipeline — nothing calls get_pretrained("facebook/musicgen-small").
SMALL_MODEL_AVAILABLE: bool = True

# How long an unsaved generated song is kept before cleanup sweeps it.
# A saved song always gets the standard 7-day window (set at save time).
UNSAVED_EXPIRY_SECONDS: int = int(os.getenv("UNSAVED_EXPIRY_SECONDS", str(6 * 3600)))

# Signs the session cookie (see main.py's SessionMiddleware). The fallback
# below is DEV-ONLY -- it must never be relied on outside local development,
# since anyone who knows it could forge a valid session cookie. Set a real
# random value via the SESSION_SECRET_KEY env var for anything beyond that.
SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "dev-only-insecure-secret-change-me")


def ensure_storage_dirs() -> None:
    """Create storage directories if they don't already exist."""
    for directory in (DIR_ORIGINALS, DIR_CONVERTED, DIR_MIDI, DIR_TEMP):
        directory.mkdir(parents=True, exist_ok=True)
