"""POST /api/v1/printers/discover over a virtual LAN."""
from unittest.mock import patch

import pytest

from tests.virtual_printers.virtual_network import VirtualNetwork


@pytest.fixture
def lan():
    n = VirtualNetwork()
    n.add_bambu("192.168.7.20", serial="01P00A111111111", model="C12", name="Bambu-P1S")
    n.add_elegoo("192.168.7.40")
    n.add_moonraker("192.168.7.30", needs_key=True)
    with patch("app.api.routes.printers._discovery_network", return_value=n):
        yield n


async def test_discover_lists_what_answers_with_prefill_ready_connection_config(client, lan):
    resp = await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/24"]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["ranges"], body["scanned"], body["truncated"]) == (["192.168.7.0/24"], 254, False)
    by_ip = {f["ip"]: f for f in body["found"]}
    assert set(by_ip) == {"192.168.7.20", "192.168.7.30", "192.168.7.40"}
    bambu = by_ip["192.168.7.20"]
    assert (bambu["printer_type"], bambu["display_name"], bambu["model"], bambu["serial"]) == ("bambu", "Bambu Lab", "P1S", "01P00A111111111")
    assert bambu["connection_config"] == {"ip_address": "192.168.7.20", "serial_number": "01P00A111111111"}
    assert "access_code" not in bambu["connection_config"]                  # secrets are never discovered
    assert by_ip["192.168.7.30"]["note"] == "Requires an API key"
    assert all(f["already_added"] is False for f in body["found"])


async def test_already_added_printers_are_flagged_by_ip(client, lan, create_printer):
    await create_printer(name="Existing", printer_type="elegoo_centauri", connection_config={"ip_address": "192.168.7.40"})

    found = (await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/24"]})).json()["found"]

    assert {f["ip"]: f["already_added"] for f in found} == {
        "192.168.7.20": False, "192.168.7.30": False, "192.168.7.40": True}


async def test_no_ranges_means_the_hosts_own_network(client, lan):
    with patch("app.services.discovery.local_ranges", return_value=["192.168.7.32/29"]):
        body = (await client.post("/api/v1/printers/discover", json={})).json()
    assert body["ranges"] == ["192.168.7.32/29"] and body["scanned"] == 6
    with patch("app.services.discovery.local_ranges", return_value=[]):
        assert (await client.post("/api/v1/printers/discover", json={})).status_code == 422


@pytest.mark.parametrize("ranges", [["8.8.8.0/24"], ["192.168.0.0/16"], ["banana"], ["192.168.7.0/24", "1.2.3.4"],
                                    [f"10.0.{i}.0/24" for i in range(9)]])
async def test_bad_ranges_are_rejected_without_scanning(client, lan, ranges):
    resp = await client.post("/api/v1/printers/discover", json={"ranges": ranges})
    assert resp.status_code == 422
    assert lan.probes == []


async def test_a_range_outside_the_virtual_lan_finds_nothing_and_is_not_an_error(client, lan):
    body = (await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.50.0/28"]})).json()
    assert body["found"] == [] and body["scanned"] == 14


async def test_discovery_needs_the_printers_write_scope(client, lan, session_factory):
    from app.models import ApiKey
    from app.services.api_key_service import generate_key, hash_key

    async def key_with(scopes):
        raw, prefix = generate_key()
        async with session_factory() as s:
            s.add(ApiKey(name="k", key_prefix=prefix, key_hash=hash_key(raw), enabled=True, scopes=scopes,
                         created_at="2026-01-01T00:00:00"))
            await s.commit()
        return {"X-Api-Key": raw}

    body = {"ranges": ["192.168.7.0/28"]}
    assert (await client.post("/api/v1/printers/discover", json=body, headers=await key_with(["printers:read"]))).status_code == 403
    assert (await client.post("/api/v1/printers/discover", json=body, headers=await key_with(["printers:write"]))).status_code == 200


async def test_only_one_scan_runs_at_a_time(client, lan):
    import asyncio
    from app.api.routes import printers as routes
    release = asyncio.Event()
    real_scan = __import__("app.services.discovery", fromlist=["scan"]).scan

    async def slow_scan(*a, **kw):
        await release.wait()
        return await real_scan(*a, **kw)

    with patch("app.services.discovery.scan", slow_scan):
        first = asyncio.ensure_future(client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/28"]}))
        for _ in range(50):
            if routes._discovery_lock.locked():
                break
            await asyncio.sleep(0.01)
        second = await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/28"]})
        release.set()
        assert (await first).status_code == 200
    assert second.status_code == 409
    assert not routes._discovery_lock.locked()                                    # released afterwards


async def test_loopback_and_reserved_ranges_are_rejected_before_any_probe(client, lan):
    for r in ("127.0.0.1", "0.0.0.0/24", "255.255.255.255", "198.18.0.0/24"):
        assert (await client.post("/api/v1/printers/discover", json={"ranges": [r]})).status_code == 422
    assert lan.probes == []
