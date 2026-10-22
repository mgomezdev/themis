"""Vendor printer clients live in their plugins (BIZ-251 Phase D): the old service modules are gone, and nothing
outside `app/plugins/` imports a vendor plugin or an old vendor module. Core resolves clients through the registry."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import app

APP_DIR = Path(app.__file__).parent
PLUGINS_DIR = APP_DIR / "plugins"

OLD = ["services/bambu_mqtt.py", "services/elegoo_centauri_client.py", "services/snapmaker_client.py",
       "services/mock_printer_client.py"]
NEW = ["plugins/bambu/client.py", "plugins/elegoo_centauri/client.py", "plugins/snapmaker/client.py",
       "plugins/mock/client.py"]

FORBIDDEN = [re.compile(p) for p in (
    r"\bapp\.plugins\.(bambu|elegoo_centauri|snapmaker|mock)\b",
    r"\bplugins\.(bambu|elegoo_centauri|snapmaker|mock)\b",
    r"\bbambu_mqtt\b", r"\belegoo_centauri_client\b", r"\bsnapmaker_client\b", r"\bmock_printer_client\b",
)]


def _import_lines(path: Path):
    """(lineno, text) for every import statement, including lazy ones inside functions; relative imports are
    rendered as written so `from ..plugins.bambu import x` matches the `plugins.bambu` pattern."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield node.lineno, " ".join(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            yield node.lineno, mod + " " + " ".join(f"{mod}.{a.name}".lstrip(".") for a in node.names)


def _violations(files):
    out = []
    for path in files:
        for lineno, text in _import_lines(path):
            if any(p.search(text) for p in FORBIDDEN):
                out.append(f"{path.relative_to(APP_DIR)}:{lineno}: {text}")
    return out


def _core_files():
    return [p for p in APP_DIR.rglob("*.py") if PLUGINS_DIR not in p.parents]


@pytest.mark.parametrize("rel", OLD)
def test_old_vendor_module_is_gone(rel):
    assert not (APP_DIR / rel).exists()


@pytest.mark.parametrize("rel", NEW)
def test_vendor_client_lives_in_its_plugin(rel):
    assert (APP_DIR / rel).is_file()


def test_core_imports_no_vendor_plugin_or_old_vendor_module():
    assert _violations(_core_files()) == []


def test_the_scan_covers_core_and_leaves_the_remap_package_alone():
    files = {p.relative_to(APP_DIR).as_posix() for p in _core_files()}
    assert "services/printer_client_factory.py" in files and "main.py" in files
    assert (APP_DIR / "services" / "snapmaker").is_dir()           # the remap package stays, and is not flagged
    assert not any(p.search("app.services.snapmaker.remap import x") for p in FORBIDDEN)


def test_the_checker_flags_planted_imports(tmp_path):
    bad = tmp_path / "x.py"
    bad.write_text("def f():\n    from app.plugins.bambu.client import BambuMQTTClient\n"
                   "from .bambu_mqtt import X\nimport app.services.snapmaker_client\n")
    assert len(_violations_for(bad)) == 3


def _violations_for(path):
    return [t for _, t in _import_lines(path) if any(p.search(t) for p in FORBIDDEN)]


def test_factory_has_no_static_registry():
    src = (APP_DIR / "services" / "printer_client_factory.py").read_text(encoding="utf-8")
    assert not re.search(r"\bREGISTRY\b", src)
