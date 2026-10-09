"""Static checks: tool mapping left the printer clients (BIZ-251 Phase E)."""
import re
from pathlib import Path

import app
from app.services.abstract_printer_client import AbstractPrinterClient
from app.plugins.snapmaker.client import SnapmakerExtendedClient

APP_DIR = Path(app.__file__).parent


def test_printer_clients_have_no_remap_hook():
    assert not hasattr(AbstractPrinterClient, "remap_sliceable_3mf")
    assert not hasattr(SnapmakerExtendedClient, "remap_sliceable_3mf")


def test_slice_tool_mapping_flag():
    assert AbstractPrinterClient.slice_tool_mapping is False
    assert SnapmakerExtendedClient.slice_tool_mapping is True


def test_snapmaker_client_and_plugin_do_not_import_remap_package():
    files = [APP_DIR / "plugins" / "snapmaker" / "client.py", *(APP_DIR / "plugins" / "snapmaker").rglob("*.py")]
    assert len(files) >= 2
    for f in files:
        text = f.read_text(encoding="utf-8")
        # the remap *package* (services/snapmaker/), not the client module services/snapmaker_client.py
        for pattern in (r"snapmaker\.remap", r"from \.snapmaker\b", r"services\.snapmaker\b"):
            assert not re.search(pattern, text), f"{f.name} matches {pattern!r}"
