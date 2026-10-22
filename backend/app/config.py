from __future__ import annotations
import os
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


def get_ffmpeg_executable() -> str:
    return os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")


def get_plugins_dir() -> Path:
    """Installed plugin packages (`<id>/<version>/`) and the install staging area. Not created here: the installer does."""
    return _resolve_data_dir() / "plugins"
