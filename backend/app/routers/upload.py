import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import RATE_LIMIT_UPLOAD
from app.database import get_connection
from app.limiter import limiter
from app.services.storage import read_upload, save_original, validate_type

router = APIRouter()


@router.post("/upload", status_code=201)
@limiter.limit(RATE_LIMIT_UPLOAD)
async def upload_file(request: Request, file: UploadFile = File(...)):
    # 1. Validate — order matters: size before content inspection
    data = await read_upload(file)
    input_type, ext = validate_type(data)

    # 2. Store — UUID as filename, never the original name
    file_id = str(uuid.uuid4())
    original_key = save_original(data, file_id, ext)

    # 3. Record
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=7)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO files
                (id, owner_id, input_type, original_key, converted_key,
                 prompt, output_format, duration, job_status, created_at, expires_at)
            VALUES
                (?, NULL, ?, ?, NULL, NULL, NULL, NULL, 'queued', ?, ?)
            """,
            (file_id, input_type, original_key, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()

    return JSONResponse(
        status_code=201,
        content={
            "id": file_id,
            "input_type": input_type,
            "original_key": original_key,
            "message": f"{input_type.capitalize()} accepted. Use 'id' to trigger generation.",
        },
    )
