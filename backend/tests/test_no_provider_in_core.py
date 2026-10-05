"""Architecture guard (BIZ-202 §6): core must not name a specific inventory provider.

Today Spoolman is still wired through core (it is extracted in phase 1c), so the guard is a *ratchet*: the files
below are the ones that still mention it. A new file mentioning Spoolman fails; so does an allowlisted file that no
longer does (delete it from the list — the allowlist only ever shrinks). Local inventory has no allowance at all
outside its own plugin package."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

SPOOLMAN = re.compile(r"spoolman", re.IGNORECASE)
LOCAL_INVENTORY = re.compile(r"local[_-]?inv(entory)?", re.IGNORECASE)

# Paths relative to app/. Entries for migrations, models, auth scopes and printer_manager's preserved keys are
# permanent (spec §6); the rest are removed as phase 1c/2d move them behind the plugin.
SPOOLMAN_ALLOWLIST = {
    "api/routes/jobs.py", "api/routes/laminus.py", "api/routes/projects.py", "api/routes/queue.py",
    "api/routes/settings.py", "api/routes/spoolman.py",
    "auth.py", "main.py", "models.py",
    "migrations/runner.py", "migrations/v020_spoolman_sync_status.py", "migrations/v026_spool_low_stock.py",
    "services/catalog_service.py", "services/catalog_utils.py", "services/job_costs.py", "services/printer_manager.py",
    "services/providers/__init__.py", "services/providers/filament_inventory.py",
    "services/providers/spoolman/__init__.py", "services/providers/spoolman/adapter.py",
    "services/queue_engine.py", "services/spool_alerts.py", "services/spool_check.py", "services/spoolman_sync.py",
}
PLUGIN_HOMES = {"spoolman": "plugins/spoolman/", "local_inventory": "plugins/local_inventory/"}


def _files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" not in p.parts:
            yield p, p.relative_to(root).as_posix()


def violations(root: Path, spoolman_allow: set[str]) -> list[str]:
    out = []
    for path, rel in _files(root):
        text = path.read_text(encoding="utf-8")
        if SPOOLMAN.search(text) and rel not in spoolman_allow and not rel.startswith(PLUGIN_HOMES["spoolman"]):
            out.append(f"{rel}: mentions Spoolman but is not on the allowlist")
        if LOCAL_INVENTORY.search(text) and not rel.startswith(PLUGIN_HOMES["local_inventory"]):
            out.append(f"{rel}: mentions Local inventory outside its plugin package")
    return out


def stale_allowlist(root: Path, spoolman_allow: set[str]) -> list[str]:
    still = {rel for path, rel in _files(root) if SPOOLMAN.search(path.read_text(encoding="utf-8"))}
    return sorted(spoolman_allow - still)


def test_core_does_not_grow_new_spoolman_or_local_inventory_references():
    assert violations(APP, SPOOLMAN_ALLOWLIST) == []


def test_the_allowlist_only_shrinks():
    assert stale_allowlist(APP, SPOOLMAN_ALLOWLIST) == [], "remove these files from SPOOLMAN_ALLOWLIST"


def test_the_plugin_host_itself_is_provider_agnostic():
    for path, rel in _files(APP / "plugins"):
        if rel.startswith(("spoolman/", "local_inventory/")):
            continue
        text = path.read_text(encoding="utf-8")
        assert not SPOOLMAN.search(text) and not LOCAL_INVENTORY.search(text), rel


# --- the guard can fail ---------------------------------------------------------------------------------------------

@pytest.fixture
def tree(tmp_path):
    (tmp_path / "plugins" / "spoolman").mkdir(parents=True)
    (tmp_path / "plugins" / "local_inventory").mkdir()
    (tmp_path / "services").mkdir()
    return tmp_path


def test_planted_spoolman_reference_in_core_is_caught(tree):
    (tree / "services" / "queue.py").write_text("x = get_spoolman_thing()\n")
    assert violations(tree, set()) == ["services/queue.py: mentions Spoolman but is not on the allowlist"]
    assert violations(tree, {"services/queue.py"}) == []


def test_planted_local_inventory_reference_in_core_is_caught_and_the_plugin_package_is_exempt(tree):
    (tree / "services" / "q.py").write_text("from plugins.local_inventory import X\n")
    (tree / "plugins" / "local_inventory" / "provider.py").write_text("LOCAL_INVENTORY = 1\n")
    (tree / "plugins" / "spoolman" / "client.py").write_text("SPOOLMAN = 1\n")
    assert violations(tree, set()) == ["services/q.py: mentions Local inventory outside its plugin package"]


def test_a_stale_allowlist_entry_is_caught(tree):
    (tree / "services" / "clean.py").write_text("x = 1\n")
    assert stale_allowlist(tree, {"services/clean.py"}) == ["services/clean.py"]
