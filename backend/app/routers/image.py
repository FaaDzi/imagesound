"""
GET /image/{id}

Serves the original uploaded image for image-input songs.
DB-checked — files are never exposed via a public static folder.
GIFs are served as a static PNG frame (middle frame) for consistent cover rendering.
"""

import io
import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from PIL import Image

from app.config import DIR_ORIGINALS
from app.database import get_connection

router = APIRouter()

_EXT_MIME = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
}


@router.get("/image/{file_id}")
def get_image(file_id: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT input_type, original_key FROM files WHERE id=?",
            (file_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["input_type"] != "image" or not row["original_key"]:
        raise HTTPException(status_code=404, detail="No image for this entry.")

    file_path = DIR_ORIGINALS / row["original_key"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Image file missing from storage.")

    suffix = file_path.suffix.lower()

    # GIF: extract middle frame and return as a static PNG so the cover is a clean still.
    if suffix == ".gif":
        try:
            img = Image.open(file_path)
            n = getattr(img, "n_frames", 1)
            img.seek((n - 1) // 2)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
        except Image.DecompressionBombError:
            raise HTTPException(status_code=415, detail="Image exceeds the maximum allowed pixel count.")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    mime = _EXT_MIME.get(suffix) or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(
        path=str(file_path),
        media_type=mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )
