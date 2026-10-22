"""Bambu LAN file access: assumptions behind `BambuMQTTClient.list_files/download_file/delete_file` and
`tests/virtual_printers/fake_bambu_ftps.py`."""
import io
import zipfile

import pytest

from app.plugins.bambu.client import BambuMQTTClient, parse_unix_list_line


@pytest.fixture
def client(bambu_cfg) -> BambuMQTTClient:
    c = BambuMQTTClient.__new__(BambuMQTTClient)
    c._ip, c._access_code, c._serial_number = bambu_cfg["host"], bambu_cfg["access_code"], "unused"
    return c


def _raw_list(client, path: str = "") -> list[str]:
    lines: list[str] = []
    with client._ftps() as ftp:
        ftp.retrlines(f"LIST {path}".strip(), lines.append)
    return lines


def test_implicit_ftps_login_with_documented_credentials(client):
    with client._ftps() as ftp:            # connect :990, login bblp/<access code>, PROT P — raises on any deviation
        print("FTPS welcome:", ftp.getwelcome())


def test_list_output_is_ls_l_format_for_every_entry(client):
    lines = _raw_list(client)
    print("RAW LIST (/):", *lines, sep="\n  ")
    unparsed = [l for l in lines if parse_unix_list_line(l) is None and not l.startswith("total")]
    assert not unparsed, f"lines our parser can't read (update parse_unix_list_line + the virtual printer): {unparsed}"


def test_listed_sizes_match_the_SIZE_command(client):
    files = client.list_files()
    print("printable files:", [(f.id, f.size) for f in files])
    if not files:
        pytest.skip("no printable files on the printer to compare")
    with client._ftps() as ftp:
        ftp.voidcmd("TYPE I")
        for f in files[:5]:
            assert ftp.size(f.id) == f.size, f"listed size of {f.id} disagrees with SIZE"


def test_cache_directory_behaviour_is_recorded(client):
    import ftplib
    try:
        lines = _raw_list(client, "/cache")
        print("/cache exists, entries:", *lines, sep="\n  ")
    except ftplib.error_perm as e:               # the ONLY failure list_files tolerates for /cache: a 550
        assert str(e).startswith("550"), f"a missing /cache must answer 550 (client relies on it), got {e}"
        print("no /cache on this printer:", e)


def test_download_of_a_listed_file_matches_its_size_and_is_a_zip(client):
    files = sorted(client.list_files(), key=lambda f: f.size)
    candidates = [f for f in files if f.name.lower().endswith(".3mf")]
    if not candidates:
        pytest.skip("no .3mf on the printer")
    f = candidates[0]
    data = client.download_file(f.id)
    assert data is not None and len(data) == f.size
    assert zipfile.is_zipfile(io.BytesIO(data)), "a .3mf should be a zip archive"


def test_write_round_trip_upload_list_delete(client, require_write, sacrificial_name):
    from protocol_verification.conftest import SACRIFICIAL_BODY
    assert client.upload_file(SACRIFICIAL_BODY, sacrificial_name)
    try:
        listed = {f.id: f for f in client.list_files()}
        assert sacrificial_name in listed and listed[sacrificial_name].size == len(SACRIFICIAL_BODY)
        assert client.download_file(sacrificial_name) == SACRIFICIAL_BODY
    finally:
        assert client.delete_file(sacrificial_name)
    assert sacrificial_name not in {f.id for f in client.list_files()}
