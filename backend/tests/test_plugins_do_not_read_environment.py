"""Architecture guard (BIZ-289, CLAUDE.md "Plugin configuration"): a plugin never reads `os.environ`/`getenv`. Environment-derived
values reach plugins through core (`app.config.read_env` via the plugin host: PluginContext, defaults.toml `[legacy_env]`)."""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ENV = re.compile(r"\bos\.environ\b|\bgetenv\b|from os import[^\n]*\benviron\b")

# Everything a plugin ships lives under these roots. Add the home of any plugin-owned service package here.
SCANNED = ("plugins/", "services/providers/laminus/")
# Host-side files that legitimately touch the environment, with the reason. Plugin code may never be listed.
ALLOWLIST = {
    # Core's installer builds the *subprocess* env (PATH/SYSTEMROOT) for pip when installing a plugin: it is host code that
    # runs before any plugin code, never loaded as a plugin, and passes nothing to a plugin.
    "plugins/installer.py",
}


def _hits(root: Path, allow: set[str]) -> list[str]:
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in p.parts or not rel.startswith(SCANNED) or rel in allow:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if ENV.search(line.split("#", 1)[0]):
                out.append(f"{rel}:{n}: {line.strip()}")
    return out


def test_no_plugin_or_plugin_provider_reads_the_environment():
    assert _hits(APP, ALLOWLIST) == [], "plugins get env-derived values from core (see docs/plugins.md), never os.environ"


def test_the_allowlist_only_holds_host_files_that_still_need_it():
    for rel in ALLOWLIST:
        assert ENV.search((APP / rel).read_text(encoding="utf-8")), f"{rel} no longer reads the environment: drop it from ALLOWLIST"
        assert rel.startswith("plugins/") and "/" not in rel[len("plugins/"):], "only top-level host files, never a plugin package"


def test_the_scan_catches_a_violation(tmp_path):
    d = tmp_path / "plugins" / "evil"
    d.mkdir(parents=True)
    (d / "__init__.py").write_text('import os\nx = os.environ.get("A")\ny = os.getenv("B")  \n')
    (d / "ok.py").write_text('# os.environ in a comment is fine\nx = 1\n')
    assert len(_hits(tmp_path, set())) == 2
