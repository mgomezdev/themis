"""Assemble a sliceable 3MF: keep the uploaded model's geometry and model-attached
overrides, swap in Themis's generated project config.

Per the override-handling design, only the global ``project_settings.config`` is
replaced. ``model_settings.config`` (per-object overrides, modifiers, support
enforcers/blockers, per-part extruder map) and the mesh paint inside ``3D/*`` are
preserved so they re-layer onto the new presets. ``slice_info.config`` is dropped
(Orca regenerates it). See the ``slicer-cli-architecture`` memory.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

# Embedded files Themis owns / regenerates — everything else is copied verbatim.
_REPLACED = "metadata/project_settings.config"
_DROPPED = ("metadata/slice_info.config",)


def build_sliceable_3mf(source_3mf: str | Path, project_config: dict, out_path: str | Path) -> Path:
    """Copy ``source_3mf`` preserving geometry + ``model_settings.config`` + paint,
    replacing ``project_settings.config`` with ``project_config``."""
    source_3mf, out_path = Path(source_3mf), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    config_bytes = json.dumps(project_config).encode("utf-8")

    with zipfile.ZipFile(source_3mf) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        names = {n.lower() for n in zin.namelist()}
        for item in zin.namelist():
            low = item.lower()
            if low == _REPLACED or low in _DROPPED:
                continue
            zout.writestr(item, zin.read(item))
        zout.writestr("Metadata/project_settings.config", config_bytes)
    return out_path


def source_has_project_settings(source_3mf: str | Path) -> bool:
    """True if the uploaded 3MF carries embedded slicer settings (a real project,
    not a bare geometry 3MF). Used to decide whether the override check runs."""
    try:
        with zipfile.ZipFile(source_3mf) as z:
            return any(n.lower() == _REPLACED for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
