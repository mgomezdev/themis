from __future__ import annotations
import os
import sys
from pathlib import Path


def get_data_dir() -> Path:
    return Path(os.environ.get("THEMIS_DATA_DIR", "/data"))


def _resolve_data_dir() -> Path:
    # Match database.py: explicit env, else <repo-root>/data (robust for local dev,
    # unlike get_data_dir()'s Docker-oriented /data default).
    env = os.environ.get("THEMIS_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "data"


def get_library_dir() -> Path:
    env = os.environ.get("THEMIS_LIBRARY_DIR")
    path = Path(env) if env else _resolve_data_dir() / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_filecache_dir() -> Path:
    path = _resolve_data_dir() / "filecache"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def _default_orca_executable() -> str:
    # Explicit override always wins (used in Docker / CI).
    if "ORCA_EXECUTABLE" in os.environ:
        return os.environ["ORCA_EXECUTABLE"]
    # Windows local dev: OrcaSlicer installs as orca-slicer.exe under Program Files.
    if sys.platform == "win32":
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "OrcaSlicer" / "orca-slicer.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "OrcaSlicer" / "orca-slicer.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OrcaSlicer" / "orca-slicer.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    # Linux/Mac local dev or Docker default (on PATH).
    return "orcaslicer"


def get_orca_executable() -> str:
    return _default_orca_executable()


def get_ffmpeg_executable() -> str:
    return os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")


def get_laminus_sidecar_url() -> str | None:
    """Base URL of the laminus sidecar service (e.g. 'http://localhost:5000').
    Returns None when LAMINUS_SIDECAR_URL is not set (direct CLI mode)."""
    return os.environ.get("LAMINUS_SIDECAR_URL")
