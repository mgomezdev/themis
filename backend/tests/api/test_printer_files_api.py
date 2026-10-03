"""Printer file browser API (BIZ-169): capability gating, merged view, print/delete/to-library actions, and the
direct-start gate shared with upload-and-print. Clients are mocks here; the real FTPS/Moonraker client code runs
against virtual printers in tests/services/test_printer_file_clients.py."""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from app.models import Job, UploadedFile
from app.services.abstract_printer_client import PrinterCapabilities, PrinterFile
from app.services.printer_manager import printer_manager
from tests.conftest import make_3mf_bytes


@pytest_asyncio.fixture
async def pid(create_printer) -> int:
    return await create_printer(name="Atlas", printer_type="elegoo_centauri", connection_config={"ip_address": "192.0.2.1"})


def _client(**caps):
    c = MagicMock()
    c.connected = True
    c.is_idle = True
    c.is_printing = False
    c.get_capabilities.return_value = PrinterCapabilities(**caps)
    c.list_files.return_value = [
        PrinterFile(id="sub", name="sub", size=0, is_dir=True),
        PrinterFile(id="b.gcode", name="b.gcode", size=20, modified_at="2025-10-01T00:00:00+00:00",
                    metadata={"estimated_seconds": 60}),
        PrinterFile(id="A.3mf", name="A.3mf", size=10),
        PrinterFile(id="readme.txt", name="readme.txt", size=1),
    ]
    c.delete_file.return_value = True
    c.start_print.return_value = True
    c.download_file.return_value = make_3mf_bytes()
    return c


FULL = dict(file_browser=True, file_delete=True, file_download=True)


async def test_listing_returns_dirs_first_then_files_with_metadata_and_a_printable_flag(client, pid):
    printer_manager._clients[pid] = mock = _client(**FULL)

    body = (await client.get(f"/api/v1/printers/{pid}/files")).json()

    assert body["printer_id"] == pid and body["directory"] == "/"
    assert (body["can_delete"], body["can_download"]) == (True, True)
    assert [f["id"] for f in body["files"]] == ["sub", "A.3mf", "b.gcode", "readme.txt"]       # dirs first, then by name
    by_id = {f["id"]: f for f in body["files"]}
    assert by_id["b.gcode"]["metadata"] == {"estimated_seconds": 60} and by_id["b.gcode"]["size"] == 20
    assert [by_id[i]["printable"] for i in ("sub", "A.3mf", "b.gcode", "readme.txt")] == [False, True, True, False]
    mock.list_files.assert_called_once_with("/")

    await client.get(f"/api/v1/printers/{pid}/files", params={"directory": "sub"})
    mock.list_files.assert_called_with("sub")


async def test_listing_needs_the_capability_and_a_connected_printer(client, pid):
    assert (await client.get(f"/api/v1/printers/{pid}/files")).status_code == 503
    printer_manager._clients[pid] = _client()
    assert (await client.get(f"/api/v1/printers/{pid}/files")).status_code == 409
    assert (await client.get("/api/v1/printers/999/files")).status_code == 404


async def test_a_listing_the_printer_refuses_is_a_502_with_the_reason_not_an_empty_list(client, pid):
    mock = _client(**FULL)
    mock.list_files.side_effect = Exception("530 Login incorrect.")
    printer_manager._clients[pid] = mock
    resp = await client.get(f"/api/v1/printers/{pid}/files")
    assert resp.status_code == 502 and "530 Login incorrect" in resp.json()["detail"]
    mock.list_files.side_effect = ValueError("Invalid directory: '..'")
    assert (await client.get(f"/api/v1/printers/{pid}/files", params={"directory": ".."})).status_code == 422


async def test_a_listing_that_times_out_is_a_502_not_a_hang(client, pid):
    mock = _client(**FULL)
    mock.list_files.side_effect = lambda d: __import__("time").sleep(0.5)
    printer_manager._clients[pid] = mock
    with patch("app.api.routes.printers._LIST_TIMEOUT_S", 0.05):
        assert (await client.get(f"/api/v1/printers/{pid}/files")).status_code == 502


async def test_merged_view_lists_each_capable_printer_and_isolates_failures(client, create_printer):
    ok = await create_printer(name="A-ok")
    off = await create_printer(name="B-off")
    boom = await create_printer(name="C-boom")
    unsupported = await create_printer(name="D-nofiles")
    printer_manager._clients[ok] = _client(**FULL)
    offline = _client(**FULL)
    offline.connected = False
    printer_manager._clients[off] = offline
    broken = _client(**FULL)
    broken.list_files.side_effect = RuntimeError("ftp exploded")
    printer_manager._clients[boom] = broken
    printer_manager._clients[unsupported] = _client()

    body = (await client.get("/api/v1/printers/files/all")).json()

    by_name = {p["printer_name"]: p for p in body["printers"]}
    assert set(by_name) == {"A-ok", "B-off", "C-boom"}                       # unsupported printers are omitted
    assert [f["id"] for f in by_name["A-ok"]["files"]] == ["sub", "A.3mf", "b.gcode", "readme.txt"] and by_name["A-ok"]["error"] is None
    assert by_name["B-off"]["error"] == "Printer not connected" and by_name["B-off"]["files"] == []
    assert (by_name["A-ok"]["can_delete"], by_name["A-ok"]["can_download"]) == (True, True)
    assert (by_name["B-off"]["can_delete"], by_name["B-off"]["can_download"]) == (False, False)
    assert "ftp exploded" in by_name["C-boom"]["error"]


async def test_merged_view_with_no_printers_is_empty(client):
    assert (await client.get("/api/v1/printers/files/all")).json() == {"printers": []}


# ── print ────────────────────────────────────────────────────────────────────

def _print(client, pid, file_id="b.gcode"):
    return client.post(f"/api/v1/printers/{pid}/files/print", json={"file_id": file_id})


async def test_print_starts_the_stored_file_and_marks_the_plate_not_ready(client, pid):
    printer_manager._clients[pid] = mock = _client(**FULL)

    resp = await _print(client, pid, "sub/b.gcode")

    assert resp.status_code == 200 and resp.json() == {"ok": True, "file_id": "sub/b.gcode", "started": True}
    mock.start_print.assert_called_once()
    assert mock.start_print.call_args.args[0] == "sub/b.gcode"
    assert printer_manager.is_awaiting_plate_clear(pid)
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["awaiting_plate_clear"] is True


async def test_print_is_gated_like_upload_and_print(client, pid, session_factory):
    mock = _client(**FULL)
    printer_manager._clients[pid] = mock

    mock.is_idle = False
    assert (await _print(client, pid)).status_code == 409
    mock.is_idle = True

    printer_manager.set_awaiting_plate_clear(pid, True)
    resp = await _print(client, pid)
    assert resp.status_code == 409 and "plate" in resp.json()["detail"]
    printer_manager.set_awaiting_plate_clear(pid, False)

    async with session_factory() as s:
        f = UploadedFile(original_filename="a.3mf", stored_path="/x", plates=[], uploaded_at="t")
        s.add(f)
        await s.flush()
        s.add(Job(uploaded_file_id=f.id, plate_number=1, status="slicing", assigned_printer_id=pid,
                  queue_position=1.0, created_at="t", updated_at="t"))
        await s.commit()
    assert (await _print(client, pid)).status_code == 409
    mock.start_print.assert_not_called()


async def test_print_claims_the_printer_first_and_releases_it_if_the_printer_refuses(client, pid):
    mock = _client(**FULL)
    seen = {}

    def start(name, opts):
        seen["gate_during_start"] = printer_manager.is_awaiting_plate_clear(pid)
        return False
    mock.start_print.side_effect = start
    printer_manager._clients[pid] = mock

    assert (await _print(client, pid)).status_code == 502
    assert seen["gate_during_start"] is True
    assert not printer_manager.is_awaiting_plate_clear(pid)
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["awaiting_plate_clear"] is False


async def test_print_rejects_non_printable_names_and_needs_the_capability(client, pid):
    printer_manager._clients[pid] = mock = _client(**FULL)
    assert (await _print(client, pid, "readme.txt")).status_code == 422
    assert (await client.post(f"/api/v1/printers/{pid}/files/print", json={"file_id": ""})).status_code == 422
    mock.start_print.assert_not_called()
    printer_manager._clients[pid] = _client()
    assert (await _print(client, pid)).status_code == 409


# ── delete ───────────────────────────────────────────────────────────────────

def _delete(client, pid, file_id="b.gcode"):
    return client.delete(f"/api/v1/printers/{pid}/files", params={"file_id": file_id})


async def test_delete_removes_the_file_and_reports_a_refusal_as_502(client, pid):
    printer_manager._clients[pid] = mock = _client(**FULL)
    assert (await _delete(client, pid, "sub/b.gcode")).json() == {"ok": True}
    mock.delete_file.assert_called_once_with("sub/b.gcode")
    mock.delete_file.return_value = False
    assert (await _delete(client, pid)).status_code == 502


@pytest.mark.parametrize("reported,file_id,blocked", [
    ("/local/b.gcode", "b.gcode", True),                  # Elegoo: absolute path
    ("Benchy", "Benchy.gcode.3mf", True),                 # Bambu: subtask_name without the extension
    ("benchy.GCODE.3MF", "cache/Benchy.gcode.3mf", True), # case and directory don't matter
    ("Benchy", "Other.gcode.3mf", False),
    ("", "anything.gcode", True),                         # printer busy but name unknown → refuse rather than guess
])
async def test_delete_refuses_the_file_being_printed_but_not_others(client, pid, reported, file_id, blocked):
    mock = _client(**FULL)
    mock.is_idle = False                                  # covers the start-up window, not just RUNNING
    mock.is_printing = False
    printer_manager._clients[pid] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={"current_print": reported}):
        resp = await _delete(client, pid, file_id)
    assert (resp.status_code == 409) is blocked
    assert mock.delete_file.called is (not blocked)


async def test_delete_is_unrestricted_on_an_idle_printer(client, pid):
    printer_manager._clients[pid] = mock = _client(**FULL)
    assert (await _delete(client, pid, "whatever.gcode")).status_code == 200
    mock.delete_file.assert_called_once()


async def test_delete_needs_the_capability(client, pid):
    printer_manager._clients[pid] = mock = _client(file_browser=True)
    assert (await _delete(client, pid)).status_code == 409
    mock.delete_file.assert_not_called()


# ── to-library ───────────────────────────────────────────────────────────────

@pytest.fixture
def library(tmp_path):
    (tmp_path / "library").mkdir()
    (tmp_path / "filecache").mkdir()
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        yield tmp_path / "library"


def _to_library(client, pid, file_id="A.3mf"):
    return client.post(f"/api/v1/printers/{pid}/files/to-library", json={"file_id": file_id})


async def test_to_library_downloads_into_the_library_and_dedupes(client, pid, library):
    printer_manager._clients[pid] = mock = _client(**FULL)

    first = await _to_library(client, pid, "sub/A.3mf")

    assert first.status_code == 200, first.text
    rec = first.json()
    assert rec["original_filename"] == "A.3mf" and rec["folder"] == "/From Printers"
    assert (library / "From Printers" / "A.3mf").read_bytes() == make_3mf_bytes()
    mock.download_file.assert_called_once_with("sub/A.3mf", 300 * 1024 * 1024)
    again = (await _to_library(client, pid, "sub/A.3mf")).json()
    assert again["id"] == rec["id"]                                           # same content → same record
    assert [f["id"] for f in (await client.get("/api/v1/files")).json()] == [rec["id"]]


async def test_to_library_refuses_gcode_and_reports_download_failures(client, pid, library):
    printer_manager._clients[pid] = mock = _client(**FULL)
    assert (await _to_library(client, pid, "b.gcode")).status_code == 422
    assert (await _to_library(client, pid, "b.gcode.3mf")).status_code == 422   # sliced archive: not a model (BIZ-196)
    mock.download_file.assert_not_called()
    mock.download_file.return_value = None
    assert (await _to_library(client, pid)).status_code == 502
    printer_manager._clients[pid] = _client(file_browser=True, file_delete=True)
    assert (await _to_library(client, pid)).status_code == 409                 # no file_download capability


async def test_to_library_passes_the_cap_to_the_download_and_maps_an_abort_to_413(client, pid, library):
    from app.services.abstract_printer_client import FileTooLargeError
    mock = _client(**FULL)
    mock.download_file.side_effect = FileTooLargeError("too big")
    printer_manager._clients[pid] = mock
    with patch("app.api.routes.printers._MAX_DIRECT_UPLOAD_BYTES", 10):
        assert (await _to_library(client, pid)).status_code == 413
    assert mock.download_file.call_args.args[1] == 10                          # the cap reached the client
    assert not (library / "From Printers").exists()


async def test_to_library_runs_the_thumbnail_background_task(client, pid, library):
    printer_manager._clients[pid] = _client(**FULL)
    with patch("app.api.routes.files.regen_file_thumbnails") as regen:
        assert (await _to_library(client, pid)).status_code == 200
    regen.assert_called_once()                                                  # actually executed, not just queued


async def test_concurrent_direct_starts_on_one_printer_start_exactly_one_print(client, pid):
    mock = _client(**FULL)
    import time

    def start(name, opts):
        time.sleep(0.05)                       # widen the window between the checks and the claim
        return True
    mock.start_print.side_effect = start
    printer_manager._clients[pid] = mock

    from app.api.routes import printers as routes
    real_gate = routes._set_plate_gate

    async def slow_gate(*a, **kw):
        await asyncio.sleep(0.05)              # latency between "checks passed" and "printer claimed"
        return await real_gate(*a, **kw)

    with patch.object(routes, "_set_plate_gate", slow_gate):
        r1, r2 = await asyncio.gather(_print(client, pid, "a.gcode"), _print(client, pid, "b.gcode"))

    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    assert mock.start_print.call_count == 1
