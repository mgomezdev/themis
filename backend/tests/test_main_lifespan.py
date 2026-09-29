"""App startup/shutdown wiring (app.main.lifespan). Runs the real lifespan with the module-level DB session
factory redirected to a per-test database and the long-running services (queue loop, Spoolman sync) replaced
by spies, then asserts the singletons end up wired the way the rest of the app assumes."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.main as main
from app.api.websocket import connection_manager
from app.database import Base, _set_sqlite_pragmas
from app.models import Job, JobPrinterConfig, Printer, UploadedFile
from app.services.printer_manager import printer_manager
from app.services.queue_engine import queue_engine

PLACEHOLDER = "Elegoo Centauri Carbon (placeholder)"


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """File-backed DB with the app's pragmas (foreign keys ON), like the real one."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    event.listens_for(engine.sync_engine, "connect")(_set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def boot(session_factory, tmp_path, monkeypatch):
    """Everything lifespan touches, redirected or spied. Returns the spies; restores the singletons afterwards."""
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("THEMIS_LIBRARY_DIR", raising=False)
    monkeypatch.delenv("LAMINUS_SIDECAR_URL", raising=False)

    spies = MagicMock()
    spies.init_db = AsyncMock()
    monkeypatch.setattr(main, "init_db", spies.init_db)
    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "SlicerService", MagicMock)
    spies.engine_start, spies.engine_stop = AsyncMock(), AsyncMock()
    monkeypatch.setattr(queue_engine, "start", spies.engine_start, raising=False)
    monkeypatch.setattr(queue_engine, "stop", spies.engine_stop, raising=False)
    spies.spoolman_configure = MagicMock()
    spies.spoolman_start, spies.spoolman_stop = AsyncMock(), AsyncMock()
    monkeypatch.setattr(main.spoolman_sync_loop, "configure", spies.spoolman_configure)
    monkeypatch.setattr(main.spoolman_sync_loop, "start", spies.spoolman_start)
    monkeypatch.setattr(main.spoolman_sync_loop, "stop", spies.spoolman_stop)

    saved = {k: getattr(printer_manager, k) for k in
             ("_loop", "_on_state_broadcast", "_on_job_complete", "_session_factory")}
    yield spies
    for key, value in saved.items():
        setattr(printer_manager, key, value)
    queue_engine.__dict__.clear()  # back to the uninitialized shell other tests expect


async def _seed_printer(factory, **fields) -> int:
    async with factory() as s:
        printer = Printer(connection_config={}, printer_type=fields.pop("printer_type", "mock"), **fields)
        s.add(printer)
        await s.commit()
        return printer.id


async def test_startup_wires_the_printer_manager_queue_engine_and_background_services(boot, session_factory, tmp_path):
    library = tmp_path / "data" / "library"
    library.mkdir(parents=True)
    (library / ".legacy_migrated").touch()  # an already-upgraded install: the legacy migration no longer creates folders
    async with main.lifespan(main.app):
        boot.init_db.assert_awaited_once()
        assert (library / "Job Uploads").is_dir()

        assert printer_manager._session_factory is session_factory
        assert printer_manager._on_state_broadcast == connection_manager.broadcast
        assert printer_manager._loop is not None and printer_manager._loop.is_running()

        assert queue_engine._factory is session_factory
        assert queue_engine._mgr is printer_manager
        assert printer_manager._on_job_complete == queue_engine.handle_print_complete
        boot.engine_start.assert_awaited_once()
        boot.spoolman_configure.assert_called_once_with(session_factory)
        boot.spoolman_start.assert_awaited_once()
        boot.engine_stop.assert_not_awaited()


async def test_startup_restores_the_plate_gate_and_reconnects_only_enabled_printers(boot, session_factory):
    gated = await _seed_printer(session_factory, name="Gated", enabled=True, awaiting_plate_clear=True)
    idle = await _seed_printer(session_factory, name="Idle", enabled=True)
    disabled = await _seed_printer(session_factory, name="Off", enabled=False, awaiting_plate_clear=True)

    async with main.lifespan(main.app):
        assert printer_manager.is_awaiting_plate_clear(gated) is True   # survives the restart via the DB
        assert printer_manager.is_awaiting_plate_clear(idle) is False
        assert set(printer_manager.get_all_printer_ids()) == {gated, idle}  # disabled printers are not connected
        assert disabled not in printer_manager.get_all_printer_ids()


async def test_shutdown_stops_background_services_and_disconnects_every_printer(boot, session_factory):
    printer_id = await _seed_printer(session_factory, name="P", enabled=True)
    async with main.lifespan(main.app):
        client = printer_manager.get_client(printer_id)
        client.disconnect = MagicMock(wraps=client.disconnect)

    boot.engine_stop.assert_awaited_once()
    boot.spoolman_stop.assert_awaited_once()
    client.disconnect.assert_called_once()
    assert printer_manager.get_all_printer_ids() == []


async def test_startup_removes_only_the_legacy_placeholder_printer(boot, session_factory):
    await _seed_printer(session_factory, name=PLACEHOLDER, printer_type="elegoo_centauri", enabled=False)
    keep = await _seed_printer(session_factory, name="Real printer", enabled=False)

    async with main.lifespan(main.app):
        pass

    async with session_factory() as s:
        assert [p.id for p in (await s.execute(select(Printer))).scalars()] == [keep]


async def test_a_placeholder_still_referenced_by_a_job_is_kept_with_a_warning_and_startup_continues(boot, session_factory, caplog):
    placeholder = await _seed_printer(session_factory, name=PLACEHOLDER, printer_type="elegoo_centauri", enabled=False)
    async with session_factory() as s:
        f = UploadedFile(original_filename="a.3mf", stored_path="/x/a.3mf", plates=[], uploaded_at="t")
        s.add(f)
        await s.flush()
        job = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="queued", created_at="t", updated_at="t")
        s.add(job)
        await s.flush()
        s.add(JobPrinterConfig(job_id=job.id, printer_id=placeholder, print_profile="0.2", filament_type="any", filament_color="any"))
        await s.commit()

    with caplog.at_level(logging.WARNING, logger="app"):
        async with main.lifespan(main.app):
            boot.engine_start.assert_awaited_once()  # startup went on

    async with session_factory() as s:
        assert (await s.get(Printer, placeholder)) is not None
    assert any("Could not remove placeholder printer" in r.getMessage() for r in caplog.records)


async def test_an_unreachable_sidecar_only_warns_and_the_catalog_warmup_is_still_scheduled(boot, monkeypatch, caplog):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", "http://sidecar.invalid:5000")
    monkeypatch.setattr("app.services.laminus_sidecar_client.LaminusSidecarClient.health",
                        MagicMock(side_effect=ConnectionError("no route to host")))
    warm = AsyncMock()
    monkeypatch.setattr("app.api.routes.laminus.warm_catalog_cache", warm)

    with caplog.at_level(logging.WARNING, logger="app"):
        async with main.lifespan(main.app):
            boot.engine_start.assert_awaited_once()
            await asyncio.sleep(0)  # let the fire-and-forget warm-up task run

    assert any("sidecar.invalid" in r.getMessage() and "not reachable" in r.getMessage() for r in caplog.records)
    warm.assert_awaited_once()
