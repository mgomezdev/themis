from __future__ import annotations
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_data_dir
from ...database import get_session
from ...models import UploadedFile
from ...services.three_mf_parser import parse_three_mf

router = APIRouter(prefix="/api/v1/files", tags=["files"])


def _to_dict(f: UploadedFile) -> dict:
    return {
        "id": f.id,
        "original_filename": f.original_filename,
        "stored_path": f.stored_path,
        "plates": f.plates,
        "uploaded_at": f.uploaded_at,
    }


@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not (file.filename or "").lower().endswith(".3mf"):
        raise HTTPException(422, "Only .3mf files are accepted")

    data_dir = get_data_dir()
    file_uuid = str(uuid.uuid4())
    upload_dir = data_dir / "uploads" / file_uuid
    thumb_dir = upload_dir / "thumbnails"
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_path = upload_dir / "model.3mf"
    content = await file.read()
    stored_path.write_bytes(content)

    plates_raw = parse_three_mf(str(stored_path), thumbnail_dir=str(thumb_dir))
    plates_json = [
        {
            "plate_number": p.plate_number,
            "thumbnail_path": p.thumbnail_path,
            "estimated_time": p.estimated_time,
            "filament_g": p.filament_g,
        }
        for p in plates_raw
    ]

    record = UploadedFile(
        original_filename=file.filename,
        stored_path=str(stored_path),
        plates=plates_json,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return _to_dict(record)


@router.get("/{file_id}/plates")
async def get_plates(
    file_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return record.plates or []


@router.get("/{file_id}/thumbnails/{filename}")
async def get_thumbnail(
    file_id: int,
    filename: str,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    stored = Path(record.stored_path)
    thumb_path = stored.parent / "thumbnails" / filename
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(str(thumb_path), media_type="image/png")
