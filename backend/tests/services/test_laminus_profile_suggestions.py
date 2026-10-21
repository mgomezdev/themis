"""BIZ-294: the Laminus adapter's machine-profile suggestions, against stubbed responses of the Laminus profiles API (BIZ-293)."""
import json

import httpx
import pytest

from app.services.providers.laminus.adapter import LaminusSlicingProvider
from app.services.providers.slicing import SlicingProviderError
from tests.fake_providers import FakeSlicingProvider
from tests.services.test_slicing_provider_contract import sidecar  # noqa: F401  (fixture)

CANDIDATES = {"catalog_version": "0123456789abcdef", "candidates": [
    {"ref": "machine:bambu-lab-p1s-0.4-nozzle", "uuid": "u1", "name": "Bambu Lab P1S 0.4 nozzle", "nozzle_diameter_mm": 0.4,
     "flow": "standard", "confidence": 0.97, "match_reason": "exact_model"},
    {"ref": "machine:x", "uuid": "u2", "name": "", "confidence": 0.1},                  # nameless: unusable, dropped
    {"ref": "machine:bambu-lab-a1-0.4-nozzle", "uuid": "u3", "name": "Bambu Lab A1 0.4 nozzle", "nozzle_diameter_mm": 0.4,
     "flow": "standard", "confidence": 0.3, "match_reason": "vendor_only"}]}
VARIANTS = {"catalog_version": "0123456789abcdef", "variants": [
    {"ref": "machine:bambu-lab-p1s-0.4-nozzle", "uuid": "u1", "name": "Bambu Lab P1S 0.4 nozzle", "nozzle_diameter_mm": 0.4,
     "flow": "standard", "is_default_guess": True},
    {"ref": "machine:bambu-lab-p1s-0.4-high-flow-nozzle", "uuid": "u4", "name": "Bambu Lab P1S 0.4 high flow nozzle",
     "nozzle_diameter_mm": 0.4, "flow": "high_flow", "is_default_guess": False}]}


def provider():
    return LaminusSlicingProvider("http://laminus.test")


def test_the_adapter_advertises_and_maps_machine_candidates(sidecar):  # noqa: F811
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=CANDIDATES, headers={"X-Catalog-Version": "0123456789abcdef"})
    sidecar.handler = handler

    got = provider().suggest_machines("Bambu Lab", "P1S", "bambu/p1s")

    assert provider().SUGGEST_PROFILES is True
    assert seen == [("POST", "/api/v1/profiles/machines/suggest", {"manufacturer": "Bambu Lab", "model": "P1S", "model_key": "bambu/p1s"})]
    assert [(c.name, c.ref, c.nozzle_diameter_mm, c.flow, c.confidence, c.match_reason) for c in got] == [
        ("Bambu Lab P1S 0.4 nozzle", "machine:bambu-lab-p1s-0.4-nozzle", 0.4, "standard", 0.97, "exact_model"),
        ("Bambu Lab A1 0.4 nozzle", "machine:bambu-lab-a1-0.4-nozzle", 0.4, "standard", 0.3, "vendor_only")]


def test_the_adapter_maps_nozzle_variants(sidecar):  # noqa: F811
    sidecar.handler = lambda request: httpx.Response(200, json=VARIANTS)

    got = provider().nozzle_variants("Bambu Lab", "P1S")

    assert [(v.name, v.flow, v.is_default_guess) for v in got] == [
        ("Bambu Lab P1S 0.4 nozzle", "standard", True), ("Bambu Lab P1S 0.4 high flow nozzle", "high_flow", False)]
    assert got[1].ref == "machine:bambu-lab-p1s-0.4-high-flow-nozzle" and got[1].nozzle_diameter_mm == 0.4


def test_empty_answers_are_empty_lists_not_errors(sidecar):  # noqa: F811
    sidecar.handler = lambda request: httpx.Response(200, json={"catalog_version": "x", "candidates": [], "variants": []})
    assert provider().suggest_machines("A", "B") == [] and provider().nozzle_variants("A", "B") == []


@pytest.mark.parametrize("status", [404, 422, 503])
def test_a_sidecar_that_cannot_answer_is_a_neutral_provider_error(sidecar, status):  # noqa: F811
    sidecar.handler = lambda request: httpx.Response(status, json={"error": "catalog_unavailable"})
    with pytest.raises(SlicingProviderError, match=str(status)):
        provider().suggest_machines("A", "B")
    with pytest.raises(SlicingProviderError, match=str(status)):
        provider().nozzle_variants("A", "B")


def test_an_unreachable_sidecar_is_a_neutral_provider_error(sidecar):  # noqa: F811
    def boom(request):
        raise httpx.ConnectError("down")
    sidecar.handler = boom
    with pytest.raises(SlicingProviderError):
        provider().suggest_machines("A", "B")


def test_a_provider_without_the_feature_says_so():
    p = FakeSlicingProvider()
    assert p.SUGGEST_PROFILES is False
    with pytest.raises(SlicingProviderError, match="cannot suggest"):
        p.suggest_machines("A", "B")
    with pytest.raises(SlicingProviderError, match="cannot list"):
        p.nozzle_variants("A", "B")
