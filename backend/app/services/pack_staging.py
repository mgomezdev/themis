"""Staged pack files (GH #269).

Project generation writes each new pack's 3MF under `Job Pack 3MFs/_tmp_<id>/` and commits its `UploadedFile` row before
the pack's jobs, then moves it to its job-ID folder. A pack whose jobs never commit (a refused takeover, a lost requeue
claim, a failure or crash between the two commits) would leave that file and row behind as a stray library entry. A staged
file that jobs reference (its post-commit move failed, GH #261) is live and is kept.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Job, Project, ProjectItem, SlicedVersion, UploadedFile
from .artifact_store import ArtifactStore

logger = logging.getLogger(__name__)

PACK_FOLDER = "Job Pack 3MFs"
STAGING_PREFIX = f"{PACK_FOLDER}/_tmp_"


def is_staged_key(key: str) -> bool:
    return key.startswith(STAGING_PREFIX)


def _referenced(file_id):
    return (exists().where(Job.uploaded_file_id == file_id)
            | exists().where(ProjectItem.file_id == file_id)
            | exists().where(Project.result_file_id == file_id)
            # a pack sliced and cached (it stayed staged after a failed GH #261 move): its gcode is kept, so is it
            | exists().where(SlicedVersion.source_file_id == file_id))


async def discard_if_unreferenced(factory: async_sessionmaker, store: ArtifactStore, key: str,
                                  file_id: int | None) -> bool:
    """Delete a staged pack's row (if `file_id`) and file unless something references the row. True if discarded.
    The row is deleted in its own short transaction, guarded by the reference check in the same statement, so a pack whose
    jobs did commit is never removed."""
    if not is_staged_key(key):
        return False
    async with factory() as side:
        if file_id is None:     # its row may have committed although the caller never learnt its id
            file_id = (await side.execute(select(UploadedFile.id).where(UploadedFile.relative_path == key))).scalar()
    if file_id is not None:
        async with factory() as side:
            gone = await side.execute(delete(UploadedFile).where(UploadedFile.id == file_id, ~_referenced(UploadedFile.id)))
            if gone.rowcount == 0 and await side.get(UploadedFile, file_id) is not None:
                return False             # referenced: live, keep it
            await side.commit()
    await asyncio.to_thread(store.delete, key, missing_ok=True)
    return True


async def sweep(factory: async_sessionmaker, store: ArtifactStore) -> int:
    """Remove staged packs left by an earlier process: files under the staging folder whose row is missing or
    unreferenced. Run at startup, before any generation can be staging. Returns the number of files removed."""
    keys = [info.key for info in await asyncio.to_thread(lambda: list(store.list(PACK_FOLDER))) if is_staged_key(info.key)]
    removed = 0
    for key in keys:
        try:
            if await discard_if_unreferenced(factory, store, key, None):
                removed += 1
                logger.info("Removed stray staged pack %s", key)
        except Exception:
            logger.exception("Could not remove stray staged pack %s", key)
    return removed
