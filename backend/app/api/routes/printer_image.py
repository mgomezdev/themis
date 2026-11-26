"""A printer's picture: the image its plugin declares for the printer's model.

Part of the `printers` routes: registered on the same `router` and imported at the bottom of printers.py."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...services.printer_images import printer_image_path
from .printers import _get_or_404, router


@router.get(
    "/{printer_id}/image",
    summary="Printer model picture (from its plugin)",
    responses={404: {"description": "Printer not found, or its plugin declares no picture for the model"}},
    dependencies=[Depends(require_scope("printers:read"))],
)
async def printer_image(printer_id: int, session: AsyncSession = Depends(get_session)) -> FileResponse:
    printer = await _get_or_404(printer_id, session)
    path = printer_image_path(printer.plugin_id, printer.model_id)
    if path is None:
        raise HTTPException(404, "No picture for this printer model")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
