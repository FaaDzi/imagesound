"""
POST /describe  -- image -> music prompt text, no audio generated. Stores the
prompt on the DB row so /generate can use it later without re-describing.

POST /lyrics    -- draft lyrics for a vocal song from an image and/or the
button's prompt text. Admin only while vocals are being tuned.

POST /theme     -- invent a music prompt from nothing, for the "surprise me"
button when there is no image to read (remixing an existing song starts with an
empty box). Local model only; see pipeline/describers.random_theme.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.access import owner_filter
from app.config import DIR_ORIGINALS, RATE_LIMIT_DESCRIBE
from app.database import get_connection
from app.limiter import limiter

router = APIRouter()


class DescribeRequest(BaseModel):
    id: str
    model: str | None = None   # phrase the prompt for this model (see GET /models); omitted -> default


@router.post("/describe")
@limiter.limit(RATE_LIMIT_DESCRIBE)
async def describe(request: Request, req: DescribeRequest):
    clause, params = owner_filter(request)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, input_type, original_key FROM files WHERE id=?" + clause,
            (req.id, *params),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    if row["input_type"] != "image":
        raise HTTPException(
            status_code=400,
            detail=f"Only image files can be described (input_type='{row['input_type']}').",
        )

    image_path = DIR_ORIGINALS / row["original_key"]
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Source image missing from storage.")

    # Run Gemini in a thread — it blocks for ~8s and must not stall the event loop.
    # Hard timeout so a slow/hung Gemini response doesn't freeze the frontend forever.
    from pipeline.describers import DescriberError
    from pipeline.generate_song import GEMINI_MAX_TOTAL_SEC, describe_image
    from pipeline.models import ModelError, get_model
    try:
        get_model(req.model) if req.model else None
    except ModelError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Derived from the pipeline's own retry budget rather than set independently:
    # a timeout shorter than the retries it is waiting on turns every quota wall
    # into a misleading "timed out" instead of the actual error. The margin
    # covers the requests themselves, which the budget only counts waits for.
    _TIMEOUT_SEC = GEMINI_MAX_TOTAL_SEC + 20
    try:
        prompt = await asyncio.wait_for(
            asyncio.to_thread(describe_image, image_path, req.model),
            timeout=_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        # NOTE: the worker thread is not cancelled by this -- asyncio.to_thread
        # cannot interrupt it -- so the call continues in the background and
        # still spends quota. The budget above exists to keep that rare.
        raise HTTPException(
            status_code=503,
            detail=(
                "Image description timed out — the model may be busy. "
                "Try again, or switch to Text input to write your own prompt."
            ),
        )
    except DescriberError as e:
        # Every configured backend failed. The message carries each one's reason
        # (quota, overloaded, Ollama not running, model not pulled), which is the
        # whole point of reporting it rather than letting a bare 500 escape.
        raise HTTPException(
            status_code=503,
            detail=f"{e} Or switch to Text input to write your own prompt.",
        )

    with get_connection() as conn:
        conn.execute("UPDATE files SET prompt=? WHERE id=?", (prompt, req.id))
        conn.commit()

    return {"id": req.id, "prompt": prompt}


class LyricsRequest(BaseModel):
    id: str | None = None        # an uploaded image to write about; optional
    prompt: str | None = Field(default=None, max_length=1000)  # the music description
    language: str = "ja"
    duration: int = Field(default=60, ge=10, le=600)
    # The user's own sections ("[Verse]", "///" ...) to write into; omitted,
    # the genre's plan is used.
    structure: str | None = Field(default=None, max_length=2000)


@router.post("/lyrics")
@limiter.limit(RATE_LIMIT_DESCRIBE)
async def lyrics(request: Request, req: LyricsRequest):
    """One lyrics draft. Costs one describer request (Gemini, or the local
    model when Gemini is out of quota), so it only runs when asked for."""

    from pipeline.describers import DescriberError
    from pipeline.generate_song import GEMINI_MAX_TOTAL_SEC, _image_frames
    from pipeline.lyrics import LANGUAGES, TESTED_LANGUAGES, write_lyrics

    if req.language not in LANGUAGES:
        raise HTTPException(status_code=422, detail=f"Unsupported language '{req.language}'.")
    if req.language not in TESTED_LANGUAGES:
        raise HTTPException(status_code=422, detail=f"Lyrics in '{req.language}' are locked until "
                                                    "that language has been tested.")

    images = None
    if req.id:
        clause, params = owner_filter(request)
        with get_connection() as conn:
            row = conn.execute(
                "SELECT input_type, original_key FROM files WHERE id=?" + clause,
                (req.id, *params),
            ).fetchone()
        # Only an image is worth sending; for a song or text the prompt says it all.
        if row is not None and row["input_type"] == "image":
            path = DIR_ORIGINALS / row["original_key"]
            if path.is_file():
                images = _image_frames(path)
    if not images and not (req.prompt and req.prompt.strip()):
        raise HTTPException(status_code=400, detail="Describe the music first -- the lyrics are written to fit it.")

    try:
        text, bpm = await asyncio.wait_for(
            asyncio.to_thread(write_lyrics, language=req.language, seconds=req.duration,
                              caption=req.prompt, images=images, structure=req.structure),
            timeout=GEMINI_MAX_TOTAL_SEC + 20,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Writing lyrics timed out -- try again.")
    except DescriberError as e:
        raise HTTPException(status_code=503, detail=str(e))
    # The tempo the writer picked. The UI puts it on the Lock BPM control:
    # vocal songs always run at a locked tempo (see pipeline/lyrics.py).
    return {"lyrics": text, "bpm": bpm}


@router.post("/theme")
@limiter.limit(RATE_LIMIT_DESCRIBE)
async def theme(request: Request):
    """Invent a music prompt with no image to go on.

    Runs on the local model only. It costs nothing and has no quota, which suits
    a button people press repeatedly while looking for an idea -- spending a
    rate-limited Gemini call on each press would be the wrong trade.
    """
    from pipeline.describers import DescriberError, random_theme

    try:
        # Local generation is fast (~0.5s warm) but the first call after idle
        # pays a model load, so allow for that rather than the usual budget.
        prompt = await asyncio.wait_for(asyncio.to_thread(random_theme), timeout=90)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Theme generation timed out.")
    except DescriberError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"prompt": prompt}
