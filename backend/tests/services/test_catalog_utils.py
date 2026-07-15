"""Tests for catalog_utils.py — catalog_name_sets and compute_drift."""
from __future__ import annotations

import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base
from app.models import Job, JobPrinterConfig, Printer, UploadedFile
from app.services.catalog_utils import catalog_name_sets, compute_drift


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def drift_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _add_printer(session: AsyncSession, **kwargs) -> Printer:
    defaults = dict(
        name="Printer A",
        printer_type="elegoo_centauri",
        connection_config={},
        current_orca_printer_profile=None,
        loaded_filaments=[],
    )
    defaults.update(kwargs)
    p = Printer(**defaults)
    session.add(p)
    await session.flush()
    return p


async def _add_queued_job(
    session: AsyncSession,
    print_profile: str = "Standard Quality",
    filament_profile: str | None = None,
    status: str = "queued",
) -> tuple[Job, JobPrinterConfig]:
    """Add an UploadedFile + Job + JobPrinterConfig in one shot."""
    f = UploadedFile(
        original_filename="test.3mf",
        stored_path="/data/uploads/test.3mf",
        plates=[],
        uploaded_at=_now(),
    )
    session.add(f)
    await session.flush()

    p = await _add_printer(session)

    j = Job(
        uploaded_file_id=f.id,
        plate_number=1,
        queue_position=1.0,
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(j)
    await session.flush()

    cfg = JobPrinterConfig(
        job_id=j.id,
        printer_id=p.id,
        print_profile=print_profile,
        filament_profile=filament_profile,
    )
    session.add(cfg)
    await session.flush()
    return j, cfg


# ---------------------------------------------------------------------------
# catalog_name_sets — pure function tests (no async needed)
# ---------------------------------------------------------------------------

def test_catalog_name_sets_normal():
    catalog = {
        "machine": [{"name": "Bambu X1C"}, {"name": "Bambu P1S"}],
        "process": [{"name": "0.20mm Standard"}],
        "filament": [
            {"name": "Bambu PLA Basic", "uuid": "aaaa-1111"},
            {"name": "Bambu ABS", "uuid": "bbbb-2222"},
        ],
    }
    machines, processes, filaments, uuids = catalog_name_sets(catalog)
    assert machines == {"Bambu X1C", "Bambu P1S"}
    assert processes == {"0.20mm Standard"}
    assert filaments == {"Bambu PLA Basic", "Bambu ABS"}
    assert uuids == {"aaaa-1111", "bbbb-2222"}


def test_catalog_name_sets_empty():
    machines, processes, filaments, uuids = catalog_name_sets({})
    assert machines == set()
    assert processes == set()
    assert filaments == set()
    assert uuids == set()


def test_catalog_name_sets_missing_name_skipped():
    catalog = {
        "machine": [{"name": "Good Machine"}, {"uuid": "no-name-only-uuid"}],
        "process": [{}],
        "filament": [
            {"name": "Good Filament", "uuid": "uuid-good"},
            {"name": ""},          # falsy name — skip
            {"uuid": "uuid-only"}, # missing name — skip name, capture uuid
        ],
    }
    machines, processes, filaments, uuids = catalog_name_sets(catalog)
    assert machines == {"Good Machine"}
    assert processes == set()
    assert filaments == {"Good Filament"}
    # uuid-only entry has no "name" but has "uuid" — uuid IS captured
    assert uuids == {"uuid-good", "uuid-only"}


# ---------------------------------------------------------------------------
# compute_drift — async tests
# ---------------------------------------------------------------------------

OLD_CAT = {
    "machine": [{"name": "Bambu X1C"}],
    "process": [{"name": "0.20mm Standard"}],
    "filament": [{"name": "Bambu PLA Basic", "uuid": "aaaa-1111"}],
}
NEW_CAT_SAME = OLD_CAT  # no removals
NEW_CAT_ALL_DIFFERENT = {
    "machine": [{"name": "Bambu X1C"}],
    "process": [{"name": "0.20mm Standard"}],
    "filament": [{"name": "Bambu PLA Basic", "uuid": "aaaa-1111"}],
}


@pytest.mark.asyncio
async def test_compute_drift_no_removals_returns_none(drift_session):
    result = await compute_drift(OLD_CAT, NEW_CAT_SAME, drift_session, None)
    assert result is None


@pytest.mark.asyncio
async def test_compute_drift_removals_unreferenced_returns_none(drift_session):
    """Removed profile exists in diff but no DB rows reference it → None."""
    new_cat = {
        "machine": [{"name": "Bambu X1C New"}],  # removed "Bambu X1C"
        "process": [{"name": "0.20mm Standard"}],
        "filament": [{"name": "Bambu PLA Basic", "uuid": "aaaa-1111"}],
    }
    # No printers in DB at all → nothing stale references the removed machine
    result = await compute_drift(OLD_CAT, new_cat, drift_session, None)
    assert result is None


@pytest.mark.asyncio
async def test_compute_drift_stale_printer_profile(drift_session):
    """Two printers with the same stale machine profile → one grouped entry with both IDs."""
    # Add two printers both using "Bambu X1C" which is removed in new_cat
    p1 = await _add_printer(
        drift_session,
        name="Printer One",
        current_orca_printer_profile="Bambu X1C",
    )
    p2 = await _add_printer(
        drift_session,
        name="Printer Two",
        current_orca_printer_profile="Bambu X1C",
    )

    new_cat = {
        "machine": [{"name": "Bambu X1C New"}],
        "process": [{"name": "0.20mm Standard"}],
        "filament": [{"name": "Bambu PLA Basic", "uuid": "aaaa-1111"}],
    }

    result = await compute_drift(OLD_CAT, new_cat, drift_session, None)
    assert result is not None

    printer_entries = result["pending"]["printers"]
    # Should be exactly one group for (current_orca_printer_profile, "Bambu X1C")
    assert len(printer_entries) == 1
    entry = printer_entries[0]
    assert entry["stale_value"] == "Bambu X1C"
    assert entry["field"] == "current_orca_printer_profile"
    assert set(entry["affected_printer_ids"]) == {p1.id, p2.id}
    assert set(entry["affected_printer_names"]) == {"Printer One", "Printer Two"}


@pytest.mark.asyncio
async def test_compute_drift_two_queued_jobs_same_stale_profile(drift_session):
    """Two queued jobs sharing the same stale print_profile → single grouped entry, two config IDs."""
    new_cat = {
        "machine": [{"name": "Bambu X1C"}],
        "process": [{"name": "0.20mm Standard New"}],  # removed "0.20mm Standard"
        "filament": [{"name": "Bambu PLA Basic", "uuid": "aaaa-1111"}],
    }

    _, cfg1 = await _add_queued_job(drift_session, print_profile="0.20mm Standard")
    _, cfg2 = await _add_queued_job(drift_session, print_profile="0.20mm Standard")

    result = await compute_drift(OLD_CAT, new_cat, drift_session, None)
    assert result is not None

    job_entries = result["pending"]["jobs"]
    # One group for print_profile="0.20mm Standard"
    process_entries = [e for e in job_entries if e["field"] == "print_profile"]
    assert len(process_entries) == 1
    entry = process_entries[0]
    assert entry["stale_value"] == "0.20mm Standard"
    assert set(entry["affected_config_ids"]) == {cfg1.id, cfg2.id}


@pytest.mark.asyncio
async def test_compute_drift_spoolman_stale_name(drift_session):
    """Spoolman filaments referencing a removed profile name → one grouped entry per (preset, name)."""
    # "Bambu PLA Basic" exists in OLD_CAT but is removed in new_cat
    new_cat = {
        "machine": [{"name": "Bambu X1C"}],
        "process": [{"name": "0.20mm Standard"}],
        "filament": [{"name": "Bambu PLA New", "uuid": "bbbb-9999"}],
    }

    # Two Spoolman filaments both have "Bambu PLA Basic" listed for the same printer preset
    spool_filaments = [
        {
            "id": 1,
            "name": "Spool A",
            "extra": {"orca_profiles": json.dumps(json.dumps({"Bambu X1C 0.4 nozzle": ["Bambu PLA Basic"]}))},
        },
        {
            "id": 2,
            "name": "Spool B",
            "extra": {"orca_profiles": json.dumps(json.dumps({"Bambu X1C 0.4 nozzle": ["Bambu PLA Basic"]}))},
        },
    ]

    class FakeSpoolmanCfg:
        enabled = True
        url = "http://spoolman:7912"
        api_key = None

    with patch("app.services.catalog_utils.fetch_filaments", new=AsyncMock(return_value=spool_filaments)):
        result = await compute_drift(OLD_CAT, new_cat, drift_session, FakeSpoolmanCfg())

    assert result is not None
    spool_entries = result["pending"]["spoolman_filaments"]
    # One entry grouped by (printer_preset, stale_name)
    assert len(spool_entries) == 1
    entry = spool_entries[0]
    assert entry["printer_preset"] == "Bambu X1C 0.4 nozzle"
    assert entry["stale_name"] == "Bambu PLA Basic"
    assert set(entry["affected_filament_ids"]) == {1, 2}
    assert set(entry["affected_filament_names"]) == {"Spool A", "Spool B"}


@pytest.mark.asyncio
async def test_compute_drift_spoolman_fetch_failure_sets_error(drift_session):
    """Spoolman HTTP failure → spoolman_error set; printer hits still captured."""
    removed_uuid = "aaaa-1111"
    new_cat = {
        "machine": [{"name": "Bambu X1C New"}],  # remove "Bambu X1C"
        "process": [{"name": "0.20mm Standard"}],
        "filament": [{"name": "Bambu PLA New", "uuid": "bbbb-9999"}],
    }

    # Add a printer that references the removed machine profile
    await _add_printer(
        drift_session,
        name="Printer One",
        current_orca_printer_profile="Bambu X1C",
    )

    class FakeSpoolmanCfg:
        enabled = True
        url = "http://spoolman:7912"
        api_key = None

    with patch(
        "app.services.catalog_utils.fetch_filaments",
        new=AsyncMock(side_effect=Exception("connection refused")),
    ):
        result = await compute_drift(OLD_CAT, new_cat, drift_session, FakeSpoolmanCfg())

    assert result is not None
    assert result["spoolman_error"] == "connection refused"
    # Printer hit should still appear
    assert len(result["pending"]["printers"]) == 1


@pytest.mark.asyncio
async def test_compute_drift_spoolman_disabled_skips_section(drift_session):
    """SpoolmanConfig.enabled=False → fetch_filaments never called."""
    new_cat = {
        "machine": [{"name": "Bambu X1C"}],
        "process": [{"name": "0.20mm Standard"}],
        "filament": [{"name": "Bambu PLA New", "uuid": "bbbb-9999"}],  # aaaa-1111 removed
    }

    # Add a printer to ensure something is returned (otherwise result is None)
    await _add_printer(
        drift_session,
        name="Printer X",
        current_orca_printer_profile=None,
        loaded_filaments=[{"filament_profile": "Bambu PLA Basic", "slot": 0}],
    )

    class FakeSpoolmanCfgDisabled:
        enabled = False
        url = "http://spoolman:7912"
        api_key = None

    mock_fetch = AsyncMock(return_value=[])
    with patch("app.services.catalog_utils.fetch_filaments", new=mock_fetch):
        result = await compute_drift(OLD_CAT, new_cat, drift_session, FakeSpoolmanCfgDisabled())

    mock_fetch.assert_not_called()
    # If filament was stale, it should show in printers section even though spoolman skipped
    assert result is not None
    assert result["pending"]["spoolman_filaments"] == []
