import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.websocket import connection_manager, websocket_endpoint
from .database import init_db
from .services.printer_manager import printer_manager

_default_static = Path(__file__).parent.parent.parent / "frontend" / "dist"
STATIC_DIR = Path(os.environ.get("THEMIS_STATIC_DIR", str(_default_static)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    loop = asyncio.get_event_loop()
    printer_manager.set_loop(loop)
    printer_manager.set_broadcast_callback(connection_manager.broadcast)
    yield


app = FastAPI(title="Themis", lifespan=lifespan)

app.add_api_websocket_route("/ws", websocket_endpoint)


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
