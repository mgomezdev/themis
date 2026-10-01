"""Elegoo SDCP file listing: assumptions behind `ElegooCentauriClient.list_files/delete_file/start_print`."""
import time

import pytest

from app.services.elegoo_centauri_client import ElegooCentauriClient, _Cmd


@pytest.fixture(scope="module")
def client(elegoo_cfg):
    c = ElegooCentauriClient(ip_address=elegoo_cfg["host"], port=elegoo_cfg["port"])
    c.connect()
    deadline = time.time() + 15
    while not c.connected and time.time() < deadline:
        time.sleep(0.25)
    if not c.connected:
        pytest.fail("could not connect to the printer's SDCP websocket")
    time.sleep(1.5)                      # let the first status frame deliver the mainboard id
    yield c
    c.disconnect()


def test_get_file_list_response_shape(client):
    ok, resp = client._send_with_response(_Cmd.GET_FILE_LIST, {"Url": "/local/"})
    print("raw GET_FILE_LIST response:", resp)
    assert ok, "printer did not ack GET_FILE_LIST"
    entries = resp.get("FileList")
    assert isinstance(entries, list)
    for e in entries:
        assert "name" in e, f"entry without 'name': {e}"
        assert isinstance(e.get("size", 0), (int, float))
    kinds = {e.get("type") for e in entries}
    print("distinct entry 'type' values (how are directories marked?):", kinds)


def test_client_listing_agrees_with_the_raw_response(client):
    _ok, resp = client._send_with_response(_Cmd.GET_FILE_LIST, {"Url": "/local/"})
    listed = client.list_files("/")
    assert [f.id for f in listed] == [e["name"] for e in resp.get("FileList", [])]
    for f in listed:
        print("id:", f.id, "| display name:", f.name)
        assert f.name and "/" not in f.name


def test_listed_ids_are_what_start_print_expects(client):
    """start_print prefixes bare names with /local/ and passes absolute paths through; confirm listing ids are one
    of those two forms so a listed id can be handed straight back."""
    for f in client.list_files("/"):
        assert f.id.startswith("/") or "/" not in f.id, f"unexpected id form: {f.id}"
