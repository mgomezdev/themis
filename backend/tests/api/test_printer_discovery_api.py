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


async def _scan(client) -> dict[tuple, dict]:
    found = (await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/24"]})).json()["found"]
    return {(f["ip"], f["plugin_id"]): f for f in found}


async def test_same_ip_same_model_is_already_added(client, lan, create_printer):
    """BIZ-369: a printer is its (IP, model) — the scan row says which saved printer it is and whether its setup is confirmed."""
    pid = await create_printer(name="Bay 2", configured=False, plugin_id="bambu", manufacturer_id="bambu", model_id="p1s",
                               connection_config={"ip_address": "192.168.7.20"})
    row = (await _scan(client))[("192.168.7.20", "bambu")]
    assert row["already_added"] is True and row["ip_in_use_by"] is None
    assert row["added_printer"] == {"printer_id": pid, "name": "Bay 2", "configured": False, "config_error": None}


async def test_same_ip_different_model_sets_ip_in_use_by(client, lan, create_printer):
    pid = await create_printer(name="A1-Bay4", plugin_id="bambu", manufacturer_id="bambu", model_id="a1",
                               connection_config={"ip_address": "192.168.7.20"})
    row = (await _scan(client))[("192.168.7.20", "bambu")]
    assert (row["already_added"], row["added_printer"]) == (False, None)
    assert row["ip_in_use_by"] == {"printer_id": pid, "name": "A1-Bay4", "model": "A1"}


async def test_discovered_rows_carry_the_contract_keys(client, lan):
    from tests.api.test_response_contracts import assert_carries
    for row in (await _scan(client)).values():
        assert_carries("discovered_printer", row)


async def test_unmatched_model_same_plugin_is_already_added(client, lan, create_printer):
    """The scan couldn't name the model (model_uuid None): the same IP served by the same plugin is the same printer; the other
    provider answering at that IP sees it as an IP conflict instead."""
    pid = await create_printer(name="U1", plugin_id="snapmaker", manufacturer_id="snapmaker", model_id="u1_extended",
                               connection_config={"ip_address": "192.168.7.30"})
    rows = await _scan(client)
    snap, moon = rows[("192.168.7.30", "snapmaker")], rows[("192.168.7.30", "moonraker")]
    assert snap["model_uuid"] is None and snap["already_added"] is True and snap["added_printer"]["printer_id"] == pid
    assert moon["already_added"] is False and moon["ip_in_use_by"]["printer_id"] == pid


async def test_no_ranges_means_the_hosts_own_network(client, lan):
    with patch("app.services.discovery.local_ranges", return_value=["192.168.7.32/29"]):
        body = (await client.post("/api/v1/printers/discover", json={})).json()
    assert body["ranges"] == ["192.168.7.32/29"] and body["scanned"] == 6
    with patch("app.services.discovery.local_ranges", return_value=[]):
        assert (await client.post("/api/v1/printers/discover", json={})).status_code == 422


@pytest.mark.parametrize("ranges", [["8.8.8.0/24"], ["10.0.0.0/15"], ["banana"], ["192.168.7.0/24", "1.2.3.4"],
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


async def test_discovered_printers_name_the_plugin_that_serves_them(client, lan):
    found = (await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/24"]})).json()["found"]

    assert {f["ip"]: (f["printer_type"], f["plugin_id"]) for f in found} == {
        "192.168.7.20": ("bambu", "bambu"),
        "192.168.7.30": ("snapmaker_extended", "snapmaker"),      # the client's printer_type differs from its plugin's id
        "192.168.7.40": ("elegoo_centauri", "elegoo_centauri"),
    }


async def test_plugin_scan_only_probes_selected_plugin_and_accepts_wildcards(client, lan):
    from app.plugins.host import plugin_host
    await plugin_host.start()
    response = await client.post('/api/v1/printers/discover', json={
        'ranges': ['192.168.7.x'], 'plugin_id': 'snapmaker',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['scanned'] == 254 and not body['truncated']
    assert [(p['plugin_id'], p['ip']) for p in body['found']] == [('snapmaker', '192.168.7.30')]
    assert all(p[0] == 'http' for p in lan.probes)


@pytest.mark.parametrize('plugin_id', ['missing', 'laminus'])
async def test_invalid_plugin_is_rejected_before_probing(client, lan, plugin_id):
    response = await client.post('/api/v1/printers/discover', json={'ranges': ['192.168.7.x'], 'plugin_id': plugin_id})
    assert response.status_code == 422
    assert lan.probes == []


async def test_bambu_settings_discovery_finds_p1s_without_probing_other_vendors(client, lan):
    from app.plugins.host import plugin_host
    await plugin_host.start()
    assert (await client.get('/api/v1/plugins/bambu')).json()['printer_discovery'] is True
    response = await client.post('/api/v1/printers/discover', json={
        'ranges': ['192.168.7.x'], 'plugin_id': 'bambu',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['scanned'] == 254 and not body['truncated']
    printer, = body['found']
    assert (printer['plugin_id'], printer['model'], printer['ip']) == ('bambu', 'P1S', '192.168.7.20')
    assert printer['model_uuid'] is not None
    assert printer['connection_config'] == {'ip_address': '192.168.7.20', 'serial_number': '01P00A111111111'}
    assert 'access_code' not in printer['connection_config']
    assert not any(probe[0] == 'http' for probe in lan.probes)


async def test_disabled_plugin_is_rejected_and_settings_advertise_discovery(client, lan):
    from app.plugins.host import plugin_host
    await plugin_host.start()
    assert (await client.get('/api/v1/plugins/snapmaker')).json()['printer_discovery'] is True
    assert (await client.get('/api/v1/plugins/laminus')).json()['printer_discovery'] is False
    await plugin_host.update_config('snapmaker', enabled=False)
    response = await client.post('/api/v1/printers/discover', json={'ranges': ['192.168.7.x'], 'plugin_id': 'snapmaker'})
    assert response.status_code == 422
    assert lan.probes == []


async def test_discovered_free_text_model_matches_the_registry_when_unambiguous_else_offers_the_custom_path(client, lan):
    found = (await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/24"]})).json()["found"]
    by_ip = {f["ip"]: f for f in found}
    p1s = next(m for m in (await client.get("/api/v1/printer-models", params={"plugin_id": "bambu"})).json() if m["model_id"] == "p1s")

    assert by_ip["192.168.7.20"]["model_uuid"] == p1s["id"]         # "P1S" -> the one known Bambu P1S
    assert by_ip["192.168.7.30"]["model_uuid"] is None              # unmatched: the UI shows the explicit custom path


async def test_a_moonraker_host_is_offered_once_per_moonraker_provider_so_the_connection_path_is_the_users_explicit_choice(client, lan):
    body = (await client.post("/api/v1/printers/discover", json={"ranges": ["192.168.7.0/24"]})).json()

    moonraker_hits = sorted((f["plugin_id"], f["printer_type"]) for f in body["found"] if f["ip"] == "192.168.7.30")

    assert moonraker_hits == [("moonraker", "moonraker"), ("snapmaker", "snapmaker_extended")]
