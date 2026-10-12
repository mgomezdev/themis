"""Offline file-format provider (BIZ-281, decision in docs/architecture/3mf-ownership.md).

Slicer vocabulary that is pure local work on files Themis already holds: which settings a job may override (and their
labels), comparing a project's embedded settings with a merged preset config, and reading print estimates out of sliced
gcode / archives. No network, no server URL, so it works with no slicing server configured; `get_format_provider()`
(in `providers/slicing.py`) never returns None. Network work (catalog, merged config, slice, arrange) stays on
`SlicingProvider`. Returns the neutral frozen DTOs from `providers/contract.py`; routes serialize them to the JSON the
frontend already reads.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .contract import CuratedSetting, OverrideInspection, PlateEstimate


class FormatProvider(ABC):
    @abstractmethod
    def curated_settings(self) -> tuple[CuratedSetting, ...]:
        """The high-impact setting keys (with display labels) that job-level overrides and the embedded-settings
        view may touch. Order is display order."""

    @abstractmethod
    def parse_estimates(self, artifact_path: str, plate_number: int | None = None) -> PlateEstimate:
        """Grams / seconds / per-extruder grams from a sliced artifact (gcode or sliced archive); each field None
        independently when it can't be read. `plate_number` picks the plate inside a multi-plate archive."""

    @abstractmethod
    def inspect_overrides(self, source_project: str, merged_config: dict, slots: int) -> OverrideInspection:
        """Compare a project file's embedded settings with `merged_config` (what the chosen presets would produce)
        and the printer's `slots` capacity."""

    def curated_keys(self) -> tuple[str, ...]:
        return tuple(s.key for s in self.curated_settings())
