"""Deleting a printer drops its plugin mapping: a reused id must not inherit the old printer's dormancy (BIZ-251 review)."""
from app.services.printer_manager import printer_manager


async def test_deleting_a_printer_forgets_its_plugin_mapping(client, create_printer):
    pid = await create_printer()
    assert printer_manager._printer_plugin.get(pid) == "bambu"

    assert (await client.delete(f"/api/v1/printers/{pid}")).status_code == 204

    assert pid not in printer_manager._printer_plugin
