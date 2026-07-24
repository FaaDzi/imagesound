"""
POST /describe

Runs only the Gemini step from the pipeline (image -> music prompt text).
Does NOT generate audio. Stores the prompt on the DB row so /generate can
use it directly later, skipping the re-describe step.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import DIR_ORIGINALS, RATE_LIMIT_DESCRIBE
from app.database import get_connection
from app.limiter import limiter

router = APIRouter()


class DescribeRequest(BaseModel):
    id: str


@router.post("/describe")
@limiter.limit(RATE_LIMIT_DESCRIBE)
async def describe(request: Request, req: DescribeRequest):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, input_type, original_key FROM files WHERE id=?",
            (req.id,),
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
    from pipeline.generate_song import describe_image
    _TIMEOUT_SEC = 45
    try:
        prompt = await asyncio.wait_for(
            asyncio.to_thread(describe_image, image_path),
            timeout=_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Image description timed out — Gemini may be busy. "
                "Try again, or switch to Text input to write your own prompt."
            ),
        )

    with get_connection() as conn:
        conn.execute("UPDATE files SET prompt=? WHERE id=?", (prompt, req.id))
        conn.commit()

    return {"id": req.id, "prompt": prompt}
