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

_PER_EXTRUDER_KEYS = {
    # Nozzle/extruder properties
    "nozzle_diameter",
    "nozzle_type",
    "nozzle_volume",
    "nozzle_volume_type",
    "min_layer_height",
    "max_layer_height",
    "retraction_length",
    "retraction_speed",
    "deretraction_speed",
    "retract_lift_above",
    "retract_lift_below",
    "retract_lift_enforce",
    "wipe",
    "wipe_distance",
    "z_hop",
    "z_hop_types",
    "retract_restart_extra",
    "retract_when_changing_layer",
    "retract_before_wipe",
    "nozzle_temperature",
    "nozzle_temperature_initial_layer",
    "nozzle_temperature_range_high",
    "nozzle_temperature_range_low",
    "required_nozzle_HRC",
    "extruder_offset",
    "extruder_type",
    "extruder_colour",
    "long_retractions_when_cut",
    "retract_length_toolchange",
    "retract_restart_extra_toolchange",
    "travel_slope",
    "z_hop_when_prime",
    # Additional per-extruder/nozzle properties found in multi-extruder profiles
    "printer_extruder_id",
    "printer_extruder_variant",
    "default_nozzle_volume_type",
    "extruder_printable_height",
    "extruder_variant_list",
    "nozzle_flush_dataset",
    # Filament properties (one per extruder/slot in multi-extruder configs)
    "filament_type",
    "filament_colour",
    "filament_settings_id",
    "filament_diameter",
    "filament_flow_ratio",
    "filament_density",
    "filament_cost",
    "filament_vendor",
    "filament_notes",
    "filament_soluble",
    "filament_is_support",
    "filament_change_length",
    "filament_wipe",
    "filament_wipe_distance",
    "filament_z_hop",
    "filament_z_hop_types",
    "filament_retract_before_wipe",
    "filament_retract_lift_above",
    "filament_retract_lift_below",
    "filament_retract_lift_enforce",
    "filament_retract_restart_extra",
    "filament_retract_when_changing_layer",
    "filament_retraction_distances_when_cut",
    "filament_retraction_length",
    "filament_retraction_minimum_travel",
    "filament_retraction_speed",
    "filament_deretraction_speed",
    "filament_loading_speed",
    "filament_loading_speed_start",
    "filament_unloading_speed",
    "filament_unloading_speed_start",
    "filament_shrink",
    "filament_shrinkage_compensation_z",
    "filament_stamping_distance",
    "filament_stamping_loading_speed",
    "filament_toolchange_delay",
    "filament_tower_interface_pre_extrusion_dist",
    "filament_tower_interface_pre_extrusion_length",
    "filament_tower_interface_print_temp",
    "filament_tower_interface_purge_volume",
    "filament_tower_ironing_area",
    # All extra per-filament settings that need expansion
    "activate_air_filtration",
    "activate_chamber_temp_control",
    "adaptive_pressure_advance",
    "adaptive_pressure_advance_bridges",
    "adaptive_pressure_advance_model",
    "adaptive_pressure_advance_overhangs",
    "additional_cooling_fan_speed",
    "chamber_temperature",
    "close_fan_the_first_x_layers",
    "complete_print_exhaust_fan_speed",
    "cool_plate_temp",
    "cool_plate_temp_initial_layer",
    "default_filament_colour",
    "dont_slow_down_outer_wall",
    "during_print_exhaust_fan_speed",
    "enable_overhang_bridge_fan",
    "enable_pressure_advance",
    "eng_plate_temp",
    "eng_plate_temp_initial_layer",
    "fan_cooling_layer_time",
    "fan_max_speed",
    "fan_min_speed",
    "filament_adaptive_volumetric_speed",
    "filament_adhesiveness_category",
    "filament_colour_type",
    "filament_cooling_final_speed",
    "filament_cooling_initial_speed",
    "filament_cooling_moves",
    "filament_end_gcode",
    "filament_extruder_variant",
    "filament_flush_temp",
    "filament_flush_volumetric_speed",
    "filament_ids",
    "filament_ironing_flow",
    "filament_ironing_inset",
    "filament_ironing_spacing",
    "filament_ironing_speed",
    "filament_long_retractions_when_cut",
    "filament_map",
    "filament_max_volumetric_speed",
    "filament_minimal_purge_on_wipe_tower",
    "filament_multi_colour",
    "filament_multitool_ramming",
    "filament_multitool_ramming_flow",
    "filament_multitool_ramming_volume",
    "filament_printable",
    "filament_ramming_parameters",
    "filament_self_index",
    "filament_start_gcode",
    "flush_multiplier",
    "full_fan_speed_layer",
    "hot_plate_temp",
    "hot_plate_temp_initial_layer",
    "idle_temperature",
    "internal_bridge_fan_speed",
    "ironing_fan_speed",
    "long_retractions_when_ec",
    "overhang_fan_speed",
    "overhang_fan_threshold",
    "pellet_flow_coefficient",
    "pressure_advance",
    "reduce_fan_stop_start_freq",
    "retraction_distances_when_ec",
    "slow_down_for_layer_cooling",
    "slow_down_layer_time",
    "slow_down_min_speed",
    "supertack_plate_temp",
    "supertack_plate_temp_initial_layer",
    "support_material_interface_fan_speed",
    "temperature_vitrification",
    "textured_cool_plate_temp",
    "textured_cool_plate_temp_initial_layer",
    "textured_plate_temp",
    "textured_plate_temp_initial_layer",
    "volumetric_speed_coefficients",
}


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
    plate_count: int = 1,
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

    # Set compatible printers for print settings to match active printer ID
    config["print_compatible_printers"] = [config["printer_settings_id"]]

    # Expand per-extruder and per-filament list settings to match the extruder count
    # if the machine profile specifies multiple nozzles/extruders.
    n_extruders = len(config.get("nozzle_diameter", []))
    if n_extruders > 1:
        for key in _PER_EXTRUDER_KEYS:
            if key in config and isinstance(config[key], list):
                val = config[key]
                if len(val) < n_extruders:
                    last_item = val[-1] if val else ""
                    config[key] = val + [last_item] * (n_extruders - len(val))

        # 1-based sequential slot indices required by OrcaSlicer for multi-extruder indexing
        config["printer_extruder_id"] = [str(i + 1) for i in range(n_extruders)]
        config["filament_self_index"] = [str(i + 1) for i in range(n_extruders)]

        # Expand transition flush volumes matrix and vector to prevent out-of-bounds access
        matrix_size = 64
        current_matrix = config.get("flush_volumes_matrix", [])
        if len(current_matrix) < matrix_size:
            config["flush_volumes_matrix"] = ["0"] * matrix_size

        vector_size = 8
        current_vector = config.get("flush_volumes_vector", [])
        if len(current_vector) < vector_size:
            last_item = current_vector[-1] if current_vector else "140"
            config["flush_volumes_vector"] = current_vector + [last_item] * (vector_size - len(current_vector))

    # Construct inherits_group hierarchically
    inherits_process = process.get("inherits") or process.get("inherits_group") or ""
    if isinstance(inherits_process, list):
        inherits_process = inherits_process[0] if inherits_process else ""

    inherits_filaments = []
    for f in filaments:
        inh = f.get("inherits") or f.get("inherits_group") or ""
        if isinstance(inh, list):
            inh = inh[0] if inh else ""
        inherits_filaments.append(inh)
    if len(inherits_filaments) < n_extruders:
        last = inherits_filaments[-1] if inherits_filaments else ""
        inherits_filaments += [last] * (n_extruders - len(inherits_filaments))

    inherits_printer = machine.get("name") or machine.get("printer_settings_id") or ""
    if isinstance(inherits_printer, list):
        inherits_printer = inherits_printer[0] if inherits_printer else ""

    config["inherits_group"] = [inherits_process] + inherits_filaments + [inherits_printer]

    # Expand plate-specific list settings to match the plate count
    for key in ["wipe_tower_x", "wipe_tower_y"]:
        if key in config and isinstance(config[key], list):
            val = config[key]
            if len(val) < plate_count:
                last_item = val[-1] if val else "0"
                config[key] = val + [last_item] * (plate_count - len(val))

    return config


def project_config_json(machine, process, filaments, filament_colours=None, plate_count=1) -> str:
    return json.dumps(build_project_config(machine, process, filaments, filament_colours, plate_count))
