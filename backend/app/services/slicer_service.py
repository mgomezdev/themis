from __future__ import annotations
import zipfile
import xml.etree.ElementTree as ET

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_data_dir, get_orca_executable
from .mesh_3mf_builder import build_sliceable_3mf, stl_to_3mf
from .preset_resolver import PresetNotFoundError, PresetResolver
from .project_config_builder import build_project_config

logger = logging.getLogger(__name__)

_SLICE_TIMEOUT = 600
_EXPORT_3MF = "--export-3mf"


class SliceError(Exception):
    pass


@dataclass
class SliceRequest:
    """What a single (job, printer) slice needs.

    ``machine_preset`` is the printer's ``current_orca_printer_profile``;
    ``process_preset``/``filament_presets`` are OrcaSlicer preset names.
    ``export_args`` are the printer-specific OrcaSlicer output args (from
    ``AbstractPrinterClient.orca_export_args``): ``[]`` yields raw gcode (the
    default), ``["--export-3mf", "<name>.gcode.3mf"]`` yields the archive. Orca
    always writes gcode to ``--outputdir``; ``--export-3mf`` adds the archive.
    """
    job_id: int
    source_3mf: str
    plate_number: int
    machine_preset: str
    process_preset: str
    filament_presets: list[str]
    filament_colours: list[str] = field(default_factory=list)
    export_args: list[str] = field(default_factory=list)
    tool_index: int | None = None


class SlicerService:
    def __init__(self, orca_executable: str | None = None, data_dir: str | None = None) -> None:
        self._orca = orca_executable or get_orca_executable()
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()
        self._resolver = PresetResolver()

    # ── public API ────────────────────────────────────────────────────────────
    def slice(self, req: SliceRequest) -> str:
        """Resolve presets → embed them in a sliceable 3MF → slice → return the
        printer-correct artifact path (raw gcode or .gcode.3mf, per req.export_args).
        Falls back to a geometry-only re-slice (GUI "import geometry only") on failure.
        """
        config = self._build_config(req)
        out_dir = self._data_dir / "gcode" / str(req.job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        prepared = out_dir / "prepared.3mf"

        # Bare STL: wrap the mesh into a fresh 3MF with our config (no model_settings
        # to preserve, so the recovery tier doesn't apply).
        if Path(req.source_3mf).suffix.lower() == ".stl":
            stl_to_3mf(req.source_3mf, config, prepared, tool_index=req.tool_index)
            return self._run(prepared, req, out_dir)

        # Primary: preserve model_settings (per-object overrides / paint).
        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=False, tool_index=req.tool_index)
        try:
            return self._run(prepared, req, out_dir)
        except SliceError as primary_err:
            logger.warning("Slice failed for job %s; retrying geometry-only: %s", req.job_id, primary_err)

        # Recovery: drop the file's own settings/overrides, apply ours fresh.
        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=True, tool_index=req.tool_index)
        return self._run(prepared, req, out_dir)

    # ── internals ─────────────────────────────────────────────────────────────
    def _build_config(self, req: SliceRequest) -> dict:
        try:
            machine = self._resolver.resolve(req.machine_preset, "machine")
            process = self._resolver.resolve(req.process_preset, "process")
            filaments = [self._resolver.resolve(name, "filament") for name in req.filament_presets]
        except PresetNotFoundError as e:
            raise SliceError(f"preset resolution failed: {e}") from e

        # Resolve the plate count from the source 3MF if possible
        plate_count = 1
        source_path = Path(req.source_3mf)
        if source_path.suffix.lower() == ".3mf":
            try:
                with zipfile.ZipFile(source_path) as z:
                    if "Metadata/model_settings.config" in z.namelist():
                        root = ET.fromstring(z.read("Metadata/model_settings.config"))
                        plates = root.findall(".//plate")
                        if plates:
                            plate_count = len(plates)
            except Exception as e:
                logger.warning("Failed to parse plate count from %s: %s", req.source_3mf, e)

        return build_project_config(machine, process, filaments, req.filament_colours or None, plate_count=plate_count)

    def _run(self, input_3mf: Path, req: SliceRequest, out_dir: Path) -> str:
        """Run OrcaSlicer with the universal base + the printer's export args, then
        return the artifact the printer wants (the --export-3mf file if requested,
        otherwise the raw gcode Orca wrote to --outputdir)."""
        # Clear prior outputs so result detection picks up this run's files.
        for stale in (*out_dir.glob("*.gcode"), *out_dir.glob("*.gcode.3mf")):
            stale.unlink(missing_ok=True)

        cmd = [self._orca, "--slice", str(req.plate_number), "--outputdir", str(out_dir),
               "--arrange", "1",
               *req.export_args, str(input_3mf)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=_SLICE_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise SliceError(f"OrcaSlicer timed out after {_SLICE_TIMEOUT}s") from e

        # The printer asked for the 3MF archive.
        if _EXPORT_3MF in req.export_args:
            target = out_dir / req.export_args[req.export_args.index(_EXPORT_3MF) + 1]
            if target.exists():
                return str(target)
        else:
            gcodes = sorted(out_dir.glob("*.gcode"))
            if gcodes:
                return str(gcodes[0])

        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        raise SliceError(detail[-500:] or "OrcaSlicer produced no output")
