"""Laminus' offline `FormatProvider`: Orca vocabulary (curated keys + labels, `; filament used` lines) over local files.
Never touches the network; constructed with no arguments."""
from __future__ import annotations

from ..contract import CuratedSetting, OverrideInspection, PlateEstimate
from ..format_provider import FormatProvider
from . import gcode, overrides


class LaminusFormatProvider(FormatProvider):
    def curated_settings(self) -> tuple[CuratedSetting, ...]:
        return overrides.CURATED_SETTINGS

    def parse_estimates(self, artifact_path: str, plate_number: int | None = None) -> PlateEstimate:
        grams, seconds, per_extruder = gcode.parse_gcode_estimates(artifact_path, plate_number)
        return PlateEstimate(grams=grams, seconds=seconds,
                             per_extruder_grams=tuple(per_extruder) if per_extruder is not None else None)

    def inspect_overrides(self, source_project: str, merged_config: dict, slots: int) -> OverrideInspection:
        return overrides.inspect_overrides(source_project, merged_config, slots)
