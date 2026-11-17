from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer
from app.services.queue_engine import QueueEngine, _slice_params
from app.services.slicer_service import SlicerService
from tests.services.test_queue_engine import _make_mock_printer_manager


async def test_target_overrides_survive_materialization_and_win_only_for_their_target(client,upload_3mf,create_printer,session_factory):
    a,b = await create_printer(),await create_printer(name="B")
    fid = await upload_3mf()
    r = await client.post("/api/v1/jobs",json={"uploaded_file_id":fid,"overrides":{"wall_loops":"2","layer_height":"0.2"},
        "printer_configs":[{"printer_id":a,"print_profile":"Standard","overrides":{"wall_loops":4}}],
        "model_targets":[{"model_uuid":None,"print_profile":"Standard","overrides":{"layer_height":0.1}}]})
    assert r.status_code == 201,r.text
    async with session_factory() as s:
        job = await s.get(Job,r.json()["id"])
        configs = {c.printer_id:c for c in (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job.id))).scalars()}
        assert configs[a].overrides == {"wall_loops":"4"}
        assert configs[b].overrides == {"layer_height":"0.1"}
        for pid,expected in ((a,{"wall_loops":"4","layer_height":"0.2"}),(b,{"wall_loops":"2","layer_height":"0.1"})):
            params = _slice_params(job,configs[pid],await s.get(Printer,pid),None,machine_preset="Machine")
            assert params["overrides"] == expected


@pytest.mark.parametrize("busy,strict,winner",[(False,False,"preferred"),(True,False,"fallback"),(True,True,None)])
async def test_queue_preference_prefers_ready_and_falls_back_only_when_allowed(
        client,upload_3mf,create_printer,session_factory,busy,strict,winner):
    fallback = await create_printer(name="Fallback",printer_type="elegoo_centauri")
    preferred = await create_printer(name="Preferred",printer_type="elegoo_centauri")
    model = (await client.get(f"/api/v1/printers/{preferred}")).json()["model_uuid"]
    fid = await upload_3mf(filename="plate.gcode",data=b"G28\n")
    await client.put(f"/api/v1/files/{fid}/eligibility",json={"model_uuids":[model]})
    r = await client.post("/api/v1/jobs",json={"uploaded_file_id":fid,
        "printer_configs":[{"printer_id":fallback},{"printer_id":preferred}],
        "printer_preference":{"entries":[preferred],"strict":strict}})
    assert r.status_code == 201,r.text
    jid = r.json()["id"]
    manager = _make_mock_printer_manager([fallback,preferred])
    manager.is_printer_ready.side_effect = lambda pid:not(busy and pid == preferred)
    engine = QueueEngine(session_factory,manager,MagicMock(spec=SlicerService))
    engine._run_slice_and_print = AsyncMock()
    engine._broadcast_job = AsyncMock()
    async with session_factory() as s:
        await engine._try_claim_for_printer(s,fallback)
        if not busy:
            await engine._try_claim_for_printer(s,preferred)
        job = await s.get(Job,jid)
        expected = preferred if winner == "preferred" else fallback if winner else None
        assert job.assigned_printer_id == expected
    await engine.cancel_job_tasks()


async def test_preference_never_adds_a_printer_outside_targets(client,upload_3mf,create_printer,session_factory):
    target,preferred = await create_printer(),await create_printer(name="Preferred")
    r = await client.post("/api/v1/jobs",json={"uploaded_file_id":await upload_3mf(),
        "printer_configs":[{"printer_id":target,"print_profile":"Standard"}],
        "printer_preference":{"entries":[preferred],"strict":True}})
    assert r.status_code == 201,r.text
    async with session_factory() as s:
        assert list((await s.execute(select(JobPrinterConfig.printer_id).where(JobPrinterConfig.job_id == r.json()["id"]))).scalars()) == [target]


async def test_unknown_and_presliced_target_overrides_are_rejected(client,upload_3mf,create_printer):
    pid = await create_printer(printer_type="elegoo_centauri")
    fid = await upload_3mf()
    r = await client.post("/api/v1/jobs",json={"uploaded_file_id":fid,"printer_configs":[{"printer_id":pid,"overrides":{"unknown":1}}]})
    assert r.status_code == 422
    fid = await upload_3mf(filename="plate.gcode",data=b"G28\n")
    r = await client.post("/api/v1/jobs",json={"uploaded_file_id":fid,"printer_configs":[{"printer_id":pid,"overrides":{"wall_loops":3}}]})
    assert r.status_code == 422


async def test_session_can_report_key_label_without_exposing_the_key(client):
    plain = (await client.get("/api/v1/auth/me")).json()
    assert "key_name" not in plain
    identity = (await client.get("/api/v1/auth/me?include_identity=true")).json()
    assert identity["key_name"] == "test-fixture" and identity["role"] == "staff"
    assert client.headers["X-Api-Key"] not in str(identity)
