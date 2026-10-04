"""Job-creation guard: detect overrides in an uploaded 3MF that the chosen
presets would change, so the user can decide (change preset/printer or accept).

We replace the uploaded global ``project_settings.config`` with the chosen
presets, so any *global* setting the file customized that the chosen preset
changes is "lost". We surface only a curated set of high-impact intent keys
(not 600 noisy diffs). Per-object/modifier/paint overrides are preserved and not
flagged; slot/extruder capacity is checked separately. See the
``slicer-cli-architecture`` memory.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

# High-impact process "intent" settings worth surfacing when they change.
CURATED_KEYS: tuple[str, ...] = (
    "enable_support",
    "support_type",
    "support_threshold_angle",
    "support_on_build_plate_only",
    "raft_layers",
    "brim_type",
    "brim_width",
    "sparse_infill_density",
    "sparse_infill_pattern",
    "wall_loops",
    "top_shell_layers",
    "bottom_shell_layers",
    "layer_height",
    "ironing_type",
)

_PROJECT = "metadata/project_settings.config"
_MODEL = "metadata/model_settings.config"


def _read_embedded(source_3mf: Path) -> tuple[dict | None, str | None]:
    project = model = None
    try:
        with zipfile.ZipFile(source_3mf) as z:
            for n in z.namelist():
                low = n.lower()
                if low == _PROJECT:
                    project = json.loads(z.read(n))
                elif low == _MODEL:
                    model = z.read(n).decode("utf-8", "ignore")
    except (zipfile.BadZipFile, OSError):
        pass
    return project, model


def _max_extruder_index(model_settings_xml: str | None) -> int:
    if not model_settings_xml:
        return 0
    idxs = [int(m) for m in re.findall(r'key="extruder"\s+value="(\d+)"', model_settings_xml)]
    return max(idxs) if idxs else 0


def inspect_overrides(source_3mf: str | Path, generated_config: dict, printer_slots: int) -> dict:
    """Compare an uploaded 3MF's embedded settings against the config Themis will
    apply. Returns findings for the New Job alert; empty findings → submit clean.
    """
    source_3mf = Path(source_3mf)
    embedded, model_xml = _read_embedded(source_3mf)

    _s = lambda v: ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
    setting_changes: list[dict] = []
    if embedded:
        for key in CURATED_KEYS:
            if key not in embedded or key not in generated_config:
                continue
            was, now = _s(embedded[key]), _s(generated_config[key])
            if was != now:
                setting_changes.append({"key": key, "from": was, "to": now})

    # Slot/extruder capacity: the file may assign parts to slots the printer lacks.
    used_slots = _max_extruder_index(model_xml)
    slot_warning = None
    if used_slots > printer_slots:
        slot_warning = {"used_slots": used_slots, "printer_slots": printer_slots}

    return {
        "has_embedded_settings": embedded is not None,
        "setting_changes": setting_changes,
        "slot_warning": slot_warning,
        "has_findings": bool(setting_changes or slot_warning),
    }
