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

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from .api.routes.admin_account import router as admin_account_router
from .api.routes.api_keys import router as api_keys_router
from .api.routes.customer_portal import router as customer_portal_router
from .api.routes.cameras import router as cameras_router
from .api.routes.customers import router as customers_router
from .api.routes.files import router as files_router
from .api.routes.orders import router as orders_router
from .api.routes.payments import router as payments_router
from .api.routes.fleet import router as fleet_router
from .api.routes.jobs import router as jobs_router
from .api.routes.alarms import router as alarms_router
from .api.routes.labor import router as labor_router
from .api.routes.laminus import router as laminus_router
from .api.routes.maintenance import router as maintenance_router
from .api.routes.printers import router as printers_router
from .api.routes.projects import router as projects_router
from .api.routes.public import router as public_router
from .api.routes.queue import router as queue_router
from .api.routes.session import router as session_router
from .api.routes.settings import router as settings_router
from .api.routes.spoolman import router as spoolman_router
from .api.routes.tags import router as tags_router
from .api.websocket import connection_manager, websocket_endpoint
from .database import SessionLocal, init_db
from .services.printer_manager import printer_manager
from .services.queue_engine import QueueEngine, queue_engine
from .services.slicer_service import SlicerService
from .services.spoolman_sync import spoolman_sync_loop
from .version import get_git_sha, get_version

_default_static = Path(__file__).parent.parent.parent / "frontend" / "dist"
STATIC_DIR = Path(os.environ.get("THEMIS_STATIC_DIR", str(_default_static)))


async def _remove_placeholder_printer_from_db() -> None:
    """Remove legacy placeholder printer if present from old installations."""
    from sqlalchemy import select as _select, delete as _delete
    from .models import Printer as _Printer
    _NAME = "Elegoo Centauri Carbon (placeholder)"
    async with SessionLocal() as _sess:
        existing = (await _sess.execute(
            _select(_Printer).where(_Printer.name == _NAME)
        )).scalar_one_or_none()
        if existing is not None:
            try:
                await _sess.execute(_delete(_Printer).where(_Printer.name == _NAME))
                await _sess.commit()
                logging.getLogger("app").info("Removed legacy placeholder printer")
            except Exception:
                logging.getLogger("app").warning(
                    "Could not remove placeholder printer (jobs may reference it) — remove manually"
                )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    await _remove_placeholder_printer_from_db()

    # File library: migrate legacy uploads, then index the library dir.
    from . import config as _config
    from .services.library_scanner import LibraryScanner, migrate_legacy_uploads
    # Ensure the default job-upload folder always exists (always shown, never deleted).
    (_config.get_library_dir() / "Job Uploads").mkdir(parents=True, exist_ok=True)
    async with SessionLocal() as _s:
        await migrate_legacy_uploads(
            _s, _config._resolve_data_dir(), _config.get_library_dir(), _config.get_filecache_dir())
        await LibraryScanner(_s, _config.get_library_dir(), _config.get_filecache_dir()).scan()

    try:                                   # bound the alarm history (resolved alarms older than 90 days)
        from .services import alarms as _alarms
        async with SessionLocal() as _s:
            await _alarms.purge_old(_s)
    except Exception:
        logging.getLogger("app").exception("Could not purge old alarms")

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

    spoolman_sync_loop.configure(SessionLocal)
    await spoolman_sync_loop.start()

    # Warn early if the sidecar is configured but unreachable; then warm the
    # catalog cache in the background so the first user request is fast.
    from .config import get_laminus_sidecar_url as _get_sidecar_url
    _sidecar_url = _get_sidecar_url()
    if _sidecar_url:
        try:
            from .services.laminus_sidecar_client import LaminusSidecarClient, SidecarError
            await asyncio.to_thread(LaminusSidecarClient(_sidecar_url).health)
            logging.getLogger("app").info("Laminus sidecar healthy at %s", _sidecar_url)
        except Exception as e:
            logging.getLogger("app").warning(
                "Laminus sidecar at %s is not reachable: %s", _sidecar_url, e
            )
        # Kick off catalog warm-up in the background — don't block startup.
        from .api.routes.laminus import warm_catalog_cache as _warm_catalog
        asyncio.create_task(_warm_catalog())

    yield

    await spoolman_sync_loop.stop()
    await queue_engine.stop()
    for pid in list(printer_manager._clients.keys()):
        printer_manager.disconnect_printer(pid)


# Registers the X-Api-Key security scheme in the OpenAPI schema so /docs shows
# an "Authorize" button. Documentation ergonomics only — auto_error=False means
# this never rejects a request itself; actual enforcement is entirely
# app.auth.require_scope, applied per-route. Attached to the /health route
# below rather than app-level: FastAPI's `dependencies=` on the FastAPI()
# constructor also applies to add_api_websocket_route's DI-wrapped /ws route,
# and that combination breaks under app.dependency_overrides (which every
# test sets) with a TypeError inside FastAPI's dependency resolution —
# registering it on one ordinary HTTP route avoids that entirely while still
# adding the scheme to the OpenAPI schema exactly once.
_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)

app = FastAPI(
    title="Themis",
    description=(
        "Print queue API for the Themis 3D printing management system. "
        "Manages files, printers, jobs, projects, orders, queue, and settings. "
        "Requires the Laminus OrcaSlicer sidecar for slicing operations."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_api_websocket_route("/ws", websocket_endpoint)
app.include_router(admin_account_router)
app.include_router(api_keys_router)
app.include_router(cameras_router)
app.include_router(customers_router)
app.include_router(customer_portal_router)
app.include_router(session_router)
app.include_router(orders_router)
app.include_router(printers_router)
app.include_router(fleet_router)
app.include_router(files_router)
app.include_router(jobs_router)
app.include_router(alarms_router)
app.include_router(labor_router)
app.include_router(laminus_router)
app.include_router(maintenance_router)
app.include_router(projects_router)
app.include_router(payments_router)
app.include_router(public_router)
app.include_router(queue_router)
app.include_router(settings_router)
app.include_router(spoolman_router)
app.include_router(tags_router)


@app.get("/api/v1/health", dependencies=[Depends(_api_key_header)])
async def health() -> dict:
    return {"status": "ok", "version": get_version(), "git_sha": get_git_sha()}


def _resolve_within(root_dir: Path, full_path: str) -> Path | None:
    """Resolve full_path under root_dir, or None if it escapes.

    `root_dir / full_path` is a lexical join: pathlib discards the base
    entirely when the right operand is absolute ("/etc/passwd"), and does not
    normalise "..". Both (and symlinks pointing outside) must be rejected before
    the file is served.
    """
    root = root_dir.resolve()
    try:
        candidate = (root / full_path).resolve()
    except (OSError, ValueError):
        return None
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def register_spa(target: FastAPI, static_dir: Path) -> None:
    """Serve the built React app from static_dir: hashed assets with long cache; every other
    path falls back to index.html with no-cache so browsers always revalidate and pick up new
    deploys without a hard refresh."""
    target.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @target.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(_: Request, full_path: str):
        if full_path:
            file = _resolve_within(static_dir, full_path)
            if file is not None and file.is_file():
                return FileResponse(file)
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )


if STATIC_DIR.exists():
    register_spa(app, STATIC_DIR)
