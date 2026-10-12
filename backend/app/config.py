from __future__ import annotations
import os
import sys
from pathlib import Path


def read_env(name: str) -> str | None:
    """The one place a plugin-related environment value is read (BIZ-289): plugins never touch `os.environ`; the plugin host
    calls this for the deprecated variables a plugin's `defaults.toml` maps in `[legacy_env]`."""
    return os.environ.get(name)


def get_app_data_dir() -> Path:
    """The Themis data directory (THEMIS_DATA_DIR, else <repo>/data), as handed to plugins through their context."""
    return _resolve_data_dir()


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


# Remaining Orca symbol: consumed only by services/thumbnail_regen.py (local OrcaSlicer CLI); BIZ-284 deletes both.
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


def get_plugins_dir() -> Path:
    """Installed plugin packages (`<id>/<version>/`) and the install staging area. Not created here: the installer does."""
    return _resolve_data_dir() / "plugins"
