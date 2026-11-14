"""Shared setup for tests that generate print jobs from a project's STL items."""
import contextlib
import json
from unittest.mock import patch


@contextlib.contextmanager
def library_env(tmp_path):
    """Point the library and file-cache directories at `tmp_path` (as `upload_3mf` does)."""
    (tmp_path / "library").mkdir(exist_ok=True)
    (tmp_path / "filecache").mkdir(exist_ok=True)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
            patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        yield


async def make_project(client, tmp_path, items, *, stage=None) -> tuple[int, list[int]]:
    """items = [(filename, quantity, {filament ask fields})] -> (project id, item ids). Each STL has its own bytes."""
    body = {"name": "Routing"} | ({"stage": stage} if stage else {})
    item_ids = []
    with library_env(tmp_path):
        project_id = (await client.post("/api/v1/projects", json=body)).json()["id"]
        for name, qty, ask in items:
            up = await client.post("/api/v1/files/upload", files={"file": (name, f"solid {name}\nendsolid".encode(), "application/octet-stream")})
            assert up.status_code == 201, up.text
            r = await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": up.json()["id"], "quantity": qty, **ask})
            assert r.status_code == 201, r.text
            item_ids.append(r.json()["id"])
    return project_id, item_ids


def quantities_of(job) -> dict[int, int]:
    return {int(k): v for k, v in json.loads(job.project_item_quantities).items()}
