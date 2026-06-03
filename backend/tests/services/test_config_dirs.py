# backend/tests/services/test_config_dirs.py
import importlib
from pathlib import Path


def test_library_dir_defaults_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("THEMIS_LIBRARY_DIR", raising=False)
    import app.config as config
    importlib.reload(config)
    assert config.get_library_dir() == tmp_path / "library"
    assert config.get_filecache_dir() == tmp_path / "filecache"


def test_library_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "models"))
    import app.config as config
    importlib.reload(config)
    assert config.get_library_dir() == tmp_path / "models"
