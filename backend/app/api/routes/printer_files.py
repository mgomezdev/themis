"""Files on the printer itself: direct upload-and-print, listing, printing, deleting and copying stored files to the library.

Part of the `printers` routes: registered on the same `router` and imported at the bottom of printers.py.
What tests replace on that module is read as `_printers.<name>` at call time, so one patch
point covers every printers route.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from fastapi import Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ...auth import require_scope
from ...database import get_session
from ...models import Job, Printer
from ...services import print_start
from ...services.library_scanner import is_presliced_name
from ...services.printer_topology import check_device, lock_for

from . import printers as _printers
from .printers import (  # noqa: I001
    _get_or_404,
    _list_printer_files,
    _stem,
    router,
)
from .printer_controls import _CONSOLE_RESPONSES, _ok_or_502, _require_capability


async def _check_start_client(session, client, printer_id: int) -> Printer:
    try:
        printer = await check_device(session, printer_id)
        if _printers.printer_manager.get_client(printer_id) is not client or not client.connected:
            raise ValueError("Printer session changed or disconnected during upload")
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if not client.is_idle:
        raise HTTPException(409, "Printer is not idle")
    return printer


async def _direct_start(session: AsyncSession, client, printer_id: int, file_id: str, failure: str,
                        *, upload: Callable[[], Awaitable[None]] | None = None) -> None:
    """Hold the topology lock through preparation and reserve the plate before submitting a start."""
    from ...services.abstract_printer_client import StartPrintOptions

    manager = _printers.printer_manager
    plate_error = "The previous plate has not been cleared; mark the printer ready first"
    async with lock_for(printer_id):
        printer = await _check_start_client(session, client, printer_id)
        if printer.awaiting_plate_clear or manager.is_awaiting_plate_clear(printer_id):
            raise HTTPException(409, plate_error)
        busy = await session.execute(
            select(Job.id).where(Job.assigned_printer_id == printer_id, Job.status.in_(_BUSY_JOB_STATUSES)).limit(1))
        if busy.first() is not None:
            raise HTTPException(409, "A queued job currently holds this printer")
        if upload is not None:
            await upload()
        async with print_start.lock_for(printer_id):
            await _check_start_client(session, client, printer_id)
            if not await print_start.reserve_plate(session, manager, printer_id, queued=False):
                raise HTTPException(409, plate_error)
            options = StartPrintOptions(gcode_path=os.path.basename(file_id))
            if not await print_start.send(client, file_id, options):
                raise HTTPException(502, failure)


_UPLOAD_EXTENSIONS = (".gcode", ".gcode.3mf", ".3mf", ".bgcode")
_MAX_DIRECT_UPLOAD_BYTES = 300 * 1024 * 1024
_BUSY_JOB_STATUSES = ("slicing", "sliced", "uploading", "printing", "paused")


@router.post(
    "/{printer_id}/upload",
    summary="Upload a file to the printer, bypassing the queue",
    responses={
        **_CONSOLE_RESPONSES,
        413: {"description": "File too large"},
        422: {"description": "Unsupported file type"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def upload_to_printer(
    printer_id: int,
    file: UploadFile = File(...),
    start: bool = Form(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Send a .gcode / .3mf / .bgcode file straight to the printer. With `start`, also begin printing it —
    only when the printer is idle and no queued job holds it; the plate is then marked not-ready (same
    gate as a queue-started print)."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_capability(client, "direct_upload", "direct upload")
    name = os.path.basename((file.filename or "").replace("\\", "/"))
    if not name or not name.lower().endswith(_UPLOAD_EXTENSIONS) or any(c in name for c in "\x00\r\n"):
        raise HTTPException(422, f"Unsupported file; expected one of {', '.join(_UPLOAD_EXTENSIONS)}")
    data = await file.read(_printers._MAX_DIRECT_UPLOAD_BYTES + 1)
    if len(data) > _printers._MAX_DIRECT_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 300 MB")
    if not start:
        if not await asyncio.to_thread(client.upload_file, data, name):
            raise HTTPException(502, "Printer rejected the upload")
        return {"ok": True, "filename": name, "started": False}

    async def upload() -> None:
        if not await asyncio.to_thread(client.upload_file, data, name):
            raise HTTPException(502, "Printer rejected the upload")

    await _direct_start(session, client, printer_id, name, "Uploaded, but the printer would not start the print", upload=upload)
    return {"ok": True, "filename": name, "started": True}


class FileRef(BaseModel):
    file_id: str = Field(..., min_length=1, max_length=1024)


_FILE_RESPONSES = {
    **_CONSOLE_RESPONSES,
    409: {"description": "Not supported by this printer, or the printer/queue state forbids it"},
}


@router.get(
    "/{printer_id}/files",
    summary="List files stored on a printer",
    responses=_FILE_RESPONSES,
    dependencies=[Depends(require_scope("printers:read"))],
)
async def list_files_on_printer(
    printer_id: int, directory: str = "/", session: AsyncSession = Depends(get_session),
) -> dict:
    """One directory of the printer's own storage (`directory` is an entry id from a previous listing, `/` for
    the root). Includes slicer metadata (print time, filament) where the protocol offers it."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_capability(client, "file_browser", "browsing its files")
    try:
        files = await _list_printer_files(client, directory)
    except asyncio.TimeoutError:
        raise HTTPException(502, "Timed out listing files")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:                       # login refused, port closed, 4xx/5xx from the printer…
        raise HTTPException(502, f"Could not list files: {e}")
    caps = client.get_capabilities()
    return {"printer_id": printer_id, "directory": directory, "files": files,
            "can_delete": caps.file_delete, "can_download": caps.file_download}


@router.post(
    "/{printer_id}/files/print",
    summary="Print a file already stored on the printer",
    responses=_FILE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def print_stored_file(printer_id: int, body: FileRef, session: AsyncSession = Depends(get_session)) -> dict:
    """Start a stored file. Same gate as a direct upload-and-print: the printer must be idle, its plate cleared
    and not held by a queue job; it is then marked not-ready like any started print."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_capability(client, "file_browser", "browsing its files")
    if not body.file_id.lower().endswith((".gcode", ".3mf", ".bgcode")):
        raise HTTPException(422, "Not a printable file")

    await _direct_start(session, client, printer_id, body.file_id, "The printer would not start the print")
    return {"ok": True, "file_id": body.file_id, "started": True}


@router.delete(
    "/{printer_id}/files",
    summary="Delete a file from the printer",
    responses=_FILE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def delete_stored_file(printer_id: int, file_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Permanently remove a file from the printer's storage. Refused for the file currently being printed."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_capability(client, "file_delete", "deleting files")
    if not client.is_idle:
        # Not idle covers the start-up window too (heating/levelling) when state isn't RUNNING yet. Firmwares
        # report the running file inconsistently (with/without extension, with a path), so compare stems, and
        # when the name is unknown refuse rather than guess.
        current = _printers.printer_manager.get_normalized_state(printer_id).get("current_print") or ""
        if not current or _stem(current) == _stem(file_id):
            raise HTTPException(409, "That file is (or may be) being printed right now")
    return _ok_or_502(await asyncio.to_thread(client.delete_file, file_id))


@router.post(
    "/{printer_id}/files/to-library",
    summary="Copy a file from the printer into the file library",
    responses={**_FILE_RESPONSES, 413: {"description": "File too large"}, 422: {"description": "Not a library file type"}},
    dependencies=[Depends(require_scope("files:write"))],
)
async def copy_stored_file_to_library(
    printer_id: int, body: FileRef, session: AsyncSession = Depends(get_session),
) -> dict:
    """Download a stored .3mf / .stl from the printer and add it to the library (folder `/From Printers`,
    deduplicated by content like any upload). Sliced output (.gcode, .gcode.3mf) is refused: this adds models."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_capability(client, "file_download", "downloading files")
    name = os.path.basename(body.file_id)
    if not name.lower().endswith((".3mf", ".stl")) or is_presliced_name(name):
        raise HTTPException(422, "Only .3mf and .stl models can be added to the library (not sliced gcode)")
    from ...services.abstract_printer_client import FileTooLargeError
    try:
        data = await asyncio.to_thread(client.download_file, body.file_id, _printers._MAX_DIRECT_UPLOAD_BYTES)
    except FileTooLargeError:
        raise HTTPException(413, "File is larger than 300 MB")     # aborted mid-transfer, never fully buffered
    if data is None:
        raise HTTPException(502, "The printer would not hand over the file")
    from io import BytesIO
    from starlette.datastructures import UploadFile as StarletteUpload
    from .files import upload_file as library_upload
    return await library_upload(StarletteUpload(file=BytesIO(data), filename=name), "/From Printers", session)
