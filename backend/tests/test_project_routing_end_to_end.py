"""Project generation -> print routing across every layer: generate a project whose pack has two plates (3 + 1 parts), print both on
two mock printers through the real engine, review them, and generate only what is still missing."""
from sqlalchemy import select

from app.models import Job, ProjectItem
from app.services.printer_manager import printer_manager
from tests.pack_fixtures import auto_pack, packed_3mf
from tests.fake_providers import fake_packer
from tests.generate_helpers import library_env, make_project, quantities_of
from tests.test_workflow_end_to_end import farm, run_cycle  # noqa: F401  (the `farm` fixture)

from unittest.mock import patch


async def _mock_printer(client, name: str) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": name, "printer_type": "mock", "connection_config": {},
        "orca_printer_profiles": ["Mock Machine"], "machine_profile_override": "Mock Machine",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _jobs(session_factory, project_id):
    async with session_factory() as s:
        return (await s.execute(select(Job).where(Job.project_id == project_id).order_by(Job.id))).scalars().all()


async def test_a_failed_part_is_reprinted_by_generating_only_what_remains(client, farm, session_factory, tmp_path):
    p1, p2 = await _mock_printer(client, "Mock 1"), await _mock_printer(client, "Mock 2")
    project_id, (item,) = await make_project(client, tmp_path, [("a.stl", 4, {})])
    packer = fake_packer(packed_3mf([["a__m0"] * 3, ["a__m0"]]))

    with library_env(tmp_path):
        # ── generate: two plates, each job knows what it holds ───────────────────────────────────────────────
        with patch("app.api.routes.projects.get_slicing_provider", return_value=packer):
            resp = await client.post(f"/api/v1/projects/{project_id}/generate",
                                     json={"eligible_printer_ids": [p1, p2], "process_preset": "0.20mm"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["files"][0]["plate_contents"] == "exact"
        jobs = await _jobs(session_factory, project_id)
        assert [quantities_of(j) for j in jobs] == [{item: 3}, {item: 1}]
        big, small = jobs
        assert (await client.get(f"/api/v1/projects/{project_id}")).json()["stage"] == "queued"

        # ── the engine prints both plates, one per printer ───────────────────────────────────────────────────
        await run_cycle(farm.engine)
        await run_cycle(farm.engine)
        statuses = {j.id: j.status for j in await _jobs(session_factory, project_id)}
        assert statuses == {big.id: "printing", small.id: "printing"}
        for printer_id in (p1, p2):
            client_ = printer_manager.get_client(printer_id)
            assert client_.is_printing is True
            client_.stop_print()
            await printer_manager.on_print_complete(printer_id, None)
        assert {j.id: j.status for j in await _jobs(session_factory, project_id)} == {big.id: "complete", small.id: "complete"}

        # nothing is credited until someone reviews the plates; and nothing is left to generate meanwhile
        assert (await client.get(f"/api/v1/projects/{project_id}")).json()["items"][0]["quantity_completed"] == 0
        with patch("app.api.routes.projects.get_slicing_provider", return_value=packer):
            idle = await client.post(f"/api/v1/projects/{project_id}/generate",
                                     json={"eligible_printer_ids": [p1, p2], "quantities": "remaining"})
        assert idle.status_code == 422 and "Nothing left" in idle.json()["detail"]

        # ── review: one of the three parts on the big plate came out bad ─────────────────────────────────────
        assert (await client.put(f"/api/v1/jobs/{big.id}/outcome", json={
            "failures": [{"project_item_id": item, "quantity_failed": 1}]})).status_code == 200
        assert (await client.put(f"/api/v1/jobs/{small.id}/outcome", json={"failures": []})).status_code == 200
        async with session_factory() as s:
            row = await s.get(ProjectItem, item)
            assert (row.quantity_completed, row.quantity_failed) == (3, 1)

        # ── generate what is missing: exactly the one failed part ────────────────────────────────────────────
        reprint = fake_packer(b"")
        reprint.pack_models.side_effect = auto_pack(3)
        with patch("app.api.routes.projects.get_slicing_provider", return_value=reprint):
            again = await client.post(f"/api/v1/projects/{project_id}/generate",
                                      json={"eligible_printer_ids": [p1, p2], "process_preset": "0.20mm", "quantities": "remaining"})
        assert again.status_code == 200, again.text

    (call,) = reprint.pack_models.call_args_list
    assert (len(call.args[0]), call.kwargs["counts"], call.kwargs["labels"]) == (1, [1], ["a__m0"])
    new_jobs = [j for j in await _jobs(session_factory, project_id) if j.id not in (big.id, small.id)]
    assert [(quantities_of(j), j.status) for j in new_jobs] == [({item: 1}, "queued")]
    assert [j["id"] for j in again.json()["jobs"]] == [new_jobs[0].id]
