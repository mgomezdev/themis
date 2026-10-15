"""BIZ-294 resolver: printer variant -> mapping row -> machine profile, with the raw per-printer override on top."""
import pytest

from app.models import PrinterModelSlicerProfile as Row
from app.services.slicer_profiles import NozzleVariant, resolve_from_rows

M = "model-1"


def row(d, flow="standard", name=None, default=False, provider="a", model=M):
    return Row(model_uuid=model, provider_plugin_id=provider, nozzle_diameter_mm=d, flow=flow,
               machine_profile=name or f"M {d} {flow}", is_default=default)


ROWS = [row(0.4, default=True), row(0.6), row(0.4, "high_flow")]


def resolve(**kw):
    base = dict(override=None, model_uuid=M, model_name="P1S", nozzle_variant=None, provider_id="a", rows=ROWS)
    return resolve_from_rows(**{**base, **kw})


def test_no_variant_uses_the_models_default_row():
    r = resolve()
    assert (r.profile, r.reason, r.provider_id, r.from_override) == ("M 0.4 standard", None, "a", False)


def test_a_variant_picks_the_matching_row_and_flow_is_part_of_the_variant():
    assert resolve(nozzle_variant={"diameter_mm": 0.6, "flow": "standard"}).profile == "M 0.6 standard"
    assert resolve(nozzle_variant={"diameter_mm": 0.4, "flow": "high_flow"}).profile == "M 0.4 high_flow"
    assert resolve(nozzle_variant={"diameter_mm": 0.4}).profile == "M 0.4 standard"       # flow defaults to standard


def test_an_unmapped_variant_has_no_profile_and_the_reason_names_model_and_variant():
    r = resolve(nozzle_variant={"diameter_mm": 0.8, "flow": "standard"})
    assert r.profile is None and r.reason == "no machine profile for P1S 0.8mm"
    r = resolve(nozzle_variant={"diameter_mm": 0.4, "flow": "high_flow"}, rows=[row(0.4, default=True)])
    assert r.reason == "no machine profile for P1S 0.4mm high-flow"


def test_a_model_with_no_rows_reports_the_default_nozzle():
    assert resolve(rows=[]).reason == "no machine profile for P1S default nozzle"


def test_rows_are_per_provider_and_per_model():
    rows = [row(0.4, name="Other slicer 0.4", default=True, provider="b"), row(0.4, name="Other model", default=True, model="m2")]
    assert resolve(rows=rows).profile is None
    assert resolve(rows=rows, provider_id="b").profile == "Other slicer 0.4"


def test_the_raw_override_wins_over_everything_even_without_a_model_or_provider():
    r = resolve(override="Custom", nozzle_variant={"diameter_mm": 0.8}, rows=[])
    assert (r.profile, r.from_override) == ("Custom", True)
    assert resolve(override="Custom", model_uuid=None, provider_id=None).profile == "Custom"


def test_a_printer_without_a_model_or_a_provider_has_no_profile():
    r = resolve(model_uuid=None, model_name=None)
    assert r.profile is None and "no registered model" in r.reason
    assert resolve(provider_id=None).profile is None


@pytest.mark.parametrize("raw", [None, "x", {}, {"diameter_mm": 0}, {"diameter_mm": -1}, {"diameter_mm": "a"}, {"diameter_mm": 0.4, "flow": "turbo"}])
def test_a_malformed_variant_parses_to_none_and_means_the_default(raw):
    assert NozzleVariant.parse(raw) is None
    assert resolve(nozzle_variant=raw).profile == "M 0.4 standard"


def test_variant_labels():
    assert NozzleVariant(0.4).label == "0.4mm" and NozzleVariant(0.4, "high_flow").label == "0.4mm high-flow"
    assert NozzleVariant(0.4, "high_flow").as_dict() == {"diameter_mm": 0.4, "flow": "high_flow"}
