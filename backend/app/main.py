import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# The app had no logging configuration, so service-level logger.info/warning
# calls (printer connect attempts, MQTT errors) went nowhere. Configure a root
# handler so they're visible. uvicorn uses disable_existing_loggers=False, so
# this co-exists with uvicorn's own access/error logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)

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
from .api.routes.tags import router as tags_router
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

    # Seed placeholder Elegoo Centauri Carbon for test slicing (idempotent).
    from sqlalchemy import select as _select
    from .models import Printer as _Printer
    async with SessionLocal() as _sess:
        _existing = (await _sess.execute(
            _select(_Printer).where(_Printer.name == "Elegoo Centauri Carbon (placeholder)")
        )).scalar_one_or_none()
        if _existing is None:
            _sess.add(_Printer(
                name="Elegoo Centauri Carbon (placeholder)",
                printer_type="elegoo_centauri",
                connection_config={"ip_address": "192.0.2.1"},
                current_orca_printer_profile="Elegoo Centauri Carbon 0.4 nozzle",
                orca_printer_profiles=["Elegoo Centauri Carbon 0.4 nozzle"],
                loaded_filaments=[{
                    "slot": 0,
                    "type": "PLA",
                    "color": "white",
                    "filament_profile": "Elegoo PLA @ECC",
                }],
                enabled=True,
                queue_on=True,
            ))
            await _sess.commit()
            logging.getLogger("app").info("Seeded placeholder Elegoo Centauri Carbon printer")

    # File library: migrate legacy uploads, then index the library dir.
    from . import config as _config
    from .services.library_scanner import LibraryScanner, migrate_legacy_uploads
    # Ensure the default job-upload folder always exists (always shown, never deleted).
    (_config.get_library_dir() / "Job Uploads").mkdir(parents=True, exist_ok=True)
    async with SessionLocal() as _s:
        await migrate_legacy_uploads(
            _s, _config._resolve_data_dir(), _config.get_library_dir(), _config.get_filecache_dir())
        await LibraryScanner(_s, _config.get_library_dir(), _config.get_filecache_dir()).scan()

    loop = asyncio.get_running_loop()

    # Wire printer manager
    printer_manager.set_loop(loop)
    printer_manager.set_broadcast_callback(connection_manager.broadcast)
    printer_manager.set_session_factory(SessionLocal)
    await printer_manager.load_awaiting_plate_clear_from_db()
    await printer_manager.connect_all_enabled_printers(SessionLocal)

    # Initialise and wire queue engine
    QueueEngine.__init__(
        queue_engine,
        session_factory=SessionLocal,
        printer_manager=printer_manager,
        slicer_service=SlicerService(),
        broadcast_cb=connection_manager.broadcast,
    )
    printer_manager.set_job_complete_callback(queue_engine.handle_print_complete)
    await queue_engine.start()

    # Warn early if the sidecar is configured but unreachable
    from .config import get_orca_sidecar_url as _get_sidecar_url
    _sidecar_url = _get_sidecar_url()
    if _sidecar_url:
        try:
            from .services.orca_sidecar_client import OrcaSidecarClient, SidecarError
            await asyncio.to_thread(OrcaSidecarClient(_sidecar_url).health)
            logging.getLogger("app").info("Orca sidecar healthy at %s", _sidecar_url)
        except Exception as e:
            logging.getLogger("app").warning(
                "Orca sidecar at %s is not reachable: %s", _sidecar_url, e
            )

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
app.include_router(tags_router)


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
