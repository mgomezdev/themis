"""One-shot script: re-extract plate thumbnails from the 3MF source files,
replacing any OrcaSlicer-regenerated thumbnails with the originals baked in
by the slicer that created each file.

Run from the backend directory with the venv active:
    python scripts/reextract_thumbnails.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_filecache_dir
from app.database import SessionLocal, init_db
from app.models import UploadedFile
from app.services.three_mf_parser import parse_three_mf
from sqlalchemy import select


async def main() -> None:
    await init_db()

    filecache = get_filecache_dir()

    async with SessionLocal() as session:
        rows = (await session.execute(select(UploadedFile))).scalars().all()
        updated = 0
        skipped = 0

        for f in rows:
            if not f.stored_path or not Path(f.stored_path).suffix.lower() == ".3mf":
                skipped += 1
                continue
            if not Path(f.stored_path).exists():
                print(f"  MISSING  [{f.id}] {f.original_filename}")
                skipped += 1
                continue

            thumb_dir = filecache / str(f.id) / "thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)

            plates_raw = parse_three_mf(f.stored_path, thumbnail_dir=str(thumb_dir))
            if not plates_raw:
                print(f"  NO PLATES [{f.id}] {f.original_filename}")
                skipped += 1
                continue

            existing = {p["plate_number"]: p for p in (f.plates or [])}
            new_plates = []
            for p in plates_raw:
                old = existing.get(p.plate_number, {})
                new_plates.append({**old,
                    "plate_number": p.plate_number,
                    "thumbnail_path": p.thumbnail_path or old.get("thumbnail_path"),
                    "estimated_time": p.estimated_time or old.get("estimated_time", 0),
                    "filament_g": p.filament_g or old.get("filament_g", 0.0),
                })

            f.plates = new_plates
            thumb_count = sum(1 for p in new_plates if p["thumbnail_path"])
            print(f"  OK  [{f.id}] {f.original_filename}  ({len(new_plates)} plates, {thumb_count} thumbnails)")
            updated += 1

        await session.commit()

    print(f"\nDone: {updated} updated, {skipped} skipped.")


if __name__ == "__main__":
    asyncio.run(main())
