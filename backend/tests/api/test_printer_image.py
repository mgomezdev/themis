"""A printer's picture comes from its plugin's package via the neutral /image route (no vendor named in core)."""
import dataclasses
import os

import pytest

from app.plugins import _REGISTRY, plugin_root, registered_plugins
from app.plugins.host import plugin_host
from app.services.printer_images import printer_image_path
from tests.printer_ips import ip_config

PNG = b"\x89PNG\r\n\x1a\n"


def _declared():
    return [(m.id, model.id, model.image) for m in registered_plugins() for mfr in m.manufacturers for model in mfr.models if model.image]


def test_bundled_printer_plugins_ship_a_png_for_every_model():
    declared = _declared()
    assert {(p, m) for p, m, _ in declared} >= {("bambu", "p1s"), ("bambu", "x1c"), ("bambu", "h2d"), ("snapmaker", "u1_extended"),
                                                ("elegoo_centauri", "centauri")}
    for plugin_id, model_id, _ in declared:
        path = printer_image_path(plugin_id, model_id)
        assert path is not None and path.read_bytes().startswith(PNG), (plugin_id, model_id)


async def _printer(client, **extra):
    r = await client.post("/api/v1/printers", json={"name": "P", "printer_type": "bambu", "connection_config": ip_config(), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_image_route_serves_the_plugins_png(client):
    pid = await _printer(client)
    r = await client.get(f"/api/v1/printers/{pid}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(PNG)
    assert r.content == printer_image_path("bambu", "p1s").read_bytes()


async def test_image_route_404_for_unknown_printer(client):
    assert (await client.get("/api/v1/printers/999999/image")).status_code == 404


async def test_image_route_404_when_model_has_no_declared_image(client, monkeypatch):
    manifest = _REGISTRY["bambu"]
    bare = dataclasses.replace(manifest, manufacturers=tuple(
        dataclasses.replace(mf, models=tuple(dataclasses.replace(m, image=None) for m in mf.models)) for mf in manifest.manufacturers))
    pid = await _printer(client)
    monkeypatch.setitem(_REGISTRY, "bambu", bare)
    assert (await client.get(f"/api/v1/printers/{pid}/image")).status_code == 404


async def test_image_route_404_when_plugin_disabled(client, monkeypatch):
    pid = await _printer(client)
    monkeypatch.setattr(plugin_host, "is_enabled", lambda plugin_id: False)
    assert (await client.get(f"/api/v1/printers/{pid}/image")).status_code == 404


def _with_image(monkeypatch, image):
    manifest = _REGISTRY["bambu"]
    changed = dataclasses.replace(manifest, manufacturers=tuple(
        dataclasses.replace(mf, models=tuple(dataclasses.replace(m, image=image) for m in mf.models)) for mf in manifest.manufacturers))
    monkeypatch.setitem(_REGISTRY, "bambu", changed)


@pytest.mark.parametrize("bad", ["../../../../etc/passwd", "images/x.txt", "/etc/hosts", "images/missing.png"])
def test_declared_path_must_be_an_existing_png(monkeypatch, bad):
    _with_image(monkeypatch, bad)
    assert printer_image_path("bambu", "p1s") is None


def test_png_outside_the_plugin_package_is_refused(monkeypatch, tmp_path):
    """Only the containment check stops these: each is a real .png that exists."""
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    root = plugin_root("bambu")
    link = root / "images" / "_escape_test.png"
    link.symlink_to(outside)
    try:
        for image in (str(outside), os.path.relpath(outside, root), "images/_escape_test.png"):
            _with_image(monkeypatch, image)
            assert printer_image_path("bambu", "p1s") is None, image
    finally:
        link.unlink()
