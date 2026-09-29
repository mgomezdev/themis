"""Tests for /api/v1/laminus/catalog/* routes (Features 2, 3, and 4)."""
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient

import app.api.routes.laminus as lmod

SAMPLE_CATALOG = {
    "machine": [{"name": "Bambu X1C 0.4 nozzle", "uuid": "m1"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
    "filament": [{"name": "Generic PLA", "uuid": "f1"}],
}


# ---- catalog/status tests (Feature 3) ----

async def test_catalog_status_cold_cache_unconfigured(client: AsyncClient):
    """Status when no sidecar configured."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["status"] == "unconfigured"
    assert body["catalog_counts"] is None


async def test_catalog_status_includes_catalog_counts(client: AsyncClient):
    """catalog/status returns catalog_counts when cache is warm."""
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._health_memo = None
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")
    body = resp.json()
    assert body["catalog_counts"] == {"machine": 1, "process": 1, "filament": 1}


async def test_catalog_status_online(client: AsyncClient):
    """status='online' when health returns catalog_loaded=true."""
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._health_memo = None
    health_resp = MagicMock()
    health_resp.status_code = 200
    health_resp.json.return_value = {
        "catalog_loaded": True, "catalog_building": False, "catalog_profile_count": 50
    }
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_resp):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "online"


async def test_catalog_status_building_via_flag(client: AsyncClient):
    """status='building' when catalog_building=true."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    health_resp = MagicMock()
    health_resp.status_code = 200
    health_resp.json.return_value = {
        "catalog_loaded": False, "catalog_building": True, "catalog_profile_count": None
    }
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_resp):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "building"


async def test_catalog_status_building_via_503(client: AsyncClient):
    """status='building' when health returns 503 (catalog rebuild in progress)."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    health_resp = MagicMock()
    health_resp.status_code = 503
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_resp):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "building"


async def test_catalog_status_offline_when_health_fails(client: AsyncClient):
    """status='offline' when health check raises."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", side_effect=Exception("connection refused")):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "offline"


# ---- refresh drift-gate tests (Feature 2) ----

async def test_refresh_cold_cache_commits_immediately(client: AsyncClient):
    """Cold cache (first sync) commits without drift check."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._pending_sync = None

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (json.dumps(SAMPLE_CATALOG).encode(), SAMPLE_CATALOG)
        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert lmod._catalog_dict == SAMPLE_CATALOG
    assert lmod._pending_sync is None


async def test_refresh_no_drift_commits_and_returns_ok(client: AsyncClient):
    """Identical catalog (no drift) commits immediately."""
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._pending_sync = None

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift:
        mock_fetch.return_value = (json.dumps(SAMPLE_CATALOG).encode(), SAMPLE_CATALOG)
        mock_drift.return_value = None  # no drift

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert lmod._pending_sync is None


async def test_refresh_drift_returns_pending_remaps_and_parks_catalog(client: AsyncClient):
    """Drift detected → pending_remaps returned, old catalog stays, _pending_sync set."""
    old_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = old_bytes
    lmod._pending_sync = None

    new_catalog = {"machine": [], "process": [], "filament": []}
    new_bytes = json.dumps(new_catalog).encode()

    drift_payload = {
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Bambu X1C 0.4 nozzle",
                          "options_kind": "machine", "required": True,
                          "affected_printer_ids": [1], "affected_printer_names": ["X1C"],
                          "affected_slots": [None]}],
            "jobs": [], "spoolman_filaments": [],
        },
        "options": {"machine": [], "process": [], "filament": []},
        "spoolman_error": None,
    }

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift:
        mock_fetch.return_value = (new_bytes, new_catalog)
        mock_drift.return_value = drift_payload

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending_remaps"
    assert "sync_id" in body
    assert len(body["pending"]["printers"]) == 1

    # Old catalog still active
    assert lmod._catalog_bytes == old_bytes
    # Pending sync was parked
    assert lmod._pending_sync is not None
    assert lmod._pending_sync["sync_id"] == body["sync_id"]
    assert lmod._pending_sync["raw"] == new_bytes


# ---- confirm-remap tests (Feature 4) ----

async def test_confirm_remap_no_pending_returns_409(client: AsyncClient):
    """No pending slot → 409."""
    lmod._pending_sync = None
    catalog_before = lmod._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "any-id",
        "resolutions": {"printers": [], "jobs": [], "spoolman_filaments": []}
    })
    assert resp.status_code == 409
    assert lmod._pending_sync is None
    assert lmod._catalog_bytes == catalog_before


async def test_confirm_remap_wrong_sync_id_returns_409(client: AsyncClient):
    """Wrong sync_id → 409."""
    lmod._pending_sync = {
        "sync_id": "correct-id", "raw": b'{}', "catalog": {},
        "pending": {"printers": [], "jobs": [], "spoolman_filaments": []},
        "created_at": 0,
    }
    catalog_before = lmod._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "wrong-id",
        "resolutions": {"printers": [], "jobs": [], "spoolman_filaments": []}
    })
    assert resp.status_code == 409
    assert lmod._pending_sync is not None and lmod._pending_sync["sync_id"] == "correct-id"  # still parked
    assert lmod._catalog_bytes == catalog_before  # not committed


async def test_confirm_remap_missing_required_printer_resolution_returns_422(client: AsyncClient):
    """Missing required printer resolution → 422."""
    lmod._pending_sync = {
        "sync_id": "sync-1", "raw": b'{}', "catalog": {},
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Stale Machine",
                          "required": True, "options_kind": "machine",
                          "affected_printer_ids": [1], "affected_printer_names": ["P1"],
                          "affected_slots": [None]}],
            "jobs": [], "spoolman_filaments": [],
        },
        "created_at": 0,
    }
    catalog_before = lmod._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-1",
        "resolutions": {"printers": [], "jobs": [], "spoolman_filaments": []}
    })
    assert resp.status_code == 422
    assert lmod._pending_sync is not None and lmod._pending_sync["sync_id"] == "sync-1"  # operator can retry
    assert lmod._catalog_bytes == catalog_before


async def test_confirm_remap_invalid_job_resolution_returns_422(client: AsyncClient, session_factory, create_job):
    """A job resolution value not present in the new catalog is rejected, not applied blindly."""
    from sqlalchemy import select
    from app.models import JobPrinterConfig

    job_id = await create_job(print_profile="Old Process")
    async with session_factory() as s:
        config_id = (await s.execute(
            select(JobPrinterConfig.id).where(JobPrinterConfig.job_id == job_id))).scalar_one()
    lmod._pending_sync = {
        "sync_id": "sync-job",
        "raw": b"{}",
        "catalog": {"machine": [], "process": [], "filament": []},
        "pending": {
            "printers": [],
            "jobs": [{"field": "print_profile", "stale_value": "Old Process",
                      "options_kind": "process", "required": False,
                      "affected_config_ids": [config_id], "affected_file_names": [f"job#{job_id}"]}],
            "spoolman_filaments": [],
        },
        "created_at": 0,
    }
    catalog_before = lmod._catalog_bytes
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-job",
        "resolutions": {
            "printers": [],
            "jobs": [{"field": "print_profile", "stale_value": "Old Process",
                      "new_value": "Not In Catalog"}],
            "spoolman_filaments": [],
        },
    })
    assert resp.status_code == 422
    async with session_factory() as s:  # nothing was applied
        assert (await s.get(JobPrinterConfig, config_id)).print_profile == "Old Process"
    assert lmod._pending_sync is not None and lmod._pending_sync["sync_id"] == "sync-job"
    assert lmod._catalog_bytes == catalog_before


async def test_confirm_remap_malformed_resolutions_returns_422_not_500(client: AsyncClient):
    """A malformed resolutions payload is a validation error, not an unhandled 500."""
    lmod._pending_sync = {
        "sync_id": "sync-malformed",
        "raw": b"{}",
        "catalog": {},
        "pending": {"printers": [], "jobs": [], "spoolman_filaments": []},
        "created_at": 0,
    }
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-malformed",
        "resolutions": {"printers": "not-a-list", "jobs": [], "spoolman_filaments": []},
    })
    assert resp.status_code == 422
    assert lmod._pending_sync is not None and lmod._pending_sync["sync_id"] == "sync-malformed"


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

    lmod._pending_sync = {
        "sync_id": "sync-apply",
        "raw": pending_bytes,
        "catalog": new_catalog,
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Stale Machine",
                          "required": True, "options_kind": "machine",
                          "affected_printer_ids": [printer_id], "affected_printer_names": ["Test Printer"],
                          "affected_slots": [None]}],
            "jobs": [], "spoolman_filaments": [],
        },
        "created_at": 0,
    }

    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-apply",
        "resolutions": {
            "printers": [{"field": "current_orca_printer_profile",
                          "stale_value": "Stale Machine", "new_value": "New Machine"}],
            "jobs": [], "spoolman_filaments": [],
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["applied"]["printers"] == 1
    assert lmod._pending_sync is None
    assert lmod._catalog_dict == new_catalog

    # Verify DB updated
    printer_resp = await client.get(f"/api/v1/printers/{printer_id}")
    assert printer_resp.json()["current_orca_printer_profile"] == "New Machine"


async def test_confirm_remap_spoolman_only_raw_none_skips_commit_catalog(client: AsyncClient):
    """raw=None (Spoolman-only pending): clears pending, does NOT swap catalog."""
    original_catalog = lmod._catalog_dict
    lmod._pending_sync = {
        "sync_id": "spoolman-only",
        "raw": None,
        "catalog": None,
        "pending": {
            "printers": [], "jobs": [],
            "spoolman_filaments": [{
                "printer_preset": "Bambu X1C 0.4 nozzle",
                "stale_name": "Old PLA",
                "required": False,
                "affected_filament_ids": [5],
                "affected_filament_names": ["Red PLA"],
            }],
        },
        "created_at": 0,
    }

    with patch("app.services.spoolman_service.fetch_filament", new_callable=AsyncMock,
               return_value={"extra": {"orca_profiles": '"\\"{}\\""'}}), \
         patch("app.services.spoolman_service.patch_filament", new_callable=AsyncMock):
        resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
            "sync_id": "spoolman-only",
            "resolutions": {
                "printers": [], "jobs": [],
                "spoolman_filaments": [{
                    "printer_preset": "Bambu X1C 0.4 nozzle",
                    "stale_name": "Old PLA",
                    "new_name": None,
                    "affected_filament_ids": [5],
                }]
            }
        })

    assert resp.status_code == 200
    assert lmod._pending_sync is None
    assert lmod._catalog_dict is original_catalog  # NOT swapped


# ---- confirm-remap: applying each resolution type -------------------------------------------

from sqlalchemy import select

from app.models import JobPrinterConfig, Printer, SpoolmanConfig

_NEW_CATALOG = {
    "machine": [{"name": "New Machine", "uuid": "m2"}],
    "process": [{"name": "0.20mm New", "uuid": "p2"}],
    "filament": [{"name": "New PLA", "uuid": "f2"}],
}


def _park(pending: dict, *, raw: bytes | None = b'{"new": true}', catalog: dict | None = _NEW_CATALOG, sync_id="sync-x"):
    lmod._pending_sync = {"sync_id": sync_id, "raw": raw, "catalog": catalog, "pending": pending, "created_at": 0}
    return sync_id


def _pending(printers=(), jobs=(), spoolman=()):
    return {"printers": list(printers), "jobs": list(jobs), "spoolman_filaments": list(spoolman)}


async def _confirm(client, sync_id, *, printers=(), jobs=(), spoolman=()):
    return await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": sync_id,
        "resolutions": {"printers": list(printers), "jobs": list(jobs), "spoolman_filaments": list(spoolman)},
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
    assert resp.json() == {"status": "ok", "applied": {"printers": 3, "jobs": 0, "spoolman_filaments": 0},
                           "spoolman_failures": []}  # a, b machine + a slot 0; missing printer & out-of-range slot skipped
    row_a, row_b = await _printer_row(session_factory, a), await _printer_row(session_factory, b)
    assert row_a.current_orca_printer_profile == row_b.current_orca_printer_profile == "New Machine"
    assert [s["filament_profile"] for s in row_a.loaded_filaments] == ["New PLA", "Keep Me"]
    assert row_a.loaded_filaments[0]["type"] == "PLA"  # the rest of the slot survives
    assert lmod._catalog_dict == _NEW_CATALOG and lmod._catalog_bytes == b'{"new": true}'
    assert lmod._pending_sync is None


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
    assert resp.json()["applied"] == {"printers": 0, "jobs": 3, "spoolman_filaments": 0}
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
    assert lmod._pending_sync is not None and lmod._pending_sync["sync_id"] == "sync-x"  # operator can retry


async def test_confirm_remap_failure_midway_rolls_back_every_change_and_keeps_the_pending_remap(client, create_printer, session_factory):
    pid = await create_printer(current_orca_printer_profile="Old Machine")
    sync = _park(_pending(
        printers=[{"field": "current_orca_printer_profile", "stale_value": "Old Machine", "required": True,
                   "options_kind": "machine", "affected_printer_ids": [pid], "affected_printer_names": ["P"],
                   "affected_slots": [None]}],
        jobs=[{"field": "print_profile", "stale_value": "Old Process", "options_kind": "process", "required": False,
               "affected_config_ids": None, "affected_file_names": []}],   # malformed: blows up after the printer update
    ))
    catalog_before = lmod._catalog_dict

    with pytest.raises(TypeError):
        await _confirm(client, sync, printers=[
            {"field": "current_orca_printer_profile", "stale_value": "Old Machine", "new_value": "New Machine"}])

    assert (await _printer_row(session_factory, pid)).current_orca_printer_profile == "Old Machine"  # not half-applied
    assert lmod._catalog_dict is catalog_before
    assert lmod._pending_sync is not None


# ---- confirm-remap: Spoolman follow-up (best effort, after the DB commit) ----------------------

def _spoolman_entry(*ids, preset="Bambu X1C 0.4 nozzle", stale="Old PLA"):
    return {"printer_preset": preset, "stale_name": stale, "required": False,
            "affected_filament_ids": list(ids), "affected_filament_names": [f"fil{i}" for i in ids]}


def _orca_extra(profiles: dict) -> dict:
    return {"extra": {"orca_profiles": json.dumps(json.dumps(profiles))}}  # Spoolman stores it double-encoded


async def _enable_spoolman(session_factory):
    async with session_factory() as s:
        s.add(SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test", api_key="k"))
        await s.commit()


async def test_confirm_remap_patches_spoolman_filaments_and_reports_the_ones_that_failed(client, session_factory):
    await _enable_spoolman(session_factory)
    preset = "Bambu X1C 0.4 nozzle"
    stored = {
        5: {preset: ["Old PLA", "Keep A"], "Other Printer": ["Old PLA"]},
        6: {preset: ["Old PLA"]},
        7: {preset: ["Old PLA"]},
    }

    async def fake_fetch(url, key, fil_id):
        if fil_id == 7:
            raise RuntimeError("spoolman down")
        return _orca_extra(stored[fil_id])

    patched = {}
    async def fake_patch(url, key, fil_id, profiles):
        patched[fil_id] = profiles

    sync = _park(_pending(spoolman=[_spoolman_entry(5, 6, 7)]))
    with patch("app.services.spoolman_service.fetch_filament", new=fake_fetch), \
         patch("app.services.spoolman_service.patch_filament", new=fake_patch):
        resp = await _confirm(client, sync, spoolman=[
            {"printer_preset": preset, "stale_name": "Old PLA", "new_name": "New PLA"}])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"]["spoolman_filaments"] == 2
    assert body["spoolman_failures"] == ["filament 7: spoolman down"]
    # stale name swapped for the new one on this preset only; other presets and other names untouched
    assert patched[5] == {preset: ["Keep A", "New PLA"], "Other Printer": ["Old PLA"]}
    assert patched[6] == {preset: ["New PLA"]}
    assert 7 not in patched
    assert lmod._catalog_dict == _NEW_CATALOG and lmod._pending_sync is None  # a Spoolman failure never blocks the commit


async def test_confirm_remap_without_a_replacement_removes_the_stale_name_and_drops_an_emptied_preset(client, session_factory):
    await _enable_spoolman(session_factory)
    preset = "Bambu X1C 0.4 nozzle"
    patched = {}

    async def fake_patch(url, key, fil_id, profiles):
        patched[fil_id] = profiles

    sync = _park(_pending(spoolman=[_spoolman_entry(5)]))
    with patch("app.services.spoolman_service.fetch_filament", new=AsyncMock(return_value=_orca_extra({preset: ["Old PLA"]}))), \
         patch("app.services.spoolman_service.patch_filament", new=fake_patch):
        resp = await _confirm(client, sync, spoolman=[{"printer_preset": preset, "stale_name": "Old PLA", "new_name": None}])

    assert resp.status_code == 200 and resp.json()["applied"]["spoolman_filaments"] == 1
    assert patched == {5: {}}  # the preset key is removed rather than left as an empty list


async def test_confirm_remap_skips_spoolman_entirely_when_it_is_not_configured(client):
    sync = _park(_pending(spoolman=[_spoolman_entry(5)]))
    with patch("app.services.spoolman_service.fetch_filament", new=AsyncMock()) as fetch:
        resp = await _confirm(client, sync)

    assert resp.status_code == 200
    assert resp.json()["applied"]["spoolman_filaments"] == 0
    fetch.assert_not_called()
    assert lmod._pending_sync is None


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

