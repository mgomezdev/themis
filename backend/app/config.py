from __future__ import annotations
import os
import sys
from pathlib import Path


def get_data_dir() -> Path:
    return Path(os.environ.get("THEMIS_DATA_DIR", "/data"))


def _default_orca_config_dir() -> str:
    # Explicit override always wins (used in Docker / CI).
    if "ORCA_CONFIG_DIR" in os.environ:
        return os.environ["ORCA_CONFIG_DIR"]
    # Windows local dev: OrcaSlicer writes to %APPDATA%\OrcaSlicer.
    if sys.platform == "win32" or os.environ.get("APPDATA"):
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return str(Path(appdata) / "OrcaSlicer")
    # Linux/Mac local dev or Docker default.
    return "/root/.config/OrcaSlicer"


def get_orca_config_dir() -> Path:
    return Path(_default_orca_config_dir())


def get_orca_executable() -> str:
    return os.environ.get("ORCA_EXECUTABLE", "orcaslicer")


def get_ffmpeg_executable() -> str:
    return os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")
