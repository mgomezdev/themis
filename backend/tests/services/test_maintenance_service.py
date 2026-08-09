import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models import MaintenanceItem, MaintenanceTrigger, Printer, PrinterMaintenanceState
from app.services import maintenance_service as ms

CATALOG = [
    {"name": "Elegoo Centauri Carbon 0.4 nozzle", "vendor": "Elegoo", "printer_model": "Centauri Carbon", "nozzle": "0.4"},
    {"name": "Bambu Lab X1 Carbon 0.4 nozzle", "vendor": "Bambu Lab", "printer_model": "X1 Carbon", "nozzle": "0.4"},
]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def test_resolve_vendor_model_matches_catalog():
    resolved = ms.resolve_vendor_model("Elegoo Centauri Carbon 0.4 nozzle", CATALOG)
    assert resolved == ("Elegoo", "Centauri Carbon")


def test_resolve_vendor_model_returns_none_when_unset_or_unmatched():
    assert ms.resolve_vendor_model(None, CATALOG) is None
    assert ms.resolve_vendor_model("Unknown Preset", CATALOG) is None


def test_item_applies_to_printer_general_always_applies():
    item = MaintenanceItem(name="Wash plate", scope="general")
    assert ms.item_applies_to_printer(item, None) is True
    assert ms.item_applies_to_printer(item, ("Elegoo", "Centauri Carbon")) is True


def test_item_applies_to_printer_model_scoped_matches_exactly():
    item = MaintenanceItem(name="AMS desiccant", scope="model",
                            machine_vendor="Bambu Lab", machine_model="X1 Carbon")
    assert ms.item_applies_to_printer(item, ("Bambu Lab", "X1 Carbon")) is True
    assert ms.item_applies_to_printer(item, ("Elegoo", "Centauri Carbon")) is False
    assert ms.item_applies_to_printer(item, None) is False


@pytest.mark.asyncio
async def test_compute_due_status_job_count_trigger(db):
    async with db() as session:
        printer = Printer(name="P1", printer_type="elegoo_centauri", connection_config={},
                           current_orca_printer_profile="Elegoo Centauri Carbon 0.4 nozzle",
                           lifetime_job_count=12, lifetime_print_seconds=0)
        session.add(printer)
        item = MaintenanceItem(name="Wash plate", scope="general",
                                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        session.add(item)
        await session.flush()
        trigger = MaintenanceTrigger(maintenance_item_id=item.id, trigger_type="job_count", amount=10, unit=None)
        session.add(trigger)
        await session.commit()
        printer_id, item_id = printer.id, item.id

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: [trigger]}, CATALOG)

    assert len(rows) == 1
    assert rows[0]["due"] is True  # 12 jobs since baseline 0 >= threshold 10


@pytest.mark.asyncio
async def test_compute_due_status_model_scoped_item_excludes_non_matching_printer(db):
    async with db() as session:
        printer = Printer(name="P1", printer_type="elegoo_centauri", connection_config={},
                           current_orca_printer_profile="Elegoo Centauri Carbon 0.4 nozzle")
        session.add(printer)
        item = MaintenanceItem(name="AMS desiccant", scope="model",
                                machine_vendor="Bambu Lab", machine_model="X1 Carbon",
                                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        session.add(item)
        await session.flush()
        trigger = MaintenanceTrigger(maintenance_item_id=item.id, trigger_type="job_count", amount=1, unit=None)
        session.add(trigger)
        await session.commit()
        printer_id, item_id = printer.id, item.id

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: [trigger]}, CATALOG)

    assert rows == []  # Elegoo printer never matches a Bambu-scoped item


@pytest.mark.asyncio
async def test_mark_done_resets_baseline_and_clears_due(db):
    async with db() as session:
        printer = Printer(name="P1", printer_type="elegoo_centauri", connection_config={},
                           lifetime_job_count=20, lifetime_print_seconds=0)
        session.add(printer)
        item = MaintenanceItem(name="Wash plate", scope="general",
                                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        session.add(item)
        await session.commit()
        printer_id, item_id = printer.id, item.id

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        state = await ms.mark_done(session, printer, item)
        assert state.baseline_job_count == 20

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        trigger = MaintenanceTrigger(maintenance_item_id=item_id, trigger_type="job_count", amount=10, unit=None)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: [trigger]}, CATALOG)
    assert rows[0]["due"] is False  # 20 - baseline(20) = 0, below threshold 10


@pytest.mark.asyncio
async def test_compute_due_status_calendar_trigger_overdue(db):
    async with db() as session:
        printer = Printer(name="P1", printer_type="elegoo_centauri", connection_config={})
        session.add(printer)
        item = MaintenanceItem(name="Lube rails", scope="general",
                                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        session.add(item)
        await session.flush()
        # Seed the state directly (bypassing _get_or_init_state's "now" default)
        # so last_done_at is far enough in the past to be overdue.
        long_ago = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        state = PrinterMaintenanceState(printer_id=printer.id, maintenance_item_id=item.id,
                                         last_done_at=long_ago, baseline_job_count=0,
                                         baseline_print_seconds=0)
        session.add(state)
        trigger = MaintenanceTrigger(maintenance_item_id=item.id, trigger_type="calendar",
                                      amount=3, unit="months")
        session.add(trigger)
        await session.commit()
        printer_id, item_id = printer.id, item.id

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: [trigger]}, CATALOG)

    assert rows[0]["due"] is True  # 100 days elapsed >= 3 months (90 days)


@pytest.mark.asyncio
async def test_compute_due_status_calendar_trigger_not_yet_due(db):
    async with db() as session:
        printer = Printer(name="P1", printer_type="elegoo_centauri", connection_config={})
        session.add(printer)
        item = MaintenanceItem(name="Lube rails", scope="general",
                                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        session.add(item)
        await session.flush()
        # Seed the state directly with a recent last_done_at so the calendar
        # trigger has not yet elapsed.
        recently = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        state = PrinterMaintenanceState(printer_id=printer.id, maintenance_item_id=item.id,
                                         last_done_at=recently, baseline_job_count=0,
                                         baseline_print_seconds=0)
        session.add(state)
        trigger = MaintenanceTrigger(maintenance_item_id=item.id, trigger_type="calendar",
                                      amount=3, unit="months")
        session.add(trigger)
        await session.commit()
        printer_id, item_id = printer.id, item.id

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: [trigger]}, CATALOG)

    assert rows[0]["due"] is False  # 10 days elapsed < 3 months (90 days)


def test_common_maintenance_templates_are_well_formed():
    templates = ms.COMMON_MAINTENANCE_TEMPLATES
    assert len(templates) >= 8
    for t in templates:
        assert t["name"] and t["description"]
        assert len(t["triggers"]) >= 1
        for trig in t["triggers"]:
            assert trig["trigger_type"] in ("calendar", "job_time", "job_count")
            assert trig["amount"] > 0
            if trig["trigger_type"] == "calendar":
                assert trig["unit"] in ("hours", "days", "weeks", "months")
            else:
                assert trig["unit"] is None


@pytest.mark.asyncio
async def test_mark_done_resets_every_trigger_baseline_not_just_the_one_that_fired(db):
    """An item with a job_count trigger AND a calendar trigger, where BOTH are
    independently in violation before mark_done (job_count over threshold, calendar
    long overdue) — mark_done must reset both baselines together, not just whichever
    one a caller might think "fired". Verified two ways, not just a before/after
    `due` bool: (1) due flips True->False across the reset, and (2) a small bump to
    lifetime_job_count afterward proves baseline_job_count was actually moved to the
    printer's current count (not left at its old value, which would make the item
    incorrectly due again almost immediately)."""
    old_last_done = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()

    async with db() as session:
        printer = Printer(name="P1", printer_type="elegoo_centauri", connection_config={},
                           current_orca_printer_profile="Elegoo Centauri Carbon 0.4 nozzle",
                           lifetime_job_count=10, lifetime_print_seconds=0)
        session.add(printer)
        item = MaintenanceItem(name="Multi-trigger item", scope="general",
                                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        session.add(item)
        await session.flush()
        job_count_trigger = MaintenanceTrigger(maintenance_item_id=item.id, trigger_type="job_count",
                                                amount=5, unit=None)
        calendar_trigger = MaintenanceTrigger(maintenance_item_id=item.id, trigger_type="calendar",
                                               amount=1, unit="months")
        session.add(job_count_trigger)
        session.add(calendar_trigger)
        # Baseline job_count=0 (so lifetime 10 >= threshold 5 -> job_count IS due) and
        # last_done_at 200 days ago (so calendar, threshold 30 days, IS ALSO due) —
        # both triggers are independently in violation before the reset.
        state = PrinterMaintenanceState(printer_id=printer.id, maintenance_item_id=item.id,
                                         last_done_at=old_last_done, baseline_job_count=0,
                                         baseline_print_seconds=0)
        session.add(state)
        await session.commit()
        printer_id, item_id = printer.id, item.id
        triggers = [job_count_trigger, calendar_trigger]

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: triggers}, CATALOG)
    assert rows[0]["due"] is True  # both triggers agree it's due, before any reset

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        await ms.mark_done(session, printer, item)

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: triggers}, CATALOG)
    assert rows[0]["due"] is False  # reset cleared both triggers, not just one

    # Isolate that baseline_job_count specifically moved to 10 (not left at 0):
    # a small bump that stays under the job_count threshold (5) relative to the
    # NEW baseline must still read as not-due. If baseline_job_count had been left
    # at 0, 12 - 0 = 12 >= 5 would incorrectly flip due back to True.
    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.lifetime_job_count = 12
        await session.commit()

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        item = await session.get(MaintenanceItem, item_id)
        rows = await ms.compute_due_status(session, [printer], [item], {item_id: triggers}, CATALOG)
    assert rows[0]["due"] is False  # 12 - baseline(10) = 2 < 5 -> confirms baseline_job_count was reset
