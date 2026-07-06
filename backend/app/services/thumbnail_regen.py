"""thumbnail_regen.py — Regenerate 3MF plate thumbnails via OrcaSlicer headless.

Run once per plate with --slice N --arrange 0 --export-3mf so OrcaSlicer renders
the plate as-is (no re-arrangement) and bakes a fresh thumbnail into the output
3MF.  The thumbnail is then extracted and stored in the filecache.

Called as a FastAPI BackgroundTask after every library upload so stale thumbnails
baked by third-party tools (e.g. gridfinity-customizer) are always replaced with
a render that matches the actual geometry.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import zipfile
from pathlib import Path

from ..config import get_filecache_dir, get_orca_executable
from ..database import SessionLocal
from ..models import UploadedFile

logger = logging.getLogger(__name__)

_TIMEOUT = 120  # seconds per plate before giving up


async def regen_file_thumbnails(file_id: int) -> None:
    """Background coroutine: regenerate thumbnails for every plate in *file_id*.

    Opens its own DB session (the request session is gone by the time this runs).
    Silently no-ops if OrcaSlicer isn't configured or the file has no plates.
    """
    async with SessionLocal() as session:
        f = await session.get(UploadedFile, file_id)
        if f is None or not f.stored_path:
            return

        plates = list(f.plates or [])
        if not plates:
            return

        stored_path = f.stored_path
        plate_numbers = [p["plate_number"] for p in plates if not p.get("thumbnail_path")]
        if not plate_numbers:
            return
        thumb_dir = get_filecache_dir() / str(file_id) / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_running_loop()
        new_thumbs: dict[int, str] = await loop.run_in_executor(
            None, _regen_sync, stored_path, plate_numbers, str(thumb_dir)
        )

        if not new_thumbs:
            return

        f.plates = [
            {**p, "thumbnail_path": new_thumbs.get(p["plate_number"], p.get("thumbnail_path"))}
            for p in plates
        ]
        await session.commit()
        logger.info("Thumbnails regenerated for file %d (plates: %s)", file_id, sorted(new_thumbs))


def _regen_sync(stored_path: str, plate_numbers: list[int], thumb_dir: str) -> dict[int, str]:
    """Sync worker (runs in ThreadPoolExecutor): render each plate, return saved paths.

    Returns {plate_number: path_to_saved_png} for every plate successfully rendered.
    """
    try:
        orca = get_orca_executable()
    except Exception:
        logger.debug("OrcaSlicer not configured; skipping thumbnail regen")
        return {}

    result: dict[int, str] = {}
    for num in plate_numbers:
        path = _render_plate_thumbnail(orca, stored_path, num, Path(thumb_dir))
        if path:
            result[num] = path
    return result


def _render_plate_thumbnail(orca: str, stored_path: str, num: int, thumb_dir: Path) -> str | None:
    """OrcaSlicer thumbnail execution seam — replace this function body to switch backends.

    Renders plate *num* of *stored_path* without rearranging geometry (--arrange 0),
    extracts the PNG from the output 3MF, saves it to *thumb_dir/plate_{num}.png*,
    and returns the saved path. Returns None on any failure.

    OrcaSlicer may renumber plates in single-plate export (always outputs plate_1.png
    regardless of requested plate), so we probe both plate_{num}.png and plate_1.png.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out_3mf = Path(tmpdir) / f"thumb_p{num}.gcode.3mf"
        cmd = [
            orca,
            "--slice", str(num),
            "--outputdir", tmpdir,
            "--arrange", "0",
            "--export-3mf", str(out_3mf),
            stored_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            logger.warning("Thumbnail regen timed out for %s plate %d", stored_path, num)
            return None
        except Exception as exc:
            logger.warning("Thumbnail regen error for plate %d: %s", num, exc)
            return None

        if not out_3mf.exists():
            logger.debug("OrcaSlicer produced no output for %s plate %d", stored_path, num)
            return None

        try:
            with zipfile.ZipFile(out_3mf) as zf:
                names = set(zf.namelist())
                candidate = next(
                    (k for k in (f"Metadata/plate_{num}.png", "Metadata/plate_1.png") if k in names),
                    None,
                )
                if candidate is None:
                    logger.debug("No plate thumbnail in output for plate %d", num)
                    return None
                dest = thumb_dir / f"plate_{num}.png"
                dest.write_bytes(zf.read(candidate))
                return str(dest)
        except Exception as exc:
            logger.warning("Failed to extract thumbnail for plate %d: %s", num, exc)
            return None
