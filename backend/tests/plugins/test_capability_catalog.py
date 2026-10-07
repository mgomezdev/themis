import pytest

from app.plugins.capabilities import CORE
from app.plugins.capabilities.definition import CAP_ID_RE, CapabilityDef
from app.plugins.capabilities import filament_inventory as fi


def test_core_catalog_has_filament_inventory_v1_with_all_features():
    d = CORE["inventory.filament"]
    assert (d.id, d.version, d.label) == ("inventory.filament", 1, "Filament inventory")
    assert d.features == fi.ALL_CAPABILITIES and fi.CAPABILITY == "inventory.filament"


@pytest.mark.parametrize("cid,ok", [("inventory.filament", True), ("acme_inv.reports", True), ("a.b.c", True),
                                    ("inventory", False), ("Inventory.filament", False), ("a..b", False), (".a.b", False), ("a.b-c", False)])
def test_capability_id_shape(cid, ok):
    assert bool(CAP_ID_RE.match(cid)) is ok


def test_definition_is_immutable():
    with pytest.raises(Exception):
        CORE["inventory.filament"].version = 2        # type: ignore[misc]
    assert isinstance(CORE["inventory.filament"], CapabilityDef)
