"""The provider boundary (BIZ-232, BIZ-202): Themis core talks to Laminus through `SlicingProvider` and to Spoolman only
through the plugin host (`FilamentInventoryProvider` in `app/plugins/kinds/`).

Walks every module under `app/` and fails when code outside an adapter package (`app/services/providers/laminus/`,
`app/plugins/spoolman/`) reaches past the interface: imports an adapter-internal module (the sidecar client, the Spoolman
client, the Orca override/gcode/preset logic), names a vendor symbol, or opens its own `httpx` connection. To go through
the seam, import from `app.services.providers.slicing` or `app.plugins.kinds.filament_inventory` and use
`app.services.inventory`; to add a vendor, see docs/provider-interfaces.md.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import app

APP_DIR = Path(app.__file__).parent
PROVIDERS = "app.services.providers"
ADAPTER_PACKAGES = (f"{PROVIDERS}.laminus", "app.plugins.spoolman")

# Where the vendor implementations lived before they moved into adapter packages — must not come back.
LEGACY_MODULES = {
    "app.services.laminus_sidecar_client", "app.services.spoolman_service", "app.services.override_inspector",
    "app.services.profile_index", "app.services.preset_resolver",
    f"{PROVIDERS}.spoolman", f"{PROVIDERS}.filament_inventory",        # moved to app/plugins (BIZ-202)
}
# Vendor-specific symbols core must not name (importing, calling or aliasing them).
VENDOR_NAMES = {
    "LaminusSidecarClient", "SidecarError", "SidecarNotReady", "parse_gcode_estimates", "_parse_gcode_estimates",
    "get_laminus_sidecar_url",
}
# The accessor modules own the registry wiring: they may import the adapter packages (to register them), and the
# slicing accessor may read the configured URL.
ACCESSORS = {f"{PROVIDERS}.slicing"}
URL_READERS = {f"{PROVIDERS}.slicing", "app.config"}
# Modules allowed to import httpx outside the adapters: each talks to something that is not Laminus or Spoolman
# (printers, webhooks, notification channels, camera streams, network discovery). A new importer fails this test:
# if it needs Laminus/Spoolman, go through the provider; otherwise add it here with the reason.
HTTPX_ALLOWED = {
    "app.services.camera_proxy": "printer camera streams",
    "app.services.discovery_net": "printer discovery",
    "app.services.elegoo_centauri_client": "Elegoo printer API",
    "app.services.snapmaker_client": "Moonraker printer API",
    "app.services.notification_service": "ntfy/Discord notifications",
    "app.services.webhook_service": "outbound webhooks",
    "app.plugins.installer": "GitHub plugin download",
}


def _in(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _in_adapter(module: str) -> str | None:
    return next((p for p in ADAPTER_PACKAGES if _in(module, p)), None)


def _imports(module: str, is_package: bool, tree: ast.AST):
    """Yield (lineno, dotted imported module or module.name) with relative imports resolved."""
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            yield node.lineno, source
            for a in node.names:                       # `from pkg import submodule` imports pkg.submodule too
                yield node.lineno, f"{source}.{a.name}"


def _names(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute):
            yield node.lineno, node.attr
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                yield node.lineno, a.name


def violations(sources: dict[str, tuple[str, bool]]) -> list[str]:
    """`sources`: {dotted module: (source text, is_package)} → human-readable violations."""
    found: list[str] = []
    for module, (text, is_package) in sorted(sources.items()):
        tree = ast.parse(text)
        own = _in_adapter(module)
        for lineno, target in _imports(module, is_package, tree):
            where = f"{module}:{lineno}"
            if target in LEGACY_MODULES or any(_in(target, m) for m in LEGACY_MODULES):
                found.append(f"{where} imports {target} (vendor code now lives inside its adapter package)")
            for pkg in ADAPTER_PACKAGES:
                if _in(target, pkg) and own != pkg:
                    inside = target != pkg
                    if inside or module not in ACCESSORS:
                        found.append(f"{where} imports {target} — outside {pkg}; use the provider interface "
                                     f"(only the accessor modules {sorted(ACCESSORS)} may import the package to register it)")
            if _in(target, "httpx") and not own and module not in HTTPX_ALLOWED:
                found.append(f"{where} imports httpx — if this talks to Laminus/Spoolman use the provider; "
                             "otherwise add the module to HTTPX_ALLOWED")
        if not own:
            for lineno, name in _names(tree):
                if name in VENDOR_NAMES and not (name == "get_laminus_sidecar_url" and module in URL_READERS):
                    found.append(f"{module}:{lineno} uses vendor symbol {name}")
    return found


def _app_sources() -> dict[str, tuple[str, bool]]:
    out = {}
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR.parent).with_suffix("")
        parts = list(rel.parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        out[".".join(parts)] = (path.read_text(encoding="utf-8"), is_package)
    return out


def test_core_never_reaches_past_the_provider_interfaces():
    assert violations(_app_sources()) == []


def test_the_scan_covers_the_adapters_and_the_core():
    sources = _app_sources()
    assert {"app.main", "app.services.queue_engine", f"{PROVIDERS}.laminus.adapter",
            "app.plugins.spoolman.client", f"{PROVIDERS}.slicing"} <= set(sources)
    assert len(sources) > 80


# ---- prove the checker can fail: planted violations in synthetic modules -------------------------------------

@pytest.mark.parametrize("module, text, expected", [
    ("app.services.queue_engine", "from .providers.laminus.sidecar_client import LaminusSidecarClient", "outside"),
    ("app.api.routes.jobs", "from ...plugins.spoolman import client as s", "outside"),
    ("app.api.routes.jobs", "from ...plugins.spoolman.client import fetch_spools", "outside"),
    ("app.services.inventory.sync", "from ...plugins.spoolman.provider import SpoolmanProvider", "outside"),
    ("app.api.routes.jobs", "from ...services.providers import laminus", "outside"),
    ("app.services.slice_saver", "from .providers.laminus.gcode import parse_gcode_estimates", "outside"),
    ("app.services.library_scanner", "from .providers.laminus.overrides import CURATED_KEYS", "outside"),
    ("app.services.catalog_utils", "from app.services.spoolman_service import fetch_filaments", "vendor code"),
    ("app.services.three_mf_parser", "from .override_inspector import CURATED_KEYS", "vendor code"),
    ("app.services.x", "from .profile_index import ProfileIndex", "vendor code"),
    ("app.services.x", "import httpx\nhttpx.get('http://laminus/api/health')", "imports httpx"),
    ("app.services.x", "from httpx import AsyncClient", "imports httpx"),
    ("app.services.x", "from ..config import get_laminus_sidecar_url", "vendor symbol"),
    ("app.services.x", "r = _parse_gcode_estimates(path)", "vendor symbol"),
    ("app.services.x", "c = LaminusSidecarClient(url)", "vendor symbol"),
])
def test_the_checker_flags_each_kind_of_violation(module, text, expected):
    found = violations({module: (text, False)})
    assert any(expected in v for v in found), found


@pytest.mark.parametrize("module, text, is_package", [
    ("app.services.providers.slicing", "from . import laminus  # registers the adapter", False),
    ("app.services.providers.slicing", "from ... import config\nconfig.get_laminus_sidecar_url()", False),
    ("app.config", "def get_laminus_sidecar_url(): ...", False),
    ("app.services.providers.laminus.adapter", "from .sidecar_client import LaminusSidecarClient", False),
    ("app.services.providers.laminus.adapter", "from . import gcode, overrides\nimport httpx", False),
    ("app.plugins.spoolman", "from .provider import SpoolmanProvider", True),
    ("app.plugins.spoolman.provider", "from . import client\nimport httpx", False),
    ("app.api.routes.jobs", "from ...services.providers.slicing import Catalog, get_slicing_provider", False),
    ("app.api.routes.jobs", "from ...plugins.kinds.filament_inventory import InvSpool", False),
    ("app.services.inventory.read", "from ...plugins.host import plugin_host", False),
    ("app.services.webhook_service", "import httpx", False),
])
def test_the_checker_allows_the_sanctioned_paths(module, text, is_package):
    assert violations({module: (text, is_package)}) == []


def test_an_adapter_may_not_reach_into_the_other_adapter():
    found = violations({"app.plugins.spoolman.provider": ("from ...services.providers.laminus import sidecar_client", False)})
    assert found and "outside app.services.providers.laminus" in found[0]


def test_a_planted_import_in_a_real_core_module_turns_the_scan_red():
    sources = _app_sources()
    text, is_pkg = sources["app.services.queue_engine"]
    sources["app.services.queue_engine"] = (text + "\nfrom .providers.laminus.sidecar_client import SidecarError\n", is_pkg)
    found = violations(sources)
    assert found and all(v.startswith("app.services.queue_engine:") for v in found)
