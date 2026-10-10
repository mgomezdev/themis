"""PluginManifest.manufacturers: declared manufacturers/models and their construction-time validation (BIZ-262)."""
import dataclasses

import pytest

from app.plugins import PluginError, PluginManifest
from app.plugins.manifest import Manufacturer, PrinterModel
from tests.plugins.dummy_plugin import make_manifest


def _with(*manufacturers) -> PluginManifest:
    return make_manifest("fake_printer", manufacturers=tuple(manufacturers))


def _model(mid="x1", name="X1", **kw) -> PrinterModel:
    return PrinterModel(mid, name, **kw)


@pytest.mark.parametrize("build", [
    lambda: _with(Manufacturer("acme", "Acme", (_model("x1"),)), Manufacturer("acme", "Acme 2", (_model("x2"),))),
    lambda: _with(Manufacturer("acme", "Acme", (_model("x1"), _model("x1")))),
    lambda: _with(Manufacturer("acme", "Acme", (_model("x1"),)), Manufacturer("globex", "Globex", (_model("x1"),))),
], ids=["duplicate-manufacturer-id", "duplicate-model-id-in-one-manufacturer", "duplicate-model-id-across-manufacturers"])
def test_duplicate_ids_within_a_plugin_are_refused(build):
    with pytest.raises(PluginError):
        build()


@pytest.mark.parametrize("build", [
    lambda: _with(Manufacturer("Acme-Co", "Acme", (_model("x1"),))),
    lambda: _with(Manufacturer("acme", "Acme", (_model("X1"),))),
    lambda: _with(Manufacturer("acme", "Acme", (_model("1x"),))),
    lambda: _with(Manufacturer("acme", "Acme", (_model("x1", bed_mm=(0, 256)),))),
    lambda: _with(Manufacturer("acme", "Acme", (_model("x1", bed_mm=(256, -1)),))),
    lambda: _with(Manufacturer("acme", "Acme", (_model("x1", toolheads=0),))),
], ids=["bad-manufacturer-id", "uppercase-model-id", "digit-first-model-id",
        "zero-bed-x", "negative-bed-y", "zero-toolheads"])
def test_malformed_manufacturer_or_model_is_refused_at_construction(build):
    with pytest.raises(PluginError):
        build()


def test_a_valid_manifest_with_two_manufacturers_constructs_and_keeps_declared_models():
    m = _with(
        Manufacturer("acme", "Acme", (_model("x1", bed_mm=(256, 256)),
                                      _model("x1_pro", "X1 Pro", bed_mm=(300, 300), toolheads=2))),
        Manufacturer("globex", "Globex", (_model("g1", "G1"),)),
    )
    assert isinstance(m, PluginManifest)
    assert [mf.id for mf in m.manufacturers] == ["acme", "globex"]
    pro = m.manufacturers[0].models[1]
    assert (pro.id, pro.name, pro.bed_mm, pro.toolheads) == ("x1_pro", "X1 Pro", (300, 300), 2)
    g1 = m.manufacturers[1].models[0]
    assert (g1.bed_mm, g1.toolheads) == ((256, 256), 1)


def test_manufacturers_default_to_empty_and_declarations_are_frozen():
    assert make_manifest().manufacturers == ()
    m = _with(Manufacturer("acme", "Acme", (_model("x1"),)))
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.manufacturers[0].name = "Changed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.manufacturers[0].models[0].toolheads = 9
