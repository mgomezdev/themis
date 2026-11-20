"""Printer-driven print artifact routing across format, brand, model and printer settings."""
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app import plugins
from app.models import GcodeFile, JobPrinterConfig, Printer
from app.plugins import HOST_API, PluginManifest, Provide, register_plugin
from app.plugins.capabilities.gcode_produce import CAPABILITY
from app.services import slicer_profiles
from app.services.providers.contract import Artifact, ArtifactKind, InputKind, Reason
from app.services.queue_engine import build_slice_request, _slice_params
from app.services.slice_resolution import PrinterView, resolve_slicing
from app.services.slicer_service import SliceError, SliceRequest, SlicerService
from tests.fake_providers import FakeGcodeProducer
from tests.services.test_slice_save import _engine, _seed, env  # noqa: F401 — env is a pytest fixture
from tests.waiting import settle_background_tasks

ENDPOINT = "/api/v1/settings/print-conversion"


class Settings(BaseModel):
    pass


def _register_converter(plugin_id, *outputs):
    producer = FakeGcodeProducer(plugin_id)
    producer.output_kinds = frozenset(outputs)

    class Converter:
        def __init__(self, settings):
            pass

        def gcode_producer(self):
            return producer

    register_plugin(PluginManifest(id=plugin_id, name=plugin_id, version="1.0.0", host_api=HOST_API,
                                   settings_model=Settings, factory=Converter, default_enabled=True,
                                   provides={CAPABILITY: Provide()}))
    return producer


@pytest.mark.parametrize(
    ("only_raw_converter", "printer_type", "reason"),
    [
        (True, "bambu", "raw_only can't convert this file to .gcode.3mf for P1"),
        (False, "elegoo_centauri", "No slicing provider is enabled — slicing paused"),
    ],
    ids=["archive-required-with-raw-only-converter", "model-without-installed-converters"],
)
async def test_model_job_stays_blocked_when_conversion_is_impossible(
        client, session_factory, tmp_path, env, monkeypatch, only_raw_converter, printer_type, reason):
    for manifest in plugins.providers_of(CAPABILITY):
        monkeypatch.delitem(plugins._REGISTRY, manifest.id)
    if only_raw_converter:
        producer = _register_converter("raw_only", ArtifactKind.GCODE)
        probe = Mock(wraps=producer.status)
        monkeypatch.setattr(producer, "status", probe)

    queue, manager = _engine(session_factory, tmp_path, archive=only_raw_converter)
    job_id = await _seed(session_factory, printer_type=printer_type)
    printer = manager.get_client(1)
    printer.file_upload_supported = True
    for _ in range(2):
        await queue._process_queue()
        await settle_background_tasks()

        detail = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()
        assert (detail["status"], detail["block_reason"], detail["assigned_printer_id"]) == ("blocked", reason, None)

    queue._slicer.slice.assert_not_called()
    printer.upload_file.assert_not_called()
    printer.start_print.assert_not_called()
    if only_raw_converter:
        probe.assert_not_called()
        assert producer.calls == []
    async with session_factory() as session:
        config = await session.scalar(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))
        assert config.slice_failed is False
        assert await session.scalar(select(GcodeFile).where(GcodeFile.job_id == job_id)) is None


async def test_printer_output_selects_a_converter_and_carries_the_profile_to_production(
        client, create_printer, model_uuid_of, upload_3mf, session_factory, tmp_path):
    raw = _register_converter("raw_only", ArtifactKind.GCODE)
    archive = _register_converter("archive_only", ArtifactKind.GCODE_3MF)
    pid = await create_printer(machine_profile_override=None)
    raw_pid = await create_printer(printer_type="elegoo_centauri", machine_profile_override="Raw profile")
    model = await model_uuid_of(pid)
    archive_url = f"{ENDPOINT}/routes/model_3mf/gcode_3mf"
    raw_url = f"{ENDPOINT}/routes/model_3mf/gcode"
    assert (await client.put(archive_url, json={"provider_plugin_id": "raw_only"})).status_code == 422
    assert (await client.put(archive_url, json={"provider_plugin_id": "archive_only"})).status_code == 200
    assert (await client.put(raw_url, json={"provider_plugin_id": "raw_only"})).status_code == 200
    mapped = await client.post(f"/api/v1/printer-models/{model}/slicer-profiles", json={
        "provider_plugin_id": "archive_only", "machine_profile": "P1S archive", "nozzle_diameter_mm": 0.4,
    })
    assert mapped.status_code == 201, mapped.text
    source = tmp_path / "part.3mf"
    source.write_bytes(b"fake model")
    svc = SlicerService(str(tmp_path))

    async with session_factory() as session:
        for printer_id, converter, kind, profile, suffix in (
            (pid, archive, ArtifactKind.GCODE_3MF, "P1S archive", ".gcode.3mf"),
            (raw_pid, raw, ArtifactKind.GCODE, "Raw profile", ".gcode"),
        ):
            printer = await session.get(Printer, printer_id)
            resolution = await slicer_profiles.resolve_for_printer(session, printer, source_filename=source.name)
            decision = await resolve_slicing(PrinterView.of(printer, resolution), source.name)
            assert not decision.blocked, decision.message
            params = _slice_params(None, None, printer, None, resolution.profile)
            req, _ = build_slice_request(printer_id, printer_id, 1, params, None, decision.provider_id)
            req.source_3mf = str(source)
            result = svc.slice(req, tmp_path / str(printer_id))
            assert Path(result).name.endswith(suffix)
            assert converter.calls[-1].want is kind
            assert converter.calls[-1].input_kind is InputKind.MODEL_3MF
            assert converter.calls[-1].target.machine_profile == profile
            assert converter.calls[-1].target.model_uuid == printer.model_uuid

    file_id = await upload_3mf()
    response = await client.get(f"/api/v1/printers/{pid}/profiles", params={"file_id": file_id})
    assert response.json()["print_profiles"] == ["Standard"]

    wrong = tmp_path / "wrong.gcode"
    wrong.write_text("; raw output\n")
    request = SliceRequest(job_id=1, source_3mf=str(source), plate_number=1, machine_preset="P1S archive",
                           process_preset="Standard", filament_presets=[], provider_id="archive_only",
                           output_kind=ArtifactKind.GCODE_3MF)
    with patch.object(archive, "produce", return_value=Artifact(wrong, ArtifactKind.GCODE, archive.identity)):
        with pytest.raises(SliceError, match="did not return the requested .gcode.3mf"):
            svc.slice(request, tmp_path / "wrong_format")


async def test_printer_preference_overrides_model_then_brand_then_format(
        client, create_printer, model_uuid_of, session_factory):
    for plugin_id in ("route_plugin", "brand_plugin", "model_plugin", "printer_plugin"):
        _register_converter(plugin_id, ArtifactKind.GCODE_3MF)
    _register_converter("raw_only", ArtifactKind.GCODE)
    pid = await create_printer(machine_profile_override=None)
    model = await model_uuid_of(pid)
    for plugin_id, name, flow in (("route_plugin", "Route profile", "standard"),
                                  ("brand_plugin", "Brand profile", "standard"),
                                  ("model_plugin", "Model profile", "standard"),
                                  ("printer_plugin", "Printer high-flow profile", "high_flow"),
                                  ("raw_only", "Raw profile", "standard")):
        result = await client.post(f"/api/v1/printer-models/{model}/slicer-profiles", json={
            "provider_plugin_id": plugin_id, "machine_profile": name, "nozzle_diameter_mm": 0.4, "flow": flow,
        })
        assert result.status_code == 201, result.text

    async def selected():
        async with session_factory() as session:
            printer = await session.get(Printer, pid)
            profile = await slicer_profiles.resolve_for_printer(session, printer, source_filename="part.stl")
            return profile, await resolve_slicing(PrinterView.of(printer, profile), "part.stl")

    route = f"{ENDPOINT}/routes/stl/gcode_3mf"
    assert (await client.put(route, json={"provider_plugin_id": "route_plugin"})).status_code == 200
    assert (await selected())[1].provider_id == "route_plugin"
    brand = f"{ENDPOINT}/brands/bambu"
    assert (await client.put(brand, json={"provider_plugin_id": "brand_plugin"})).status_code == 200
    assert (await selected())[1].provider_id == "brand_plugin"
    assert (await client.patch(f"/api/v1/printer-models/{model}", json={"default_slicer_plugin_id": "model_plugin"})).status_code == 200
    assert (await selected())[1].provider_id == "model_plugin"

    printer_url = f"/api/v1/printers/{pid}"
    assert (await client.patch(printer_url, json={
        "preferred_slicer_plugin_id": "printer_plugin", "nozzle_variant": {"diameter_mm": 0.4, "flow": "high_flow"},
    })).status_code == 200
    profile, decision = await selected()
    assert (profile.profile, decision.provider_id, decision.blocked) == ("Printer high-flow profile", "printer_plugin", False)
    assert (await client.patch(printer_url, json={"preferred_slicer_plugin_id": "raw_only", "nozzle_variant": None})).status_code == 200
    _, decision = await selected()
    assert decision.blocked and decision.outcome.reason is Reason.PREFERENCE_INELIGIBLE
    assert ".gcode.3mf" in decision.message

    assert (await client.patch(printer_url, json={"preferred_slicer_plugin_id": None})).status_code == 200
    assert (await selected())[1].provider_id == "model_plugin"
    persisted = (await client.get(ENDPOINT)).json()
    assert next(b for b in persisted["brands"] if b["id"] == "bambu")["provider_plugin_id"] == "brand_plugin"
    assert next(r for r in persisted["routes"] if r["input_kind"] == "stl" and r["output_kind"] == "gcode_3mf")["provider_plugin_id"] == "route_plugin"
