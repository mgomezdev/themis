"""Tests for /api/v1/laminus/catalog/* routes (Features 2, 3, and 4)."""
from tests.catalog_helpers import catalog_from_dict, cached_raw, prime_catalog
from app.services import catalog_service
from app.services.providers.slicing import SlicingProviderError
from app.plugins.capabilities.filament_inventory import InvMaterial
from tests.fake_providers import FakeInventoryProvider, FakeSlicingProvider
from tests.inventory_helpers import use_provider
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient

SAMPLE_CATALOG = {
    "machine": [{"name": "Bambu X1C 0.4 nozzle", "uuid": "m1"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
    "filament": [{"name": "Generic PLA", "uuid": "f1"}],
}


# ---- catalog/status tests (Feature 3) ----

async def test_catalog_status_cold_cache_unconfigured(client: AsyncClient):
    """Status when no sidecar configured."""
    prime_catalog(None)
    catalog_service._catalog_bytes = None
    catalog_service._health_memo = None
    with patch("app.services.catalog_service.get_slicing_provider", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["status"] == "unconfigured"
    assert body["catalog_counts"] is None


async def test_catalog_status_includes_catalog_counts(client: AsyncClient):
    """catalog/status returns catalog_counts when cache is warm."""
    prime_catalog(SAMPLE_CATALOG)
    catalog_service._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    catalog_service._health_memo = None
    with patch("app.services.catalog_service.get_slicing_provider", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")
    body = resp.json()
    assert body["catalog_counts"] == {"machine": 1, "process": 1, "filament": 1}


async def test_catalog_status_online(client: AsyncClient):
    """status='online' when health returns catalog_loaded=true."""
    prime_catalog(SAMPLE_CATALOG)
    catalog_service._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    catalog_service._health_memo = None
    fake = FakeSlicingProvider()
    fake.default_health = {"catalog_loaded": True, "catalog_building": False, "catalog_profile_count": 50}
    with patch("app.services.catalog_service.get_slicing_provider", return_value=fake):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "online"


async def test_catalog_status_building_via_flag(client: AsyncClient):
    """status='building' when catalog_building=true."""
    prime_catalog(None)
    catalog_service._catalog_bytes = None
    catalog_service._health_memo = None
    fake = FakeSlicingProvider()
    fake.default_health = {"catalog_loaded": False, "catalog_building": True, "catalog_profile_count": None}
    with patch("app.services.catalog_service.get_slicing_provider", return_value=fake):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "building"


async def test_catalog_status_building_via_503(client: AsyncClient):
    """status='building' when the provider reports its "building" marker (Laminus answers 503 mid-rebuild)."""
    prime_catalog(None)
    catalog_service._catalog_bytes = None
    catalog_service._health_memo = None
    fake = FakeSlicingProvider()
    fake.default_health = {"catalog_loaded": False, "catalog_building": True}
    with patch("app.services.catalog_service.get_slicing_provider", return_value=fake):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "building"


async def test_catalog_status_offline_when_health_fails(client: AsyncClient):
    """status='offline' when health check raises."""
    prime_catalog(None)
    catalog_service._catalog_bytes = None
    catalog_service._health_memo = None
    fake = FakeSlicingProvider()
    fake.fail_on["catalog_health"] = SlicingProviderError("connection refused")
    with patch("app.services.catalog_service.get_slicing_provider", return_value=fake):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "offline"


# ---- refresh drift-gate tests (Feature 2) ----

async def test_refresh_cold_cache_commits_immediately(client: AsyncClient):
    """Cold cache (first sync) commits without drift check."""
    prime_catalog(None)
    catalog_service._catalog_bytes = None
    catalog_service._pending_sync = None

    with patch("app.services.catalog_service.fetch_catalog", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (json.dumps(SAMPLE_CATALOG).encode(), catalog_from_dict(SAMPLE_CATALOG))
        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert cached_raw() == SAMPLE_CATALOG
    assert catalog_service._pending_sync is None


async def test_refresh_no_drift_commits_and_returns_ok(client: AsyncClient):
    """Identical catalog (no drift) commits immediately."""
    prime_catalog(SAMPLE_CATALOG)
    catalog_service._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    catalog_service._pending_sync = None

    with patch("app.services.catalog_service.fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift:
        mock_fetch.return_value = (json.dumps(SAMPLE_CATALOG).encode(), catalog_from_dict(SAMPLE_CATALOG))
        mock_drift.return_value = None  # no drift

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert catalog_service._pending_sync is None


async def test_refresh_drift_returns_pending_remaps_and_parks_catalog(client: AsyncClient):
    """Drift detected → pending_remaps returned, old catalog stays, _pending_sync set."""
    old_bytes = json.dumps(SAMPLE_CATALOG).encode()
    prime_catalog(SAMPLE_CATALOG)
    catalog_service._catalog_bytes = old_bytes
    catalog_service._pending_sync = None

    new_catalog = {"machine": [], "process": [], "filament": []}
    new_bytes = json.dumps(new_catalog).encode()

    drift_payload = {
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Bambu X1C 0.4 nozzle",
                          "options_kind": "machine", "required": True,
                          "affected_printer_ids": [1], "affected_printer_names": ["X1C"],
                          "affected_slots": [None]}],
            "jobs": [], "inventory_filaments": [],
        },
        "options": {"machine": [], "process": [], "filament": []},
        "inventory_error": None,
    }

    with patch("app.services.catalog_service.fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift:
        mock_fetch.return_value = (new_bytes, catalog_from_dict(new_catalog))
        mock_drift.return_value = drift_payload

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending_remaps"
    assert "sync_id" in body
    assert len(body["pending"]["printers"]) == 1

    # Old catalog still active
    assert catalog_service._catalog_bytes == old_bytes
    # Pending sync was parked
    assert catalog_service._pending_sync is not None
    assert catalog_service._pending_sync["sync_id"] == body["sync_id"]
    assert catalog_service._pending_sync["raw"] == new_bytes


# ---- confirm-remap tests (Feature 4) ----

async def test_confirm_remap_no_pending_returns_409(client: AsyncClient):
    """No pending slot → 409."""
    catalog_service._pending_sync = None
    catalog_before = catalog_service._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "any-id",
        "resolutions": {"printers": [], "jobs": [], "inventory_filaments": []}
    })
    assert resp.status_code == 409
    assert catalog_service._pending_sync is None
    assert catalog_service._catalog_bytes == catalog_before


async def test_confirm_remap_wrong_sync_id_returns_409(client: AsyncClient):
    """Wrong sync_id → 409."""
    catalog_service._pending_sync = {
        "sync_id": "correct-id", "raw": b'{}', "catalog": catalog_from_dict({}),
        "pending": {"printers": [], "jobs": [], "inventory_filaments": []},
        "created_at": 0,
    }
    catalog_before = catalog_service._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "wrong-id",
        "resolutions": {"printers": [], "jobs": [], "inventory_filaments": []}
    })
    assert resp.status_code == 409
    assert catalog_service._pending_sync is not None and catalog_service._pending_sync["sync_id"] == "correct-id"  # still parked
    assert catalog_service._catalog_bytes == catalog_before  # not committed


async def test_confirm_remap_missing_required_printer_resolution_returns_422(client: AsyncClient):
    """Missing required printer resolution → 422."""
    catalog_service._pending_sync = {
        "sync_id": "sync-1", "raw": b'{}', "catalog": catalog_from_dict({}),
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Stale Machine",
                          "required": True, "options_kind": "machine",
                          "affected_printer_ids": [1], "affected_printer_names": ["P1"],
                          "affected_slots": [None]}],
            "jobs": [], "inventory_filaments": [],
        },
        "created_at": 0,
    }
    catalog_before = catalog_service._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-1",
        "resolutions": {"printers": [], "jobs": [], "inventory_filaments": []}
    })
    assert resp.status_code == 422
    assert catalog_service._pending_sync is not None and catalog_service._pending_sync["sync_id"] == "sync-1"  # operator can retry
    assert catalog_service._catalog_bytes == catalog_before


async def test_confirm_remap_invalid_job_resolution_returns_422(client: AsyncClient, session_factory, create_job):
    """A job resolution value not present in the new catalog is rejected, not applied blindly."""
    from sqlalchemy import select
    from app.models import JobPrinterConfig

    job_id = await create_job(print_profile="Old Process")
    async with session_factory() as s:
        config_id = (await s.execute(
            select(JobPrinterConfig.id).where(JobPrinterConfig.job_id == job_id))).scalar_one()
    catalog_service._pending_sync = {
        "sync_id": "sync-job",
        "raw": b"{}",
        "catalog": catalog_from_dict({"machine": [], "process": [], "filament": []}),
        "pending": {
            "printers": [],
            "jobs": [{"field": "print_profile", "stale_value": "Old Process",
                      "options_kind": "process", "required": False,
                      "affected_config_ids": [config_id], "affected_file_names": [f"job#{job_id}"]}],
            "inventory_filaments": [],
        },
        "created_at": 0,
    }
    catalog_before = catalog_service._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-job",
        "resolutions": {
            "printers": [],
            "jobs": [{"field": "print_profile", "stale_value": "Old Process",
                      "new_value": "Not In Catalog"}],
            "inventory_filaments": [],
        },
    })
    assert resp.status_code == 422
    async with session_factory() as s:  # nothing was applied
        assert (await s.get(JobPrinterConfig, config_id)).print_profile == "Old Process"
    assert catalog_service._pending_sync is not None and catalog_service._pending_sync["sync_id"] == "sync-job"
    assert catalog_service._catalog_bytes == catalog_before


async def test_confirm_remap_malformed_resolutions_returns_422_not_500(client: AsyncClient):
    """A malformed resolutions payload is a validation error, not an unhandled 500."""
    catalog_service._pending_sync = {
        "sync_id": "sync-malformed",
        "raw": b"{}",
        "catalog": catalog_from_dict({}),
        "pending": {"printers": [], "jobs": [], "inventory_filaments": []},
        "created_at": 0,
    }
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-malformed",
        "resolutions": {"printers": "not-a-list", "jobs": [], "inventory_filaments": []},
    })
    assert resp.status_code == 422
    assert catalog_service._pending_sync is not None and catalog_service._pending_sync["sync_id"] == "sync-malformed"


async def test_confirm_remap_updates_printer_and_commits_catalog(client: AsyncClient):
    """Valid confirm: updates Printer row, commits pending catalog, clears pending slot."""
    # Create a printer via API
    create_resp = await client.post("/api/v1/printers", json={
        "name": "Test Printer",
        "printer_type": "bambu",
        "connection_config": {"ip_address": "1.2.3.4"},
        "current_orca_printer_profile": "Stale Machine",
        "orca_printer_profiles": ["Stale Machine"],
        "loaded_filaments": [],
    })
    assert create_resp.status_code == 201
    printer_id = create_resp.json()["id"]

    new_catalog = {"machine": [{"name": "New Machine", "uuid": "m2"}], "process": [], "filament": []}
    pending_bytes = json.dumps(new_catalog).encode()

    catalog_service._pending_sync = {
        "sync_id": "sync-apply",
        "raw": pending_bytes,
        "catalog": catalog_from_dict(new_catalog),
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Stale Machine",
                          "required": True, "options_kind": "machine",
                          "affected_printer_ids": [printer_id], "affected_printer_names": ["Test Printer"],
                          "affected_slots": [None]}],
            "jobs": [], "inventory_filaments": [],
        },
        "created_at": 0,
    }

    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-apply",
        "resolutions": {
            "printers": [{"field": "current_orca_printer_profile",
                          "stale_value": "Stale Machine", "new_value": "New Machine"}],
            "jobs": [], "inventory_filaments": [],
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["applied"]["printers"] == 1
    assert catalog_service._pending_sync is None
    assert cached_raw() == new_catalog

    # Verify DB updated
    printer_resp = await client.get(f"/api/v1/printers/{printer_id}")
    assert printer_resp.json()["current_orca_printer_profile"] == "New Machine"


async def test_confirm_remap_spoolman_only_raw_none_skips_commit_catalog(client: AsyncClient):
    """raw=None (Spoolman-only pending): clears pending, does NOT swap catalog."""
    original_catalog = cached_raw()
    catalog_service._pending_sync = {
        "sync_id": "spoolman-only",
        "raw": None,
        "catalog": None,
        "pending": {
            "printers": [], "jobs": [],
            "inventory_filaments": [{
                "printer_preset": "Bambu X1C 0.4 nozzle",
                "stale_name": "Old PLA",
                "required": False,
                "affected_filament_ids": [5],
                "affected_filament_names": ["Red PLA"],
            }],
        },
        "created_at": 0,
    }

    inventory = FakeInventoryProvider(materials=[InvMaterial(ref="5", name="Red PLA")])
    await use_provider(inventory)
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "spoolman-only",
        "resolutions": {
            "printers": [], "jobs": [],
            "inventory_filaments": [{
                "printer_preset": "Bambu X1C 0.4 nozzle",
                "stale_name": "Old PLA",
                "new_name": None,
                "affected_filament_ids": [5],
            }]
        }
    })

    assert resp.status_code == 200
    assert catalog_service._pending_sync is None
    assert cached_raw() is original_catalog  # NOT swapped


# ---- confirm-remap: applying each resolution type -------------------------------------------

from sqlalchemy import select

from app.models import JobPrinterConfig, Printer

_NEW_CATALOG = {
    "machine": [{"name": "New Machine", "uuid": "m2"}],
    "process": [{"name": "0.20mm New", "uuid": "p2"}],
    "filament": [{"name": "New PLA", "uuid": "f2"}],
}


def _park(pending: dict, *, raw: bytes | None = b'{"new": true}', catalog: dict | None = _NEW_CATALOG, sync_id="sync-x"):
    catalog_service._pending_sync = {"sync_id": sync_id, "raw": raw, "catalog": catalog_from_dict(catalog), "pending": pending, "created_at": 0}
    return sync_id


def _pending(printers=(), jobs=(), spoolman=()):
    return {"printers": list(printers), "jobs": list(jobs), "inventory_filaments": list(spoolman)}


async def _confirm(client, sync_id, *, printers=(), jobs=(), spoolman=()):
    return await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": sync_id,
        "resolutions": {"printers": list(printers), "jobs": list(jobs), "inventory_filaments": list(spoolman)},
    })


async def _printer_row(session_factory, printer_id):
    async with session_factory() as s:
        return await s.get(Printer, printer_id)


async def test_confirm_remap_rewrites_the_active_preset_and_loaded_slot_profiles(client, create_printer, session_factory):
    a = await create_printer(name="A", current_orca_printer_profile="Old Machine",
                             loaded_filaments=[{"slot": 0, "filament_profile": "Old PLA", "type": "PLA"},
                                               {"slot": 1, "filament_profile": "Keep Me", "type": "PETG"}])
    b = await create_printer(name="B", current_orca_printer_profile="Old Machine")
    sync = _park(_pending(printers=[
        {"field": "current_orca_printer_profile", "stale_value": "Old Machine", "required": True,
         "options_kind": "machine", "affected_printer_ids": [a, b, 9999], "affected_printer_names": ["A", "B", "?"],
         "affected_slots": [None, None, None]},
        {"field": "filament_profile", "stale_value": "Old PLA", "required": False, "options_kind": "filament",
         "affected_printer_ids": [a, a], "affected_printer_names": ["A", "A"], "affected_slots": [0, 5]},
    ]))

    resp = await _confirm(client, sync, printers=[
        {"field": "current_orca_printer_profile", "stale_value": "Old Machine", "new_value": "New Machine"},
        {"field": "filament_profile", "stale_value": "Old PLA", "new_value": "New PLA"},
    ])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "applied": {"printers": 3, "jobs": 0, "inventory_filaments": 0},
                           "inventory_failures": []}  # a, b machine + a slot 0; missing printer & out-of-range slot skipped
    row_a, row_b = await _printer_row(session_factory, a), await _printer_row(session_factory, b)
    assert row_a.current_orca_printer_profile == row_b.current_orca_printer_profile == "New Machine"
    assert [s["filament_profile"] for s in row_a.loaded_filaments] == ["New PLA", "Keep Me"]
    assert row_a.loaded_filaments[0]["type"] == "PLA"  # the rest of the slot survives
    assert cached_raw() == _NEW_CATALOG and catalog_service._catalog_bytes == b'{"new": true}'
    assert catalog_service._pending_sync is None


async def test_confirm_remap_optional_printer_entry_without_a_resolution_clears_the_field(client, create_printer, session_factory):
    pid = await create_printer(current_orca_printer_profile="Gone Machine")
    sync = _park(_pending(printers=[
        {"field": "current_orca_printer_profile", "stale_value": "Gone Machine", "required": False,
         "options_kind": "machine", "affected_printer_ids": [pid], "affected_printer_names": ["P"],
         "affected_slots": [None]}]))

    resp = await _confirm(client, sync)

    assert resp.status_code == 200 and resp.json()["applied"]["printers"] == 1
    assert (await _printer_row(session_factory, pid)).current_orca_printer_profile is None


async def test_confirm_remap_rewrites_job_configs_and_clears_a_dropped_filament(client, create_job, session_factory):
    j1 = await create_job(print_profile="Old Process", filament_profile="Old PLA")
    j2 = await create_job(print_profile="Old Process", filament_profile="Old PLA")
    async with session_factory() as s:
        ids = [c.id for c in (await s.execute(select(JobPrinterConfig).order_by(JobPrinterConfig.id))).scalars()]
    sync = _park(_pending(jobs=[
        {"field": "print_profile", "stale_value": "Old Process", "options_kind": "process", "required": False,
         "affected_config_ids": ids + [9999], "affected_file_names": ["a", "b", "?"]},
        {"field": "filament_profile", "stale_value": "Old PLA", "options_kind": "filament", "required": False,
         "affected_config_ids": [ids[0]], "affected_file_names": ["a"]},
    ]))

    resp = await _confirm(client, sync, jobs=[
        {"field": "print_profile", "stale_value": "Old Process", "new_value": "0.20mm New"},
        {"field": "filament_profile", "stale_value": "Old PLA", "new_value": None},   # no replacement: drop it
    ])

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == {"printers": 0, "jobs": 3, "inventory_filaments": 0}
    async with session_factory() as s:
        first, second = (await s.get(JobPrinterConfig, ids[0]), await s.get(JobPrinterConfig, ids[1]))
    assert (first.print_profile, first.filament_profile) == ("0.20mm New", None)
    assert (second.print_profile, second.filament_profile) == ("0.20mm New", "Old PLA")  # untouched by entry 2


async def test_confirm_remap_lists_every_unresolved_or_invalid_entry_and_changes_nothing(client, create_printer, session_factory):
    pid = await create_printer(current_orca_printer_profile="Old Machine")
    sync = _park(_pending(
        printers=[
            {"field": "current_orca_printer_profile", "stale_value": "Old Machine", "required": True,
             "options_kind": "machine", "affected_printer_ids": [pid], "affected_printer_names": ["P"], "affected_slots": [None]},
            {"field": "filament_profile", "stale_value": "Old PLA", "required": False, "options_kind": "filament",
             "affected_printer_ids": [pid], "affected_printer_names": ["P"], "affected_slots": [0]},
        ],
        jobs=[{"field": "print_profile", "stale_value": "Old Process", "options_kind": "process", "required": False,
               "affected_config_ids": [1], "affected_file_names": ["x"]}],
    ))

    resp = await _confirm(client, sync,
                          printers=[{"field": "filament_profile", "stale_value": "Old PLA", "new_value": "Ghost PLA"}],
                          jobs=[{"field": "print_profile", "stale_value": "Old Process", "new_value": "Ghost Process"}])

    assert resp.status_code == 422
    assert resp.json()["detail"]["unresolved"] == [
        "Printer current_orca_printer_profile=Old Machine",   # required, no resolution
        "Invalid value 'Ghost PLA' for filament_profile",     # not in the new catalog
        "Invalid value 'Ghost Process' for job print_profile",
    ]
    assert (await _printer_row(session_factory, pid)).current_orca_printer_profile == "Old Machine"
    assert catalog_service._pending_sync is not None and catalog_service._pending_sync["sync_id"] == "sync-x"  # operator can retry


async def test_confirm_remap_failure_midway_rolls_back_every_change_and_keeps_the_pending_remap(client, create_printer, session_factory):
    pid = await create_printer(current_orca_printer_profile="Old Machine")
    sync = _park(_pending(
        printers=[{"field": "current_orca_printer_profile", "stale_value": "Old Machine", "required": True,
                   "options_kind": "machine", "affected_printer_ids": [pid], "affected_printer_names": ["P"],
                   "affected_slots": [None]}],
        jobs=[{"field": "print_profile", "stale_value": "Old Process", "options_kind": "process", "required": False,
               "affected_config_ids": None, "affected_file_names": []}],   # malformed: blows up after the printer update
    ))
    catalog_before = cached_raw()

    with pytest.raises(TypeError):
        await _confirm(client, sync, printers=[
            {"field": "current_orca_printer_profile", "stale_value": "Old Machine", "new_value": "New Machine"}])

    assert (await _printer_row(session_factory, pid)).current_orca_printer_profile == "Old Machine"  # not half-applied
    assert cached_raw() is catalog_before
    assert catalog_service._pending_sync is not None


# ---- confirm-remap: Spoolman follow-up (best effort, after the DB commit) ----------------------

def _spoolman_entry(*ids, preset="Bambu X1C 0.4 nozzle", stale="Old PLA"):
    return {"printer_preset": preset, "stale_name": stale, "required": False,
            "affected_filament_ids": list(ids), "affected_filament_names": [f"fil{i}" for i in ids]}


def _bound(ref, bindings):
    return InvMaterial(ref=str(ref), name=f"fil{ref}", profile_links={k: list(v) for k, v in bindings.items()})


def _links(provider, ref):
    return provider.materials[str(ref)].profile_links


async def test_confirm_remap_rewrites_filament_bindings_and_reports_the_ones_that_failed(client):
    preset = "Bambu X1C 0.4 nozzle"
    inventory = FakeInventoryProvider(materials=[
        _bound(5, {preset: ["Old PLA", "Keep A"], "Other Printer": ["Old PLA"]}),
        _bound(6, {preset: ["Old PLA"]}),
        _bound(7, {preset: ["Old PLA"]}),
    ])
    real_set = inventory.set_profile_links

    async def flaky_set(ref, links):
        if ref == "7":
            raise RuntimeError("spoolman down")
        return await real_set(ref, links)

    inventory.set_profile_links = flaky_set

    sync = _park(_pending(spoolman=[_spoolman_entry(5, 6, 7)]))
    await use_provider(inventory)
    resp = await _confirm(client, sync, spoolman=[
        {"printer_preset": preset, "stale_name": "Old PLA", "new_name": "New PLA"}])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"]["inventory_filaments"] == 2
    assert body["inventory_failures"] == ["filament 7: spoolman down"]
    # re-read: stale name swapped for the new one on this preset only; other presets and other names untouched
    assert _links(inventory, 5) == {preset: ["Keep A", "New PLA"], "Other Printer": ["Old PLA"]}
    assert _links(inventory, 6) == {preset: ["New PLA"]}
    assert _links(inventory, 7) == {preset: ["Old PLA"]}          # the failed one is left as it was
    assert cached_raw() == _NEW_CATALOG and catalog_service._pending_sync is None  # a Spoolman failure never blocks the commit


async def test_confirm_remap_without_a_replacement_removes_the_stale_name_and_drops_an_emptied_preset(client):
    preset = "Bambu X1C 0.4 nozzle"
    inventory = FakeInventoryProvider(materials=[_bound(5, {preset: ["Old PLA"]})])

    sync = _park(_pending(spoolman=[_spoolman_entry(5)]))
    await use_provider(inventory)
    resp = await _confirm(client, sync, spoolman=[{"printer_preset": preset, "stale_name": "Old PLA", "new_name": None}])

    assert resp.status_code == 200 and resp.json()["applied"]["inventory_filaments"] == 1
    assert _links(inventory, 5) == {}  # the preset key is removed rather than left as an empty list


async def test_confirm_remap_skips_spoolman_entirely_when_it_is_not_configured(client):
    sync = _park(_pending(spoolman=[_spoolman_entry(5)]))
    resp = await _confirm(client, sync)                           # no inventory provider is active at all

    assert resp.status_code == 200
    assert resp.json()["applied"]["inventory_filaments"] == 0
    assert catalog_service._pending_sync is None


async def test_confirm_remap_skips_the_binding_rewrite_for_a_provider_without_profile_bindings(client):
    inventory = FakeInventoryProvider(materials=[_bound(5, {"P": ["Old PLA"]})])
    inventory.capabilities = frozenset()

    sync = _park(_pending(spoolman=[_spoolman_entry(5, preset="P")]))
    await use_provider(inventory)
    resp = await _confirm(client, sync)

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"]["inventory_filaments"] == 0 and resp.json()["inventory_failures"] == []
    assert inventory.calls == []                                   # nothing read or written
    assert inventory.materials["5"].profile_links == {"P": ["Old PLA"]}
    assert cached_raw() == _NEW_CATALOG                            # the catalog commit still happens


async def test_confirm_remap_job_print_profile_without_a_replacement_becomes_blank_not_null(client, create_job, session_factory):
    await create_job(print_profile="Old Process")
    async with session_factory() as s:
        cfg_id = (await s.execute(select(JobPrinterConfig.id))).scalar_one()
    sync = _park(_pending(jobs=[{"field": "print_profile", "stale_value": "Old Process", "options_kind": "process",
                                 "required": False, "affected_config_ids": [cfg_id], "affected_file_names": ["a"]}]))

    resp = await _confirm(client, sync)  # operator picked no replacement for an optional entry

    assert resp.status_code == 200 and resp.json()["applied"]["jobs"] == 1
    async with session_factory() as s:
        assert (await s.get(JobPrinterConfig, cfg_id)).print_profile == ""  # the column is NOT NULL



async def test_catalog_status_memoizes_provider_health_for_thirty_seconds(client: AsyncClient):
    """Two status polls inside the memo window hit the provider once; after the TTL it is asked again."""
    catalog_service._health_memo = None
    catalog_service._health_memo_at = 0.0
    fake = FakeSlicingProvider()
    with patch("app.services.catalog_service.get_slicing_provider", return_value=fake):
        await client.get("/api/v1/laminus/catalog/status")
        await client.get("/api/v1/laminus/catalog/status")
        assert [c[0] for c in fake.calls].count("catalog_health") == 1

        catalog_service._health_memo_at -= catalog_service._HEALTH_MEMO_TTL + 1
        await client.get("/api/v1/laminus/catalog/status")
    assert [c[0] for c in fake.calls].count("catalog_health") == 2


async def test_confirm_remap_reads_the_inventory_once_and_sees_its_own_writes_across_entries(client):
    """One `list_materials` for the whole remap (not one per filament), and two entries touching the same material
    compose: the second rewrite starts from the first one's result."""
    preset = "Bambu X1C 0.4 nozzle"
    inventory = FakeInventoryProvider(materials=[_bound(5, {preset: ["Old A", "Old B"]}), _bound(6, {preset: ["Old A"]})])
    await use_provider(inventory)
    sync = _park(_pending(spoolman=[_spoolman_entry(5, 6, stale="Old A"), _spoolman_entry(5, stale="Old B")]))

    resp = await _confirm(client, sync, spoolman=[
        {"printer_preset": preset, "stale_name": "Old A", "new_name": "New A"},
        {"printer_preset": preset, "stale_name": "Old B", "new_name": "New B"}])

    assert resp.status_code == 200 and resp.json()["applied"]["inventory_filaments"] == 3
    assert inventory.calls.count("list_materials") == 1
    assert _links(inventory, 5) == {preset: ["New A", "New B"]}
    assert _links(inventory, 6) == {preset: ["New A"]}
