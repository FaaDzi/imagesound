from pathlib import Path

import filetype
from fastapi import HTTPException, UploadFile

from app.config import DIR_ORIGINALS, MAX_UPLOAD_BYTES

# Maps the extension filetype detects → (input_type, canonical_extension).
# Keyed on filetype's own extension strings, which are stable across versions.
_ALLOWED: dict[str, tuple[str, str]] = {
    "jpg":  ("image", "jpg"),
    "png":  ("image", "png"),
    "webp": ("image", "webp"),
    "gif":  ("image", "gif"),
    "wav":  ("audio", "wav"),
    "mp3":  ("audio", "mp3"),
    "ogg":  ("audio", "ogg"),
    "m4a":  ("audio", "m4a"),
}

_ALLOWED_LABEL = "JPEG, PNG, WebP, GIF (image) · WAV, MP3, OGG, M4A (audio)"


async def read_upload(upload: UploadFile) -> bytes:
    """
    Stream the upload into memory, enforcing the size cap.
    Reads one byte past the limit so we can detect oversized files
    without loading the entire payload first.
    """
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {mb} MB.")
    return data


def validate_type(data: bytes) -> tuple[str, str]:
    """
    Inspect magic bytes to determine real file type.
    Returns (input_type, extension) or raises 415.
    Never trusts filename or Content-Type header.
    """
    kind = filetype.guess(data)
    if kind is None or kind.extension not in _ALLOWED:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Accepted: {_ALLOWED_LABEL}.",
        )
    input_type, ext = _ALLOWED[kind.extension]
    return input_type, ext


def save_original(data: bytes, file_uuid: str, ext: str) -> str:
    """
    Write raw bytes to storage/originals/{uuid}.{ext}.
    Returns the stored filename (used as original_key in the DB).
    """
    filename = f"{file_uuid}.{ext}"
    dest: Path = DIR_ORIGINALS / filename
    dest.write_bytes(data)
    return filename
