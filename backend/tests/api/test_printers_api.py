import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from app.models import Printer
from app.services.abstract_printer_client import PrinterCapabilities


async def test_get_printer_types(client):
    response = await client.get("/api/v1/printers/types")
    assert response.status_code == 200
    types = response.json()
    assert isinstance(types, list)
    printer_type_names = [t["printer_type"] for t in types]
    assert "bambu" in printer_type_names
    assert "elegoo_centauri" in printer_type_names


async def test_list_printers_empty(client):
    response = await client.get("/api/v1/printers")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_printer(client):
    payload = {
        "name": "X1 Carbon",
        "printer_type": "bambu",
        "connection_config": {"ip_address": "192.168.1.10", "serial_number": "ABC", "access_code": "secret"},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    }
    response = await client.post("/api/v1/printers", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "X1 Carbon"
    assert data["printer_type"] == "bambu"
    assert data["id"] is not None


async def test_get_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {"ip_address": "1.2.3.4", "serial_number": "X", "access_code": "Y"},
        "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.get(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 200
    assert response.json()["id"] == printer_id


async def test_get_printer_not_found(client):
    response = await client.get("/api/v1/printers/9999")
    assert response.status_code == 404


async def test_update_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "Old Name", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.patch(f"/api/v1/printers/{printer_id}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


async def test_patch_can_clear_and_omit_current_preset(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P", "printer_type": "bambu", "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    pid = create.json()["id"]
    # Explicit null clears the preset.
    r = await client.patch(f"/api/v1/printers/{pid}", json={"current_orca_printer_profile": None})
    assert r.status_code == 200
    assert r.json()["current_orca_printer_profile"] is None
    # Set it again, then a PATCH that omits the key leaves it unchanged.
    await client.patch(f"/api/v1/printers/{pid}", json={"current_orca_printer_profile": "Bambu Lab P1S 0.4"})
    r = await client.patch(f"/api/v1/printers/{pid}", json={"name": "P2"})
    assert r.json()["current_orca_printer_profile"] == "Bambu Lab P1S 0.4"


async def test_delete_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "Temp", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.delete(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 204
    response = await client.get(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 404


def _make_3mf() -> bytes:
    import io, zipfile, json
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _upload_file(client, tmp_path):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / "filecache").mkdir(exist_ok=True)
        resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("m.3mf", _make_3mf(), "application/octet-stream")},
        )
    return resp.json()["id"]


async def _create_job(client, tmp_path, printer_id):
    """Create a job whose only printer config points at printer_id."""
    file_id = await _upload_file(client, tmp_path)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_type": "any", "filament_color": "any"}],
        })
    return create.json()["id"]


async def test_delete_printer_refuses_with_active_job(client, tmp_path):
    """A printer physically running a job must not be deletable — removing the DB
    row can't stop the machine, and it would strand the job unresolved."""
    from app.main import app
    from app.database import get_session
    from app.models import Job

    create = await client.post("/api/v1/printers", json={
        "name": "Busy", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    job_id = await _create_job(client, tmp_path, printer_id)

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, job_id)
    job.status = "printing"
    job.assigned_printer_id = printer_id
    await session.commit()
    await agen.aclose()

    response = await client.delete(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 409

    response = await client.get(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 200


async def test_delete_printer_disconnects_live_client(client):
    """The MQTT/WebSocket client must be torn down, not left reconnecting forever."""
    from app.services.printer_manager import printer_manager

    create = await client.post("/api/v1/printers", json={
        "name": "Live", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]

    mock_client = MagicMock()
    printer_manager._clients[printer_id] = mock_client
    try:
        response = await client.delete(f"/api/v1/printers/{printer_id}")
        assert response.status_code == 204
        mock_client.disconnect.assert_called_once()
        assert printer_id not in printer_manager.get_all_printer_ids()
    finally:
        printer_manager._clients.pop(printer_id, None)


async def test_delete_printer_unlinks_gcode_from_disk(client, tmp_path):
    """Deleting a printer's GcodeFile rows must also remove the files they point at."""
    from app.main import app
    from app.database import get_session
    from app.models import GcodeFile

    create = await client.post("/api/v1/printers", json={
        "name": "P", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    job_id = await _create_job(client, tmp_path, printer_id)

    gcode_path = tmp_path / "out.gcode"
    gcode_path.write_text("G28")

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path=str(gcode_path)))
    await session.commit()
    await agen.aclose()

    response = await client.delete(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 204
    assert not gcode_path.exists()


async def test_delete_printer_blocks_job_left_with_no_config(client, tmp_path):
    """A job whose only config pointed at the deleted printer must become visibly
    'blocked' rather than sitting in the queue unclaimable and invisible."""
    from app.main import app
    from app.database import get_session
    from app.models import Job

    create = await client.post("/api/v1/printers", json={
        "name": "OnlyOne", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    job_id = await _create_job(client, tmp_path, printer_id)

    response = await client.delete(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 204

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, job_id)
    assert job.status == "blocked"
    assert job.block_reason
    await agen.aclose()


async def test_switch_active_preset(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4", "Bambu Lab P1S 0.2"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    printer_id = create.json()["id"]
    response = await client.patch(
        f"/api/v1/printers/{printer_id}/active-preset",
        json={"preset": "Bambu Lab P1S 0.2"},
    )
    assert response.status_code == 200
    assert response.json()["current_orca_printer_profile"] == "Bambu Lab P1S 0.2"


async def test_switch_active_preset_invalid(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    printer_id = create.json()["id"]
    response = await client.patch(
        f"/api/v1/printers/{printer_id}/active-preset",
        json={"preset": "Not A Real Preset"},
    )
    assert response.status_code == 422


import pytest
from unittest.mock import MagicMock
from httpx import AsyncClient


@pytest.mark.asyncio
class _FakeConnClient:
    """Minimal client for exercising the test-connection route logic."""
    def __init__(self, connects, endpoint=None):
        self._connects = connects
        self._endpoint = endpoint
        self.connected = False

    def connect(self, loop=None):
        if self._connects:
            self.connected = True

    def control_endpoint(self):
        return self._endpoint

    def disconnect(self, *a, **k):
        pass


async def test_test_connection_success(client, monkeypatch):
    import app.api.routes.printers as pr
    monkeypatch.setattr(pr, "_TEST_CONNECT_POLL_S", 0.5)
    monkeypatch.setattr(pr, "create_client_from_config", lambda *a, **k: _FakeConnClient(connects=True))
    r = await client.post("/api/v1/printers/test-connection",
                          json={"printer_type": "bambu", "connection_config": {}})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_test_connection_unreachable_gives_hint(client, monkeypatch):
    import app.api.routes.printers as pr
    monkeypatch.setattr(pr, "_TEST_CONNECT_POLL_S", 0.3)
    # 127.0.0.1:59999 is closed → the probe classifies it as unreachable.
    monkeypatch.setattr(pr, "create_client_from_config",
                        lambda *a, **k: _FakeConnClient(connects=False, endpoint=("127.0.0.1", 59999)))
    r = await client.post("/api/v1/printers/test-connection",
                          json={"printer_type": "bambu", "connection_config": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "Couldn't reach" in body["error"]


async def test_test_connection_unknown_type_is_rejected(client):
    resp = await client.post(
        "/api/v1/printers/test-connection",
        json={"printer_type": "not_a_real_type", "connection_config": {}},
    )
    assert resp.status_code == 422


def _bambu(name: str, **extra) -> dict:
    return {
        "name": name,
        "printer_type": "bambu",
        "connection_config": {"ip_address": "192.168.1.10", "access_code": "12345678", "serial_number": name},
        **extra,
    }


async def test_list_printers_reports_disconnected_when_no_live_client(client):
    created = await client.post("/api/v1/printers", json=_bambu("SN001"))
    assert created.status_code == 201

    printers = (await client.get("/api/v1/printers")).json()
    assert [p["id"] for p in printers] == [created.json()["id"]]
    assert printers[0]["connected"] is False


async def test_loaded_filaments_default_to_empty_list(client):
    resp = await client.post("/api/v1/printers", json=_bambu("SN1"))
    assert resp.status_code == 201
    assert resp.json()["loaded_filaments"] == []


async def test_loaded_filaments_round_trip_on_create_including_null_filament_id(client):
    slots = [
        {"slot": 0, "filament_id": None, "name": "Bambu PLA Matte", "type": "PLA", "color": "#ff0000"},
        {"slot": 1, "filament_id": "GFA00", "name": "Generic PLA", "type": "PLA", "color": "#cccccc"},
    ]
    created = await client.post("/api/v1/printers", json=_bambu("SN2", loaded_filaments=slots))
    assert created.status_code == 201
    assert created.json()["loaded_filaments"] == slots

    fetched = (await client.get(f"/api/v1/printers/{created.json()['id']}")).json()
    assert fetched["loaded_filaments"] == slots
    assert fetched["loaded_filaments"][0]["filament_id"] is None


async def test_patch_loaded_filaments_replaces_and_persists(client):
    created = await client.post("/api/v1/printers", json=_bambu("SN3"))
    printer_id = created.json()["id"]
    slots = [{"slot": 0, "filament_id": None, "name": "Bambu PETG HF", "type": "PETG", "color": "#00aaff"}]

    resp = await client.patch(f"/api/v1/printers/{printer_id}", json={"loaded_filaments": slots})
    assert resp.status_code == 200
    assert resp.json()["loaded_filaments"] == slots
    assert (await client.get(f"/api/v1/printers/{printer_id}")).json()["loaded_filaments"] == slots
