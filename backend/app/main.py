import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes.files import router as files_router
from .api.routes.jobs import router as jobs_router
from .api.routes.printers import router as printers_router
from .api.routes.projects import router as projects_router
from .api.routes.queue import router as queue_router
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


app = FastAPI(title="Themis", lifespan=lifespan)

app.add_api_websocket_route("/ws", websocket_endpoint)
app.include_router(printers_router)
app.include_router(files_router)
app.include_router(projects_router)
app.include_router(jobs_router)
app.include_router(queue_router)


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
