"""Build an OrcaSlicer ``project_settings.config`` from resolved presets.

OrcaSlicer's CLI segfaults on a config whose value *types* don't match its
schema (e.g. a key it expects as a string given as an array). Rather than
reconstruct the schema by hand, we start from a complete real config as a
type+default template and override values from the resolved presets, coercing
each to the template's type. Filament settings become parallel per-slot arrays
(the multicolor/AMS model). See the ``slicer-cli-architecture`` memory.
"""
from __future__ import annotations

import json
from pathlib import Path

_REFERENCE_PATH = Path(__file__).parent / "orca_reference" / "project_settings_reference.json"

# Per-file preset metadata that must not leak into the merged project config.
_DROP_KEYS = {"type", "from", "inherits", "instantiation", "name"}


def _load_reference() -> dict:
    return json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))


def _scalar(value) -> str:
    """Extract a scalar string. Preset values are often single-element arrays."""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def _coerce(value, template_value):
    """Coerce ``value`` to match the JSON type of ``template_value``."""
    if isinstance(template_value, list):
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]
    return _scalar(value)


def build_project_config(
    machine: dict,
    process: dict,
    filaments: list[dict],
    filament_colours: list[str] | None = None,
) -> dict:
    """Merge resolved presets into a complete, correctly-typed project config.

    ``machine``/``process`` are single resolved configs; ``filaments`` is one
    resolved config per slot (length = AMS/extruder slot count).
    """
    if not filaments:
        raise ValueError("at least one filament is required")
    config = _load_reference()

    # Machine + process: scalar/array per the reference's type for each key.
    # Only override keys the reference (Orca's full serialized schema) knows;
    # preset keys absent from it are internal/deprecated and would crash the CLI
    # with an unguessable type.
    for src in (machine, process):
        for key, value in src.items():
            if key in _DROP_KEYS or key not in config:
                continue
            config[key] = _coerce(value, config[key])

    # Filament settings: a filament key is an array with one entry per loaded slot
    # IF the schema (reference) treats it as an array; otherwise a scalar (first
    # slot). Preset values are themselves usually single-element arrays, so take
    # each slot's scalar. This respects Orca's type for every key uniformly.
    filament_keys: set[str] = set()
    for f in filaments:
        filament_keys.update(k for k in f if k not in _DROP_KEYS)
    for key in filament_keys:
        if key not in config:
            continue
        per_slot = [_scalar(f.get(key, "")) for f in filaments]
        config[key] = per_slot[0] if not isinstance(config[key], list) else per_slot

    # Identity + per-slot id/colour arrays.
    config["printer_model"] = machine.get("printer_model", config.get("printer_model"))
    config["printer_settings_id"] = machine.get("name") or machine.get("printer_settings_id", "")
    config["print_settings_id"] = process.get("name", "")
    config["filament_settings_id"] = [f.get("name", "") for f in filaments]
    if filament_colours:
        config["filament_colour"] = [str(c) for c in filament_colours]
    # Embedded config establishes the active printer; the name-list gate is not used.
    config["compatible_printers"] = []
    config["from"] = "User"
    return config


def project_config_json(machine, process, filaments, filament_colours=None) -> str:
    return json.dumps(build_project_config(machine, process, filaments, filament_colours))
