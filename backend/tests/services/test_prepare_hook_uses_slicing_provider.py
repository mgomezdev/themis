"""The queue asks for slice-side tool mapping only when the printer's client needs it (BIZ-295: the request carries
`tool_index`/`filament_map`; the producer applies them through the slicing provider, never the printer client)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.slicing_env import slicing_provider

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


async def _run(db, tmp_path, *, flag, tool_index=None, filament_map=None, ask_slot=None):
    job_id = await _seed_job(db, 1)
    async with db() as s:
        printer = await s.get(Printer, 1)
        printer.machine_profile_override = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""} for _ in range(3)]   # T0..T2 (a pinned tool must exist)
        cfg = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
        cfg.tool_index = tool_index
        cfg.filament_map = filament_map
        if ask_slot is not None:                                        # only this slot is PETG: the (unpinned) ask can only match it
            printer.loaded_filaments = [{**f, "type": "PETG" if i == ask_slot else "PLA"} for i, f in enumerate(printer.loaded_filaments)]
            cfg.ask_type, cfg.ask_subtype = "PETG", "any"
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
        seen["mapping"] = (req.tool_index, req.filament_map)
        return str(gcode)

    engine = QueueEngine(db, mgr, slicer)
    _install_fake_put(engine)
    with patch.object(slicer, "slice", side_effect=fake_slice), \
         slicing_provider(provider), \
         patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock):
        await engine._run_slice_and_print(job_id, 1, 1)
    assert "mapping" in seen, "slice was never reached"
    return seen["mapping"], provider, client


@pytest.mark.asyncio
async def test_a_pinned_tool_is_requested_from_the_slicer_not_the_client(session_factory, tmp_path):
    mapping, provider, client = await _run(session_factory, tmp_path, flag=True, tool_index=2)
    assert mapping == (2, None)
    client.remap_sliceable_3mf.assert_not_called()


@pytest.mark.asyncio
async def test_a_filament_map_is_requested_from_the_slicer_not_the_client(session_factory, tmp_path):
    fmap = [{"model_filament": 1, "tool_index": 2}]
    mapping, provider, client = await _run(session_factory, tmp_path, flag=True, filament_map=fmap)
    assert mapping == (None, fmap)
    client.remap_sliceable_3mf.assert_not_called()


@pytest.mark.asyncio
async def test_no_mapping_when_neither_set(session_factory, tmp_path):
    mapping, _, _ = await _run(session_factory, tmp_path, flag=True)
    assert mapping == (None, None)


@pytest.mark.asyncio
async def test_no_mapping_when_client_flag_false(session_factory, tmp_path):
    mapping, _, _ = await _run(session_factory, tmp_path, flag=False, tool_index=2)
    assert mapping == (None, None)


@pytest.mark.asyncio
async def test_an_unpinned_ask_matched_to_a_later_tool_routes_the_model_there(session_factory, tmp_path):
    """The claim chose T2 for the ask; the model must be sliced onto T2 (and deducted from T2), not left on the default tool."""
    mapping, _, _ = await _run(session_factory, tmp_path, flag=True, ask_slot=2)
    assert mapping == (2, None)
