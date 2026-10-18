"""Core never names a bundled plugin (BIZ-301): no module outside `app/plugins/` imports a bundled plugin package or branches on a
plugin id. Core asks the plugin host for "the provider of capability X" and reads what a plugin declares; behaviour that differs
per vendor or provider lives in that plugin (docs/provider-interfaces.md "Boundary checks").

The set of plugins is read from the plugin directories, so a new bundled plugin is covered the day it is added. This generalises
`test_vendor_extraction_boundary.py` (printer vendors) and `test_no_provider_in_core.py` (inventory providers) to every plugin and
to comparisons, which they do not look at. There are no exceptions; if one is ever needed, add it here with its reason."""
from __future__ import annotations

import ast
from pathlib import Path

import app
from app import plugins
from tests.test_provider_boundary import _imports

APP_DIR = Path(app.__file__).parent
PLUGINS_DIR = APP_DIR / "plugins"


def plugin_ids() -> set[str]:
    """Every bundled plugin's id (from its themis-plugin.toml) plus its directory name."""
    return set(plugins.bundled_ids()) | {dotted.rsplit(".", 1)[-1] for dotted in plugins.BUNDLED_MODULES}


def _literals(node: ast.AST):
    """String constants in a comparison operand, looking one level into a tuple / list / set (`x in ("a", "b")`)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            yield from _literals(elt)


def violations(sources: dict[str, tuple[str, bool]], ids: set[str]) -> list[str]:
    found = []
    for module, (text, is_package) in sorted(sources.items()):
        tree = ast.parse(text)
        for lineno, target in _imports(module, is_package, tree):
            for pid in ids:
                if (target == f"app.plugins.{pid}" or target.startswith(f"app.plugins.{pid}.")):
                    found.append(f"{module}:{lineno} imports the {pid} plugin ({target}); ask the plugin host instead")
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for operand in (node.left, *node.comparators):
                    for value in _literals(operand):
                        if value in ids:
                            found.append(f"{module}:{node.lineno} compares with the plugin id {value!r}; "
                                         "branch on a declared capability or feature instead")
    return found


def _core_sources() -> dict[str, tuple[str, bool]]:
    out = {}
    for path in APP_DIR.rglob("*.py"):
        if PLUGINS_DIR in path.parents:
            continue
        parts = list(path.relative_to(APP_DIR.parent).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        out[".".join(parts[:-1] if is_package else parts)] = (path.read_text(encoding="utf-8"), is_package)
    return out


def test_core_imports_no_bundled_plugin_and_branches_on_no_plugin_id():
    assert violations(_core_sources(), plugin_ids()) == []


def test_the_scan_covers_every_bundled_plugin_and_the_core():
    ids, sources = plugin_ids(), _core_sources()
    assert {"bambu", "snapmaker", "spoolman", "local_inventory", "laminus", "notify_ntfy", "mock"} <= ids
    assert {"app.main", "app.services.queue_engine", "app.services.printer_manager"} <= set(sources)
    assert not any(m.startswith("app.plugins") for m in sources)
    assert len(sources) > 100


def test_the_scan_can_fail_a_planted_import_and_a_planted_comparison_are_caught():
    ids = {"acme_vendor"}
    planted = {
        "app.services.a": ("from ..plugins.acme_vendor.client import Thing\n", False),
        "app.services.b": ("def f(p):\n    return p.plugin_id == 'acme_vendor'\n", False),
        "app.services.c": ("def f(p):\n    return p.plugin_id in ('x', 'acme_vendor')\n", False),
        "app.services.d": ("import app.plugins.acme_vendor\n", False),
        "app.services.clean": ("def f(p):\n    return p.plugin_id == 'other'\nLABEL = 'acme_vendor'\n", False),   # a label is not a branch
    }
    found = violations(planted, ids)
    assert sorted({v.split(":")[0] for v in found}) == ["app.services.a", "app.services.b", "app.services.c", "app.services.d"]


def test_the_frontend_guard_lists_every_bundled_plugin():
    """frontend/src/pluginIdGuard.test.ts cannot read the plugin directories, so it carries the ids; they must not drift."""
    import re
    text = (APP_DIR.parents[1] / "frontend" / "src" / "pluginIdGuard.test.ts").read_text(encoding="utf-8")
    block = re.search(r"BUNDLED_PLUGIN_IDS = \[(.*?)\];", text, re.S).group(1)
    assert set(re.findall(r"'([a-z_]+)'", block)) == set(plugins.bundled_ids())
