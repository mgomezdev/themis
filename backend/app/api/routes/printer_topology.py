"""User-requested live topology refresh through the same path as plugin updates."""
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from ...auth import require_scope
from ...database import get_session
from ...services.printer_topology import refresh_device, TopologyRefreshError
from .printers import router, _get_or_404, _get_connected_client


class RefreshBody(BaseModel):
    configuration: dict | None = None


@router.post('/{printer_id}/topology/refresh', dependencies=[Depends(require_scope('printers:control'))])
async def refresh_topology(printer_id: int, body: RefreshBody, session: AsyncSession = Depends(get_session)):
    await _get_or_404(printer_id, session)
    _get_connected_client(printer_id)
    try:
        return await refresh_device(session, printer_id, body.configuration)
    except TopologyRefreshError as exc:
        raise HTTPException(502, str(exc)) from exc
