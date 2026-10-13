"""Project generation reads its STLs and publishes the pack through the ArtifactStore (BIZ-287)."""
import hashlib
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.api.routes.projects import _pack_stored_models, _stored_plate_nums
from app.models import UploadedFile
from tests.api.test_projects_api import _make_3mf_bytes, _make_stl_bytes, _setup_project_with_stl
from tests.fake_artifact_store import InMemoryArtifactStore
from tests.fake_providers import fake_packer


def test_the_packer_gets_local_paths_of_each_distinct_stl_once_and_they_are_released():
    store = InMemoryArtifactStore()
    store.put("Models/a.stl", io.BytesIO(b"AAA"))
    store.put("Models/b.stl", io.BytesIO(b"BBB"))
    seen = {}

    def pack(paths, **opts):
        seen["paths"] = list(paths)
        seen["bytes"] = [Path(p).read_bytes() for p in paths]
        seen["opts"] = opts
        return b"packed"

    slicing = MagicMock()
    slicing.pack_models.side_effect = pack

    out = _pack_stored_models(store, slicing, ["Models/a.stl", "Models/a.stl", "Models/b.stl"], bed=(1.0, 2.0, 3.0))

    assert out == b"packed" and seen["opts"] == {"bed": (1.0, 2.0, 3.0)}
    assert seen["bytes"] == [b"AAA", b"AAA", b"BBB"]                      # quantities repeat the path
    assert seen["paths"][0] == seen["paths"][1] != seen["paths"][2]       # one materialization per distinct key
    assert len(store.materialized_dirs) == 2
    assert not any(d.exists() for d in store.materialized_dirs)           # released once the packer returned


def test_a_packer_failure_still_releases_the_materialized_stls():
    store = InMemoryArtifactStore()
    store.put("a.stl", io.BytesIO(b"AAA"))
    slicing = MagicMock()
    slicing.pack_models.side_effect = RuntimeError("boom")
    try:
        _pack_stored_models(store, slicing, ["a.stl"])
    except RuntimeError:
        pass
    assert store.materialized_dirs and not any(d.exists() for d in store.materialized_dirs)


def test_plate_numbers_of_a_stored_pack_are_read_via_materialize():
    store = InMemoryArtifactStore()
    store.put("Job Pack 3MFs/p.3mf", io.BytesIO(_make_3mf_bytes(plate_count=3)))
    assert _stored_plate_nums(store, "Job Pack 3MFs/p.3mf") == [1, 2, 3]
    assert not any(d.exists() for d in store.materialized_dirs)


async def test_generate_leaves_the_pack_only_under_its_job_folder_and_no_staging_folder(client, tmp_path):
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    lib = tmp_path / "library"
    packed = _make_3mf_bytes(plate_count=1)
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_slicing_provider", return_value=fake_packer(packed)),
    ):
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": []})
    assert resp.status_code == 200
    job_id = resp.json()["jobs"][0]["id"]

    folders = sorted(p.name for p in (lib / "Job Pack 3MFs").iterdir())
    assert folders == [str(job_id)]                       # the _tmp_ staging folder did not linger
    (pack,) = list((lib / "Job Pack 3MFs" / str(job_id)).iterdir())
    assert pack.read_bytes() == packed


async def test_generate_runs_entirely_on_the_store(client, tmp_path, session_factory):
    """STLs come from the store, the pack goes to the store under its job-ID key; no local library is consulted."""
    project_id, file_id = await _setup_project_with_stl(client, tmp_path)
    async with session_factory() as s:
        stl_key = (await s.get(UploadedFile, file_id)).relative_path
    store = InMemoryArtifactStore()
    store.put(stl_key, io.BytesIO(_make_stl_bytes()))
    packed = _make_3mf_bytes(plate_count=2)
    provider = fake_packer(packed)
    stl_seen = {}

    def pack(paths, **opts):
        stl_seen["bytes"] = [Path(p).read_bytes() for p in paths]
        return packed

    provider.pack_models.side_effect = pack
    with (
        patch("app.config.get_library_dir", return_value=tmp_path / "gone"),
        patch("app.api.routes.projects.get_library_store", lambda **kw: store),
        patch("app.api.routes.projects.refresh_content_hash", return_value=False),
        patch("app.api.routes.projects.get_slicing_provider", return_value=provider),
    ):
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": []})

    assert resp.status_code == 200, resp.text
    assert stl_seen["bytes"] == [_make_stl_bytes()]
    job_ids = [j["id"] for j in resp.json()["jobs"]]
    label = f"{job_ids[0]}-{job_ids[-1]}"
    async with session_factory() as s:
        pack_row = (await s.execute(select(UploadedFile).where(UploadedFile.pack_recipe_hash.is_not(None)))).scalar_one()
    assert pack_row.relative_path == f"Job Pack 3MFs/{label}/{pack_row.original_filename}"
    assert pack_row.folder == f"/Job Pack 3MFs/{label}"
    assert store.open(pack_row.relative_path).read() == packed
    assert [k.key for k in store.list("Job Pack 3MFs")] == [pack_row.relative_path]   # staging key gone after the move
    assert (pack_row.content_hash, pack_row.size_bytes) == (hashlib.sha256(packed).hexdigest(), len(packed))
    assert pack_row.mtime == store.stat(pack_row.relative_path).mtime
    assert [p["plate_number"] for p in pack_row.plates] == [1, 2]


async def test_a_stl_missing_from_the_store_is_a_422_naming_the_file(client, tmp_path):
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    with (
        patch("app.api.routes.projects.get_library_store", lambda **kw: InMemoryArtifactStore()),
        patch("app.api.routes.projects.get_slicing_provider", return_value=fake_packer(b"x")),
    ):
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": []})
    assert resp.status_code == 422 and "part.stl" in resp.json()["detail"] and "missing" in resp.json()["detail"]


async def test_packing_with_an_unreadable_stl_key_is_a_422_not_a_500(client, tmp_path, session_factory):
    from tests.api.test_projects_api import _setup_project_with_stl
    from tests.fake_providers import fake_packer
    project_id, file_id = await _setup_project_with_stl(client, tmp_path)
    store = InMemoryArtifactStore()
    async with session_factory() as s:
        key = (await s.get(UploadedFile, file_id)).relative_path
    store.put(key, io.BytesIO(b"stl"))
    real_materialize = store.materialize

    def vanishing(k):   # present at the check, gone when the packer reads it
        store.blobs.pop(k, None)
        return real_materialize(k)

    store.materialize = vanishing
    with patch("app.api.routes.projects.get_library_store", lambda **kw: store), \
         patch("app.api.routes.projects.refresh_content_hash", return_value=False), \
         patch("app.api.routes.projects.get_slicing_provider", return_value=fake_packer(b"x")):
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": []})
    assert resp.status_code == 422 and "model file" in resp.json()["detail"]


async def test_a_stl_row_with_an_empty_key_is_reported_missing_not_a_500(client, tmp_path, session_factory):
    from tests.api.test_projects_api import _setup_project_with_stl
    from tests.fake_providers import fake_packer
    project_id, file_id = await _setup_project_with_stl(client, tmp_path)
    async with session_factory() as s:
        (await s.get(UploadedFile, file_id)).relative_path = ""   # a legacy, never-indexed row
        await s.commit()
    with patch("app.api.routes.projects.get_slicing_provider", return_value=fake_packer(b"x")):
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": []})
    assert resp.status_code == 422 and "missing" in resp.json()["detail"]
