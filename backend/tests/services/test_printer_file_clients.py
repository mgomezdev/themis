"""File browsing against virtual printers: the real Bambu (FTPS) and Snapmaker (Moonraker) client code, run over
in-memory stand-ins for the documented protocols. Whether real firmware agrees is `backend/protocol_verification`."""
import pytest

from app.services.bambu_mqtt import parse_unix_list_line
from app.services.abstract_printer_client import AbstractPrinterClient, PrinterCapabilities
from tests.virtual_printers import fake_bambu_ftps as bambu_fake
from tests.virtual_printers import fake_moonraker as moon_fake


# ── LIST parsing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("-rw-r--r--    1 0        0         1234567 Oct 01  2025 My Part.gcode.3mf", ("My Part.gcode.3mf", 1234567, False)),
    ("drwxr-xr-x    2 0        0            4096 Sep 14 2025 cache", ("cache", 4096, True)),
    ("-rw-r--r-- 1 ftp ftp 10 Jan 02 03:04 a.3mf", ("a.3mf", 10, False)),
    ("lrwxrwxrwx 1 0 0 7 Jan 02  2025 link -> target", ("link", 7, False)),
])
def test_parse_unix_list_line(line, expected):
    name, size, is_dir, _modified = parse_unix_list_line(line)
    assert (name, size, is_dir) == expected


@pytest.mark.parametrize("line", ["", "total 12", "226 Directory send OK.", "-rw-r--r-- 1 0 0 notasize Oct 01 2025 x"])
def test_parse_unix_list_line_rejects_non_entries(line):
    assert parse_unix_list_line(line) is None


def test_list_line_dates():
    assert parse_unix_list_line("-rw-r--r-- 1 0 0 1 Oct 01  2025 x")[3] == "2025-10-01T00:00:00+00:00"
    from datetime import datetime, timezone
    recent = parse_unix_list_line("-rw-r--r-- 1 0 0 1 Jan 01 00:01 x")[3]
    assert datetime.fromisoformat(recent) <= datetime.now(timezone.utc)      # a time-only date is never in the future
    assert parse_unix_list_line("-rw-r--r-- 1 0 0 1 Xyz 01 2025 x")[3] is None


# ── Bambu over virtual FTPS ──────────────────────────────────────────────────

@pytest.fixture
def bambu(monkeypatch):
    storage = bambu_fake.VirtualBambuStorage({
        "Benchy.gcode.3mf": b"AAAA",
        "plate 2.3mf": b"BB",
        "notes.txt": b"x",
        "cache/Cached.gcode.3mf": b"CCCCCC",
    })
    bambu_fake.install(monkeypatch, storage)
    return bambu_fake.make_client(), storage


def test_bambu_lists_printable_files_from_root_and_cache(bambu):
    client, storage = bambu

    files = {f.id: f for f in client.list_files()}

    assert sorted(files) == ["Benchy.gcode.3mf", "cache/Cached.gcode.3mf", "plate 2.3mf"]   # .txt and dirs filtered
    assert (files["cache/Cached.gcode.3mf"].name, files["cache/Cached.gcode.3mf"].size) == ("Cached.gcode.3mf", 6)
    assert files["plate 2.3mf"].modified_at is not None
    assert storage.logins == [("bblp", bambu_fake.ACCESS_CODE)]                              # documented credentials
    assert "PROT P" in storage.commands                                                       # data channel encrypted
    assert "LIST" in storage.commands and "LIST /cache" in storage.commands


def test_bambu_tolerates_a_firmware_without_a_cache_dir(monkeypatch):
    storage = bambu_fake.VirtualBambuStorage({"a.3mf": b"1"})
    bambu_fake.install(monkeypatch, storage)
    assert [f.id for f in bambu_fake.make_client().list_files()] == ["a.3mf"]


def test_bambu_wrong_access_code_lists_nothing_instead_of_raising(bambu):
    _client, storage = bambu
    assert bambu_fake.make_client(access_code="00000000").list_files() == []


def test_bambu_download_and_delete_round_trip(bambu):
    client, storage = bambu
    assert client.download_file("cache/Cached.gcode.3mf") == b"CCCCCC"
    assert client.delete_file("Benchy.gcode.3mf") is True
    assert "Benchy.gcode.3mf" not in storage.files
    assert "DELE Benchy.gcode.3mf" in storage.commands
    assert client.delete_file("Benchy.gcode.3mf") is False          # already gone → the printer's 550 is a False
    assert client.download_file("missing.3mf") is None


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs.3mf", "a\r\nDELE b.3mf", "x/../../y"])
def test_bambu_refuses_hostile_file_ids_before_touching_the_printer(bambu, bad):
    client, storage = bambu
    storage.commands.clear()
    assert client.delete_file(bad) is False
    assert client.download_file(bad) is None
    assert storage.commands == []                                    # never even connected


# ── Moonraker (Snapmaker) over a virtual file_manager ────────────────────────

@pytest.fixture
def moon(monkeypatch):
    server = moon_fake.VirtualMoonraker({
        "part.gcode": {"data": b"G28\n", "modified": 1_759_000_000.0,
                       "meta": {"estimated_time": 3600, "filament_total": 1200.5, "filament_weight_total": 3.5,
                                "slicer": "OrcaSlicer"}},
        "my part.gcode": {"data": b"G1\n", "modified": 1_759_000_100.0},
        "sub/inner.gcode": {"data": b"G0\n", "modified": 1_759_000_200.0, "meta": {"estimated_time": 60}},
    })
    moon_fake.install(monkeypatch, server)
    return moon_fake.make_client(), server


def test_moonraker_lists_a_directory_with_inline_metadata(moon):
    client, server = moon

    files = client.list_files("/")

    by_id = {f.id: f for f in files}
    assert set(by_id) == {"sub", "part.gcode", "my part.gcode"}
    assert by_id["sub"].is_dir and not by_id["part.gcode"].is_dir
    assert by_id["part.gcode"].metadata == {"estimated_seconds": 3600, "filament_mm": 1200.5,
                                            "filament_grams": 3.5, "slicer": "OrcaSlicer"}
    assert by_id["my part.gcode"].metadata is None                      # no metadata → None, not {}
    assert by_id["part.gcode"].modified_at.startswith("2025-")
    assert by_id["part.gcode"].size == 4
    assert ("GET", "/server/files/directory") in server.requests


def test_moonraker_subdirectory_ids_are_paths_relative_to_gcodes(moon):
    client, _ = moon
    (inner,) = client.list_files("sub")
    assert (inner.id, inner.name, inner.metadata) == ("sub/inner.gcode", "inner.gcode", {"estimated_seconds": 60})
    assert client.list_files("/nope") == []                              # 404 → empty, not an exception


def test_moonraker_download_delete_quote_paths_and_report_failures(moon):
    client, server = moon
    assert client.download_file("my part.gcode") == b"G1\n"             # spaces survive URL quoting
    assert client.delete_file("sub/inner.gcode") is True
    assert "sub/inner.gcode" not in server.files
    assert ("DELETE", "/server/files/gcodes/sub/inner.gcode") in server.requests
    assert client.delete_file("sub/inner.gcode") is False                # gone → 404 → False
    assert client.download_file("nope.gcode") is None


@pytest.mark.parametrize("bad", ["../secrets.cfg", "a/../../b", "", "/"])
def test_moonraker_refuses_traversal_without_a_request(moon, bad):
    client, server = moon
    server.requests.clear()
    assert client.delete_file(bad) is False and client.download_file(bad) is None
    assert server.requests == []


def test_moonraker_sends_the_api_key(monkeypatch):
    server = moon_fake.VirtualMoonraker({"a.gcode": {"data": b"x", "modified": 1.0}}, api_key="sekret")
    moon_fake.install(monkeypatch, server)
    assert [f.id for f in moon_fake.make_client("sekret").list_files()] == ["a.gcode"]
    assert moon_fake.make_client(None).list_files() == []                # 401 → empty


# ── capability claims ────────────────────────────────────────────────────────

def test_file_capabilities_per_vendor_match_the_implemented_operations():
    from app.services.bambu_mqtt import BambuMQTTClient
    from app.services.elegoo_centauri_client import ElegooCentauriClient
    from app.services.snapmaker_client import SnapmakerExtendedClient

    def caps(cls) -> PrinterCapabilities:
        return cls.__new__(cls).get_capabilities()

    for cls in (BambuMQTTClient, SnapmakerExtendedClient):
        c = caps(cls)
        assert (c.file_browser, c.file_delete, c.file_download) == (True, True, True)
        # each claimed operation is really overridden, not the ABC's no-op
        assert cls.list_files is not AbstractPrinterClient.list_files
        assert cls.delete_file is not AbstractPrinterClient.delete_file
        assert cls.download_file is not AbstractPrinterClient.download_file
    e = caps(ElegooCentauriClient)
    assert (e.file_browser, e.file_delete, e.file_download) == (True, True, False)
    assert ElegooCentauriClient.download_file is AbstractPrinterClient.download_file
