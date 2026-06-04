import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models import Printer
from app.services.printer_manager import PrinterManager


async def _factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_on_ams_change_preserves_mappings_by_slot():
    Session = await _factory()
    async with Session() as s:
        s.add(Printer(
            name="P", printer_type="bambu", connection_config={},
            loaded_filaments=[
                {"slot": 0, "filament_id": "OLD", "name": "old", "type": "PLA",
                 "color": "#111", "filament_profile": "Generic PLA @BBL",
                 "spoolman_spool_id": "42"},
            ],
        ))
        await s.commit()

    mgr = PrinterManager()
    mgr.set_session_factory(Session)
    await mgr.on_ams_change(1, [
        {"slot": 0, "filament_id": "NEW", "name": "new", "type": "PLA", "color": "#222"},
        {"slot": 1, "filament_id": "GFL96", "name": "mint", "type": "PLA", "color": "#0f0"},
    ])

    async with Session() as s:
        p = await s.get(Printer, 1)
        lf = {f["slot"]: f for f in p.loaded_filaments}
        # slot 0: AMS fields updated, user mappings preserved
        assert lf[0]["filament_id"] == "NEW"
        assert lf[0]["color"] == "#222"
        assert lf[0]["filament_profile"] == "Generic PLA @BBL"
        assert lf[0]["spoolman_spool_id"] == "42"
        # slot 1: brand-new tray, no mappings
        assert lf[1].get("filament_profile") is None
        assert lf[1].get("spoolman_spool_id") is None


@pytest.mark.asyncio
async def test_on_ams_change_drops_orphaned_slots():
    Session = await _factory()
    async with Session() as s:
        s.add(Printer(
            name="P", printer_type="bambu", connection_config={},
            loaded_filaments=[
                {"slot": 0, "filament_id": "A", "name": "a", "type": "PLA", "color": "#111"},
                {"slot": 1, "filament_id": "B", "name": "b", "type": "PLA", "color": "#222"},
            ],
        ))
        await s.commit()

    mgr = PrinterManager()
    mgr.set_session_factory(Session)
    await mgr.on_ams_change(1, [
        {"slot": 0, "filament_id": "A", "name": "a", "type": "PLA", "color": "#111"},
    ])

    async with Session() as s:
        p = await s.get(Printer, 1)
        assert [f["slot"] for f in p.loaded_filaments] == [0]
