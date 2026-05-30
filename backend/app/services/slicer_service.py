from __future__ import annotations

import logging
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_data_dir, get_orca_executable
from .mesh_3mf_builder import build_sliceable_3mf, stl_to_3mf
from .preset_resolver import PresetNotFoundError, PresetResolver
from .project_config_builder import build_project_config

logger = logging.getLogger(__name__)

_SLICE_TIMEOUT = 600


class SliceError(Exception):
    pass


@dataclass
class SliceRequest:
    """What a single (job, printer) slice needs.

    ``machine_preset`` is the printer's ``current_orca_printer_profile``;
    ``process_preset`` and ``filament_presets`` are OrcaSlicer preset names.
    ``filament_colours`` is per-slot (#RRGGBB), matched to ``filament_presets``.
    """
    job_id: int
    source_3mf: str
    plate_number: int
    machine_preset: str
    process_preset: str
    filament_presets: list[str]
    filament_colours: list[str] = field(default_factory=list)


class SlicerService:
    def __init__(self, orca_executable: str | None = None, data_dir: str | None = None) -> None:
        self._orca = orca_executable or get_orca_executable()
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()
        self._resolver = PresetResolver()

    # ── public API ────────────────────────────────────────────────────────────
    def slice(self, req: SliceRequest) -> str:
        """Resolve presets → generate an embedded-config 3MF → slice → return the
        extracted .gcode path. Falls back to a geometry-only re-slice (mirroring
        the GUI's "import geometry only") if the first attempt fails.
        """
        config = self._build_config(req)
        out_dir = self._data_dir / "gcode" / str(req.job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        prepared = out_dir / "prepared.3mf"

        # Bare STL: wrap the mesh into a fresh 3MF with our config (no model_settings
        # to preserve, so there's nothing for the recovery tier to strip).
        if Path(req.source_3mf).suffix.lower() == ".stl":
            stl_to_3mf(req.source_3mf, config, prepared)
            return self._run_and_extract(prepared, req.plate_number, out_dir)

        # Primary: preserve model_settings (per-object overrides / paint).
        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=False)
        try:
            return self._run_and_extract(prepared, req.plate_number, out_dir)
        except SliceError as primary_err:
            logger.warning("Slice failed for job %s; retrying geometry-only: %s", req.job_id, primary_err)

        # Recovery: drop the file's own settings/overrides, apply ours fresh.
        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=True)
        return self._run_and_extract(prepared, req.plate_number, out_dir)

    # ── internals ─────────────────────────────────────────────────────────────
    def _build_config(self, req: SliceRequest) -> dict:
        try:
            machine = self._resolver.resolve(req.machine_preset, "machine")
            process = self._resolver.resolve(req.process_preset, "process")
            filaments = [self._resolver.resolve(name, "filament") for name in req.filament_presets]
        except PresetNotFoundError as e:
            raise SliceError(f"preset resolution failed: {e}") from e
        return build_project_config(machine, process, filaments, req.filament_colours or None)

    def _run_and_extract(self, input_3mf: Path, plate_number: int, out_dir: Path) -> str:
        """Run the CLI and pull the plate gcode out of the resulting .gcode.3mf."""
        export = out_dir / "sliced.gcode.3mf"
        export.unlink(missing_ok=True)
        # NOTE: no --outputdir alongside an absolute --export path (it doubles the
        # path and fails). gcode lands at Metadata/plate_N.gcode in the archive.
        cmd = [self._orca, "--slice", str(plate_number), "--export-3mf", str(export), str(input_3mf)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=_SLICE_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise SliceError(f"OrcaSlicer timed out after {_SLICE_TIMEOUT}s") from e

        if not export.exists():
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            raise SliceError(detail[-500:])

        gcode_path = out_dir / "plate.gcode"
        with zipfile.ZipFile(export) as z:
            entries = [n for n in z.namelist() if n.lower().endswith(".gcode")]
            if not entries:
                raise SliceError("OrcaSlicer produced a 3MF with no gcode")
            gcode_path.write_bytes(z.read(entries[0]))
        return str(gcode_path)
