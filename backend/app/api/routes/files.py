from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import config
from ...database import get_session
from ...models import UploadedFile, Tag, FileTag, Job
from ...services.library_scanner import (
    LibraryScanner, folder_of, sha256_file, ACTIVE_JOB_STATUSES, MODEL_EXTS,
)

router = APIRouter(prefix="/api/v1/files", tags=["files"])


# ---------- helpers ----------

def _safe_subpath(root: Path, relative: str) -> Path:
    """Resolve `relative` under `root`, rejecting traversal outside it."""
    target = (root / relative.strip("/\\")).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(400, "Path escapes the library root")
    return target


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


def _to_dict(f: UploadedFile, tags: list[dict]) -> dict:
    return {
        "id": f.id,
        "original_filename": f.original_filename,
        "relative_path": f.relative_path,
        "folder": f.folder,
        "size_bytes": f.size_bytes,
        "plate_count": len(f.plates or []),
        "uploaded_at": f.uploaded_at,
        "missing": f.missing,
        "tags": tags,
        "thumbnail_url": _thumb_url(f),
    }


def _thumb_url(f: UploadedFile) -> str | None:
    for p in (f.plates or []):
        tp = p.get("thumbnail_path")
        if tp:
            return f"/api/v1/files/{f.id}/thumbnails/{Path(tp).name}"
    return None


# ---------- list / tree ----------

@router.get("")
async def list_files(
    folder: str | None = None,
    tags: list[str] | None = None,
    search: str | None = None,
    sort: str = "updated",
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await session.execute(select(UploadedFile))).scalars().all()
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
    return [_to_dict(r, tag_map.get(r.id, [])) for r in rows]


@router.get("/tree")
async def folder_tree(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(UploadedFile))).scalars().all()
    root: dict = {"name": "All files", "path": "", "count": 0, "children": {}}
    for r in rows:
        root["count"] += 1
        node = root
        path = ""
        for part in [p for p in r.folder.split("/") if p]:
            path += "/" + part
            node = node["children"].setdefault(
                part, {"name": part, "path": path, "count": 0, "children": {}})
            node["count"] += 1
    return root


@router.get("/dirs")
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
        node = root
        path = ""
        for part in [p for p in r.folder.split("/") if p]:
            path += "/" + part
            node = node["children"].setdefault(
                part, {"name": part, "path": path, "count": 0, "children": {}})
            node["count"] += 1
    return root


# ---------- upload ----------

@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile,
    folder: str = Form("/Job Uploads"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    fname = (file.filename or "")
    ext = Path(fname).suffix.lower()
    if ext not in MODEL_EXTS:
        raise HTTPException(422, "Only .3mf and .stl files are accepted")

    library = config.get_library_dir()
    folder_abs = _safe_subpath(library, folder)
    folder_abs.mkdir(parents=True, exist_ok=True)
    dest = LibraryScanner.unique_path(folder_abs, Path(fname).name)
    dest.write_bytes(await file.read())

    rel = dest.relative_to(library).as_posix()
    stat = dest.stat()
    scanner = LibraryScanner(session, library, config.get_filecache_dir())
    record = UploadedFile(
        original_filename=dest.name, stored_path=str(dest), relative_path=rel,
        folder=folder_of(rel), size_bytes=stat.st_size, content_hash=sha256_file(dest),
        mtime=stat.st_mtime, plates=[], missing=False,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(record)
    await session.flush()
    record.plates = scanner._parse_plates(dest, record.id)
    await session.commit()
    await session.refresh(record)
    return _to_dict(record, [])


# ---------- folders ----------

class FolderCreate(BaseModel):
    path: str


@router.post("/folders", status_code=201)
async def create_folder(body: FolderCreate) -> dict:
    library = config.get_library_dir()
    target = _safe_subpath(library, body.path)
    target.mkdir(parents=True, exist_ok=True)
    return {"path": "/" + target.relative_to(library).as_posix()}


@router.delete("/folders")
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


@router.patch("/{file_id}")
async def update_file(file_id: int, body: FilePatch,
                      session: AsyncSession = Depends(get_session)) -> dict:
    f = await session.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    library = config.get_library_dir()
    src = Path(f.stored_path)
    new_folder = body.folder if body.folder is not None else f.folder
    # Strip all path components from the supplied name so that traversal
    # sequences like "../../../evil.stl" cannot escape the library root.
    if body.name is not None:
        new_name = Path(body.name).name
        if not new_name or new_name in (".", ".."):
            raise HTTPException(400, "Invalid filename")
    else:
        new_name = f.original_filename
    folder_abs = _safe_subpath(library, new_folder)
    folder_abs.mkdir(parents=True, exist_ok=True)
    # No-op when the target is the file's existing location: skip the move so we
    # don't collide with the file itself and rename it to "name (2).ext".
    if src.exists() and folder_abs.resolve() == src.parent.resolve() and new_name == src.name:
        tag_map = await _tags_for(session, [f.id])
        return _to_dict(f, tag_map.get(f.id, []))
    dest = LibraryScanner.unique_path(folder_abs, new_name)
    # Defense-in-depth: verify dest is inside the library before touching the FS.
    library_resolved = library.resolve()
    dest_resolved = dest.resolve()
    if dest_resolved != library_resolved and library_resolved not in dest_resolved.parents:
        raise HTTPException(400, "Path escapes the library root")
    if src.exists():
        src.replace(dest)
    rel = dest.relative_to(library).as_posix()
    f.original_filename = dest.name
    f.stored_path = str(dest)
    f.relative_path = rel
    f.folder = folder_of(rel)
    await session.commit()
    tag_map = await _tags_for(session, [f.id])
    return _to_dict(f, tag_map.get(f.id, []))


# ---------- delete ----------

@router.delete("/{file_id}")
async def delete_file(file_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    f = await session.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    active = (await session.execute(
        select(Job.id).where(Job.uploaded_file_id == file_id,
                             Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
    )).first()
    if active:
        raise HTTPException(409, "File is referenced by an active job")
    p = Path(f.stored_path)
    if p.exists():
        p.unlink()
    cache = config.get_filecache_dir() / str(file_id)
    if cache.exists():
        import shutil
        shutil.rmtree(cache, ignore_errors=True)
    for link in (await session.execute(select(FileTag).where(FileTag.file_id == file_id))).scalars().all():
        await session.delete(link)
    await session.delete(f)
    await session.commit()
    return {"deleted": file_id}


# ---------- tag assign / unassign ----------

class TagAssign(BaseModel):
    tag_id: int


@router.post("/{file_id}/tags")
async def add_file_tag(file_id: int, body: TagAssign,
                       session: AsyncSession = Depends(get_session)) -> dict:
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


@router.delete("/{file_id}/tags/{tag_id}")
async def remove_file_tag(file_id: int, tag_id: int,
                          session: AsyncSession = Depends(get_session)) -> dict:
    link = (await session.execute(
        select(FileTag).where(FileTag.file_id == file_id, FileTag.tag_id == tag_id)
    )).scalar_one_or_none()
    if link is not None:
        await session.delete(link)
        await session.commit()
    return {"file_id": file_id, "tag_id": tag_id}


# ---------- rescan ----------

@router.post("/rescan")
async def rescan(session: AsyncSession = Depends(get_session)) -> dict:
    scanner = LibraryScanner(session, config.get_library_dir(), config.get_filecache_dir())
    return await scanner.scan()


# ---------- plates / thumbnails ----------

@router.get("/{file_id}/plates")
async def get_plates(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return record.plates or []


@router.get("/{file_id}/model-filaments")
async def get_model_filaments(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    from ...services.three_mf_parser import parse_model_filaments
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return parse_model_filaments(record.stored_path)


@router.get("/{file_id}/embedded-settings")
async def get_embedded_settings(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    from ...services.three_mf_parser import parse_embedded_settings
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return parse_embedded_settings(record.stored_path)


@router.get("/{file_id}/thumbnails/{filename}")
async def get_thumbnail(file_id: int, filename: str,
                        session: AsyncSession = Depends(get_session)) -> FileResponse:
    if await session.get(UploadedFile, file_id) is None:
        raise HTTPException(404, f"File {file_id} not found")
    thumb_dir = (config.get_filecache_dir() / str(file_id) / "thumbnails").resolve()
    try:
        thumb_path = (thumb_dir / filename).resolve()
        thumb_path.relative_to(thumb_dir)
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(str(thumb_path), media_type="image/png")
