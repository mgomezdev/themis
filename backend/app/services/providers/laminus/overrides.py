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

from ..contract import CuratedSetting, OverrideInspection, SettingChange, SlotWarning

# High-impact process "intent" settings worth surfacing when they change, and the only keys a job or printer may override.
_BOOL = ("0", "1")
CURATED_SETTINGS: tuple[CuratedSetting, ...] = (
    CuratedSetting("enable_support", "Enable supports", "bool", _BOOL),
    CuratedSetting("support_type", "Support type", "enum", ("normal(auto)", "tree(auto)", "normal(manual)", "tree(manual)")),
    CuratedSetting("support_threshold_angle", "Support threshold angle"),
    CuratedSetting("support_on_build_plate_only", "Support on build plate only", "bool", _BOOL),
    CuratedSetting("raft_layers", "Raft layers"),
    CuratedSetting("brim_type", "Brim type", "enum",
                   ("auto_brim", "brim_ears", "painted", "outer_only", "inner_only", "outer_and_inner", "no_brim")),
    CuratedSetting("brim_width", "Brim width"),
    CuratedSetting("sparse_infill_density", "Infill density"),
    CuratedSetting("sparse_infill_pattern", "Infill pattern"),
    CuratedSetting("wall_loops", "Wall loops"),
    CuratedSetting("top_shell_layers", "Top layers"),
    CuratedSetting("bottom_shell_layers", "Bottom layers"),
    CuratedSetting("layer_height", "Layer height"),
    CuratedSetting("ironing_type", "Ironing type", "enum", ("no ironing", "top", "topmost", "solid")),
)
CURATED_KEYS: tuple[str, ...] = tuple(c.key for c in CURATED_SETTINGS)

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


# Preset bookkeeping that names/links the file's own preset: never carried onto another printer's preset.
_NOT_CARRIED = frozenset({
    "inherits", "name", "from", "version", "print_settings_id", "setting_id", "base_id", "instantiation", "type",
    "compatible_printers", "compatible_printers_condition", "print_compatible_printers", "is_custom_defined",
    "different_settings_to_system", "renamed_from", "description",
})


def embedded_process_overrides(source_3mf: str | Path) -> dict:
    """The process settings the 3MF's author changed from its system preset, with the file's values.

    Orca/Bambu record what each preset changed in ``different_settings_to_system``: one ``;``-joined key list per
    preset — index 0 the process (print) preset, then one per filament, the printer last. Only the process entry is
    carried: filament and printer diffs are specific to the material/machine the file was made for, so they don't
    belong on another printer's preset. Keys the file doesn't actually store (or preset bookkeeping) are skipped.
    """
    embedded, _ = _read_embedded(Path(source_3mf))
    if not isinstance(embedded, dict):
        return {}
    diffs = embedded.get("different_settings_to_system")
    if isinstance(diffs, str):
        diffs = [diffs]
    if not isinstance(diffs, list) or not diffs or not isinstance(diffs[0], str):
        return {}
    keys = [k.strip() for k in diffs[0].split(";")]
    return {k: embedded[k] for k in dict.fromkeys(keys) if k and k not in _NOT_CARRIED and k in embedded}


def _max_extruder_index(model_settings_xml: str | None) -> int:
    if not model_settings_xml:
        return 0
    idxs = [int(m) for m in re.findall(r'key="extruder"\s+value="(\d+)"', model_settings_xml)]
    return max(idxs) if idxs else 0


def inspect_overrides(source_3mf: str | Path, generated_config: dict, printer_slots: int) -> OverrideInspection:
    """Compare an uploaded 3MF's embedded settings against the config Themis will
    apply. Returns findings for the New Job alert; empty findings → submit clean.
    """
    source_3mf = Path(source_3mf)
    embedded, model_xml = _read_embedded(source_3mf)

    _s = lambda v: ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
    setting_changes: list[SettingChange] = []
    if embedded:
        for key in CURATED_KEYS:
            if key not in embedded or key not in generated_config:
                continue
            was, now = _s(embedded[key]), _s(generated_config[key])
            if was != now:
                setting_changes.append(SettingChange(key, was, now))

    # Slot/extruder capacity: the file may assign parts to slots the printer lacks.
    used_slots = _max_extruder_index(model_xml)
    slot_warning = None
    if used_slots > printer_slots:
        slot_warning = SlotWarning(used_slots, printer_slots)

    return OverrideInspection(
        has_embedded_settings=embedded is not None,
        setting_changes=tuple(setting_changes),
        slot_warning=slot_warning,
    )
