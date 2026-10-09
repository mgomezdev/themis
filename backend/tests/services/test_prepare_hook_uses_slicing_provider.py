"""The queue's prepare_hook routes tool mapping through the active slicing provider, gated by the client flag."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import JobPrinterConfig
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import QueueEngine
from app.services.slicer_service import SlicerService
from tests.fake_providers import FakeSlicingProvider
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager, _seed_job
from sqlalchemy import select
from app.models import Printer


class RecordingProvider(FakeSlicingProvider):
    TOOL_MAPPING = True

    def apply_tool_mapping(self, source_3mf, *, tool_index=None, filament_map=None):
        self.calls.append(("apply_tool_mapping", Path(source_3mf), tool_index, filament_map))


async def _run(db, tmp_path, *, flag, tool_index=None, filament_map=None):
    job_id = await _seed_job(db, 1)
    async with db() as s:
        printer = await s.get(Printer, 1)
        printer.current_orca_printer_profile = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""}]
        cfg = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
        cfg.tool_index = tool_index
        cfg.filament_map = filament_map
        await s.commit()

    mgr = _make_mock_printer_manager([1])
    client = mgr.get_client.return_value
    client.slice_tool_mapping = flag
    provider = RecordingProvider()
    gcode = tmp_path / "o.gcode"
    gcode.write_text("; filament used [g] = 1.0\n")
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = tmp_path
    seen = {}

    def fake_slice(req, *a, **kw):
        seen["hook"] = req.prepare_hook
        if req.prepare_hook:
            req.prepare_hook(tmp_path / "copy.3mf")
        return str(gcode)

    engine = QueueEngine(db, mgr, slicer)
    _install_fake_put(engine)
    with patch.object(slicer, "slice", side_effect=fake_slice), \
         patch("app.services.queue_engine.get_slicing_provider", return_value=provider), \
         patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock):
        await engine._run_slice_and_print(job_id, 1, 1)
    assert "hook" in seen, "slice was never reached"
    return seen["hook"], provider, client


def _applied(provider):
    return [c for c in provider.calls if c[0] == "apply_tool_mapping"]


@pytest.mark.asyncio
async def test_tool_index_hook_calls_provider_not_client(session_factory, tmp_path):
    hook, provider, client = await _run(session_factory, tmp_path, flag=True, tool_index=2)
    assert hook is not None
    assert _applied(provider) == [("apply_tool_mapping", tmp_path / "copy.3mf", 2, None)]
    client.remap_sliceable_3mf.assert_not_called()


@pytest.mark.asyncio
async def test_filament_map_hook_calls_provider_not_client(session_factory, tmp_path):
    fmap = [{"model_filament": 1, "tool_index": 2}]
    hook, provider, client = await _run(session_factory, tmp_path, flag=True, filament_map=fmap)
    assert hook is not None
    calls = _applied(provider)
    assert len(calls) == 1 and calls[0][2] is None and calls[0][3] == fmap
    client.remap_sliceable_3mf.assert_not_called()


@pytest.mark.asyncio
async def test_no_hook_when_neither_set(session_factory, tmp_path):
    hook, provider, _ = await _run(session_factory, tmp_path, flag=True)
    assert hook is None and _applied(provider) == []


@pytest.mark.asyncio
async def test_no_hook_when_client_flag_false(session_factory, tmp_path):
    hook, provider, _ = await _run(session_factory, tmp_path, flag=False, tool_index=2)
    assert hook is None and _applied(provider) == []
