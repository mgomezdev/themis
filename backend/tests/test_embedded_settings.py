import io
import json
import zipfile
import pytest
from app.services.three_mf_parser import parse_embedded_settings


def _make_3mf(settings: dict, tmp_path) -> str:
    """Return path to a temp 3MF zip with the given project_settings.config."""
    path = tmp_path / "test.3mf"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
    path.write_bytes(buf.getvalue())
    return str(path)


def test_returns_empty_for_non_3mf(tmp_path):
    f = tmp_path / "model.stl"
    f.write_bytes(b"solid model\nendsolid")
    assert parse_embedded_settings(str(f)) == []


def test_returns_empty_when_no_curated_keys(tmp_path):
    path = _make_3mf({"some_other_key": "value"}, tmp_path)
    result = parse_embedded_settings(path)
    assert result == []


def test_returns_curated_keys_present_in_file(tmp_path):
    path = _make_3mf({"sparse_infill_pattern": "grid", "layer_height": "0.15", "some_ignored": "x"}, tmp_path)
    result = parse_embedded_settings(path)
    keys = {r["key"] for r in result}
    assert "sparse_infill_pattern" in keys
    assert "layer_height" in keys
    assert "some_ignored" not in keys
    # Check structure
    fp = next(r for r in result if r["key"] == "sparse_infill_pattern")
    assert fp["value"] == "grid"
    assert "label" in fp  # human-readable label present


def test_list_values_joined_as_string(tmp_path):
    path = _make_3mf({"enable_support": ["1"]}, tmp_path)
    result = parse_embedded_settings(path)
    assert result[0]["value"] == "1"


def test_returns_empty_for_bad_zip(tmp_path):
    f = tmp_path / "bad.3mf"
    f.write_bytes(b"not a zip file")
    assert parse_embedded_settings(str(f)) == []


# ---------- endpoint tests ----------

from httpx import AsyncClient


async def test_embedded_settings_endpoint_404(client: AsyncClient):
    resp = await client.get("/api/v1/files/9999/embedded-settings")
    assert resp.status_code == 404
