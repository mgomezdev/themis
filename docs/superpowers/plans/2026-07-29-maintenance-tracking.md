# Printer Maintenance Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users define maintenance items (general or printer-model-specific) with one or more triggers — calendar time, cumulative print time, or job count — that mark a printer's maintenance as due the moment any one trigger crosses its threshold; ship a set of common prepopulated items; surface due-ness on the Fleet screen.

**Architecture:** Three new tables (`maintenance_items`, `maintenance_triggers`, `printer_maintenance_state`) plus two lifetime-counter columns on `printers`. A new `maintenance_service.py` owns the due-status math and the built-in template list; a new `api/routes/maintenance.py` exposes CRUD + status + complete endpoints; `queue_engine.handle_print_complete` is the single hook point that increments the lifetime counters. Frontend adds a Settings → Maintenance page (list + add/edit form, reusing the existing vendor/model machine catalog for model-specific scoping) and a small due-count badge on Fleet printer cards.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy async / SQLite (backend); React 19 / TypeScript / Vite (frontend); pytest-asyncio (backend tests); Vitest + Testing Library (frontend tests).

---

## Design notes (read before starting)

- **"Model" identity** = `(vendor, printer_model)` from `GET /api/v1/printers/orca-machine-catalog` (e.g. `("Elegoo", "Centauri Carbon")`), matching how `MachinePicker.tsx` already resolves a printer's machine preset. A printer's `current_orca_printer_profile` (e.g. `"Elegoo Centauri Carbon 0.4 nozzle"`) is looked up against that catalog's `name` field to resolve its `(vendor, printer_model)` at due-computation time. This is deliberately **not** the coarser `printer_type` (`bambu`/`elegoo_centauri`/`snapmaker_extended`), which conflates different real-world models under one Bambu driver.
- **"Trips on earliest"** requires no special logic: an item is due the moment `any()` of its triggers evaluates true. Chronologically whichever trigger's threshold is crossed first is what flips `due` to true — the OR is the "earliest" semantics.
- **Lifetime counters accrue only on successful completion** (`handle_print_complete`), not on failed/cancelled prints — this matches the existing precedent where Spoolman filament deduction also only fires from this same path. Aborted prints are a known, explicitly-accepted gap (see `docs/agent/conventions.md`'s existing note on the reconcile-FAILED path never deducting Spoolman either).
- **"Months" = a fixed 30 days** for calendar triggers — no calendar-aware month arithmetic. Stated explicitly, not silently assumed.
- **Doc drift found and flagged for `concordia-docs-sync`:** `docs/agent/data-model.md` and `docs/agent/recipes.md` both claim new tables are created via `Base.metadata.create_all()` at startup with "no migration needed." This is false — `database.py::init_db()` never calls `create_all()`; every table (new or old) is created by an explicit `CREATE TABLE IF NOT EXISTS` inside a versioned migration file (verified against `v010_project_parts.py`). This plan's migration follows the **verified real pattern**, not the stale doc.

---

### Task 1: Data model + migration v011

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/app/migrations/v011_maintenance_tracking.py`
- Modify: `backend/app/migrations/runner.py`
- Test: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing migration test**

Append to `backend/tests/test_migrations.py`:

```python
@pytest.mark.asyncio
async def test_v011_adds_maintenance_tables_and_printer_counters():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent second run
        printer_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
    assert {"lifetime_job_count", "lifetime_print_seconds"} <= printer_cols
    assert {"maintenance_items", "maintenance_triggers", "printer_maintenance_state"} <= tables
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_migrations.py::test_v011_adds_maintenance_tables_and_printer_counters -v`
Expected: FAIL — `assert {"lifetime_job_count", ...} <= printer_cols` is false (columns don't exist yet).

- [ ] **Step 3: Add the new models to `models.py`**

In `backend/app/models.py`, add two columns to the existing `Printer` class (after `bed_y_mm`):

```python
    lifetime_job_count: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_print_seconds: Mapped[int] = mapped_column(Integer, default=0)
```

Then append three new classes at the end of the file:

```python
class MaintenanceItem(Base):
    __tablename__ = "maintenance_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(20), default="general")  # "general" | "model"
    machine_vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    machine_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class MaintenanceTrigger(Base):
    __tablename__ = "maintenance_triggers"

    id: Mapped[int] = mapped_column(primary_key=True)
    maintenance_item_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_items.id", ondelete="CASCADE")
    )
    trigger_type: Mapped[str] = mapped_column(String(20))  # "calendar" | "job_time" | "job_count"
    amount: Mapped[float] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # calendar only


class PrinterMaintenanceState(Base):
    __tablename__ = "printer_maintenance_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    maintenance_item_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_items.id", ondelete="CASCADE")
    )
    last_done_at: Mapped[str] = mapped_column(String(32))
    baseline_job_count: Mapped[int] = mapped_column(Integer, default=0)
    baseline_print_seconds: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 4: Write the migration file**

Create `backend/app/migrations/v011_maintenance_tracking.py`:

```python
"""Add maintenance tracking: items/triggers/per-printer state, plus lifetime counters on printers."""
from __future__ import annotations
from sqlalchemy import text

version = 11
name = "maintenance_tracking"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "lifetime_job_count" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN lifetime_job_count INTEGER NOT NULL DEFAULT 0"))
    if "lifetime_print_seconds" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN lifetime_print_seconds INTEGER NOT NULL DEFAULT 0"))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS maintenance_items (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            scope          TEXT    NOT NULL DEFAULT 'general',
            machine_vendor TEXT,
            machine_model  TEXT,
            enabled        BOOLEAN NOT NULL DEFAULT 1,
            notes          TEXT,
            created_at     TEXT    NOT NULL,
            updated_at     TEXT    NOT NULL
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS maintenance_triggers (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            maintenance_item_id  INTEGER NOT NULL
                REFERENCES maintenance_items(id) ON DELETE CASCADE,
            trigger_type         TEXT    NOT NULL,
            amount               REAL    NOT NULL,
            unit                 TEXT
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS printer_maintenance_state (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            printer_id             INTEGER NOT NULL
                REFERENCES printers(id) ON DELETE CASCADE,
            maintenance_item_id    INTEGER NOT NULL
                REFERENCES maintenance_items(id) ON DELETE CASCADE,
            last_done_at           TEXT    NOT NULL,
            baseline_job_count     INTEGER NOT NULL DEFAULT 0,
            baseline_print_seconds INTEGER NOT NULL DEFAULT 0,
            UNIQUE(printer_id, maintenance_item_id)
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS printer_maintenance_state"))
    await conn.execute(text("DROP TABLE IF EXISTS maintenance_triggers"))
    await conn.execute(text("DROP TABLE IF EXISTS maintenance_items"))
    # SQLite doesn't support DROP COLUMN before 3.35; recreate printers table
    await conn.execute(text("""
        CREATE TABLE printers_new AS
        SELECT id, name, printer_type, connection_config, awaiting_plate_clear,
               orca_printer_profiles, current_orca_printer_profile, enabled, queue_on,
               loaded_filaments, build_plate_type, no_snapshots_while_idle, bed_x_mm, bed_y_mm
        FROM printers
    """))
    await conn.execute(text("DROP TABLE printers"))
    await conn.execute(text("ALTER TABLE printers_new RENAME TO printers"))
```

- [ ] **Step 5: Register the migration in `runner.py`**

In `backend/app/migrations/runner.py`, change the import line to add `v011_maintenance_tracking`:

```python
from . import v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates, v005_project_order_merge, v006_project_links, v007_printer_bed_size, v008_job_estimates_and_queue_config, v009_drop_filament_profile_uuid, v010_project_parts, v011_maintenance_tracking

_MIGRATIONS = sorted(
    [v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates, v005_project_order_merge, v006_project_links, v007_printer_bed_size, v008_job_estimates_and_queue_config, v009_drop_filament_profile_uuid, v010_project_parts, v011_maintenance_tracking],
    key=lambda m: m.version,
)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_migrations.py -v`
Expected: PASS (all migration tests, including the new one).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/migrations/v011_maintenance_tracking.py backend/app/migrations/runner.py backend/tests/test_migrations.py
git commit -m "feat(maintenance): add data model + migration for maintenance tracking"
```

---

### Task 2: Maintenance service — vendor/model resolution + due computation

**Files:**
- Create: `backend/app/services/maintenance_service.py`
- Test: `backend/tests/services/test_maintenance_service.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/test_maintenance_service.py`:

```python
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models import MaintenanceItem, MaintenanceTrigger, Printer
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/services/test_maintenance_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.maintenance_service'`.

- [ ] **Step 3: Write the service implementation**

Create `backend/app/services/maintenance_service.py`:

```python
"""Maintenance-item due-status computation and printer vendor/model resolution."""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MaintenanceItem, MaintenanceTrigger, Printer, PrinterMaintenanceState

_CALENDAR_DAYS = {"hours": 1 / 24, "days": 1.0, "weeks": 7.0, "months": 30.0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_vendor_model(profile_name: str | None, catalog: list[dict]) -> tuple[str, str] | None:
    """Match a printer's current_orca_printer_profile against the machine catalog
    (GET /printers/orca-machine-catalog shape: {name, vendor, printer_model, nozzle})
    to get (vendor, printer_model). Returns None if unset or not found."""
    if not profile_name:
        return None
    for entry in catalog:
        if entry.get("name") == profile_name:
            return entry.get("vendor") or "", entry.get("printer_model") or ""
    return None


def item_applies_to_printer(item: MaintenanceItem, resolved: tuple[str, str] | None) -> bool:
    if item.scope == "general":
        return True
    if resolved is None:
        return False
    return item.machine_vendor == resolved[0] and item.machine_model == resolved[1]


async def _get_or_init_state(
    session: AsyncSession, printer: Printer, item: MaintenanceItem
) -> PrinterMaintenanceState:
    state = (await session.execute(
        select(PrinterMaintenanceState).where(
            PrinterMaintenanceState.printer_id == printer.id,
            PrinterMaintenanceState.maintenance_item_id == item.id,
        )
    )).scalar_one_or_none()
    if state is None:
        state = PrinterMaintenanceState(
            printer_id=printer.id,
            maintenance_item_id=item.id,
            last_done_at=_now(),
            baseline_job_count=printer.lifetime_job_count,
            baseline_print_seconds=printer.lifetime_print_seconds,
        )
        session.add(state)
        await session.flush()
    return state


def _trigger_due(trigger: MaintenanceTrigger, printer: Printer, state: PrinterMaintenanceState) -> bool:
    if trigger.trigger_type == "calendar":
        days_elapsed = (
            datetime.now(timezone.utc) - datetime.fromisoformat(state.last_done_at)
        ).total_seconds() / 86400
        threshold_days = trigger.amount * _CALENDAR_DAYS.get(trigger.unit or "days", 1.0)
        return days_elapsed >= threshold_days
    if trigger.trigger_type == "job_time":
        hours_elapsed = (printer.lifetime_print_seconds - state.baseline_print_seconds) / 3600
        return hours_elapsed >= trigger.amount
    if trigger.trigger_type == "job_count":
        jobs_elapsed = printer.lifetime_job_count - state.baseline_job_count
        return jobs_elapsed >= trigger.amount
    return False


async def compute_due_status(
    session: AsyncSession,
    printers: list[Printer],
    items: list[MaintenanceItem],
    triggers_by_item: dict[int, list[MaintenanceTrigger]],
    catalog: list[dict],
) -> list[dict]:
    """One row per (printer, applicable item):
    {printer_id, printer_name, item_id, item_name, due, last_done_at}."""
    rows: list[dict] = []
    for printer in printers:
        resolved = resolve_vendor_model(printer.current_orca_printer_profile, catalog)
        for item in items:
            if not item.enabled or not item_applies_to_printer(item, resolved):
                continue
            state = await _get_or_init_state(session, printer, item)
            triggers = triggers_by_item.get(item.id, [])
            due = any(_trigger_due(t, printer, state) for t in triggers)
            rows.append({
                "printer_id": printer.id,
                "printer_name": printer.name,
                "item_id": item.id,
                "item_name": item.name,
                "due": due,
                "last_done_at": state.last_done_at,
            })
    await session.commit()
    return rows


async def mark_done(session: AsyncSession, printer: Printer, item: MaintenanceItem) -> PrinterMaintenanceState:
    state = await _get_or_init_state(session, printer, item)
    state.last_done_at = _now()
    state.baseline_job_count = printer.lifetime_job_count
    state.baseline_print_seconds = printer.lifetime_print_seconds
    await session.commit()
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/services/test_maintenance_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/maintenance_service.py backend/tests/services/test_maintenance_service.py
git commit -m "feat(maintenance): add due-status computation service"
```

---

### Task 3: Common maintenance templates

**Files:**
- Modify: `backend/app/services/maintenance_service.py`
- Test: `backend/tests/services/test_maintenance_service.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/services/test_maintenance_service.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/services/test_maintenance_service.py::test_common_maintenance_templates_are_well_formed -v`
Expected: FAIL with `AttributeError: module 'app.services.maintenance_service' has no attribute 'COMMON_MAINTENANCE_TEMPLATES'`.

- [ ] **Step 3: Add the templates constant**

Append to `backend/app/services/maintenance_service.py`:

```python
COMMON_MAINTENANCE_TEMPLATES: list[dict] = [
    {
        "name": "Wash build plate",
        "description": "Wash with warm water and dish soap (or an IPA wipe for PEI) to keep first-layer adhesion consistent.",
        "triggers": [{"trigger_type": "job_count", "amount": 10, "unit": None}],
    },
    {
        "name": "Clean nozzle / hotend",
        "description": "Cold-pull or brass-brush the nozzle tip to clear carbon buildup.",
        "triggers": [
            {"trigger_type": "job_count", "amount": 25, "unit": None},
            {"trigger_type": "calendar", "amount": 1, "unit": "months"},
        ],
    },
    {
        "name": "Lubricate linear rails / rods",
        "description": "Apply a thin coat of PTFE or lithium grease to all linear motion rails/rods.",
        "triggers": [
            {"trigger_type": "job_time", "amount": 100, "unit": None},
            {"trigger_type": "calendar", "amount": 3, "unit": "months"},
        ],
    },
    {
        "name": "Check belt tension",
        "description": "Pluck each belt and listen for a consistent, taut tone; re-tension as needed.",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    },
    {
        "name": "Inspect / replace PTFE tube",
        "description": "Check the PTFE tube for scoring or a burnt/darkened end; replace if worn.",
        "triggers": [
            {"trigger_type": "job_time", "amount": 250, "unit": None},
            {"trigger_type": "calendar", "amount": 6, "unit": "months"},
        ],
    },
    {
        "name": "Clean cooling fans",
        "description": "Blow out dust from the part-cooling, hotend, and chamber fans.",
        "triggers": [{"trigger_type": "calendar", "amount": 3, "unit": "months"}],
    },
    {
        "name": "Inspect nozzle for wear",
        "description": "Check nozzle orifice roundness, especially after printing abrasive (CF/GF) filament.",
        "triggers": [
            {"trigger_type": "job_time", "amount": 150, "unit": None},
            {"trigger_type": "job_count", "amount": 50, "unit": None},
        ],
    },
    {
        "name": "Check for firmware / software updates",
        "description": "Check the vendor app or Themis printer settings for a pending firmware update.",
        "triggers": [{"trigger_type": "calendar", "amount": 1, "unit": "months"}],
    },
    {
        "name": "Calibrate bed level / flow",
        "description": "Run the printer's bed-leveling and flow-calibration routine.",
        "triggers": [{"trigger_type": "job_count", "amount": 20, "unit": None}],
    },
    {
        "name": "Clean chamber filter / activated carbon",
        "description": "Replace or rinse the enclosure's carbon/HEPA filter (enclosed or AMS-equipped printers).",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    },
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/services/test_maintenance_service.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/maintenance_service.py backend/tests/services/test_maintenance_service.py
git commit -m "feat(maintenance): add common maintenance item templates"
```

---

### Task 4: Maintenance API routes — items CRUD

**Files:**
- Create: `backend/app/api/routes/maintenance.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/api/test_maintenance_api.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_maintenance_api.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_create_and_list_general_item(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Wash build plate",
        "scope": "general",
        "triggers": [{"trigger_type": "job_count", "amount": 10, "unit": None}],
    })
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["name"] == "Wash build plate"
    assert item["scope"] == "general"
    assert len(item["triggers"]) == 1
    assert item["triggers"][0]["trigger_type"] == "job_count"

    r = await client.get("/api/v1/maintenance/items")
    assert r.status_code == 200
    assert any(i["name"] == "Wash build plate" for i in r.json())


@pytest.mark.asyncio
async def test_create_model_scoped_item_requires_vendor_and_model(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "AMS desiccant", "scope": "model", "triggers": [],
    })
    assert r.status_code == 422

    r = await client.post("/api/v1/maintenance/items", json={
        "name": "AMS desiccant", "scope": "model",
        "machine_vendor": "Bambu Lab", "machine_model": "X1 Carbon",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["machine_vendor"] == "Bambu Lab"


@pytest.mark.asyncio
async def test_update_item_patches_fields(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Clean fans", "scope": "general",
        "triggers": [{"trigger_type": "calendar", "amount": 3, "unit": "months"}],
    })
    item_id = r.json()["id"]

    r = await client.patch(f"/api/v1/maintenance/items/{item_id}", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_update_missing_item_404(client):
    r = await client.patch("/api/v1/maintenance/items/999", json={"enabled": False})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_item_cascades(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Check belts", "scope": "general",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    })
    item_id = r.json()["id"]

    r = await client.delete(f"/api/v1/maintenance/items/{item_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] == item_id

    r = await client.get("/api/v1/maintenance/items")
    assert all(i["id"] != item_id for i in r.json())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/api/test_maintenance_api.py -v`
Expected: FAIL — 404s (no such route registered yet).

- [ ] **Step 3: Write the route module — items CRUD only (templates/status/complete come in Task 5)**

Create `backend/app/api/routes/maintenance.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import MaintenanceItem, MaintenanceTrigger, PrinterMaintenanceState

router = APIRouter(prefix="/api/v1/maintenance", tags=["maintenance"])


class TriggerIn(BaseModel):
    trigger_type: str
    amount: float
    unit: str | None = None


class MaintenanceItemCreate(BaseModel):
    name: str
    scope: str = "general"
    machine_vendor: str | None = None
    machine_model: str | None = None
    notes: str | None = None
    triggers: list[TriggerIn] = []


class MaintenanceItemPatch(BaseModel):
    name: str | None = None
    scope: str | None = None
    machine_vendor: str | None = None
    machine_model: str | None = None
    enabled: bool | None = None
    notes: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _triggers_for(session: AsyncSession, item_id: int) -> list[MaintenanceTrigger]:
    return list((await session.execute(
        select(MaintenanceTrigger).where(MaintenanceTrigger.maintenance_item_id == item_id)
    )).scalars().all())


def _trigger_dict(t: MaintenanceTrigger) -> dict:
    return {"id": t.id, "trigger_type": t.trigger_type, "amount": t.amount, "unit": t.unit}


async def _item_dict(session: AsyncSession, item: MaintenanceItem) -> dict:
    triggers = await _triggers_for(session, item.id)
    return {
        "id": item.id, "name": item.name, "scope": item.scope,
        "machine_vendor": item.machine_vendor, "machine_model": item.machine_model,
        "enabled": item.enabled, "notes": item.notes,
        "triggers": [_trigger_dict(t) for t in triggers],
    }


@router.get("/items", summary="List maintenance items")
async def list_items(session: AsyncSession = Depends(get_session)) -> list[dict]:
    items = (await session.execute(select(MaintenanceItem).order_by(MaintenanceItem.name))).scalars().all()
    return [await _item_dict(session, i) for i in items]


@router.post(
    "/items", status_code=201, summary="Create maintenance item",
    responses={422: {"description": "scope='model' requires machine_vendor and machine_model"}},
)
async def create_item(body: MaintenanceItemCreate, session: AsyncSession = Depends(get_session)) -> dict:
    if body.scope not in ("general", "model"):
        raise HTTPException(422, "scope must be 'general' or 'model'")
    if body.scope == "model" and not (body.machine_vendor and body.machine_model):
        raise HTTPException(422, "machine_vendor and machine_model are required when scope='model'")
    now = _now()
    item = MaintenanceItem(
        name=body.name, scope=body.scope,
        machine_vendor=body.machine_vendor if body.scope == "model" else None,
        machine_model=body.machine_model if body.scope == "model" else None,
        notes=body.notes, created_at=now, updated_at=now,
    )
    session.add(item)
    await session.flush()
    for t in body.triggers:
        session.add(MaintenanceTrigger(
            maintenance_item_id=item.id, trigger_type=t.trigger_type, amount=t.amount, unit=t.unit,
        ))
    await session.commit()
    await session.refresh(item)
    return await _item_dict(session, item)


@router.patch(
    "/items/{item_id}", summary="Update maintenance item",
    responses={404: {"description": "Maintenance item not found"}},
)
async def update_item(item_id: int, body: MaintenanceItemPatch, session: AsyncSession = Depends(get_session)) -> dict:
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    if body.name is not None:
        item.name = body.name
    if body.scope is not None:
        item.scope = body.scope
    if body.machine_vendor is not None:
        item.machine_vendor = body.machine_vendor
    if body.machine_model is not None:
        item.machine_model = body.machine_model
    if body.enabled is not None:
        item.enabled = body.enabled
    if body.notes is not None:
        item.notes = body.notes
    item.updated_at = _now()
    await session.commit()
    return await _item_dict(session, item)


@router.delete(
    "/items/{item_id}", summary="Delete maintenance item",
    responses={404: {"description": "Maintenance item not found"}},
)
async def delete_item(item_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    for t in await _triggers_for(session, item_id):
        await session.delete(t)
    for s in (await session.execute(
        select(PrinterMaintenanceState).where(PrinterMaintenanceState.maintenance_item_id == item_id)
    )).scalars().all():
        await session.delete(s)
    await session.delete(item)
    await session.commit()
    return {"deleted": item_id}
```

- [ ] **Step 4: Register the router in `main.py`**

In `backend/app/main.py`, add the import next to the other route imports:

```python
from .api.routes.maintenance import router as maintenance_router
```

And add the include next to the other `include_router` calls:

```python
app.include_router(maintenance_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/api/test_maintenance_api.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/maintenance.py backend/app/main.py backend/tests/api/test_maintenance_api.py
git commit -m "feat(maintenance): add items CRUD API"
```

---

### Task 5: Maintenance API routes — triggers replace, templates, status, complete

**Files:**
- Modify: `backend/app/api/routes/maintenance.py`
- Test: `backend/tests/api/test_maintenance_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/api/test_maintenance_api.py`:

```python
@pytest.mark.asyncio
async def test_replace_triggers(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Wash plate", "scope": "general",
        "triggers": [{"trigger_type": "job_count", "amount": 10, "unit": None}],
    })
    item_id = r.json()["id"]

    r = await client.put(f"/api/v1/maintenance/items/{item_id}/triggers", json={
        "triggers": [
            {"trigger_type": "job_count", "amount": 15, "unit": None},
            {"trigger_type": "calendar", "amount": 1, "unit": "months"},
        ]
    })
    assert r.status_code == 200
    triggers = r.json()["triggers"]
    assert len(triggers) == 2
    assert {t["trigger_type"] for t in triggers} == {"job_count", "calendar"}


@pytest.mark.asyncio
async def test_templates_endpoint_returns_common_items(client):
    r = await client.get("/api/v1/maintenance/templates")
    assert r.status_code == 200
    templates = r.json()
    assert len(templates) >= 8
    assert any(t["name"] == "Wash build plate" for t in templates)


@pytest.mark.asyncio
async def test_status_endpoint_shows_due_general_item(client):
    r = await client.post("/api/v1/printers", json={
        "name": "P1", "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "10.0.0.5"},
    })
    printer_id = r.json()["id"]

    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Wash plate", "scope": "general",
        "triggers": [{"trigger_type": "job_count", "amount": 0, "unit": None}],
    })
    assert r.status_code == 201, r.text

    r = await client.get("/api/v1/maintenance/status")
    assert r.status_code == 200
    rows = [row for row in r.json() if row["printer_id"] == printer_id]
    assert len(rows) == 1
    assert rows[0]["due"] is True  # threshold 0 jobs is always crossed


@pytest.mark.asyncio
async def test_complete_marks_done_and_clears_due(client):
    r = await client.post("/api/v1/printers", json={
        "name": "P2", "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "10.0.0.6"},
    })
    printer_id = r.json()["id"]

    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Wash plate", "scope": "general",
        "triggers": [{"trigger_type": "job_count", "amount": 5, "unit": None}],
    })
    item_id = r.json()["id"]

    r = await client.post(f"/api/v1/maintenance/printers/{printer_id}/items/{item_id}/complete")
    assert r.status_code == 200
    assert r.json()["printer_id"] == printer_id

    r = await client.get("/api/v1/maintenance/status")
    row = next(row for row in r.json() if row["printer_id"] == printer_id and row["item_id"] == item_id)
    assert row["due"] is False


@pytest.mark.asyncio
async def test_complete_missing_printer_404(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Wash plate", "scope": "general",
        "triggers": [{"trigger_type": "job_count", "amount": 5, "unit": None}],
    })
    item_id = r.json()["id"]
    r = await client.post(f"/api/v1/maintenance/printers/999/items/{item_id}/complete")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/api/test_maintenance_api.py -v`
Expected: FAIL — new endpoints (`/items/{id}/triggers`, `/templates`, `/status`, `/printers/{id}/items/{id}/complete`) don't exist yet (404s).

- [ ] **Step 3: Add the remaining endpoints**

Append to `backend/app/api/routes/maintenance.py` (add these imports at the top alongside the existing ones):

```python
from ...models import Printer
from ...services import maintenance_service
from ...services.maintenance_service import COMMON_MAINTENANCE_TEMPLATES
from .printers import orca_machine_catalog
```

Add a new request model near the other Pydantic models:

```python
class TriggersReplace(BaseModel):
    triggers: list[TriggerIn]
```

Append the new routes at the end of the file:

```python
@router.put(
    "/items/{item_id}/triggers", summary="Replace an item's triggers",
    responses={404: {"description": "Maintenance item not found"}},
)
async def replace_triggers(item_id: int, body: TriggersReplace, session: AsyncSession = Depends(get_session)) -> dict:
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    for existing in await _triggers_for(session, item_id):
        await session.delete(existing)
    await session.flush()
    for t in body.triggers:
        session.add(MaintenanceTrigger(
            maintenance_item_id=item_id, trigger_type=t.trigger_type, amount=t.amount, unit=t.unit,
        ))
    item.updated_at = _now()
    await session.commit()
    return await _item_dict(session, item)


@router.get("/templates", summary="Suggested common maintenance items")
async def list_templates() -> list[dict]:
    return COMMON_MAINTENANCE_TEMPLATES


@router.get("/status", summary="Due status for every printer x applicable maintenance item")
async def maintenance_status(session: AsyncSession = Depends(get_session)) -> list[dict]:
    printers = list((await session.execute(select(Printer))).scalars().all())
    items = list((await session.execute(
        select(MaintenanceItem).where(MaintenanceItem.enabled.is_(True))
    )).scalars().all())
    triggers_by_item: dict[int, list[MaintenanceTrigger]] = {}
    for item in items:
        triggers_by_item[item.id] = await _triggers_for(session, item.id)
    catalog = await orca_machine_catalog()
    return await maintenance_service.compute_due_status(session, printers, items, triggers_by_item, catalog)


@router.post(
    "/printers/{printer_id}/items/{item_id}/complete",
    summary="Mark a maintenance item done for a printer",
    responses={404: {"description": "Printer or maintenance item not found"}},
)
async def complete_item(printer_id: int, item_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    state = await maintenance_service.mark_done(session, printer, item)
    return {"printer_id": printer_id, "item_id": item_id, "last_done_at": state.last_done_at}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/api/test_maintenance_api.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && pytest -v`
Expected: PASS (no regressions in existing tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/maintenance.py backend/tests/api/test_maintenance_api.py
git commit -m "feat(maintenance): add triggers replace, templates, status, and complete endpoints"
```

---

### Task 6: Accrue lifetime counters on job completion

**Files:**
- Modify: `backend/app/services/queue_engine.py`
- Test: `backend/tests/services/test_queue_engine.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/services/test_queue_engine.py`:

```python
@pytest.mark.asyncio
async def test_handle_print_complete_accrues_lifetime_counters(db):
    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.lifetime_job_count = 4
        printer.lifetime_print_seconds = 7200
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_seconds = 3600
        await session.commit()

    mgr = _make_mock_printer_manager([])
    qe = QueueEngine(db, mgr, MagicMock())
    await qe.handle_print_complete(printer_id)

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        assert printer.lifetime_job_count == 5
        assert printer.lifetime_print_seconds == 10800


@pytest.mark.asyncio
async def test_handle_print_complete_accrues_job_count_even_without_actual_seconds(db):
    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_seconds = None
        await session.commit()

    mgr = _make_mock_printer_manager([])
    qe = QueueEngine(db, mgr, MagicMock())
    await qe.handle_print_complete(printer_id)

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        assert printer.lifetime_job_count == 1
        assert printer.lifetime_print_seconds == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/services/test_queue_engine.py -k lifetime_counters -v`
Expected: FAIL — `printer.lifetime_job_count == 5` fails because the counter is never incremented (stays at seeded value `4`).

- [ ] **Step 3: Hoist the printer load and add the counter increment**

In `backend/app/services/queue_engine.py`, inside `handle_print_complete`, the session block currently reads (around the existing Spoolman branch):

```python
            job_id = job.id
            job.status = "complete"
            job.completed_at = _now()
            job.updated_at = _now()

            # Collect Spoolman deduction data before session closes
            actual_grams = job.actual_filament_grams
            if actual_grams is not None:
                spoolman_cfg = await session.get(SpoolmanConfig, 1)
                if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url:
                    spoolman_url = spoolman_cfg.url
                    spoolman_key = spoolman_cfg.api_key
                    printer = await session.get(Printer, printer_id)
                    loaded = (printer.loaded_filaments if printer else None) or []
```

Replace it with (hoisting the `printer` load out of the conditional, and adding the counter increment right after the job status transition):

```python
            job_id = job.id
            job.status = "complete"
            job.completed_at = _now()
            job.updated_at = _now()

            # Accrue lifetime wear counters for maintenance tracking — every
            # successfully completed job, regardless of Spoolman config.
            printer = await session.get(Printer, printer_id)
            if printer is not None:
                printer.lifetime_job_count += 1
                printer.lifetime_print_seconds += job.actual_seconds or 0

            # Collect Spoolman deduction data before session closes
            actual_grams = job.actual_filament_grams
            if actual_grams is not None:
                spoolman_cfg = await session.get(SpoolmanConfig, 1)
                if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url:
                    spoolman_url = spoolman_cfg.url
                    spoolman_key = spoolman_cfg.api_key
                    loaded = (printer.loaded_filaments if printer else None) or []
```

(The `printer = await session.get(Printer, printer_id)` line inside the `if` block is removed since `printer` is now loaded unconditionally above it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/services/test_queue_engine.py -v`
Expected: PASS (all tests, including the two new ones and the pre-existing Spoolman-deduction tests — confirming the hoist didn't break the existing `printer.loaded_filaments` lookup).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_engine.py
git commit -m "feat(maintenance): accrue printer lifetime job/print-time counters on completion"
```

---

### Task 7: Frontend API client

**Files:**
- Create: `frontend/src/api/maintenance.ts`
- Test: `frontend/src/api/maintenance.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/api/maintenance.test.ts`:

```typescript
// frontend/src/api/maintenance.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  getMaintenanceItems, createMaintenanceItem, deleteMaintenanceItem,
  getMaintenanceTemplates, getMaintenanceStatus, completeMaintenanceItem,
} from './maintenance';

beforeEach(() => vi.restoreAllMocks());

describe('maintenance api', () => {
  it('getMaintenanceItems fetches the list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{
        id: 1, name: 'Wash plate', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ id: 1, trigger_type: 'job_count', amount: 10, unit: null }],
      }]), { status: 200 })));
    const items = await getMaintenanceItems();
    expect(items[0].name).toBe('Wash plate');
    expect(items[0].triggers[0].trigger_type).toBe('job_count');
  });

  it('createMaintenanceItem posts JSON', async () => {
    const f = vi.fn(async () =>
      new Response(JSON.stringify({
        id: 2, name: 'Clean fans', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null, triggers: [],
      }), { status: 201 }));
    vi.stubGlobal('fetch', f);
    const item = await createMaintenanceItem({ name: 'Clean fans', scope: 'general', triggers: [] });
    expect(item.id).toBe(2);
    expect(f).toHaveBeenCalledWith('/api/v1/maintenance/items', expect.objectContaining({ method: 'POST' }));
  });

  it('deleteMaintenanceItem sends DELETE', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ deleted: 3 }), { status: 200 }));
    vi.stubGlobal('fetch', f);
    const result = await deleteMaintenanceItem(3);
    expect(result.deleted).toBe(3);
    expect(f).toHaveBeenCalledWith('/api/v1/maintenance/items/3', expect.objectContaining({ method: 'DELETE' }));
  });

  it('getMaintenanceTemplates fetches suggestions', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{ name: 'Wash build plate', description: 'x', triggers: [] }]), { status: 200 })));
    const templates = await getMaintenanceTemplates();
    expect(templates[0].name).toBe('Wash build plate');
  });

  it('getMaintenanceStatus fetches due rows', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{
        printer_id: 1, printer_name: 'P1', item_id: 1, item_name: 'Wash plate',
        due: true, last_done_at: '2026-01-01T00:00:00',
      }]), { status: 200 })));
    const rows = await getMaintenanceStatus();
    expect(rows[0].due).toBe(true);
  });

  it('completeMaintenanceItem posts to the complete endpoint', async () => {
    const f = vi.fn(async () =>
      new Response(JSON.stringify({ printer_id: 1, item_id: 2, last_done_at: '2026-01-01T00:00:00' }), { status: 200 }));
    vi.stubGlobal('fetch', f);
    const result = await completeMaintenanceItem(1, 2);
    expect(result.last_done_at).toBe('2026-01-01T00:00:00');
    expect(f).toHaveBeenCalledWith('/api/v1/maintenance/printers/1/items/2/complete', expect.objectContaining({ method: 'POST' }));
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/api/maintenance.test.ts`
Expected: FAIL — `Cannot find module './maintenance'`.

- [ ] **Step 3: Write the API client**

Create `frontend/src/api/maintenance.ts`:

```typescript
// frontend/src/api/maintenance.ts
import { useCallback, useEffect, useState } from 'react';

export type TriggerType = 'calendar' | 'job_time' | 'job_count';
export type CalendarUnit = 'hours' | 'days' | 'weeks' | 'months';

export interface MaintenanceTrigger {
  id?: number;
  trigger_type: TriggerType;
  amount: number;
  unit: CalendarUnit | null;
}

export interface MaintenanceItem {
  id: number;
  name: string;
  scope: 'general' | 'model';
  machine_vendor: string | null;
  machine_model: string | null;
  enabled: boolean;
  notes: string | null;
  triggers: MaintenanceTrigger[];
}

export interface MaintenanceTemplate {
  name: string;
  description: string;
  triggers: MaintenanceTrigger[];
}

export interface MaintenanceStatusRow {
  printer_id: number;
  printer_name: string;
  item_id: number;
  item_name: string;
  due: boolean;
  last_done_at: string;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const getMaintenanceItems = () => request<MaintenanceItem[]>('/api/v1/maintenance/items');

export const createMaintenanceItem = (b: {
  name: string; scope: 'general' | 'model'; machine_vendor?: string | null;
  machine_model?: string | null; notes?: string | null; triggers: MaintenanceTrigger[];
}) => request<MaintenanceItem>('/api/v1/maintenance/items', jsonInit('POST', b));

export const updateMaintenanceItem = (id: number, b: Partial<Pick<MaintenanceItem,
  'name' | 'scope' | 'machine_vendor' | 'machine_model' | 'enabled' | 'notes'>>) =>
  request<MaintenanceItem>(`/api/v1/maintenance/items/${id}`, jsonInit('PATCH', b));

export const setMaintenanceTriggers = (id: number, triggers: MaintenanceTrigger[]) =>
  request<MaintenanceItem>(`/api/v1/maintenance/items/${id}/triggers`, jsonInit('PUT', { triggers }));

export const deleteMaintenanceItem = (id: number) =>
  request<{ deleted: number }>(`/api/v1/maintenance/items/${id}`, { method: 'DELETE' });

export const getMaintenanceTemplates = () => request<MaintenanceTemplate[]>('/api/v1/maintenance/templates');

export const getMaintenanceStatus = () => request<MaintenanceStatusRow[]>('/api/v1/maintenance/status');

export const completeMaintenanceItem = (printerId: number, itemId: number) =>
  request<{ printer_id: number; item_id: number; last_done_at: string }>(
    `/api/v1/maintenance/printers/${printerId}/items/${itemId}/complete`, { method: 'POST' });

export function useMaintenanceItems(): { items: MaintenanceItem[]; refetch: () => void } {
  const [items, setItems] = useState<MaintenanceItem[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    getMaintenanceItems().then(d => { if (alive) setItems(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);
  return { items, refetch };
}

export function useMaintenanceStatus(): { rows: MaintenanceStatusRow[]; refetch: () => void } {
  const [rows, setRows] = useState<MaintenanceStatusRow[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    getMaintenanceStatus().then(d => { if (alive) setRows(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);
  return { rows, refetch };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/api/maintenance.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/maintenance.ts frontend/src/api/maintenance.test.ts
git commit -m "feat(maintenance): add frontend API client"
```

---

### Task 8: Settings → Maintenance page

**Files:**
- Modify: `frontend/src/screens/SettingsScreen.tsx`
- Test: `frontend/src/screens/SettingsScreen.maintenance.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/screens/SettingsScreen.maintenance.test.tsx`:

```tsx
// frontend/src/screens/SettingsScreen.maintenance.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/maintenance/items' && (!init || init.method === undefined)) {
      return new Response(JSON.stringify([{
        id: 1, name: 'Wash build plate', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ id: 1, trigger_type: 'job_count', amount: 10, unit: null }],
      }]), { status: 200 });
    }
    if (url === '/api/v1/maintenance/items' && init?.method === 'POST') {
      return new Response(JSON.stringify({
        id: 2, name: 'Clean fans', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ trigger_type: 'calendar', amount: 3, unit: 'months' }],
      }), { status: 201 });
    }
    if (url === '/api/v1/maintenance/templates') {
      return new Response(JSON.stringify([
        { name: 'Clean fans', description: 'Blow out dust.', triggers: [{ trigger_type: 'calendar', amount: 3, unit: 'months' }] },
      ]), { status: 200 });
    }
    if (url === '/api/v1/printers/orca-machine-catalog') {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    // Other settings sub-pages' unrelated calls (spoolman config, queue config, etc.)
    return new Response(JSON.stringify({}), { status: 200 });
  }));
}

beforeEach(() => { vi.restoreAllMocks(); stubFetch(); });

describe('Settings → Maintenance page', () => {
  it('lists existing maintenance items', async () => {
    render(<MemoryRouter initialEntries={['/settings/maintenance']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Wash build plate')).toBeInTheDocument());
    expect(screen.getByText(/10 jobs/i)).toBeInTheDocument();
  });

  it('adds a suggested template as a new item', async () => {
    render(<MemoryRouter initialEntries={['/settings/maintenance']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Wash build plate')).toBeInTheDocument());

    fireEvent.click(await screen.findByRole('button', { name: /add.*clean fans/i }));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(
      (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(
        (c: unknown[]) => c[0] === '/api/v1/maintenance/items' && (c[1] as RequestInit)?.method === 'POST'
      )
    ).toBe(true));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.maintenance.test.tsx`
Expected: FAIL — no `/settings/maintenance` page renders the expected text; the nav item doesn't exist yet.

- [ ] **Step 3: Add the `MaintenancePage` component**

In `frontend/src/screens/SettingsScreen.tsx`, add the import at the top alongside the others:

```typescript
import {
  useMaintenanceItems, createMaintenanceItem, updateMaintenanceItem, setMaintenanceTriggers,
  deleteMaintenanceItem, getMaintenanceTemplates,
  type MaintenanceItem, type MaintenanceTrigger, type MaintenanceTemplate,
} from '../api/maintenance';
import { fetchMachineCatalog, type MachinePreset } from '../api/printers';
```

Add a new icon to `SettingsIcons`:

```typescript
  maintenance: <Icon paths={["M14.7 6.3a1 1 0 0 0 1.4 0l1.6-1.6a1 1 0 0 0 0-1.4l-1.6-1.6a1 1 0 0 0-1.4 0L13.1 3.3a1 1 0 0 0 0 1.4z","M9.6 11.4 4 17a2 2 0 0 0-.6 1.4V21h2.6a2 2 0 0 0 1.4-.6l5.6-5.6"]} />,
```

Insert the `MaintenancePage` component (before the `SettingsScreen` shell section — right after `PrintDefaultsPage` finishes, i.e. immediately before the `// =========================================================================\n// Settings screen shell` comment block):

```tsx
// =========================================================================
// Maintenance page
// =========================================================================

const TRIGGER_LABEL: Record<MaintenanceTrigger['trigger_type'], string> = {
  calendar: 'Calendar', job_time: 'Print time', job_count: 'Job count',
};

function triggerChipText(t: MaintenanceTrigger): string {
  if (t.trigger_type === 'job_count') return `${t.amount} jobs`;
  if (t.trigger_type === 'job_time') return `${t.amount}h operating`;
  return `${t.amount} ${t.unit ?? 'months'}`;
}

interface ItemDraft {
  name: string;
  scope: 'general' | 'model';
  machine_vendor: string;
  machine_model: string;
  triggers: MaintenanceTrigger[];
}

function emptyDraft(): ItemDraft {
  return { name: '', scope: 'general', machine_vendor: '', machine_model: '', triggers: [] };
}

function draftFromTemplate(t: MaintenanceTemplate): ItemDraft {
  return { name: t.name, scope: 'general', machine_vendor: '', machine_model: '', triggers: t.triggers };
}

function TriggerRow({ trigger, onChange, onRemove }: {
  trigger: MaintenanceTrigger; onChange: (t: MaintenanceTrigger) => void; onRemove: () => void;
}) {
  return (
    <div className="row gap-2" style={{ alignItems: 'center' }}>
      <select className="select sm" value={trigger.trigger_type}
              onChange={e => onChange({ ...trigger, trigger_type: e.target.value as MaintenanceTrigger['trigger_type'], unit: e.target.value === 'calendar' ? 'months' : null })}>
        <option value="calendar">Calendar</option>
        <option value="job_time">Print time (hours)</option>
        <option value="job_count">Job count</option>
      </select>
      <input type="number" min={0} className="input sm" style={{ width: 80 }}
             value={trigger.amount}
             onChange={e => onChange({ ...trigger, amount: Number(e.target.value) })} />
      {trigger.trigger_type === 'calendar' && (
        <select className="select sm" value={trigger.unit ?? 'months'}
                onChange={e => onChange({ ...trigger, unit: e.target.value as MaintenanceTrigger['unit'] })}>
          <option value="hours">hours</option>
          <option value="days">days</option>
          <option value="weeks">weeks</option>
          <option value="months">months</option>
        </select>
      )}
      <button type="button" className="btn ghost icon sm" onClick={onRemove}>{Icons.x}</button>
    </div>
  );
}

function MaintenanceItemForm({ draft, catalog, onChange, onSave, onCancel }: {
  draft: ItemDraft; catalog: MachinePreset[];
  onChange: (d: ItemDraft) => void; onSave: () => void; onCancel: () => void;
}) {
  const vendors = Array.from(new Set(catalog.map(c => c.vendor))).sort();
  const models = Array.from(new Set(catalog.filter(c => c.vendor === draft.machine_vendor).map(c => c.printer_model))).sort();

  return (
    <div className="col gap-2" style={{ padding: 16, border: '1px solid var(--border-2)', borderRadius: 8, background: 'var(--bg-2)' }}>
      <input className="input" placeholder="Maintenance item name" value={draft.name}
             onChange={e => onChange({ ...draft, name: e.target.value })} />

      <div className="row gap-2">
        <label className="row gap-1" style={{ alignItems: 'center' }}>
          <input type="radio" checked={draft.scope === 'general'}
                 onChange={() => onChange({ ...draft, scope: 'general' })} />
          General (any printer)
        </label>
        <label className="row gap-1" style={{ alignItems: 'center' }}>
          <input type="radio" checked={draft.scope === 'model'}
                 onChange={() => onChange({ ...draft, scope: 'model' })} />
          Model-specific
        </label>
      </div>

      {draft.scope === 'model' && (
        <div className="row gap-2">
          <select className="select" value={draft.machine_vendor}
                  onChange={e => onChange({ ...draft, machine_vendor: e.target.value, machine_model: '' })}>
            <option value="">Vendor…</option>
            {vendors.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
          <select className="select" value={draft.machine_model} disabled={!draft.machine_vendor}
                  onChange={e => onChange({ ...draft, machine_model: e.target.value })}>
            <option value="">Model…</option>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      )}

      <div className="col gap-1">
        <div className="tiny muted">Triggers (due on whichever fires first)</div>
        {draft.triggers.map((t, i) => (
          <TriggerRow key={i} trigger={t}
                      onChange={next => onChange({ ...draft, triggers: draft.triggers.map((x, j) => j === i ? next : x) })}
                      onRemove={() => onChange({ ...draft, triggers: draft.triggers.filter((_, j) => j !== i) })} />
        ))}
        <button type="button" className="btn ghost sm" style={{ alignSelf: 'flex-start' }}
                onClick={() => onChange({ ...draft, triggers: [...draft.triggers, { trigger_type: 'job_count', amount: 10, unit: null }] })}>
          {Icons.plus} Add trigger
        </button>
      </div>

      <div className="row gap-2">
        <button className="btn primary sm" onClick={onSave}>Save</button>
        <button className="btn sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function MaintenanceItemRow({ item, onEdit, onDelete, onToggle }: {
  item: MaintenanceItem; onEdit: () => void; onDelete: () => void; onToggle: (v: boolean) => void;
}) {
  return (
    <div className="row gap-3" style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-1)', alignItems: 'center' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 500 }}>{item.name}</div>
        <div className="tiny muted">
          {item.scope === 'general' ? 'General' : `${item.machine_vendor} ${item.machine_model}`}
        </div>
      </div>
      <div className="row gap-1" style={{ flex: 1, flexWrap: 'wrap' }}>
        {item.triggers.map(t => (
          <span key={t.id} className="chip sm" title={TRIGGER_LABEL[t.trigger_type]}>{triggerChipText(t)}</span>
        ))}
      </div>
      <Toggle checked={item.enabled} onChange={onToggle} />
      <button className="btn ghost sm" onClick={onEdit}>Edit</button>
      <button className="btn ghost sm" onClick={onDelete}>Delete</button>
    </div>
  );
}

function MaintenancePage() {
  const { items, refetch } = useMaintenanceItems();
  const [templates, setTemplates] = useState<MaintenanceTemplate[]>([]);
  const [catalog, setCatalog] = useState<MachinePreset[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<ItemDraft>(emptyDraft());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMaintenanceTemplates().then(setTemplates).catch(console.error);
    fetchMachineCatalog().then(setCatalog).catch(console.error);
  }, []);

  function startCreate(fromTemplate?: MaintenanceTemplate) {
    setDraft(fromTemplate ? draftFromTemplate(fromTemplate) : emptyDraft());
    setEditingId(null);
    setCreating(true);
  }

  function startEdit(item: MaintenanceItem) {
    setDraft({
      name: item.name, scope: item.scope,
      machine_vendor: item.machine_vendor ?? '', machine_model: item.machine_model ?? '',
      triggers: item.triggers,
    });
    setCreating(false);
    setEditingId(item.id);
  }

  async function handleSave() {
    setError(null);
    try {
      if (editingId != null) {
        await updateMaintenanceItem(editingId, {
          name: draft.name, scope: draft.scope,
          machine_vendor: draft.scope === 'model' ? draft.machine_vendor : null,
          machine_model: draft.scope === 'model' ? draft.machine_model : null,
        });
        await setMaintenanceTriggers(editingId, draft.triggers);
      } else {
        await createMaintenanceItem({
          name: draft.name, scope: draft.scope,
          machine_vendor: draft.scope === 'model' ? draft.machine_vendor : null,
          machine_model: draft.scope === 'model' ? draft.machine_model : null,
          triggers: draft.triggers,
        });
      }
      setCreating(false);
      setEditingId(null);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(id: number) {
    setError(null);
    try {
      await deleteMaintenanceItem(id);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleToggle(item: MaintenanceItem, enabled: boolean) {
    setError(null);
    try {
      await updateMaintenanceItem(item.id, { enabled });
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const generalItems = items.filter(i => i.scope === 'general');
  const modelItems = items.filter(i => i.scope === 'model');

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <PageHeader
          title="Maintenance"
          sub="Track recurring maintenance for your fleet. Each item can have multiple triggers — calendar time, print hours, or job count — and is due the moment any one of them fires."
          actions={<button className="btn primary sm" onClick={() => startCreate()}>{Icons.plus} New item</button>}
        />

        {error && (
          <div style={{ marginBottom: 14, padding: '10px 14px', background: 'var(--bg-1)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
            {error}
          </div>
        )}

        {templates.length > 0 && (
          <div className="col gap-2" style={{ marginBottom: 20 }}>
            <div className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>Suggested items</div>
            <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
              {templates.map(t => (
                <button key={t.name} className="btn sm" title={t.description} onClick={() => startCreate(t)}>
                  {Icons.plus} Add {t.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {(creating || editingId != null) && (
          <div style={{ marginBottom: 16 }}>
            <MaintenanceItemForm draft={draft} catalog={catalog} onChange={setDraft}
                                  onSave={handleSave} onCancel={() => { setCreating(false); setEditingId(null); }} />
          </div>
        )}

        <div className="col gap-1">
          <div className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 8 }}>General</div>
          <div style={{ border: '1px solid var(--border-1)', borderRadius: 8, background: 'var(--bg-1)' }}>
            {generalItems.length === 0 && <div className="small muted" style={{ padding: 16 }}>No general maintenance items yet.</div>}
            {generalItems.map(item => (
              <MaintenanceItemRow key={item.id} item={item} onEdit={() => startEdit(item)}
                                  onDelete={() => handleDelete(item.id)}
                                  onToggle={v => handleToggle(item, v)} />
            ))}
          </div>

          <div className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 16 }}>Model-specific</div>
          <div style={{ border: '1px solid var(--border-1)', borderRadius: 8, background: 'var(--bg-1)' }}>
            {modelItems.length === 0 && <div className="small muted" style={{ padding: 16 }}>No model-specific items yet.</div>}
            {modelItems.map(item => (
              <MaintenanceItemRow key={item.id} item={item} onEdit={() => startEdit(item)}
                                  onDelete={() => handleDelete(item.id)}
                                  onToggle={v => handleToggle(item, v)} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire the page into the Settings nav**

In `frontend/src/screens/SettingsScreen.tsx`, update the `PageId` union and `PAGE_IDS`:

```typescript
type PageId = 'tags' | 'print' | 'maintenance' | 'spoolman' | 'spoolman-mappings' | 'webhook' | 'fleet-backup' | 'about';
```

```typescript
const PAGE_IDS: PageId[] = ['tags', 'print', 'maintenance', 'spoolman', 'spoolman-mappings', 'webhook', 'fleet-backup', 'about'];
```

Add the nav entry to the "Workshop" section:

```typescript
        { id: 'tags',          label: 'Tags',           icon: SettingsIcons.tag,     sub: 'Manage labels across files & jobs' },
        { id: 'print',         label: 'Print defaults', icon: Icons.printer,         sub: 'Queue interval & profile rescan' },
        { id: 'maintenance',   label: 'Maintenance',    icon: SettingsIcons.maintenance, sub: 'Recurring printer upkeep & schedules' },
```

Add the page dispatch:

```typescript
      {activePage === 'maintenance'        && <MaintenancePage />}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.maintenance.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 6: Type-check and run the full frontend suite**

Run: `cd frontend && npm run build`
Expected: PASS (no TypeScript errors — this is the real type-check per `conventions.md`; `tsc --noEmit` is a no-op here).

Run: `cd frontend && npx vitest run`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/screens/SettingsScreen.tsx frontend/src/screens/SettingsScreen.maintenance.test.tsx
git commit -m "feat(maintenance): add Settings → Maintenance page"
```

---

### Task 9: Fleet due-maintenance badge

**Files:**
- Create: `frontend/src/components/MaintenanceDueBadge.tsx`
- Test: `frontend/src/components/MaintenanceDueBadge.test.tsx`
- Modify: `frontend/src/screens/FleetScreen.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/MaintenanceDueBadge.test.tsx`:

```tsx
// frontend/src/components/MaintenanceDueBadge.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MaintenanceDueBadge } from './MaintenanceDueBadge';

describe('MaintenanceDueBadge', () => {
  it('renders nothing when count is 0', () => {
    const { container } = render(<MaintenanceDueBadge count={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a due count when > 0', () => {
    render(<MaintenanceDueBadge count={2} />);
    expect(screen.getByText(/2 due/i)).toBeInTheDocument();
  });

  it('singularizes for a count of 1', () => {
    render(<MaintenanceDueBadge count={1} />);
    expect(screen.getByText(/1 due/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/MaintenanceDueBadge.test.tsx`
Expected: FAIL — `Cannot find module './MaintenanceDueBadge'`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/MaintenanceDueBadge.tsx`:

```tsx
// frontend/src/components/MaintenanceDueBadge.tsx
export function MaintenanceDueBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      className="tiny"
      title={`${count} maintenance item${count === 1 ? '' : 's'} due`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 8px', borderRadius: 999,
        background: 'var(--warn-bg, rgba(234,179,8,0.12))',
        color: 'var(--warn, #eab308)',
        border: '1px solid var(--warn, #eab308)',
        fontWeight: 500,
      }}
    >
      ⚠ {count} due
    </span>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/MaintenanceDueBadge.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire it into `FleetScreen.tsx`**

In `frontend/src/screens/FleetScreen.tsx`, add the imports:

```typescript
import { useMaintenanceStatus } from '../api/maintenance';
import { MaintenanceDueBadge } from '../components/MaintenanceDueBadge';
```

Inside `FleetScreen()`, add the due-status hook and a lookup map near the top of the function body (alongside the other data hooks):

```typescript
  const { rows: maintenanceRows } = useMaintenanceStatus();
  const dueCountByPrinter = maintenanceRows.reduce<Record<number, number>>((acc, r) => {
    if (r.due) acc[r.printer_id] = (acc[r.printer_id] ?? 0) + 1;
    return acc;
  }, {});
```

Then, at each of the three `<StatusPill status={p.status} />` call sites (tile, row, and expanded card densities), add the badge immediately after:

```tsx
          <StatusPill status={p.status} />
          <MaintenanceDueBadge count={dueCountByPrinter[p.id] ?? 0} />
```

- [ ] **Step 6: Run the Fleet screen test suite to check for regressions**

Run: `cd frontend && npx vitest run src/screens/FleetScreen.test.tsx`
Expected: PASS — the existing Fleet tests don't stub `/api/v1/maintenance/status`, so `useMaintenanceStatus` will fail its fetch and fall back to `console.error` + an empty `rows` array (matching the existing `useTags`/`useTypes`-style hook error-handling convention), meaning `dueCountByPrinter` is `{}` and no badge renders — no assertions should break.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/MaintenanceDueBadge.tsx frontend/src/components/MaintenanceDueBadge.test.tsx frontend/src/screens/FleetScreen.tsx
git commit -m "feat(maintenance): show due-maintenance badge on Fleet printer cards"
```

---

## Post-implementation

- [ ] Run `superpowers:concordia-docs-sync` (or manually update `themis/docs/agent/data-model.md`, `recipes.md`, and `CONTRIBUTING.md`'s routes/data-model tables) to document the three new tables, the two new `Printer` columns, and the new `/api/v1/maintenance/*` routes — and to fix the stale "new table → `create_all`, no migration needed" claim flagged at the top of this plan.
- [ ] Regenerate `openapi.json`: `cd backend && python scripts/export_openapi.py` (CI fails if this drifts from the live app).
