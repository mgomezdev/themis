"""POST /projects/{id}/generate: the jobs it creates carry the parts their plate REALLY holds (read back from the pack), a part the
packer could not place stops generation, and `quantities: "remaining"` queues only what is still missing."""
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models import Job, PrinterModelRecord, Printer, ProjectItem, UploadedFile
from tests.fake_providers import fake_packer
from tests.generate_helpers import library_env as _library, make_project as _make_project
from tests.pack_fixtures import auto_pack, packed_3mf, png_only_3mf

PETG = {"filament_type": "PETG", "filament_subtype": "any"}


async def _generate(client, tmp_path, provider, project_id, **body):
    with _library(tmp_path), patch("app.api.routes.projects.get_slicing_provider", return_value=provider):
        return await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": [], **body})


def _packer(pack) -> object:
    """A fake provider whose pack_models returns `pack` (bytes), or calls it (a side effect) / walks a list of results."""
    provider = fake_packer(b"")
    if callable(pack):
        provider.pack_models.side_effect = pack
    elif isinstance(pack, list):
        provider.pack_models.side_effect = pack
    else:
        provider.pack_models.return_value = pack
    return provider


async def _jobs(session_factory, project_id) -> list[tuple[int, int, dict[int, int], str]]:
    import json
    async with session_factory() as s:
        rows = (await s.execute(select(Job).where(Job.project_id == project_id).order_by(Job.id))).scalars().all()
        return [(j.id, j.plate_number, {int(k): v for k, v in json.loads(j.project_item_quantities).items()}, j.status) for j in rows]


async def _set_job_status(session_factory, job_id, status):
    async with session_factory() as s:
        (await s.get(Job, job_id)).status = status
        await s.commit()


def _asked(provider, call=-1):
    """(number of paths, counts, labels) of one pack_models call."""
    c = provider.pack_models.call_args_list[call]
    return len(c.args[0]), c.kwargs["counts"], c.kwargs["labels"]


# ---- 1-5: reading the layout back --------------------------------------------------------------------------------------

async def test_each_job_carries_the_exact_parts_on_its_plate(client, tmp_path, session_factory):
    project_id, (a, b) = await _make_project(client, tmp_path, [("a.stl", 5, {}), ("b.stl", 2, {})])
    provider = _packer(packed_3mf([["a__m0"] * 3 + ["b__m1"] * 2, ["a__m0"] * 2]))

    resp = await _generate(client, tmp_path, provider, project_id)

    assert resp.status_code == 200, resp.text
    assert [(p, q) for _, p, q, _ in await _jobs(session_factory, project_id)] == [(1, {a: 3, b: 2}), (2, {a: 2})]
    (file_out,) = resp.json()["files"]
    assert file_out["plate_contents"] == "exact" and file_out["plate_count"] == 2
    (call,) = provider.pack_models.call_args_list
    assert len(call.args[0]) == 2                        # one path per item, whatever its quantity
    assert call.kwargs["counts"] == [5, 2]
    assert [l.endswith(s) for l, s in zip(call.kwargs["labels"], ("__m0", "__m1"))] == [True, True]
    assert call.kwargs["labels"] == ["a__m0", "b__m1"]


async def test_a_plate_with_no_parts_gets_no_job(client, tmp_path, session_factory):
    project_id, (a,) = await _make_project(client, tmp_path, [("a.stl", 2, {})])
    provider = _packer(packed_3mf([["a__m0"], [], ["a__m0"]]))

    resp = await _generate(client, tmp_path, provider, project_id)

    assert resp.status_code == 200, resp.text
    assert [(p, q) for _, p, q, _ in await _jobs(session_factory, project_id)] == [(1, {a: 1}), (3, {a: 1})]
    assert [j["plate_number"] for j in resp.json()["jobs"]] == [1, 3]


async def test_a_part_the_packer_could_not_place_is_a_422_that_queues_and_stores_nothing(client, tmp_path, session_factory):
    project_id, _ = await _make_project(client, tmp_path, [("big.stl", 3, {})])
    provider = _packer(packed_3mf([["big__m0"]]))        # 1 of 3 placed

    resp = await _generate(client, tmp_path, provider, project_id)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "2 x big.stl" in detail and "256 x 256 mm" in detail
    assert await _jobs(session_factory, project_id) == []
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(UploadedFile).where(UploadedFile.pack_recipe_hash.is_not(None)))).scalar() == 0
    packs = tmp_path / "library" / "Job Pack 3MFs"
    assert not packs.exists() or [p.name for p in packs.iterdir() if p.name.startswith("_tmp_")] == []


async def test_an_unplaced_part_names_the_bed_that_was_packed_for(client, tmp_path, create_printer):
    printer = await create_printer(bed_x_mm=180, bed_y_mm=220)
    project_id, _ = await _make_project(client, tmp_path, [("big.stl", 1, {})])

    resp = await _generate(client, tmp_path, _packer(packed_3mf([[]])), project_id, eligible_printer_ids=[printer])

    assert resp.status_code == 422 and "180 x 220 mm" in resp.json()["detail"]


async def test_a_pack_without_a_readable_layout_estimates_an_even_split(client, tmp_path, session_factory):
    project_id, (a,) = await _make_project(client, tmp_path, [("a.stl", 7, {})])

    resp = await _generate(client, tmp_path, _packer(png_only_3mf(3)), project_id)

    assert resp.status_code == 200, resp.text
    assert [q for _, _, q, _ in await _jobs(session_factory, project_id)] == [{a: 3}, {a: 2}, {a: 2}]
    assert resp.json()["files"][0]["plate_contents"] == "estimated"


async def test_filament_groups_pack_separately_and_a_failing_second_group_leaves_the_first_groups_jobs(client, tmp_path, session_factory):
    """Documents current partial behaviour: groups are committed one by one, so group 2's 422 does not undo group 1."""
    project_id, (a, b, c) = await _make_project(client, tmp_path, [("a.stl", 2, {}), ("b.stl", 1, PETG), ("c.stl", 2, PETG)])
    provider = _packer([
        packed_3mf([["a__m0", "a__m0"]]),                   # group 1 (no filament ask): a
        packed_3mf([["b__m0", "c__m1"]]),                   # group 2 (PETG): b + c, but one c is missing
    ])

    resp = await _generate(client, tmp_path, provider, project_id)

    assert resp.status_code == 422 and "1 x c.stl" in resp.json()["detail"]
    assert provider.pack_models.call_count == 2
    assert _asked(provider, 0) == (1, [2], ["a__m0"])
    assert _asked(provider, 1) == (2, [1, 2], ["b__m0", "c__m1"])      # indices restart per group
    assert [(p, q) for _, p, q, _ in await _jobs(session_factory, project_id)] == [(1, {a: 2})]


async def test_two_filament_groups_make_two_files_whose_jobs_only_hold_their_own_items(client, tmp_path, session_factory):
    project_id, (a, b) = await _make_project(client, tmp_path, [("a.stl", 2, {}), ("b.stl", 3, PETG)])
    provider = _packer([packed_3mf([["a__m0", "a__m0"]]), packed_3mf([["b__m0"] * 2, ["b__m0"]])])

    resp = await _generate(client, tmp_path, provider, project_id)

    assert resp.status_code == 200, resp.text
    assert provider.pack_models.call_count == 2
    body = resp.json()
    assert len(body["files"]) == 2 and {f["plate_contents"] for f in body["files"]} == {"exact"}
    jobs = await _jobs(session_factory, project_id)
    assert [q for _, _, q, _ in jobs] == [{a: 2}, {b: 2}, {b: 1}]
    first_file, second_file = body["files"][0]["id"], body["files"][1]["id"]
    assert [j["uploaded_file_id"] for j in body["jobs"]] == [first_file, second_file, second_file]


# ---- 6: quantities="remaining" -----------------------------------------------------------------------------------------

async def test_remaining_with_everything_queued_is_nothing_left(client, tmp_path, session_factory):
    project_id, (a, b) = await _make_project(client, tmp_path, [("a.stl", 4, {}), ("b.stl", 2, {})])
    provider = _packer(auto_pack(3))
    assert (await _generate(client, tmp_path, provider, project_id)).status_code == 200
    before = await _jobs(session_factory, project_id)
    assert [q for _, _, q, _ in before] == [{a: 3}, {a: 1, b: 2}]

    resp = await _generate(client, tmp_path, provider, project_id, quantities="remaining")

    assert resp.status_code == 422 and "Nothing left" in resp.json()["detail"]
    assert provider.pack_models.call_count == 1
    assert await _jobs(session_factory, project_id) == before


@pytest.mark.parametrize("dead_status", ["failed", "cancelled"])
async def test_remaining_packs_again_only_the_parts_of_a_dead_plate(client, tmp_path, session_factory, dead_status):
    project_id, (a, b) = await _make_project(client, tmp_path, [("a.stl", 4, {}), ("b.stl", 2, {})])
    provider = _packer(auto_pack(3))
    await _generate(client, tmp_path, provider, project_id)
    first, second = [j[0] for j in await _jobs(session_factory, project_id)]      # {a:3} and {a:1, b:2}
    await _set_job_status(session_factory, second, dead_status)

    resp = await _generate(client, tmp_path, provider, project_id, quantities="remaining")

    assert resp.status_code == 200, resp.text
    assert _asked(provider) == (2, [1, 2], ["a__m0", "b__m1"])
    jobs = await _jobs(session_factory, project_id)
    assert [(j[0], j[2], j[3]) for j in jobs[:2]] == [(first, {a: 3}, "queued"), (second, {a: 1, b: 2}, dead_status)]
    assert [(q, st) for _, _, q, st in jobs[2:]] == [({a: 1, b: 2}, "queued")]


async def test_remaining_after_a_reviewed_plate_with_a_failure_packs_exactly_the_failed_part(client, tmp_path, session_factory):
    project_id, (a,) = await _make_project(client, tmp_path, [("a.stl", 3, {})])
    provider = _packer(auto_pack(3))
    await _generate(client, tmp_path, provider, project_id)
    (job_id, *_), = await _jobs(session_factory, project_id)
    await _set_job_status(session_factory, job_id, "complete")
    review = await client.put(f"/api/v1/jobs/{job_id}/outcome", json={"failures": [{"project_item_id": a, "quantity_failed": 1}]})
    assert review.status_code == 200, review.text

    resp = await _generate(client, tmp_path, provider, project_id, quantities="remaining")

    assert resp.status_code == 200, resp.text
    assert _asked(provider) == (1, [1], ["a__m0"])
    assert [q for _, _, q, _ in await _jobs(session_factory, project_id)] == [{a: 3}, {a: 1}]
    async with session_factory() as s:
        item = await s.get(ProjectItem, a)
        assert (item.quantity_completed, item.quantity_failed) == (2, 1)


async def test_remaining_counts_a_finished_but_unreviewed_plate_as_pending(client, tmp_path, session_factory):
    project_id, (a,) = await _make_project(client, tmp_path, [("a.stl", 3, {})])
    provider = _packer(auto_pack(3))
    await _generate(client, tmp_path, provider, project_id)
    (job_id, *_), = await _jobs(session_factory, project_id)
    await _set_job_status(session_factory, job_id, "complete")

    resp = await _generate(client, tmp_path, provider, project_id, quantities="remaining")

    assert resp.status_code == 422 and "Nothing left" in resp.json()["detail"]
    assert provider.pack_models.call_count == 1
    assert len(await _jobs(session_factory, project_id)) == 1
    async with session_factory() as s:
        assert (await s.get(ProjectItem, a)).quantity_completed == 0      # not credited either: nobody reviewed it


async def test_remaining_after_raising_an_items_quantity_packs_only_the_difference(client, tmp_path, session_factory):
    project_id, (a,) = await _make_project(client, tmp_path, [("a.stl", 3, {})])
    provider = _packer(auto_pack(3))
    await _generate(client, tmp_path, provider, project_id)
    assert (await client.put(f"/api/v1/projects/{project_id}/items/{a}", json={"quantity": 5})).status_code == 200

    resp = await _generate(client, tmp_path, provider, project_id, quantities="remaining")

    assert resp.status_code == 200, resp.text
    assert _asked(provider) == (1, [2], ["a__m0"])
    assert [q for _, _, q, _ in await _jobs(session_factory, project_id)] == [{a: 3}, {a: 2}]


async def test_remaining_leaves_a_satisfied_item_out_of_the_pack_and_relabels_from_zero(client, tmp_path, session_factory):
    project_id, (a, b) = await _make_project(client, tmp_path, [("a.stl", 2, {}), ("b.stl", 3, {})])
    async with session_factory() as s:
        (await s.get(ProjectItem, a)).quantity_completed = 2        # item a is done
        await s.commit()
    provider = _packer(auto_pack(10))

    resp = await _generate(client, tmp_path, provider, project_id, quantities="remaining")

    assert resp.status_code == 200, resp.text
    assert _asked(provider) == (1, [3], ["b__m0"])
    assert Path(provider.pack_models.call_args.args[0][0]).name == "b.stl"
    assert [q for _, _, q, _ in await _jobs(session_factory, project_id)] == [{b: 3}]


async def test_the_default_still_packs_full_quantities_on_a_second_call(client, tmp_path, session_factory):
    project_id, (a, b) = await _make_project(client, tmp_path, [("a.stl", 4, {}), ("b.stl", 2, {})])
    provider = _packer(auto_pack(3))
    await _generate(client, tmp_path, provider, project_id)

    resp = await _generate(client, tmp_path, provider, project_id, allow_cached=False)

    assert resp.status_code == 200, resp.text
    assert provider.pack_models.call_count == 2
    assert _asked(provider, 0) == _asked(provider, 1) == (2, [4, 2], ["a__m0", "b__m1"])
    assert len(await _jobs(session_factory, project_id)) == 4


async def test_remaining_ignores_a_job_with_unparseable_part_counts(client, tmp_path, session_factory):
    project_id, (a,) = await _make_project(client, tmp_path, [("a.stl", 2, {})])
    provider = _packer(auto_pack(3))
    await _generate(client, tmp_path, provider, project_id)
    (job_id, *_), = await _jobs(session_factory, project_id)
    async with session_factory() as s:
        (await s.get(Job, job_id)).project_item_quantities = "not json"
        await s.commit()

    resp = await _generate(client, tmp_path, provider, project_id, quantities="remaining")

    assert resp.status_code == 200, resp.text
    assert _asked(provider) == (1, [2], ["a__m0"])


# ---- 7: which bed is packed for ----------------------------------------------------------------------------------------

async def test_the_pack_bed_is_the_smallest_of_each_axis_over_the_eligible_printers(client, tmp_path, create_printer):
    p1 = await create_printer(name="wide", bed_x_mm=300, bed_y_mm=200)
    p2 = await create_printer(name="tall", bed_x_mm=220, bed_y_mm=250)
    project_id, _ = await _make_project(client, tmp_path, [("a.stl", 1, {})])
    provider = _packer(auto_pack(5))

    resp = await _generate(client, tmp_path, provider, project_id, eligible_printer_ids=[p1, p2])

    assert resp.status_code == 200, resp.text
    assert provider.pack_models.call_args.kwargs["bed"] == (220.0, 200.0, 250.0)
    assert (resp.json()["pack_bed_x"], resp.json()["pack_bed_y"]) == (220.0, 200.0)


async def test_a_model_registry_bed_smaller_than_every_printer_wins(client, tmp_path, create_printer, model_uuid_of, session_factory):
    printer = await create_printer(name="big", bed_x_mm=400, bed_y_mm=400)
    model = await model_uuid_of(printer)
    async with session_factory() as s:
        record = await s.get(PrinterModelRecord, model)
        record.bed_x_mm, record.bed_y_mm = 180.0, 190.0
        await s.commit()
    project_id, _ = await _make_project(client, tmp_path, [("a.stl", 1, {})])
    provider = _packer(auto_pack(5))

    resp = await _generate(client, tmp_path, provider, project_id, eligible_model_uuids=[model])

    assert resp.status_code == 200, resp.text
    assert provider.pack_models.call_args.kwargs["bed"] == (180.0, 190.0, 250.0)
    assert (resp.json()["pack_bed_x"], resp.json()["pack_bed_y"]) == (180.0, 190.0)


@pytest.mark.parametrize("bad_bed", [(0.0, 0.0), (0.0, 100.0), (100.0, 0.0)])
async def test_a_printer_with_an_empty_bed_dimension_is_ignored(client, tmp_path, create_printer, session_factory, bad_bed):
    good = await create_printer(name="good", bed_x_mm=300, bed_y_mm=200)
    odd = await create_printer(name="odd", bed_x_mm=250, bed_y_mm=250)
    async with session_factory() as s:
        printer = await s.get(Printer, odd)
        printer.bed_x_mm, printer.bed_y_mm = bad_bed
        await s.commit()
    project_id, _ = await _make_project(client, tmp_path, [("a.stl", 1, {})])
    provider = _packer(auto_pack(5))

    resp = await _generate(client, tmp_path, provider, project_id, eligible_printer_ids=[good, odd])

    assert resp.status_code == 200, resp.text
    assert provider.pack_models.call_args.kwargs["bed"] == (300.0, 200.0, 250.0)


async def test_with_no_usable_bed_at_all_the_default_256_is_used(client, tmp_path, create_printer, session_factory):
    odd = await create_printer(name="odd")
    async with session_factory() as s:
        (await s.get(Printer, odd)).bed_x_mm = 0.0
        await s.commit()
    project_id, _ = await _make_project(client, tmp_path, [("a.stl", 1, {})])
    provider = _packer(auto_pack(5))

    resp = await _generate(client, tmp_path, provider, project_id, eligible_printer_ids=[odd])

    assert resp.status_code == 200, resp.text
    assert provider.pack_models.call_args.kwargs["bed"] == (256.0, 256.0, 250.0)


# ---- 8-10: validation, reuse, stage ------------------------------------------------------------------------------------

async def test_an_unknown_eligible_printer_is_a_422_and_nothing_is_packed(client, tmp_path, create_printer, session_factory):
    real = await create_printer()
    project_id, _ = await _make_project(client, tmp_path, [("a.stl", 1, {})])
    provider = _packer(auto_pack(5))

    resp = await _generate(client, tmp_path, provider, project_id, eligible_printer_ids=[real, 9999])

    assert resp.status_code == 422 and "9999" in resp.json()["detail"]
    provider.pack_models.assert_not_called()
    assert await _jobs(session_factory, project_id) == []


async def test_an_identical_second_generate_reuses_the_pack_and_still_reads_exact_quantities(client, tmp_path, session_factory):
    project_id, (a, b) = await _make_project(client, tmp_path, [("a.stl", 5, {}), ("b.stl", 2, {})])
    provider = _packer(packed_3mf([["a__m0"] * 3 + ["b__m1"] * 2, ["a__m0"] * 2]))
    first = await _generate(client, tmp_path, provider, project_id)
    assert first.status_code == 200 and first.json()["files"][0]["pack_reused"] is False

    second = await _generate(client, tmp_path, provider, project_id)

    assert second.status_code == 200, second.text
    assert provider.pack_models.call_count == 1
    (file_out,) = second.json()["files"]
    assert file_out["pack_reused"] is True and file_out["plate_contents"] == "exact"
    assert file_out["id"] == first.json()["files"][0]["id"]
    assert [q for _, _, q, _ in await _jobs(session_factory, project_id)] == [{a: 3, b: 2}, {a: 2}, {a: 3, b: 2}, {a: 2}]


async def test_generating_for_a_draft_project_is_a_409(client, tmp_path, session_factory):
    project_id, _ = await _make_project(client, tmp_path, [("a.stl", 1, {})], stage="draft")
    provider = _packer(auto_pack(5))

    resp = await _generate(client, tmp_path, provider, project_id)

    assert resp.status_code == 409
    provider.pack_models.assert_not_called()
    assert await _jobs(session_factory, project_id) == []
