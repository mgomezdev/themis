"""BIZ-291: Laminus as a neutral `GcodeProducer` (identity, readiness, target support, profiles) over a fake sidecar."""
from __future__ import annotations

import httpx
import pytest

from app.services import catalog_service
from app.services.providers import contract as c
from app.services.providers.laminus.adapter import LaminusSlicingProvider, catalog_from_legacy
from app.services.providers.laminus.connection import PLUGIN_VERSION, LaminusConnection, LaminusSettings
from app.services.providers.laminus.producer import ENGINE_ID, PROVIDER_ID, LaminusGcodeProducer
from tests.services.test_slicing_provider_contract import _RAW_CATALOG, URL, sidecar  # noqa: F401  (fixture)

BED = (256.0, 256.0, 256.0)
TARGET = c.TargetPrinter(BED, "uuid-x1c")


def producer() -> LaminusGcodeProducer:
    return LaminusGcodeProducer(LaminusSlicingProvider(URL), version="9.9")


def test_identity_is_local_and_names_the_plugin_and_engine():
    ident = producer().identity
    assert (ident.provider_id, ident.version, ident.engine_id) == (PROVIDER_ID, "9.9", ENGINE_ID)
    assert ident.engine_version is None            # unknown until the sidecar answers


def test_status_ready_reports_the_engine_version_the_sidecar_gave(sidecar):  # noqa: F811
    st = producer().status()
    assert st.state is c.Readiness.READY and st.ready
    assert st.identity.engine_version == "2.3.2"


def test_status_not_ready_when_the_sidecar_answers_but_is_starting(sidecar):  # noqa: F811
    sidecar.handler = lambda request: httpx.Response(503)
    st = producer().status()
    assert st.state is c.Readiness.NOT_READY and not st.ready and "503" in st.detail


def test_status_unreachable_when_the_sidecar_does_not_answer(sidecar):  # noqa: F811
    def boom(request):
        raise httpx.ConnectError("refused")
    sidecar.handler = boom
    st = producer().status()
    assert st.state is c.Readiness.UNREACHABLE and not st.ready and st.detail


def test_status_passes_the_timeout_to_the_health_call(sidecar):  # noqa: F811
    producer().status(timeout=1.5)
    assert sidecar.client_kwargs[-1]["timeout"] == 1.5


def test_supports_target_refuses_only_a_nonsensical_bed():
    p = producer()
    assert p.supports_target(TARGET) is True
    assert p.supports_target(c.TargetPrinter((0.0, 256.0, 256.0))) is False


def test_profiles_come_from_the_cached_catalog_only(monkeypatch):
    p = producer()
    monkeypatch.setattr(catalog_service, "_catalog", None)
    assert p.profiles("process", TARGET) == []                 # nothing cached: no network fetch
    monkeypatch.setattr(catalog_service, "_catalog", catalog_from_legacy(_RAW_CATALOG))
    assert [x.name for x in p.profiles("process", TARGET)] == ["0.20mm Standard"]
    assert {x.name for x in p.profiles("filament", TARGET)} == {"PLA @A", "PETG @A"}
    assert all(x.kind == "filament" for x in p.profiles("filament", TARGET))
    assert p.profiles("machine", TARGET) == []                 # only process/filament are advertised


def test_curated_settings_are_the_orca_vocabulary():
    keys = {s.key for s in producer().curated_settings()}
    assert "layer_height" in keys


def test_produce_rejects_gcode_inputs_and_honours_cancel(tmp_path):
    p = producer()
    req = c.ProduceRequest(tmp_path / "a.gcode", c.InputKind.GCODE, 1, TARGET)
    with pytest.raises(c.ProviderError) as e:
        p.produce(req, tmp_path)
    assert e.value.code is c.ErrorCode.UNSUPPORTED_INPUT
    token = c.CancelToken()
    token.cancel()
    with pytest.raises(c.ProviderError) as e:
        p.produce(c.ProduceRequest(tmp_path / "a.3mf", c.InputKind.MODEL_3MF, 1, TARGET), tmp_path, cancel=token)
    assert e.value.code is c.ErrorCode.CANCELLED


def test_produce_for_a_model_is_unsupported_until_the_target_carries_a_machine_profile(tmp_path):
    with pytest.raises(c.ProviderError) as e:
        producer().produce(c.ProduceRequest(tmp_path / "a.3mf", c.InputKind.MODEL_3MF, 1, TARGET), tmp_path)
    assert e.value.code is c.ErrorCode.UNSUPPORTED_TARGET


def test_connection_builds_the_producer_with_the_plugin_options_and_none_when_unconfigured():
    assert LaminusConnection(LaminusSettings(url=None)).gcode_producer() is None
    conn = LaminusConnection(LaminusSettings(url=URL, request_timeout_s=11.0, poll_timeout_s=12.0))
    prod = conn.gcode_producer()
    assert isinstance(prod, LaminusGcodeProducer) and prod.identity.version == PLUGIN_VERSION
    assert prod._slicing._request_timeout_s == 11.0 and prod._slicing._poll_timeout_s == 12.0


def test_plugin_version_matches_the_package_manifest():
    from pathlib import Path
    toml = (Path(__file__).parents[2] / "app/plugins/laminus/themis-plugin.toml").read_text()
    assert f'version = "{PLUGIN_VERSION}"' in toml


def test_the_plugin_instance_satisfies_the_gcode_produce_protocol():
    from app.plugins.capabilities.gcode_produce import SlicingPlugin
    assert isinstance(LaminusConnection(LaminusSettings(url=URL)), SlicingPlugin)
    assert not isinstance(object(), SlicingPlugin)
