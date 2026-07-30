# ImageSound

Turns an image, a text prompt, or an uploaded audio clip into an AI-generated
song. An image is first described by Gemini into a text music prompt; that
prompt (typed directly, or produced from the image) drives Meta's MusicGen
(via `audiocraft`) to generate audio, optionally guided by an uploaded audio
clip as a melody reference. Generated songs can also be converted to MIDI
(Basic Pitch) and downloaded in several audio formats.

## Architecture

Two processes, run together by `run.py`:

- **Backend** — FastAPI + SQLite (`backend/`). Handles uploads, queues
  generation jobs on a background worker, serves generated audio/images, and
  scores each finished song for quality via a separate FAD (Frechet Audio
  Distance) subprocess.
- **Frontend** — React + Vite + TypeScript (`src/`), three routes: Home
  (upload/prompt entry), Player (generation controls + playback), Library
  (saved songs).

The generation pipeline itself (`pipeline/generate_song.py`) is standalone
Python, callable outside the backend too — it wraps the Gemini
image-to-prompt step and the MusicGen text-to-audio step behind two
functions.

See `design.md` for the frontend's visual design system (not reproduced
here — this file is setup/orientation only).

## Prerequisites

- Node.js 18+ (frontend)
- Python 3.10 (both venvs below were built against 3.10.11 — other 3.10.x
  builds should work, but this hasn't been tested against other minor
  versions)
- An NVIDIA GPU is not strictly required, but MusicGen generation on CPU is
  impractical — the install steps below pull a CUDA build of `torch`
- Windows: `run.py` currently assumes Windows (hardcoded `.venv\Scripts\`
  paths, `taskkill` for process cleanup, PowerShell for process inspection).
  Running the two halves manually (`uvicorn` + `npm run dev`) should still
  work on other platforms; the launcher script itself won't.

## Setup

### 1. Frontend dependencies

```
npm install
```

### 2. Environment variables

```
copy .env.example .env
```

Edit the new `.env` (repo root) and set `GEMINI_API_KEY`. This file is read
directly by `pipeline/generate_song.py` (the Gemini image-to-prompt step)
and also doubles as Vite's env file for the frontend (`VITE_API_BASE`,
default `http://localhost:8000`). `ALLOWED_ORIGINS` (backend CORS) and
`VITE_API_BASE` already default to the right values for local dev — you
only need to touch `GEMINI_API_KEY`.

Optionally also:

```
copy backend\.env.example backend\.env
```

This only overrides `DATABASE_PATH`/`STORAGE_ROOT`, both of which have
working defaults (`backend/app.db`, `backend/storage/`) — skip it unless you
need to point them elsewhere.

### 3. Main Python environment (`.venv`)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install -r pipeline\requirements.txt
```

The `--index-url` above is for CUDA 12.1; check your CUDA version
(`nvidia-smi`) and swap in `cu118`/`cu124` as needed — see the header comment
in `musicgen_test\requirements.txt` for the other URLs. Installing plain
`pip install torch` gives a CPU-only build with `torch.cuda.is_available()`
silently `False`.

### 4. FAD scoring environment (`.venv-fad`)

Song quality scoring (FAD / Frechet Audio Distance, `fad_score_one.py`) runs
in a second, fully isolated venv:

```
python -m venv .venv-fad
.venv-fad\Scripts\activate
pip install fadtk soundfile
```

This has to be a separate environment: `fadtk` needs `torch>=2.3`, while the
main `.venv` above is pinned to `torch==2.1.0+cu121` because that's the
version `audiocraft`/`xformers` require. The two torch versions can't
coexist in one environment, so FAD scoring runs as a subprocess against its
own venv instead. There's no committed requirements file for `.venv-fad`
(`pip install fadtk` pulls a compatible torch on its own) — if FAD scoring
fails to run (missing `.venv-fad`, import error, etc.) it's caught and the
song's `fad_score`/`fad_verdict` are simply left `null`; a scoring failure
never blocks or fails a generation job.

## Running it

```
python run.py
```

Starts the backend (`:8000`) and frontend (`:3000`) together. Press Ctrl+C
once to stop both, or just run `python run.py` again (even from a different
terminal) — it detects the running instance and stops it instead of starting
a second one, so it doubles as a start/stop switch.

## Known limitations

**No authentication.** Every generated file is addressed by a UUID
(`/audio/{id}`, `/image/{id}`, `/download/{id}`, etc.), and the backend does
not currently check who's asking — any client that knows or can guess an
id can fetch that file. `backend/app/routers/audio.py` marks the gap
explicitly:

```python
# DB gate — only serve files that finished successfully.
# TODO: add `AND owner_id=?` here once auth exists.
```

This is a known, deliberately deferred gap rather than an oversight —
there's no login system yet, so there's no owner to check against. Adding
one (and the corresponding `owner_id` filtering across the audio/image/
download endpoints) is planned future work, not something already mitigated.
