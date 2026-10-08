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
from fastapi.responses import FileResponse, JSONResponse
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
from .api.routes.inventory import router as inventory_router
from .api.routes.plugins import router as plugins_router
from .api.routes.capabilities import router as capabilities_router
from .api.routes.plugin_install import router as plugin_install_router
from .api.routes.tags import router as tags_router
from .api.websocket import connection_manager, websocket_endpoint
from .database import SessionLocal, init_db
from .services.printer_manager import printer_manager
from .services.queue_engine import QueueEngine, queue_engine
from .services.slicer_service import SlicerService
from .services.inventory.sync import inventory_sync_loop
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
    try:
        from .plugins import loader as _loader, migrations as _plugin_migrations
        await _loader.reconcile(SessionLocal, _loader.last_report, dict(_plugin_migrations.failed))
    except Exception:
        logging.getLogger("app").exception("Could not record installed-plugin status; continuing")

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

    from .plugins.host import plugin_host
    plugin_host.configure(SessionLocal)
    try:
        await plugin_host.start()
    except Exception:
        logging.getLogger("app").exception("Plugin host failed to start; continuing without plugins")

    inventory_sync_loop.configure(SessionLocal)
    await inventory_sync_loop.start()

    # Warn early if the sidecar is configured but unreachable; then warm the
    # catalog cache in the background so the first user request is fast.
    from .services.providers.slicing import get_slicing_provider
    _slicing = get_slicing_provider()
    if _slicing is not None:
        try:
            await asyncio.to_thread(_slicing.health)
            logging.getLogger("app").info("Laminus sidecar healthy at %s", _slicing.identity)
        except Exception as e:
            logging.getLogger("app").warning(
                "Laminus sidecar at %s is not reachable: %s", _slicing.identity, e
            )
        # Kick off catalog warm-up in the background — don't block startup.
        from .services import catalog_service
        asyncio.create_task(catalog_service.warm())

    yield

    await inventory_sync_loop.stop()
    await plugin_host.stop()
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
app.include_router(inventory_router)
app.include_router(plugin_install_router)
app.include_router(plugins_router)
app.include_router(capabilities_router)

# Plugins register at import time too (not only in init_db) so their routers can be mounted before the app starts.
from .plugins import load_bundled, registered_plugins  # noqa: E402
from .services.inventory.provider import CapabilityUnavailable  # noqa: E402

load_bundled()
from . import config as _cfg  # noqa: E402
from .database import _data_dir as _data_dir_for_plugins  # noqa: E402
from .plugins.loader import load_installed  # noqa: E402

load_installed(_cfg.get_plugins_dir(), Path(_data_dir_for_plugins) / "themis.db")      # installed packages; failures are contained
for _manifest in registered_plugins():
    _mounted: set[int] = set()
    for _router in (*_manifest.routers, *(r for p in _manifest.provides.values() for r in p.routers)):
        if id(_router) not in _mounted:
            _mounted.add(id(_router))
            app.include_router(_router, prefix=f"/api/v1/plugins/{_manifest.id}")
    for _router in _manifest.alias_routers:                  # deprecated aliases keep their historical absolute paths
        app.include_router(_router)


@app.middleware("http")
async def _cap_plugin_upload(request: Request, call_next):
    """The multipart body of a plugin upload is parsed before authentication runs, so refuse an oversized or length-less one
    up front (the installer enforces the exact cap again while streaming)."""
    if request.method == "POST" and request.url.path == "/api/v1/plugins/install":
        from .plugins import installer as _installer
        length = request.headers.get("content-length", "")
        if not length.isdigit() or int(length) > _installer.MAX_ARCHIVE_BYTES + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Plugin archive is too large (or has no Content-Length)"})
    return await call_next(request)


@app.exception_handler(CapabilityUnavailable)
async def _capability_unavailable(_: Request, exc: CapabilityUnavailable) -> JSONResponse:
    """A feature needs a plugin kind/capability that is not available: 409 with a machine-readable body."""
    return JSONResponse(status_code=409, content={"error": "capability_unavailable", "capability": exc.capability_id, "feature": exc.feature})
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
    if "\x00" in full_path:  # Windows resolve() accepts NUL instead of raising ValueError
        return None
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
