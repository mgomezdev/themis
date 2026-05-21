from __future__ import annotations
import os
from pathlib import Path


def get_data_dir() -> Path:
    return Path(os.environ.get("THEMIS_DATA_DIR", "/data"))


def get_orca_config_dir() -> Path:
    return Path(os.environ.get("ORCA_CONFIG_DIR", "/root/.config/OrcaSlicer"))


def get_orca_executable() -> str:
    return os.environ.get("ORCA_EXECUTABLE", "orcaslicer")
