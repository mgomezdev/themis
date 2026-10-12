from __future__ import annotations
import os
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


def get_ffmpeg_executable() -> str:
    return os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")


def get_plugins_dir() -> Path:
    """Installed plugin packages (`<id>/<version>/`) and the install staging area. Not created here: the installer does."""
    return _resolve_data_dir() / "plugins"
