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
from .api.websocket import connection_manager, websocket_endpoint
from .database import SessionLocal, init_db
from .services.printer_manager import printer_manager

_default_static = Path(__file__).parent.parent.parent / "frontend" / "dist"
STATIC_DIR = Path(os.environ.get("THEMIS_STATIC_DIR", str(_default_static)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    loop = asyncio.get_event_loop()
    printer_manager.set_loop(loop)
    printer_manager.set_broadcast_callback(connection_manager.broadcast)
    printer_manager.set_session_factory(SessionLocal)
    await printer_manager.load_awaiting_plate_clear_from_db()
    yield


app = FastAPI(title="Themis", lifespan=lifespan)

app.add_api_websocket_route("/ws", websocket_endpoint)
app.include_router(printers_router)
app.include_router(files_router)
app.include_router(projects_router)
app.include_router(jobs_router)


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
