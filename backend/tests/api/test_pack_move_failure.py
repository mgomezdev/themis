"""GH #261: a pack whose jobs are committed but whose folder move then fails is still reported and estimated."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Job, UploadedFile
from tests.fake_providers import FakeProjectConstructor

pytestmark = pytest.mark.usefixtures("fixed_groups")


async def _project_with_two_packs(client, upload_3mf, create_printer) -> tuple[int, int]:
    """A project whose generation makes two packs (two print groups), so a failure in the first must not stop the second."""
    printer = await create_printer(name="A")
    pid = (await client.post("/api/v1/projects", json={"name": "Moves"})).json()["id"]
    for name in ("a.stl", "b.stl"):
        fid = await upload_3mf(filename=name, data=f"solid {name}\nendsolid\n".encode())   # distinct: two separate packs
        item = (await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": 1})).json()
        if name == "b.stl":
            group = (await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "Second"})).json()
            assert (await client.put(f"/api/v1/projects/{pid}/items/{item['id']}", json={"group_id": group["id"]})).status_code == 200
    return pid, printer


async def test_a_failed_pack_folder_move_still_returns_the_queued_jobs(client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.api.routes import projects as project_routes
    pid, printer = await _project_with_two_packs(client, upload_3mf, create_printer)
    store = project_routes.get_library_store

    def store_whose_move_fails(*args, **kwargs):
        real = store(*args, **kwargs)
        if kwargs.get("prune_empty"):
            def broken_move(*_a, **_k):
                raise OSError("disk full")
            real.move = broken_move
        return real

    with patch.object(project_routes, "get_library_store", side_effect=store_whose_move_fails), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": [printer], "process_preset": "0.20mm"})
    assert r.status_code == 200, r.text
    async with session_factory() as s:
        jobs = (await s.execute(select(Job).where(Job.project_id == pid))).scalars().all()
        pack = await s.get(UploadedFile, jobs[0].uploaded_file_id)
    assert [j["id"] for j in r.json()["jobs"]] == [j.id for j in jobs] and len(jobs) == 2      # both packs processed
    assert jobs[0].uploaded_file_id != jobs[1].uploaded_file_id
    assert "/_tmp_" in pack.relative_path and (tmp_path / "library" / pack.relative_path).is_file()   # still at its staging key


async def test_a_move_whose_record_fails_is_moved_back_so_the_row_and_disk_agree(client, upload_3mf, create_printer,
                                                                               session_factory, tmp_path, monkeypatch):
    """The move succeeded but recording it failed: the file goes back to the key the row still names, and the second pack
    still runs (a rollback of the request's session would have expired everything it still uses)."""
    from app.api.routes import project_generate
    pid, printer = await _project_with_two_packs(client, upload_3mf, create_printer)
    real_sessionmaker = project_generate.async_sessionmaker
    calls = []

    def failing_side_session(*args, **kwargs):
        maker = real_sessionmaker(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:                     # the first side session is the idempotency factory; the next records a move
            class _Broken:
                async def __aenter__(self):
                    raise RuntimeError("database is locked")

                async def __aexit__(self, *exc):
                    return False
            return lambda: _Broken()
        return maker

    monkeypatch.setattr(project_generate, "async_sessionmaker", failing_side_session)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": [printer], "process_preset": "0.20mm"})
    assert r.status_code == 200, r.text
    async with session_factory() as s:
        jobs = (await s.execute(select(Job).where(Job.project_id == pid).order_by(Job.id))).scalars().all()
        packs = [await s.get(UploadedFile, j.uploaded_file_id) for j in jobs]
    assert len(jobs) == 2
    for pack in packs:                          # whichever key each row names, the file is there
        assert (tmp_path / "library" / pack.relative_path).is_file(), pack.relative_path
    assert any("/_tmp_" in p.relative_path for p in packs) and any("/_tmp_" not in p.relative_path for p in packs)
