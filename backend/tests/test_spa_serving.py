"""Serving the built React app: deep links fall back to index.html, and nothing outside the dist directory
is ever readable through the catch-all route (path traversal, absolute paths, symlinks)."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import _resolve_within, register_spa

INDEX = "<html>themis</html>"
NO_CACHE = "no-cache, must-revalidate"


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "app.js").write_text("console.log('app')")
    (root / "favicon.ico").write_bytes(b"ico-bytes")
    (root / "folder").mkdir()
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    return root


@pytest.fixture
async def spa(dist):
    app = FastAPI()
    register_spa(app, dist)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_root_and_client_side_routes_get_index_html_that_must_be_revalidated(spa):
    for path in ("/", "/queue", "/projects/5/edit"):
        resp = await spa.get(path)
        assert (resp.status_code, resp.text) == (200, INDEX), path
        assert resp.headers["cache-control"] == NO_CACHE, path


async def test_real_files_are_served_as_is(spa):
    resp = await spa.get("/favicon.ico")
    assert (resp.status_code, resp.content) == (200, b"ico-bytes")
    resp = await spa.get("/assets/app.js")
    assert (resp.status_code, resp.text) == (200, "console.log('app')")


async def test_a_missing_asset_is_a_404_not_the_app_shell(spa):
    assert (await spa.get("/assets/missing.js")).status_code == 404


async def test_a_directory_path_falls_back_to_index_html(spa):
    resp = await spa.get("/folder")
    assert resp.text == INDEX


@pytest.mark.parametrize("path", ["/%2e%2e/secret.txt", "/%2e%2e/%2e%2e/etc/passwd"])
async def test_dot_dot_traversal_never_leaks_files_outside_dist(spa, path):
    resp = await spa.get(path)
    assert "TOP SECRET" not in resp.text and "root:" not in resp.text
    assert resp.text == INDEX


async def test_dot_dot_traversal_under_the_assets_mount_never_leaks_either(spa):
    resp = await spa.get("/assets/%2e%2e/%2e%2e/secret.txt")  # handled by Starlette's StaticFiles, not our route
    assert "TOP SECRET" not in resp.text
    assert resp.status_code == 404


async def test_an_absolute_path_never_leaks_files_outside_dist(spa, tmp_path):
    resp = await spa.get(f"//{(tmp_path / 'secret.txt').as_posix().lstrip('/')}")
    assert "TOP SECRET" not in resp.text
    assert resp.text == INDEX


# ---------------------------------------------------------------------------
# _resolve_within directly
# ---------------------------------------------------------------------------

def test_resolve_within_accepts_files_and_the_root_itself(dist):
    assert _resolve_within(dist, "favicon.ico") == (dist / "favicon.ico").resolve()
    assert _resolve_within(dist, "assets/app.js") == (dist / "assets" / "app.js").resolve()
    assert _resolve_within(dist, "") == dist.resolve()


@pytest.mark.parametrize("attempt", ["../secret.txt", "assets/../../secret.txt", "/etc/passwd", "a/../../.."])
def test_resolve_within_rejects_anything_that_escapes_the_root(dist, attempt):
    assert _resolve_within(dist, attempt) is None


def test_resolve_within_rejects_a_symlink_that_points_outside(dist, tmp_path):
    (dist / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert _resolve_within(dist, "link.txt") is None


def test_resolve_within_rejects_a_nul_byte_path(dist):
    assert _resolve_within(dist, "bad\x00name") is None


def test_resolve_within_serves_files_when_the_root_itself_is_reached_through_a_symlink(dist, tmp_path):
    (tmp_path / "current").symlink_to(dist)  # e.g. a deploy pointing 'current' at a release directory

    assert _resolve_within(tmp_path / "current", "favicon.ico") == (dist / "favicon.ico").resolve()
    assert _resolve_within(tmp_path / "current", "../secret.txt") is None
