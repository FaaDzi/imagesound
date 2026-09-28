"""
generate_song.py -- the pipeline's public entry point.

  describe_image(image_path, model_id=None) -> str
  generate_song(model_id=..., duration=..., prompt=... | image_path=...) -> (wav_path, prompt)

Model-agnostic: this module owns the Gemini image->prompt step and nothing
about any particular music model. Generation is delegated to the model's own
worker process via pipeline.runner, looked up in pipeline.models.

CLI smoke test (no backend needed):
  python -m pipeline.generate_song --prompt "lo-fi piano, rainy mood" --duration 15
"""

import io
import logging
import os
import re
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

# .env is at the project root — one level above pipeline/
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

from google import genai
from google.genai import types
from PIL import Image

from pipeline.lyrics import (chops_lyrics, prepare_lyrics, resolve_voice, tempo_for_caption, vocal_caption,
                             write_lyrics)
from pipeline.models import ModelError, default_model_id, get_model
from pipeline.prompting import gif_instruction, image_instruction
from pipeline.runner import run_worker

# Pinned deliberately, and to a Lite model.
#
# This was "gemini-flash-latest", which is a moving alias onto whatever the
# newest Flash is. That is the worst target for a free-tier key on two counts:
# the newest Flash models carry the smallest free quotas (reported around 20
# requests/day against ~500/day for Lite -- Google no longer publishes the
# numbers, see your own https://aistudio.google.com/rate-limit), and the alias
# moves without notice, so the quota could shrink overnight with no change here.
#
# The newest Flash is also visibly the busiest: measured back to back on the
# same image, gemini-flash-latest returned 503 twice and took 40.1s to get an
# answer, while both Lite models answered first try in ~3s.
#
# Override with GEMINI_MODEL in .env to try another one without editing code.
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
_WORK_DIR = OUTPUT_DIR / ".work"

# Constructed lazily on first real use so importing this module doesn't
# require GEMINI_API_KEY to already be set.
_gemini_client: "genai.Client | None" = None


def _get_gemini_client() -> "genai.Client":
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(f"GEMINI_API_KEY not found. Looked in: {_ENV_PATH}")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


# ── Gemini: image -> prompt ───────────────────────────────────────────────────

# Gemini's free tier fails two transient ways: 503 "model is overloaded", and
# 429 once the per-minute request quota is spent (5/min per model at the time
# of writing, which a burst of testing reaches easily). Both are worth
# retrying. Every other client error -- bad key, unknown model -- fails
# immediately, because retrying those can never help.
_GEMINI_MAX_ATTEMPTS = 4
_GEMINI_RETRY_BACKOFF_SEC = 2.0   # doubles each attempt: 2s, 4s, 8s

# Wall-clock ceiling for one image->prompt call including all its retries.
# This is a *budget*, not a guess: the HTTP route that calls this has to wait
# for it synchronously, so the two have to be agreed rather than set apart.
# See GEMINI_MAX_TOTAL_SEC's use in backend/app/routers/describe.py -- an
# earlier version allowed a single 65s sleep behind a 45s route timeout, which
# meant the retry for a per-minute quota could never finish before the route
# gave up and reported a timeout instead of the real cause.
GEMINI_MAX_TOTAL_SEC = 40.0

# Passing no config makes the SDK take its automatic-function-calling path,
# which logs a "Direct use of AFC in Models.generate_content is not
# recommended" warning on the first call of every process. We declare no tools,
# so that path does nothing except deep-copy the config and run a one-iteration
# loop. Turning AFC off explicitly takes the direct path and drops the warning.
_GEMINI_CONFIG = types.GenerateContentConfig(
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)


def _gemini_retry_after(err) -> "float | None":
    """Seconds Google asked us to wait, if it said.

    Quota errors carry a `retryDelay`, and honouring it beats guessing: a
    doubling backoff from 2s gives up after ~14s total, which is well short of
    the minute a per-minute quota needs to roll over.
    """
    details = getattr(err, "details", None)
    if isinstance(details, dict):
        for entry in details.get("error", {}).get("details", []) or []:
            delay = entry.get("retryDelay") if isinstance(entry, dict) else None
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    pass
    match = re.search(r"retry in ([\d.]+)\s*s", str(err))
    return float(match.group(1)) if match else None


def _gemini_generate_content(model: str, contents: list) -> "types.GenerateContentResponse":
    from google.genai import errors as genai_errors

    deadline = time.monotonic() + GEMINI_MAX_TOTAL_SEC
    last_err: Exception | None = None
    for attempt in range(1, _GEMINI_MAX_ATTEMPTS + 1):
        try:
            return _get_gemini_client().models.generate_content(
                model=model, contents=contents, config=_GEMINI_CONFIG)
        except genai_errors.ServerError as e:
            last_err = e
        except genai_errors.ClientError as e:
            if getattr(e, "code", None) != 429:
                raise
            last_err = e
        if attempt == _GEMINI_MAX_ATTEMPTS:
            break
        # A second of margin: the server's delay is when the window opens,
        # and arriving exactly on it just earns another 429.
        asked = _gemini_retry_after(last_err)
        wait = (asked + 1.0) if asked else _GEMINI_RETRY_BACKOFF_SEC * (2 ** (attempt - 1))
        # Sleeping past the budget only guarantees the caller times out first
        # and reports a timeout rather than what actually went wrong. Better to
        # stop now and let the real error through, with the wait in the log so
        # it is obvious this was a quota wall and not a failure.
        if time.monotonic() + wait > deadline:
            log.warning("[gemini] %s asks for a %.0fs wait, over the %.0fs budget "
                        "for one request -- giving up after attempt %d/%d",
                        type(last_err).__name__, wait, GEMINI_MAX_TOTAL_SEC,
                        attempt, _GEMINI_MAX_ATTEMPTS)
            break
        log.warning("[gemini] attempt %d/%d failed (%s) -- retrying in %.0fs",
                    attempt, _GEMINI_MAX_ATTEMPTS, type(last_err).__name__, wait)
        time.sleep(wait)
    raise last_err


def _gif_frames(image_path: Path) -> "list[tuple[bytes, str]]":
    """Up to 3 frames (first/middle/last) as PNG bytes, for ONE describe call."""
    img = Image.open(image_path)
    n = getattr(img, "n_frames", 1)
    if n == 1:
        indices = [0]
    elif n == 2:
        indices = [0, n - 1]
    else:
        indices = [0, (n - 1) // 2, n - 1]

    frames: list[tuple[bytes, str]] = []
    for idx in indices:
        img.seek(idx)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        frames.append((buf.getvalue(), "image/png"))
    log.info("[gif] sending %d frame(s) in one call (n_frames=%d)", len(frames), n)
    return frames


def _still_frame(image_path: Path) -> "list[tuple[bytes, str]]":
    img = Image.open(image_path)
    buf = io.BytesIO()
    fmt = (img.format or "PNG").upper()
    if fmt not in ("JPEG", "PNG", "WEBP"):
        fmt = "PNG"
    img.save(buf, format=fmt)
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
    return [(buf.getvalue(), mime)]


def _image_frames(image_path: Path) -> "list[tuple[bytes, str]]":
    return _gif_frames(image_path) if image_path.suffix.lower() == ".gif" else _still_frame(image_path)


def _image_to_prompt(image_path: Path, style: str) -> str:
    """Read the image with whichever backend answers first.

    Which backends, and in what order, is pipeline/describers.py's business --
    by default Gemini with a local Ollama model behind it, so a quota wall
    degrades the result instead of failing the request.
    """
    from pipeline.describers import describe

    if image_path.suffix.lower() == ".gif":
        return describe(_gif_frames(image_path),
                        lambda compact: gif_instruction(style, compact))
    return describe(_still_frame(image_path),
                    lambda compact: image_instruction(style, compact))


def describe_image(image_path: "str | Path", model_id: "str | None" = None) -> str:
    """Image -> a prompt phrased for `model_id`. Generates no audio."""
    spec = get_model(model_id or default_model_id())
    return _image_to_prompt(Path(image_path), spec.prompt_style)


# ── Generation ────────────────────────────────────────────────────────────────

def generate_song(
    *,
    model_id: str,
    duration: int,
    prompt: "str | None" = None,
    image_path: "str | Path | None" = None,
    reference_path: "str | Path | None" = None,
    reference_mode: "str | None" = None,
    options: "dict | None" = None,
    lyrics: "str | None" = None,
    on_status=None,
    on_progress=None,
    cancel_check=None,
) -> "tuple[Path, str]":
    """Generate one song and return (wav_path, prompt_used).

    Give a text `prompt`, or an `image_path` (Gemini writes the prompt), or
    both (the prompt wins and Gemini is skipped). `lyrics` is sung when the
    `vocals` option is "lyrics"; left empty, they are drafted here the same way
    the prompt is. `reference_path` +
    `reference_mode` ("cover" / "style", whichever the model declares) let a
    model condition on an existing song.

    on_status(state)     -- "loading_model" then "processing", as the worker starts up.
    on_progress(0..1)    -- generation progress.
    cancel_check() -> bool -- polled ~1/s; True kills the worker (raises WorkerCancelled).

    Raises ModelError for an unknown/unavailable model or a request the model
    can't satisfy; WorkerError if generation itself fails.
    """
    spec = get_model(model_id)
    available, reason = spec.availability()
    if not available:
        raise ModelError(f"{spec.label} is not available: {reason}")
    if reference_path is not None and reference_mode is None:
        raise ModelError("A reference song needs a reference mode.")
    clean_options = spec.validate_request(duration, reference_mode if reference_path else None, options or {})

    if not (prompt and prompt.strip()):
        if image_path is None:
            raise ModelError("Provide a prompt or an image.")
        if on_status:
            on_status("processing")   # the Gemini call counts as work in progress
        prompt = _image_to_prompt(Path(image_path), spec.prompt_style)
    prompt = prompt.strip()

    # Vocals. The stored prompt stays the user's; only the model sees the
    # added vocal words (pipeline/lyrics.py has why each piece is there).
    vocals = clean_options.get("vocals", "off")
    drafted_bpm = None
    if vocals == "lyrics":
        if not (lyrics and lyrics.strip()):
            if on_status:
                on_status("processing")
            lyrics, drafted_bpm = write_lyrics(
                language=clean_options.get("vocal_language", "ja"), seconds=duration,
                caption=prompt, images=_image_frames(Path(image_path)) if image_path else None)
        lyrics = lyrics.strip()
    elif vocals == "chops":
        lyrics = chops_lyrics(duration)
    else:
        lyrics = None
    # Vocal songs never run with a free tempo: left to itself the model fell
    # into half time mid-song (pipeline/lyrics.py, "Tempo"). "Lock BPM" at 0
    # means "pick for me" here, so pick.
    if vocals != "off" and "bpm" in clean_options and not clean_options["bpm"]:
        clean_options["bpm"] = drafted_bpm or tempo_for_caption(prompt)
    # Resolved here, after drafting, so "auto" is decided once per song and a
    # draft written before the Voice setting changed still gets the right tag.
    voice = resolve_voice(clean_options.get("voice", "female"), prompt) if vocals == "lyrics" else None
    if voice:
        lyrics = prepare_lyrics(lyrics, voice)

    out_path = OUTPUT_DIR / f"song_{uuid.uuid4().hex[:8]}.wav"
    request = {
        "prompt": vocal_caption(prompt, vocals, voice or "female", clean_options.get("vocal_tone", "default")),
        "lyrics": lyrics,
        "duration": duration,
        "output_path": str(out_path),
        "reference": ({"path": str(reference_path), "mode": reference_mode} if reference_path else None),
        "options": clean_options,
    }

    log.info("[generate] model=%s duration=%ds reference=%s vocals=%s voice=%s bpm=%s prompt=%r",
             spec.id, duration, reference_mode if reference_path else None, vocals, voice,
             clean_options.get("bpm") or "free", prompt)
    if vocals == "lyrics":
        log.info("[generate] lyrics:\n%s", lyrics)
    t0 = time.perf_counter()
    run_worker(spec, request, _WORK_DIR, on_status=on_status, on_progress=on_progress,
               cancel_check=cancel_check)
    elapsed = time.perf_counter() - t0
    log.info("[generate] %s done in %.1fs (%.2fx realtime) -> %s",
             spec.id, elapsed, elapsed / duration, out_path.name)
    return out_path, prompt


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Generate one song from the command line.")
    ap.add_argument("--model", default=default_model_id())
    ap.add_argument("--prompt")
    ap.add_argument("--image")
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--reference", help="existing audio file to condition on")
    ap.add_argument("--reference-mode", choices=["cover", "style"])
    ap.add_argument("--cover-strength", type=float)
    ap.add_argument("--bpm", type=int, help="lock the tempo")
    ap.add_argument("--vocals", choices=["off", "lyrics", "chops"])
    ap.add_argument("--language", help="lyrics language code (ja, en tested; ko, zh locked)")
    ap.add_argument("--lyrics-file", help="lyrics to sing; omitted with --vocals lyrics = drafted")
    ap.add_argument("--voice", choices=["auto", "female", "male", "duet"])
    ap.add_argument("--tone", help="admin tone test: bright, breathy, raspy, soft, falsetto")
    a = ap.parse_args()

    opts = {"cover_strength": a.cover_strength} if a.cover_strength is not None else {}
    if a.bpm is not None:
        opts["bpm"] = a.bpm
    if a.vocals:
        opts["vocals"] = a.vocals
    if a.language:
        opts["vocal_language"] = a.language
    if a.voice:
        opts["voice"] = a.voice
    if a.tone:
        opts["vocal_tone"] = a.tone
    wav, used = generate_song(
        model_id=a.model, duration=a.duration, prompt=a.prompt, image_path=a.image,
        reference_path=a.reference, reference_mode=a.reference_mode, options=opts,
        lyrics=Path(a.lyrics_file).read_text(encoding="utf-8") if a.lyrics_file else None,
        on_status=lambda s: print(f"[status] {s}", flush=True),
        on_progress=lambda f: print(f"[progress] {f:.0%}", flush=True),
    )
    print(f"\nprompt: {used}\nsaved : {wav}")
