"""BIZ-359: per-plate project-item counts come from the pack's manifest; round-robin only when there is none or it doesn't add up."""
from types import SimpleNamespace

from app.api.routes.projects import _plate_item_quantities, _round_robin_quantities
from app.services.providers.contract import PlateManifest


def _item(id_, file_id, qty):
    return SimpleNamespace(id=id_, file_id=file_id, quantity=qty)


# two library files; items 11 and 12 share file 1 (summed into one pack input), item 13 is file 2
ITEMS = [_item(11, 1, 2), _item(12, 1, 3), _item(13, 2, 4)]
WANTED = [("a.stl", 5, "1"), ("b.stl", 4, "2")]


def test_manifest_counts_are_split_over_items_of_one_file_in_item_order():
    m = PlateManifest(plates={1: {0: 4}, 2: {0: 1, 1: 4}})
    assert _plate_item_quantities(m, WANTED, ITEMS, [1, 2]) == [
        {"11": 2, "12": 2, "13": 0},
        {"11": 0, "12": 1, "13": 4},
    ]


def test_no_manifest_means_none():
    assert _plate_item_quantities(None, WANTED, ITEMS, [1, 2]) is None


def test_totals_that_do_not_match_the_request_are_rejected():
    short = PlateManifest(plates={1: {0: 4}, 2: {1: 4}})          # input 0 placed 4 of 5
    assert _plate_item_quantities(short, WANTED, ITEMS, [1, 2]) is None


def test_plates_that_differ_from_the_file_are_rejected():
    m = PlateManifest(plates={1: {0: 5, 1: 4}})
    assert _plate_item_quantities(m, WANTED, ITEMS, [1, 2]) is None                         # the file has plate 2 too
    assert _plate_item_quantities(m, WANTED, ITEMS, [1]) == [{"11": 2, "12": 3, "13": 4}]


def test_an_unknown_input_index_is_rejected():
    m = PlateManifest(plates={1: {0: 5, 1: 4, 2: 1}})
    assert _plate_item_quantities(m, WANTED, ITEMS, [1]) is None


def test_round_robin_fallback_is_the_previous_guess():
    assert _round_robin_quantities(ITEMS, 2) == [{"11": 1, "12": 2, "13": 2}, {"11": 1, "12": 1, "13": 2}]


# ---- through POST /projects/{id}/generate ---------------------------------------------------------------------------------

import io
import json
import zipfile
from unittest.mock import patch

from app.models import Job
from tests.fake_providers import FakeProjectConstructor

_STL = b"solid x\nendsolid x\n"


def _two_plate_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in (1, 2):
            zf.writestr(f"Metadata/plate_{i}.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


_MANIFEST_MARK = "Metadata/test_manifest.json"


def _two_plate_3mf_with_manifest() -> bytes:
    """Plate 1 holds only the first input, plate 2 only the second (a mix round-robin gets wrong), recorded IN the file."""
    buf = io.BytesIO(_two_plate_3mf())
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr(_MANIFEST_MARK, json.dumps({"1": {"0": 3}, "2": {"1": 2}}))
    return buf.getvalue()


class _ManifestPacker(FakeProjectConstructor):
    """Reads its manifest out of the file it is given, so a reused pack only gets counts by reading the stored file."""
    def plate_manifest(self, project_3mf):
        with zipfile.ZipFile(project_3mf) as zf:
            if _MANIFEST_MARK not in zf.namelist():
                return None
            raw = json.loads(zf.read(_MANIFEST_MARK))
        return PlateManifest(plates={int(p): {int(i): n for i, n in c.items()} for p, c in raw.items()})


class _BrokenManifestPacker(FakeProjectConstructor):
    def plate_manifest(self, project_3mf):
        raise RuntimeError("third-party bug")


async def _generate(client, project_id, tmp_path, packer, **body):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"process_preset": "0.20mm Standard", **body})
    assert resp.status_code == 200, resp.text
    _generate.last = resp.json()
    return sorted(resp.json()["jobs"], key=lambda j: j["plate_number"])


async def _quantities(session_factory, job_ids):
    async with session_factory() as s:
        return [json.loads((await s.get(Job, j)).project_item_quantities) for j in job_ids]


async def test_generate_stores_the_manifest_counts_and_progress_follows_them(client, upload_3mf, session_factory, tmp_path):
    project_id = (await client.post("/api/v1/projects", json={"name": "Mix"})).json()["id"]
    items = []
    for name, qty in (("a.stl", 3), ("b.stl", 2)):
        fid = await upload_3mf(filename=name, data=f"solid {name}\nendsolid\n".encode())   # distinct bytes: the library dedups
        items.append((await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": fid, "quantity": qty})).json()["id"])
    a, b = items

    jobs = await _generate(client, project_id, tmp_path, _ManifestPacker(output=_two_plate_3mf_with_manifest()))
    assert await _quantities(session_factory, [j["id"] for j in jobs]) == [{str(a): 3, str(b): 0}, {str(a): 0, str(b): 2}]

    async with session_factory() as s:                                  # plate 1 printed; report it all good
        (await s.get(Job, jobs[0]["id"])).status = "complete"
        await s.commit()
    assert (await client.put(f"/api/v1/jobs/{jobs[0]['id']}/outcome", json={"failures": []})).status_code == 200
    counts = {i["id"]: i["quantity_completed"] for i in (await client.get(f"/api/v1/projects/{project_id}")).json()["items"]}
    assert counts == {a: 3, b: 0}

    # A reused pack (same recipe, allow_cached) reads the manifest from the stored file: same real counts.
    again = await _generate(client, project_id, tmp_path, _ManifestPacker(output=b"unused"), allow_cached=True)
    assert _generate.last["files"][0]["pack_reused"] is True
    assert await _quantities(session_factory, [j["id"] for j in again]) == [{str(a): 3, str(b): 0}, {str(a): 0, str(b): 2}]


async def test_generate_without_a_manifest_keeps_the_round_robin_guess(client, upload_3mf, session_factory, tmp_path):
    project_id = (await client.post("/api/v1/projects", json={"name": "Old"})).json()["id"]
    fid = await upload_3mf(filename="a.stl", data=_STL)
    item = (await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": fid, "quantity": 3})).json()["id"]
    jobs = await _generate(client, project_id, tmp_path, FakeProjectConstructor(output=_two_plate_3mf()))
    assert await _quantities(session_factory, [j["id"] for j in jobs]) == [{str(item): 2}, {str(item): 1}]


async def test_a_provider_whose_manifest_read_raises_falls_back_instead_of_failing(client, upload_3mf, session_factory, tmp_path):
    project_id = (await client.post("/api/v1/projects", json={"name": "Broken"})).json()["id"]
    fid = await upload_3mf(filename="a.stl", data=_STL)
    item = (await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": fid, "quantity": 3})).json()["id"]
    jobs = await _generate(client, project_id, tmp_path, _BrokenManifestPacker(output=_two_plate_3mf()))
    assert await _quantities(session_factory, [j["id"] for j in jobs]) == [{str(item): 2}, {str(item): 1}]
