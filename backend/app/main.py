import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes.files import router as files_router
from .api.routes.orders import router as orders_router
from .api.routes.fleet import router as fleet_router
from .api.routes.jobs import router as jobs_router
from .api.routes.printers import router as printers_router
from .api.routes.queue import router as queue_router
from .api.routes.settings import router as settings_router
from .api.routes.spoolman import router as spoolman_router
from .api.websocket import connection_manager, websocket_endpoint
from .database import SessionLocal, init_db
from .services.printer_manager import printer_manager
from .services.queue_engine import QueueEngine, queue_engine
from .services.slicer_service import SlicerService

_default_static = Path(__file__).parent.parent.parent / "frontend" / "dist"
STATIC_DIR = Path(os.environ.get("THEMIS_STATIC_DIR", str(_default_static)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    loop = asyncio.get_running_loop()

    # Wire printer manager
    printer_manager.set_loop(loop)
    printer_manager.set_broadcast_callback(connection_manager.broadcast)
    printer_manager.set_session_factory(SessionLocal)
    await printer_manager.load_awaiting_plate_clear_from_db()
    await printer_manager.connect_all_enabled_printers(SessionLocal)

    # Initialise and wire queue engine
    # Stop any previously running engine before re-initializing
    if getattr(queue_engine, '_task', None) is not None:
        await queue_engine.stop()
    QueueEngine.__init__(
        queue_engine,
        session_factory=SessionLocal,
        printer_manager=printer_manager,
        slicer_service=SlicerService(),
        broadcast_cb=connection_manager.broadcast,
    )
    printer_manager.set_job_complete_callback(queue_engine.handle_print_complete)
    await queue_engine.start()

    yield

    await queue_engine.stop()
    for pid in list(printer_manager._clients.keys()):
        printer_manager.disconnect_printer(pid)


app = FastAPI(title="Themis", lifespan=lifespan)

app.add_api_websocket_route("/ws", websocket_endpoint)
app.include_router(orders_router)
app.include_router(printers_router)
app.include_router(fleet_router)
app.include_router(files_router)
app.include_router(jobs_router)
app.include_router(queue_router)
app.include_router(settings_router)
app.include_router(spoolman_router)


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.exists():
    # Serve hashed assets with long cache; index.html with no-cache so browsers
    # always revalidate and pick up new deploys without a hard refresh.
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(_: Request, full_path: str):
        file = STATIC_DIR / full_path
        if file.is_file() and full_path:
            return FileResponse(file)
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
