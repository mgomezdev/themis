# backend/tests/services/test_slicer_service.py
import struct
import zipfile as _zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.slicer_service import SlicerService, SliceRequest, SliceError


def _req(tmp_path, export_args=None, **kw):
    src = tmp_path / "model.3mf"
    src.write_bytes(b"dummy")
    return SliceRequest(
        job_id=1, source_3mf=str(src), plate_number=1,
        machine_preset="Elegoo Centauri Carbon", process_preset="0.20mm Standard",
        filament_presets=["Generic PLA"], filament_colours=["#FFFFFF"],
        export_args=export_args or [],
        **kw,
    )


def _make_service(tmp_path, catalog=None):
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    svc._catalog_cache = catalog or {
        "machine": [{"name": "Elegoo Centauri Carbon", "uuid": "m1"}],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [{"name": "Generic PLA", "uuid": "f1"}],
    }
    svc._catalog_ts = float("inf")  # never re-fetch
    return svc


# ── error paths ────────────────────────────────────────────────────────────────

def test_raises_when_no_sidecar_url(tmp_path):
    svc = _make_service(tmp_path)
    with patch("app.config.get_orca_sidecar_url", return_value=None):
        with pytest.raises(SliceError, match="ORCA_SIDECAR_URL"):
            svc.slice(_req(tmp_path))


def test_raises_when_machine_not_in_catalog(tmp_path):
    svc = _make_service(tmp_path, catalog={
        "machine": [],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [{"name": "Generic PLA", "uuid": "f1"}],
    })
    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"):
        with pytest.raises(SliceError, match="not found in Orca sidecar catalog"):
            svc.slice(_req(tmp_path))


def test_raises_when_filament_not_in_catalog(tmp_path):
    svc = _make_service(tmp_path, catalog={
        "machine": [{"name": "Elegoo Centauri Carbon", "uuid": "m1"}],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [],
    })
    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"):
        with pytest.raises(SliceError, match="not found in Orca sidecar catalog"):
            svc.slice(_req(tmp_path))


def test_raises_with_clear_message_when_sidecar_unreachable(tmp_path):
    """Catalog fetch failure surfaces 'unreachable', not a misleading profile-not-found message."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    svc._catalog_cache = None
    svc._catalog_ts = 0.0  # force a cache refresh attempt

    from app.services import orca_sidecar_client as _mod
    mock_client = MagicMock()
    mock_client.get_catalog.side_effect = _mod.SidecarError("Connection refused")
    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"), \
         patch.object(_mod, "OrcaSidecarClient", return_value=mock_client):
        with pytest.raises(SliceError, match="Orca sidecar unreachable"):
            svc.slice(_req(tmp_path))


def test_sidecar_error_converted_to_slice_error(tmp_path):
    svc = _make_service(tmp_path)
    from app.services import orca_sidecar_client as _mod
    mock_client = MagicMock()
    mock_client.slice_start.side_effect = _mod.SidecarError("timeout")
    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"), \
         patch.object(_mod, "OrcaSidecarClient", return_value=mock_client):
        with pytest.raises(SliceError, match="timeout"):
            svc.slice(_req(tmp_path))


# ── happy paths ────────────────────────────────────────────────────────────────

def test_default_returns_raw_gcode(tmp_path):
    svc = _make_service(tmp_path)
    gcode = tmp_path / "gcode" / "1" / "plate_1.gcode"
    gcode.parent.mkdir(parents=True, exist_ok=True)
    gcode.write_text("G28\n")

    from app.services import orca_sidecar_client as _mod
    mock_client = MagicMock()
    mock_client.slice_start.return_value = "job-1"
    mock_client.poll_status.return_value = {"status": "completed", "sliced_file": "plate_1.gcode"}
    mock_client.download.return_value = gcode

    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"), \
         patch.object(_mod, "OrcaSidecarClient", return_value=mock_client):
        path = svc.slice(_req(tmp_path))

    assert path == str(gcode)
    mock_client.slice_start.assert_called_once()
    call_kw = mock_client.slice_start.call_args[1]
    assert call_kw.get("export_3mf") is False


def test_export_3mf_flag_forwarded(tmp_path):
    svc = _make_service(tmp_path)
    archive = tmp_path / "gcode" / "1" / "model.gcode.3mf"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"PK")

    from app.services import orca_sidecar_client as _mod
    mock_client = MagicMock()
    mock_client.slice_start.return_value = "job-1"
    mock_client.poll_status.return_value = {"status": "completed", "sliced_file": "model.gcode.3mf"}
    mock_client.download.return_value = archive

    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"), \
         patch.object(_mod, "OrcaSidecarClient", return_value=mock_client):
        path = svc.slice(_req(tmp_path, export_args=["--export-3mf", "model.gcode.3mf"]))

    assert path.endswith("model.gcode.3mf")
    call_kw = mock_client.slice_start.call_args[1]
    assert call_kw.get("export_3mf") is True


def test_extra_config_passed_to_client(tmp_path):
    svc = _make_service(tmp_path)
    gcode = tmp_path / "gcode" / "1" / "plate_1.gcode"
    gcode.parent.mkdir(parents=True, exist_ok=True)
    gcode.write_text("G28\n")

    from app.services import orca_sidecar_client as _mod
    mock_client = MagicMock()
    mock_client.slice_start.return_value = "job-1"
    mock_client.poll_status.return_value = {"status": "completed", "sliced_file": "plate_1.gcode"}
    mock_client.download.return_value = gcode

    overrides = {"curr_bed_type": "textured_plate", "layer_height": "0.15"}
    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"), \
         patch.object(_mod, "OrcaSidecarClient", return_value=mock_client):
        svc.slice(_req(tmp_path, extra_config=overrides))

    call_kw = mock_client.slice_start.call_args[1]
    assert call_kw.get("extra_config") == overrides


def test_slice_calls_inject_thumbnail_for_3mf_source(tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, plate=1)
    gcode = tmp_path / "gcode" / "1" / "plate_1.gcode"
    gcode.parent.mkdir(parents=True, exist_ok=True)
    gcode.write_text("G28\n")

    from app.services import orca_sidecar_client as _mod
    mock_client = MagicMock()
    mock_client.slice_start.return_value = "job-1"
    mock_client.poll_status.return_value = {"status": "completed", "sliced_file": "plate_1.gcode"}
    mock_client.download.return_value = gcode

    req = _req(tmp_path)
    req.source_3mf = str(three_mf)

    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"), \
         patch.object(_mod, "OrcaSidecarClient", return_value=mock_client), \
         patch.object(svc, "_inject_thumbnail") as mock_inject:
        svc.slice(req)

    mock_inject.assert_called_once()
    args = mock_inject.call_args[0]
    assert args[0].endswith(".gcode")
    assert args[1] == str(three_mf)
    assert args[2] == 1


# ── _inject_thumbnail ──────────────────────────────────────────────────────────

def _png(width: int = 64, height: int = 64) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + b"\x00\x00\x00\x00"
    iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
    return sig + ihdr + iend


def _3mf_with_thumb(tmp_path, *, plate: int | None = 1, name: str | None = None) -> Path:
    path = tmp_path / "model.3mf"
    entry = name or f"plate_{plate}.png"
    with _zipfile.ZipFile(path, "w") as z:
        z.writestr(f"Metadata/{entry}", _png())
    return path


def test_inject_thumbnail_prepends_header_to_gcode(tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, plate=1)
    gcode = tmp_path / "out.gcode"
    gcode.write_text("G28\nG1 X0\n")

    svc._inject_thumbnail(str(gcode), str(three_mf), plate_number=1)

    content = gcode.read_text()
    assert content.startswith("; thumbnail begin 64x64 ")
    assert "; thumbnail end" in content
    assert "G28" in content


def test_inject_thumbnail_falls_back_to_thumbnail_png(tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, name="thumbnail.png")
    gcode = tmp_path / "out.gcode"
    gcode.write_text("G28\n")

    svc._inject_thumbnail(str(gcode), str(three_mf), plate_number=1)

    assert "; thumbnail begin" in gcode.read_text()


def test_inject_thumbnail_falls_back_to_preview_png(tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, name="preview.png")
    gcode = tmp_path / "out.gcode"
    gcode.write_text("G28\n")

    svc._inject_thumbnail(str(gcode), str(three_mf), plate_number=1)

    assert "; thumbnail begin" in gcode.read_text()


def test_inject_thumbnail_noop_when_no_thumbnail_in_zip(tmp_path):
    svc = _make_service(tmp_path)
    path = tmp_path / "model.3mf"
    with _zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/model_settings.config", "<config/>")
    gcode = tmp_path / "out.gcode"
    original = "G28\nG1 X0\n"
    gcode.write_text(original)

    svc._inject_thumbnail(str(gcode), str(path), plate_number=1)

    assert gcode.read_text() == original


def test_inject_thumbnail_noop_for_invalid_png_magic(tmp_path):
    svc = _make_service(tmp_path)
    path = tmp_path / "model.3mf"
    with _zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/plate_1.png", b"not-a-png-at-all")
    gcode = tmp_path / "out.gcode"
    original = "G28\n"
    gcode.write_text(original)

    svc._inject_thumbnail(str(gcode), str(path), plate_number=1)

    assert gcode.read_text() == original
