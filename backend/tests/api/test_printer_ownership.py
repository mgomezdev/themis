"""Ownership applies across plugin/model boundaries, imports and concurrent writes."""
import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import Printer
from app.services import printer_setup
from tests.api.test_fleet_import import _backup, _import, _printer

SNAP = {"plugin_id": "snapmaker", "manufacturer_id": "snapmaker", "model_id": "u1_extended"}
GENERIC = {"plugin_id": "moonraker", "manufacturer_id": "generic", "model_id": "custom_klipper"}


async def add(client, identity, host, **cfg):
    return await client.post("/api/v1/printers", json={**identity, "connection_config": {"ip_address": host, **cfg}})


@pytest.mark.parametrize("cfg", [{}, {"port": 9999}, {"api_key": "different"}])
async def test_same_device_cannot_be_claimed_by_generic_and_manufacturer_plugins(client, cfg):
    first = await add(client, SNAP, "printer.local")
    assert first.status_code == 201, first.text
    refused = await add(client, GENERIC, " PRINTER.LOCAL. ", **cfg)
    assert refused.status_code == 409
    assert len((await client.get("/api/v1/printers")).json()) == 1


async def test_serial_detects_device_at_another_address(client):
    assert (await add(client, SNAP, "10.0.0.2", serial_number="abc123")).status_code == 201
    assert (await add(client, GENERIC, "10.0.0.3", serial_number=" ABC123 ")).status_code == 409


async def test_edit_cannot_take_another_devices_address_and_is_atomic(client):
    first = (await add(client, SNAP, "10.0.0.2")).json()
    second = (await add(client, GENERIC, "10.0.0.3")).json()
    r = await client.patch(f"/api/v1/printers/{second['id']}", json={"name": "Changed", "connection_config": first["connection_config"]})
    assert r.status_code == 409
    after = (await client.get(f"/api/v1/printers/{second['id']}")).json()
    assert (after["name"], after["connection_config"]) == (second["name"], second["connection_config"])


async def test_disabled_binding_still_owns_device_until_deleted(client):
    first = (await add(client, SNAP, "10.0.0.2")).json()
    await client.patch(f"/api/v1/printers/{first['id']}", json={"enabled": False})
    assert (await add(client, GENERIC, "10.0.0.2")).status_code == 409
    assert (await client.delete(f"/api/v1/printers/{first['id']}")).status_code == 204
    assert (await add(client, GENERIC, "10.0.0.2")).status_code == 201


async def test_import_skips_conflicts_in_database_and_within_same_backup(client):
    assert (await add(client, SNAP, "10.0.0.2")).status_code == 201
    def backed(name, host):
        return {**_printer(name, "moonraker", connection_config={"ip_address": host}), **GENERIC}
    r = await _import(client, _backup(backed("duplicate", "10.0.0.2"), backed("new", "10.0.0.3"), backed("twice", "10.0.0.3")))
    assert r.status_code == 200, r.text
    assert (r.json()["imported"], r.json()["skipped"]) == (1, 2)
    assert len([w for w in r.json()["warnings"] if "already owns" in w]) == 2


async def test_database_guard_serializes_concurrent_claims(session_factory):
    async def claim(plugin):
        async with session_factory() as s:
            s.add(Printer(name=plugin, printer_type=plugin, plugin_id=plugin, connection_config={"ip_address": "10.0.0.2"}))
            try:
                await s.commit()
                return True
            except IntegrityError as exc:
                assert "printer ownership conflict" in str(exc)
                await s.rollback()
                return False
    assert sorted(await asyncio.gather(claim("snapmaker"), claim("moonraker"))) == [False, True]
    async with session_factory() as s:
        assert len((await s.execute(select(Printer))).scalars().all()) == 1


async def test_database_guard_rejects_update_even_without_api_validation(client, session_factory):
    first = (await add(client, SNAP, "10.0.0.2")).json()
    second = (await add(client, GENERIC, "10.0.0.3")).json()
    async with session_factory() as s:
        row = await s.get(Printer, second["id"])
        row.connection_config = first["connection_config"]
        with pytest.raises(IntegrityError, match="printer ownership conflict"):
            await s.commit()
        await s.rollback()
        assert (await s.get(Printer, second["id"])).connection_config == second["connection_config"]


async def test_legacy_duplicates_are_preserved_but_not_connected(client, session_factory, mocker):
    first = (await add(client, SNAP, "10.0.0.2")).json()
    async with session_factory() as s:
        from app.migrations.v081_printer_ownership import down, up
        await down(s)
        duplicate = Printer(name="Legacy", plugin_id="moonraker", printer_type="moonraker", connection_config={"ip_address": "10.0.0.2"})
        s.add(duplicate)
        await s.commit()
        await up(s)
        await s.commit()
        duplicate_id = duplicate.id
    connect = mocker.patch("app.services.printer_client_factory.create_client")
    for pid in (first["id"], duplicate_id):
        outcome = await printer_setup.evaluate(pid, session_factory)
        assert not outcome.ok and "already owns" in outcome.error
    connect.assert_not_called()
    from app.services.printer_manager import printer_manager
    startup_connect = mocker.patch("app.services.printer_manager.create_client")
    await printer_manager.connect_all_enabled_printers(session_factory)
    startup_connect.assert_not_called()
    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM printers"))).scalar() == 2
