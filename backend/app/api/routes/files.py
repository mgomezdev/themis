from __future__ import annotations
import asyncio
import contextlib
import io
import os
import logging
from datetime import datetime, timezone
import mimetypes
import uuid
from pathlib import Path
from typing import AsyncIterator, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import config
from ...auth import require_scope
from ...database import get_session
from ...models import UploadedFile, Tag, FileTag, Job, Printer, ProjectItem, SlicedVersion
from ...services.library_scanner import (
    LibraryScanner, file_kind, folder_of, is_presliced_name, ACTIVE_JOB_STATUSES, MODEL_EXTS,
)
from ...services import gcode_eligibility, model_targets, slice_cache, slicer_profiles
from ...services.artifact_store import (
    ArtifactError, ArtifactExists, ArtifactNotFound, ArtifactStore, InvalidArtifactKey, PART_SUFFIX, validate_key,
)
from ...services.library_store import (
    folder_key, get_filecache_store, get_library_store, is_reserved_name, join_key, key_name, move_unique, unique_key, with_local_path,
)
from ...services.queue_engine import queue_engine
from ...services.providers.slicing import get_slicing_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/files", tags=["files"])


# ---------- helpers ----------

def _safe_subpath(root: Path, relative: str) -> Path:
    """Resolve `relative` under `root`, rejecting traversal outside it."""
    target = (root / relative.strip("/\\")).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(400, "Path escapes the library root")
    return target


def _content_disposition(filename: str) -> str:
    """Same header Starlette's FileResponse(filename=...) builds."""
    try:
        filename.encode("ascii")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        return f"attachment; filename*=utf-8''{quote(filename)}"


async def _artifact_response(store: ArtifactStore, key: str, media_type: str, filename: str | None = None) -> StreamingResponse:
    """Stream a stored artifact without a local path (a remote store has none). `ArtifactNotFound` -> 404 by the caller."""
    fh = await asyncio.to_thread(store.open, key)
    try:
        size = os.fstat(fh.fileno()).st_size   # the opened inode, so a concurrent overwrite cannot desync the length
    except (AttributeError, OSError, io.UnsupportedOperation):   # non-file handle (remote backend)
        size = (await asyncio.to_thread(store.stat, key)).size

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in iterate_in_threadpool(iter(lambda: fh.read(1024 * 1024), b"")):
                yield chunk
        finally:
            fh.close()

    headers = {"content-length": str(size)}
    if filename is not None:
        headers["content-disposition"] = _content_disposition(filename)
    return StreamingResponse(body(), media_type=media_type, headers=headers)


async def _tags_for(session: AsyncSession, file_ids: list[int]) -> dict[int, list[dict]]:
    if not file_ids:
        return {}
    rows = (await session.execute(
        select(FileTag.file_id, Tag).join(Tag, Tag.id == FileTag.tag_id)
        .where(FileTag.file_id.in_(file_ids))
    )).all()
    out: dict[int, list[dict]] = {}
    for file_id, tag in rows:
        out.setdefault(file_id, []).append(
            {"id": tag.id, "name": tag.name, "color": tag.color, "category": tag.category})
    return out


async def _cache_for(session: AsyncSession, file_ids: list[int]) -> dict:
    """Slicing-cache facts for these files, batched: `counts` = how many cached versions each model has (versions
    whose file is present), `versions` = the version a cached file *is* (BIZ-193/196)."""
    if not file_ids:
        return {"counts": {}, "versions": {}, "eligibility": {}}
    eligibility = await gcode_eligibility.eligibility_for_files(session, file_ids)
    counts = dict((await session.execute(
        select(SlicedVersion.source_file_id, func.count(SlicedVersion.id))
        .join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
        .where(SlicedVersion.source_file_id.in_(file_ids), UploadedFile.missing.is_(False))
        .group_by(SlicedVersion.source_file_id)
    )).all())
    versions = {v.file_id: v for v in (await session.execute(
        select(SlicedVersion).where(SlicedVersion.file_id.in_(file_ids))
    )).scalars().all()}
    source_ids = {v.source_file_id for v in versions.values() if v.source_file_id}
    sources = {f.id: f for f in (await session.execute(
        select(UploadedFile).where(UploadedFile.id.in_(source_ids)))).scalars().all()} if source_ids else {}
    return {"counts": counts, "versions": versions, "sources": sources, "eligibility": eligibility}


def _version_summary(v: SlicedVersion, source: UploadedFile | None) -> dict:
    return {
        "id": v.id, "source_file_id": v.source_file_id,
        "source_filename": source.original_filename if source else None, "plate_number": v.plate_number,
        "machine_preset": v.machine_preset, "process_preset": v.process_preset,
        "filament_presets": v.filament_presets, "filament_type": v.filament_type, "filament_color": v.filament_color,
    }


def _to_dict(f: UploadedFile, tags: list[dict], cache: dict | None = None) -> dict:
    version = (cache or {}).get("versions", {}).get(f.id)
    source = (cache or {}).get("sources", {}).get(version.source_file_id) if version else None
    thumb = _thumb_url(f)
    if thumb is None and source is not None:   # a cached gcode with no embedded preview shows its model's plate
        thumb = next((t["thumbnail_url"] for t in _plate_thumbnail_urls(source)
                      if t["plate_number"] == version.plate_number), None) or _thumb_url(source)
    return {
        "id": f.id,
        "original_filename": f.original_filename,
        "relative_path": f.relative_path,
        "folder": f.folder,
        "size_bytes": f.size_bytes,
        "plate_count": len(f.plates or []),
        "uploaded_at": f.uploaded_at,
        "missing": f.missing,
        "kind": file_kind(f.original_filename),
        # Slicing cache: how many cached versions this model has / the version this cached file is.
        "sliced_version_count": (cache or {}).get("counts", {}).get(f.id, 0),
        "sliced_version": _version_summary(version, source) if version else None,
        # Pre-sliced G-code only (BIZ-263): `known` False = legacy/unknown machine eligibility; `model_uuids` = the printer models
        # it may be sent to (core registry ids). Null for model files, which the slicer targets at the printer.
        "eligibility": ({"known": bool(f.eligibility_known), "model_uuids": (cache or {}).get("eligibility", {}).get(f.id, [])}
                        if is_presliced_name(f.original_filename) else None),
        "tags": tags,
        "thumbnail_url": thumb,
        "plate_thumbnails": _plate_thumbnail_urls(f),
    }


def _plate_thumbnail_urls(f: UploadedFile) -> list[dict]:
    return [
        {"plate_number": p["plate_number"],
         "thumbnail_url": f"/api/v1/files/{f.id}/thumbnails/{Path(p['thumbnail_path']).name}"}
        for p in (f.plates or []) if p.get("thumbnail_path")
    ]


def _thumb_url(f: UploadedFile) -> str | None:
    urls = _plate_thumbnail_urls(f)
    return urls[0]["thumbnail_url"] if urls else None


def _tree_insert(root: dict, folder: str) -> None:
    node, path = root, ""
    for part in (p for p in folder.split("/") if p):
        path += "/" + part
        node = node["children"].setdefault(part, {"name": part, "path": path, "count": 0, "children": {}})
        node["count"] += 1


# ---------- list / tree ----------

@router.get("", summary="List files", dependencies=[Depends(require_scope("files:read"))])
async def list_files(
    folder: str | None = None,
    tags: list[str] | None = Query(None),  # must be Query(): a bare list[str] is read as a JSON *body* on GET and silently ignored
    search: str | None = None,
    sort: str = "updated",
    kind: Literal["all", "models", "sliced"] = Query("all"),
    eligible_model: str | None = Query(None, description="Only pre-sliced files eligible for this printer-model UUID"),
    include_unknown_eligibility: bool = Query(False, description="With eligible_model: also list pre-sliced files whose eligibility is unknown"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """All files in the library. Optional filters: `folder` (prefix match),
    `tags` (all supplied tags must be present), `search` (filename substring),
    `kind` (`models` = sliceable .3mf/.stl, `sliced` = pre-sliced .gcode/.gcode.3mf, `all` default).
    `sort` accepts `updated` (default), `name`, or `size`."""
    rows = (await session.execute(select(UploadedFile))).scalars().all()
    if kind != "all":
        want_sliced = kind == "sliced"
        rows = [r for r in rows if is_presliced_name(r.original_filename) == want_sliced]
    if eligible_model:
        by_file = await gcode_eligibility.eligibility_for_files(session, [r.id for r in rows])
        rows = [r for r in rows if is_presliced_name(r.original_filename)
                and ((r.eligibility_known and eligible_model in by_file.get(r.id, []))
                     or (include_unknown_eligibility and not r.eligibility_known))]
    if folder:
        rows = [r for r in rows if r.folder == folder or r.folder.startswith(folder.rstrip("/") + "/")]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.original_filename or "").lower()]
    tag_map = await _tags_for(session, [r.id for r in rows])
    if tags:
        wanted = set(tags)
        rows = [r for r in rows if wanted.issubset({t["name"] for t in tag_map.get(r.id, [])})]
    if sort == "name":
        rows.sort(key=lambda r: (r.original_filename or "").lower())
    elif sort == "size":
        rows.sort(key=lambda r: r.size_bytes, reverse=True)
    else:
        rows.sort(key=lambda r: r.uploaded_at, reverse=True)
    cache = await _cache_for(session, [r.id for r in rows])
    return [_to_dict(r, tag_map.get(r.id, []), cache) for r in rows]


@router.get("/tree", summary="Folder tree (index-derived)", dependencies=[Depends(require_scope("files:read"))])
async def folder_tree(session: AsyncSession = Depends(get_session)) -> dict:
    """Hierarchical folder tree derived from the file index. Each node has
    `name`, `path`, `count` (files in this folder and below), and `children`.
    Empty folders are not included — use `/dirs` for those."""
    rows = (await session.execute(select(UploadedFile))).scalars().all()
    root: dict = {"name": "All files", "path": "", "count": 0, "children": {}}
    for r in rows:
        root["count"] += 1
        _tree_insert(root, r.folder)
    return root


@router.get("/dirs", summary="Folder tree with empty dirs", dependencies=[Depends(require_scope("files:read"))])
async def folder_dirs(session: AsyncSession = Depends(get_session)) -> dict:
    """Folder hierarchy from the actual on-disk library directory — includes
    EMPTY folders (unlike /tree, which is index-derived) — with recursive file
    counts overlaid from the index. Used by the move-destination picker."""
    library = config.get_library_dir()
    root: dict = {"name": "All files", "path": "", "count": 0, "children": {}}

    def _ensure(parts: list[str]) -> None:
        node = root
        path = ""
        for part in parts:
            path += "/" + part
            node = node["children"].setdefault(
                part, {"name": part, "path": path, "count": 0, "children": {}})

    # 1) skeleton from real directories (so empty folders appear)
    if library.exists():
        for d in sorted(p for p in library.rglob("*") if p.is_dir()):
            rel = d.relative_to(library).as_posix()
            _ensure([p for p in rel.split("/") if p])

    # 2) overlay recursive file counts from the index
    rows = (await session.execute(select(UploadedFile))).scalars().all()
    for r in rows:
        root["count"] += 1
        _tree_insert(root, r.folder)
    return root


# ---------- upload ----------

@router.post(
    "/upload",
    status_code=201,
    summary="Upload a file",
    responses={
        422: {"description": "Unsupported file type (only .3mf, .stl and .gcode accepted)"},
    },
    dependencies=[Depends(require_scope("files:write"))],
)
async def upload_file(
    file: UploadFile,
    folder: str = Form("/Job Uploads"),
    session: AsyncSession = Depends(get_session),
    eligible_model_uuids: list[str] = Form([]),
) -> dict:
    """Upload a .3mf, .stl, .gcode or .gcode.3mf (sliced archive) file to the library. If identical content already exists
    in the target folder the existing record is returned (deduplication by SHA-256).
    Thumbnail generation is triggered in the background for (unsliced) .3mf files.
    A pre-sliced file may carry `eligible_model_uuids` (printer-model registry ids it may be sent to); without them its machine
    eligibility is unknown until set (BIZ-263)."""
    if not isinstance(eligible_model_uuids, list):    # other routes call this function directly: Form's default object is "none given"
        eligible_model_uuids = []
    fname = (file.filename or "")
    ext = Path(fname).suffix.lower()
    if ext not in MODEL_EXTS:
        raise HTTPException(422, "Only .3mf, .stl and .gcode files are accepted")

    library = config.get_library_dir()
    folder_abs = _safe_subpath(library, folder)
    folder_abs.mkdir(parents=True, exist_ok=True)
    rel_dir = folder_abs.relative_to(library).as_posix()
    # folder_of() prepends "/" and uses the parent — mirror that for the root case too
    target_folder = "/" if rel_dir == "." else "/" + rel_dir
    folder_k = "" if rel_dir == "." else rel_dir
    clean_name = Path(fname).name
    try:
        validate_key(join_key(folder_k, clean_name))
    except InvalidArtifactKey:
        raise HTTPException(400, "Invalid filename")

    # Stream into a scratch artifact in the target folder (hashed while streaming; multi-hundred-MB packed plates are
    # routine, never whole in memory). The content hash decides dedup, and the collision-free final name is claimed
    # with an exclusive move, so a short scratch name also sidesteps any name-length limit.
    store = get_library_store()
    scratch = join_key(folder_k, f".upload-{uuid.uuid4().hex}{PART_SUFFIX}")
    try:
        info = await asyncio.to_thread(store.put, scratch, file.file)
        incoming_hash = info.sha256

        # Dedup: return existing record if same content is already in this folder.
        existing = (await session.execute(
            select(UploadedFile)
            .where(UploadedFile.content_hash == incoming_hash)
            .where(UploadedFile.folder == target_folder)
            .limit(1)
        )).scalar_one_or_none()
        if existing:
            if not await asyncio.to_thread(store.exists, existing.relative_path):
                # Orphaned record: file was deleted from disk but the DB row was not
                # cleaned up. Restore it from the just-uploaded bytes so the record
                # stays valid.
                with contextlib.suppress(ArtifactExists):   # lost a race to another restore: theirs is the same bytes
                    await asyncio.to_thread(store.move, scratch, existing.relative_path)
            if eligible_model_uuids and is_presliced_name(existing.original_filename):
                await _apply_eligibility(session, existing, eligible_model_uuids)
                await session.commit()
            return _to_dict(existing, [], await _cache_for(session, [existing.id]))

        placed = await asyncio.to_thread(move_unique, store, scratch, folder_k, clean_name)
    finally:
        await asyncio.to_thread(store.delete, scratch, missing_ok=True)  # no-op once moved

    key = placed.key
    scanner = LibraryScanner(session, library, config.get_filecache_dir(),
                             library_store=store, filecache_store=get_filecache_store())
    record = UploadedFile(
        original_filename=key_name(key), relative_path=key,
        folder=folder_of(key), size_bytes=placed.size, content_hash=incoming_hash,
        mtime=placed.mtime, plates=[], missing=False,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(record)
    await session.flush()
    record.plates = await asyncio.to_thread(scanner.parse_plates, key, record.id)
    if eligible_model_uuids and is_presliced_name(record.original_filename):
        try:
            await _apply_eligibility(session, record, eligible_model_uuids)
        except HTTPException:
            await asyncio.to_thread(store.delete, key, missing_ok=True)   # a rejected eligibility must not leave an unregistered upload behind
            await session.rollback()
            raise
    await session.commit()
    await session.refresh(record)
    return _to_dict(record, [], await _cache_for(session, [record.id]))


async def _apply_eligibility(session: AsyncSession, record: UploadedFile, model_uuids: list[str]) -> None:
    try:
        await gcode_eligibility.set_eligibility(session, record, model_uuids)
    except ValueError as e:
        raise HTTPException(422, str(e))


class EligibilityBody(BaseModel):
    model_uuids: list[str]


@router.get("/{file_id}/eligibility", summary="Machine eligibility of a pre-sliced G-code file",
            responses={404: {"description": "File not found"}}, dependencies=[Depends(require_scope("files:read"))])
async def get_eligibility(file_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """`{known, models: [{model_uuid, source, display_name, manufacturer_name}]}`. `known: false` = legacy/unknown: it is never
    treated as a confirmed match, and a job for it needs explicit confirmation."""
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return await gcode_eligibility.eligibility_of(session, file_id)


@router.put("/{file_id}/eligibility", summary="Set the printer models a pre-sliced G-code file may be sent to",
            responses={404: {"description": "File not found"}, 422: {"description": "Not a pre-sliced file, or an unknown model"}},
            dependencies=[Depends(require_scope("files:write"))])
async def put_eligibility(file_id: int, body: EligibilityBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Replace the file's eligibility with exactly these registry models. Queued jobs are re-checked at their next queue pull."""
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    await _apply_eligibility(session, record, body.model_uuids)
    await session.commit()
    queue_engine.wake()
    return await gcode_eligibility.eligibility_of(session, file_id)


# ---------- folders ----------

class FolderCreate(BaseModel):
    path: str


@router.post(
    "/folders",
    status_code=201,
    summary="Create folder",
    responses={
        400: {"description": "Path escapes the library root"},
    },
    dependencies=[Depends(require_scope("files:write"))],
)
async def create_folder(body: FolderCreate) -> dict:
    """Create a directory in the library. Parent directories are created as needed."""
    library = config.get_library_dir()
    target = _safe_subpath(library, body.path)
    target.mkdir(parents=True, exist_ok=True)
    return {"path": "/" + target.relative_to(library).as_posix()}


@router.delete(
    "/folders",
    summary="Delete empty folder",
    responses={
        400: {"description": "Cannot delete the library root or Job Uploads folder"},
        404: {"description": "Folder not found"},
        409: {"description": "Folder is not empty"},
    },
    dependencies=[Depends(require_scope("files:write"))],
)
async def delete_folder(path: str) -> dict:
    """Delete an EMPTY folder. Refuses (409) if it contains any files or
    subfolders, and never deletes the library root."""
    library = config.get_library_dir()
    target = _safe_subpath(library, path)
    if target.resolve() == library.resolve():
        raise HTTPException(400, "Cannot delete the library root")
    if target.resolve() == (library / "Job Uploads").resolve():
        raise HTTPException(400, "The Job Uploads folder cannot be deleted")
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "Folder not found")
    if any(target.iterdir()):
        raise HTTPException(409, "Folder is not empty — remove its contents first")
    target.rmdir()
    return {"deleted": "/" + target.relative_to(library).as_posix()}


# ---------- rename / move ----------

class FilePatch(BaseModel):
    name: str | None = None
    folder: str | None = None


@router.patch(
    "/{file_id}",
    summary="Rename or move file",
    responses={
        400: {"description": "Invalid filename or path escapes the library root"},
        404: {"description": "File not found"},
    },
    dependencies=[Depends(require_scope("files:write"))],
)
async def update_file(file_id: int, body: FilePatch,
                      session: AsyncSession = Depends(get_session)) -> dict:
    """Rename a file, move it to a different folder, or both. The destination folder
    is created if it does not exist. Filename path separators are stripped to prevent
    directory traversal."""
    f = await session.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    library = config.get_library_dir()
    store = get_library_store()
    src_key = f.relative_path
    new_folder = body.folder if body.folder is not None else f.folder
    # Strip all path components from the supplied name so that traversal
    # sequences like "../../../evil.stl" cannot escape the library root.
    if body.name is not None:
        new_name = Path(body.name).name
        if not new_name or new_name in (".", "..") or is_reserved_name(new_name):
            raise HTTPException(400, "Invalid filename")
    else:
        new_name = f.original_filename
    folder_abs = _safe_subpath(library, new_folder)
    folder_abs.mkdir(parents=True, exist_ok=True)
    rel_dir = folder_abs.relative_to(library).as_posix()
    dest_folder = "" if rel_dir == "." else rel_dir
    try:
        validate_key(join_key(dest_folder, new_name))
    except InvalidArtifactKey:
        raise HTTPException(400, "Invalid filename")
    on_disk = await asyncio.to_thread(store.exists, src_key)
    # No-op when the target is the file's existing location: skip the move so we
    # don't collide with the file itself and rename it to "name (2).ext".
    if on_disk and dest_folder == folder_key(src_key) and new_name == key_name(src_key):
        tag_map = await _tags_for(session, [f.id])
        return _to_dict(f, tag_map.get(f.id, []), await _cache_for(session, [f.id]))
    if on_disk:
        dest_key = (await asyncio.to_thread(move_unique, store, src_key, dest_folder, new_name)).key
    else:   # the bytes are gone: only the row follows
        dest_key = await asyncio.to_thread(unique_key, store, dest_folder, new_name)
    moved_folder = folder_of(dest_key) != f.folder
    f.original_filename = key_name(dest_key)
    f.relative_path = dest_key
    f.folder = folder_of(dest_key)
    if moved_folder:   # its cached versions live "next to the model": they follow a move (not a rename)
        try:
            await _move_versions_with(session, store, f.id, dest_folder)
        except HTTPException:
            if on_disk and await asyncio.to_thread(store.exists, dest_key) and not await asyncio.to_thread(store.exists, src_key):
                try:
                    await asyncio.to_thread(store.move, dest_key, src_key)   # the model goes back too: nothing moved, nothing committed
                except ArtifactError:
                    logger.exception("Couldn't put %s back at %s after a failed version move", dest_key, src_key)
            raise
    await session.commit()
    tag_map = await _tags_for(session, [f.id])
    return _to_dict(f, tag_map.get(f.id, []), await _cache_for(session, [f.id]))


async def _move_versions_with(session: AsyncSession, store: ArtifactStore, model_id: int, dest_folder: str) -> None:
    rows = (await session.execute(
        select(UploadedFile).join(SlicedVersion, SlicedVersion.file_id == UploadedFile.id)
        .where(SlicedVersion.source_file_id == model_id)
    )).scalars().all()
    done: list[tuple[str, str]] = []
    try:
        for vf in rows:
            vsrc = vf.relative_path
            if folder_key(vsrc) == dest_folder or not await asyncio.to_thread(store.exists, vsrc):
                continue   # gone, or already next to the model (don't rename it against itself)
            vdest = (await asyncio.to_thread(move_unique, store, vsrc, dest_folder, vf.original_filename)).key
            done.append((vdest, vsrc))
            vf.original_filename, vf.relative_path, vf.folder = key_name(vdest), vdest, folder_of(vdest)
    except ArtifactError as exc:
        for moved_to, came_from in reversed(done):   # put the files back where the (uncommitted) rows say they are
            with contextlib.suppress(ArtifactError):
                await asyncio.to_thread(store.move, moved_to, came_from)
        raise HTTPException(500, f"Couldn't move the model's sliced versions: {exc}") from exc


# ---------- delete ----------

@router.delete(
    "/{file_id}",
    summary="Delete file",
    responses={
        404: {"description": "File not found"},
        409: {"description": "File (or a cached version being deleted with it) is referenced by an active job or a "
                            "project item, or the model has cached versions and `versions` wasn't given"},
    },
    dependencies=[Depends(require_scope("files:write"))],
)
async def delete_file(
    file_id: int,
    versions: Literal["delete", "keep"] | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a library file. A model with cached sliced versions needs `versions`: `delete` removes them too,
    `keep` leaves them as standalone gcode (no longer linked to a model). Without it the answer is 409 listing them."""
    f = await session.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    await _refuse_if_in_use(session, f)
    linked = (await session.execute(
        select(SlicedVersion, UploadedFile).join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
        .where(SlicedVersion.source_file_id == file_id).order_by(SlicedVersion.id)
    )).all()
    if linked and versions is None:
        raise HTTPException(409, {
            "message": "This model has cached sliced versions — delete them too, or keep them as standalone gcode",
            "versions": [{"id": v.id, "file_id": vf.id, "name": vf.original_filename, "folder": vf.folder}
                         for v, vf in linked],
        })
    doomed = [f]
    if linked and versions == "delete":
        for _, vf in linked:   # all or nothing: refuse before deleting anything
            await _refuse_if_in_use(session, vf)
        doomed += [vf for _, vf in linked]
    elif linked:   # keep: they stay as plain gcode, no longer linked to the model
        for v, _ in linked:
            v.source_file_id = None

    # A file that finished jobs printed can't lose its row (job history points at it): like a file that vanished from
    # disk (see LibraryScanner.scan), its bytes go and the row stays, marked missing.
    printed = set((await session.execute(
        select(Job.uploaded_file_id).where(Job.uploaded_file_id.in_([d.id for d in doomed])))).scalars().all())
    keys = [(d.id, d.relative_path, d.id not in printed) for d in doomed]
    for d in doomed:
        if d.id in printed:
            d.missing = True
            continue
        for link in (await session.execute(select(FileTag).where(FileTag.file_id == d.id))).scalars().all():
            await session.delete(link)
        await session.delete(d)
    await session.commit()

    # Only touch the filesystem after the DB is committed — otherwise a commit failure (e.g. an unanticipated FK
    # reference) leaves the file deleted but the row still pointing at it.
    store, cache = get_library_store(), get_filecache_store()
    for did, key, row_gone in keys:
        # The rows are already committed: one unremovable file (a stale row with an invalid/escaping key, an I/O error)
        # must not turn the delete into a 500 or leave the remaining files behind.
        try:
            await asyncio.to_thread(store.delete, key, missing_ok=True)
        except ArtifactError:
            logger.exception("Couldn't remove library file %r of deleted record %s", key, did)
        if row_gone:   # a kept (missing) row keeps its thumbnails for the job history
            try:
                await asyncio.to_thread(cache.delete_prefix, str(did))
            except ArtifactError:
                logger.exception("Couldn't remove thumbnails of deleted record %s", did)
    return {"deleted": file_id, "deleted_versions": [d.id for d in doomed[1:]]}


async def _refuse_if_in_use(session: AsyncSession, f: UploadedFile) -> None:
    active = (await session.execute(
        select(Job.id).where(Job.uploaded_file_id == f.id,
                             Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
    )).first()
    if active:
        raise HTTPException(409, f"{f.original_filename} is referenced by an active job")
    referenced = (await session.execute(
        select(ProjectItem.id).where(ProjectItem.file_id == f.id).limit(1)
    )).first()
    if referenced:
        raise HTTPException(409, f"{f.original_filename} is referenced by a project item")


# ---------- tag assign / unassign ----------

class TagAssign(BaseModel):
    tag_id: int


@router.post(
    "/{file_id}/tags",
    summary="Assign tag to file",
    responses={
        404: {"description": "File or tag not found"},
    },
    dependencies=[Depends(require_scope("files:write"))],
)
async def add_file_tag(file_id: int, body: TagAssign,
                       session: AsyncSession = Depends(get_session)) -> dict:
    """Assign a tag to a file. Idempotent — no error if the tag is already assigned."""
    if await session.get(UploadedFile, file_id) is None:
        raise HTTPException(404, f"File {file_id} not found")
    if await session.get(Tag, body.tag_id) is None:
        raise HTTPException(404, f"Tag {body.tag_id} not found")
    existing = (await session.execute(
        select(FileTag).where(FileTag.file_id == file_id, FileTag.tag_id == body.tag_id)
    )).scalar_one_or_none()
    if existing is None:
        session.add(FileTag(file_id=file_id, tag_id=body.tag_id))
        await session.commit()
    return {"file_id": file_id, "tag_id": body.tag_id}


@router.delete("/{file_id}/tags/{tag_id}", summary="Remove tag from file",
              dependencies=[Depends(require_scope("files:write"))])
async def remove_file_tag(file_id: int, tag_id: int,
                          session: AsyncSession = Depends(get_session)) -> dict:
    """Remove a tag from a file. Idempotent — no error if the tag is not assigned."""
    link = (await session.execute(
        select(FileTag).where(FileTag.file_id == file_id, FileTag.tag_id == tag_id)
    )).scalar_one_or_none()
    if link is not None:
        await session.delete(link)
        await session.commit()
    return {"file_id": file_id, "tag_id": tag_id}


# ---------- rescan ----------

@router.post("/rescan", summary="Rescan library", dependencies=[Depends(require_scope("files:write"))])
async def rescan(session: AsyncSession = Depends(get_session)) -> dict:
    """Walk the library directory and sync the file index — adds missing records,
    marks orphaned records as missing, and re-parses plate metadata."""
    scanner = LibraryScanner(session, config.get_library_dir(), config.get_filecache_dir(),
                             library_store=get_library_store(), filecache_store=get_filecache_store())
    return await scanner.scan()


# ---------- slicing cache ----------

@router.get(
    "/{file_id}/sliced-versions",
    summary="List a model's cached sliced versions",
    responses={404: {"description": "File not found"}},
    dependencies=[Depends(require_scope("files:read"))],
)
async def list_sliced_versions(
    file_id: int, plate: int | None = None, session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """The cached slices of this model (BIZ-193), newest first, optionally for one `plate`; versions whose file is
    missing are left out. Each carries the settings it was sliced with and three flags: `source_changed` (the model
    changed since), `stale` (`true`/`false`/`null` = unknown — presets or OrcaSlicer changed since, with
    `stale_reasons`), and `printable_now` (an enabled printer of that make/model can take the file)."""
    model = await session.get(UploadedFile, file_id)
    if model is None:
        raise HTTPException(404, f"File {file_id} not found")
    q = (select(SlicedVersion, UploadedFile).join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
         .where(SlicedVersion.source_file_id == file_id, UploadedFile.missing.is_(False))
         .order_by(SlicedVersion.id.desc()))
    if plate is not None:
        q = q.where(SlicedVersion.plate_number == plate)
    rows = (await session.execute(q)).all()
    printers = (await session.execute(select(Printer).where(Printer.enabled.is_(True)))).scalars().all()
    profiles = await slicer_profiles.resolve_many(session, list(printers))
    models_by_profile: dict[str, list[str]] = {}
    for m in await slicer_profiles.mapping_rows(session):       # the models whose slicer profile a version was sliced for
        models_by_profile.setdefault(m.machine_profile, [])
        if m.model_uuid not in models_by_profile[m.machine_profile]:
            models_by_profile[m.machine_profile].append(m.model_uuid)
    slicing = get_slicing_provider()
    fkeys = list(dict.fromkeys((v.machine_preset, v.process_preset, tuple(v.filament_presets or [])) for v, _ in rows))
    results = await asyncio.gather(*(asyncio.to_thread(slice_cache.cached_fingerprint, m, pr, list(fl), slicing)
                                     for m, pr, fl in fkeys))
    fingerprints = dict(zip(fkeys, results))
    out = []
    for v, f in rows:
        fkey = (v.machine_preset, v.process_preset, tuple(v.filament_presets or []))
        stale, reasons = slice_cache.staleness(v.preset_content_hash, v.slicer_version, fingerprints[fkey])
        overrides = {k: val for k, val in (v.extra_config or {}).items() if k != "curr_bed_type"}
        out.append({
            "id": v.id, "file_id": f.id, "name": f.original_filename, "kind": file_kind(f.original_filename),
            "plate_number": v.plate_number, "machine_preset": v.machine_preset, "process_preset": v.process_preset,
            "filament_presets": v.filament_presets, "filament_type": v.filament_type,
            "filament_color": v.filament_color, "bed_type": (v.extra_config or {}).get("curr_bed_type"),
            "overrides": overrides, "estimated_seconds": v.estimated_seconds, "filament_grams": v.filament_grams,
            "created_at": v.created_at,
            "source_changed": bool(model.content_hash) and model.content_hash != v.source_content_hash,
            "stale": stale, "stale_reasons": reasons,
            "model_uuids": models_by_profile.get(v.machine_preset, []),
            "printable_now": any(profiles[p.id].profile == v.machine_preset
                                 and model_targets.accepts_file(p.printer_type, f.original_filename)
                                 for p in printers),
        })
    return out


# ---------- plates / thumbnails ----------

@router.get(
    "/{file_id}/plates",
    summary="Get file plates",
    responses={
        404: {"description": "File not found"},
    },
    dependencies=[Depends(require_scope("files:read"))],
)
async def get_plates(file_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Plate metadata extracted from the 3MF (estimated time, filament grams, thumbnail path)."""
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return {"filename": record.original_filename, "plates": record.plates or []}


@router.get(
    "/{file_id}/model-filaments",
    summary="Get 3MF model filaments",
    responses={
        404: {"description": "File not found"},
    },
    dependencies=[Depends(require_scope("files:read"))],
)
async def get_model_filaments(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Filament definitions embedded in the 3MF model XML (type, colour, vendor)."""
    from ...services.three_mf_parser import parse_model_filaments
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return await asyncio.to_thread(with_local_path, get_library_store(), record.relative_path, parse_model_filaments, [])


@router.get(
    "/{file_id}/embedded-settings",
    summary="Get 3MF embedded settings",
    responses={
        404: {"description": "File not found"},
    },
    dependencies=[Depends(require_scope("files:read"))],
)
async def get_embedded_settings(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    """OrcaSlicer project settings embedded in `Metadata/project_settings.config` inside the 3MF."""
    from ...services.three_mf_parser import parse_embedded_settings
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return await asyncio.to_thread(with_local_path, get_library_store(), record.relative_path, parse_embedded_settings, [])


@router.get(
    "/{file_id}/thumbnails/{filename}",
    summary="Serve plate thumbnail",
    responses={
        400: {"description": "Invalid thumbnail filename"},
        404: {"description": "File or thumbnail not found"},
    },
    dependencies=[Depends(require_scope("files:read"))],
)
async def get_thumbnail(file_id: int, filename: str,
                        session: AsyncSession = Depends(get_session)) -> StreamingResponse:
    """Return a PNG thumbnail for a specific plate. The filename is the basename
    of the thumbnail path from the plates list."""
    if await session.get(UploadedFile, file_id) is None:
        raise HTTPException(404, f"File {file_id} not found")
    try:   # validate_key refuses ".", ".." and empty segments exactly (not by substring), plus backslashes
        return await _artifact_response(get_filecache_store(), f"{file_id}/thumbnails/{filename}", "image/png")
    except InvalidArtifactKey:
        raise HTTPException(400, "Invalid filename")
    except ArtifactNotFound:
        raise HTTPException(404, "Thumbnail not found")


@router.get("/{file_id}/download", summary="Download raw file",
           dependencies=[Depends(require_scope("files:read"))])
async def download_file(file_id: int, session: AsyncSession = Depends(get_session)) -> StreamingResponse:
    """Serve the raw uploaded file (STL, 3MF, etc.) for client-side use."""
    f = await session.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    media_type = mimetypes.guess_type(f.original_filename)[0] or "application/octet-stream"
    try:
        return await _artifact_response(get_library_store(), f.relative_path, media_type, f.original_filename)
    except InvalidArtifactKey:
        raise HTTPException(400, "Invalid file path")
    except ArtifactNotFound:
        raise HTTPException(404, "File data not found")
