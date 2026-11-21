from unittest.mock import MagicMock
import pytest
from app.models import Printer, JobPrinterConfig
from app.services import filament_matching
from app.services.printer_topology import check_assignment


async def test_nozzle_or_slot_change_rejects_prepared_assignment(session_factory, create_job, create_printer):
    printer_id = await create_printer()
    job_id = await create_job(printer_id=printer_id)
    from sqlalchemy import select
    async with session_factory() as s:
        printer = await s.get(Printer, printer_id)
        printer.topology_revision = 2
        config = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
        config.topology_revision = 1
        await s.commit()
        with pytest.raises(ValueError, match='topology changed'):
            await check_assignment(s, job_id, printer_id)


def test_empty_printer_cannot_satisfy_undefined_filament_request():
    printer = MagicMock(single_colour_only=False, topology={'tools': [{'id':'a','nozzle_mm':.4}], 'positions':[{'id':'a'}], 'swapping':False}, topology_error=None, loaded_filaments=[{'slot':0, 'empty':True}])
    config = MagicMock(filament_map=None, tool_index=None, ask_manufacturer=None, ask_type=None, ask_subtype=None, ask_color=None)
    assert not filament_matching.evaluate_for_printer(config, printer).eligible


async def test_dispatch_requires_a_valid_declared_topology(session_factory, create_job, create_printer):
    pid = await create_printer()
    jid = await create_job(printer_id=pid)
    async with session_factory() as s:
        row = await s.get(Printer, pid)
        row.topology = None
        await s.commit()
        with pytest.raises(ValueError, match='topology'):
            await check_assignment(s, jid, pid)


def test_missing_topology_is_not_reported_as_an_eligible_printer():
    from types import SimpleNamespace
    p = SimpleNamespace(name='Unknown', topology=None, loaded_filaments=[], single_colour_only=False)
    c = SimpleNamespace(filament_map=None, tool_index=None, ask_type=None, ask_subtype=None, ask_manufacturer=None, ask_color=None)
    verdict = filament_matching.evaluate_for_printer(c, p)
    assert not verdict.eligible and any('topology' in reason.lower() for reason in verdict.reasons)
