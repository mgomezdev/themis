from __future__ import annotations
import subprocess
from pathlib import Path

from ..config import get_data_dir, get_orca_executable


class SliceError(Exception):
    pass


class SlicerService:
    def __init__(
        self,
        orca_executable: str | None = None,
        data_dir: str | None = None,
    ) -> None:
        self._orca = orca_executable or get_orca_executable()
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()

    def slice(
        self,
        job_id: int,
        file_path: str,
        plate_number: int,
        print_profile: str,
        filament_profile: str,
    ) -> str:
        """Run OrcaSlicer headlessly. Returns path to the output .gcode file.

        Raises SliceError on non-zero exit or if no .gcode file is produced.
        """
        output_dir = self._data_dir / "gcode" / str(job_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self._orca,
            "--export-gcode",
            "--plate", str(plate_number),
            "--printer-profile", print_profile,
            "--filament-profile", filament_profile,
            "--output", str(output_dir),
            file_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            raise SliceError(result.stderr or result.stdout or f"Exit code {result.returncode}")

        gcode_files = list(output_dir.glob("*.gcode"))
        if not gcode_files:
            raise SliceError(f"OrcaSlicer exited 0 but no .gcode file found in {output_dir}")

        return str(gcode_files[0])
