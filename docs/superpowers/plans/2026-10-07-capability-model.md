# Capability model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plugin `kind` with a capability model: plugins declare the named, versioned capabilities they provide/require/define; the host keeps one selected provider per capability; a Settings -> Capabilities page lets the user choose.

**Architecture:** `kind` is removed from the manifest and toml; `PluginManifest` gains `provides` (capability id -> `Provide`), `requires`, `optional`, `defines`. `PluginHost` keys everything by capability id (`active/has/part/call(cap, ...)`), persists choices in a new `capability_selections` table (migration v040, replaces `extension_slots`), builds one instance per plugin, and gates plugins with unmet `requires` as "waiting". Provider routers are served at `/api/v1/plugins/<id>/...` as today and, through a dispatcher, at `/api/v1/capabilities/<cap>/...`.

**Tech Stack:** Python 3.13, FastAPI, async SQLAlchemy 2 + aiosqlite, pytest; React + Vite + TypeScript, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-07-capability-model-design.md` (handoff: `docs/superpowers/handoffs/2026-10-07-capability-model-handoff.md`)

## Global Constraints

- `host_api` **stays 1** (`HOST_API` in `backend/app/plugins/manifest.py`); no `kind` shim, no back-compat.
- Core capability id for filament inventory: `inventory.filament`, contract version `1`.
- Plugin-defined capability ids must start with `<plugin_id>.`; plugins cannot redefine core ids.
- Capability id regex: `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`. Plugin id regex unchanged (`ID_RE`).
- toml lists: `provides = ["cap@N"]`, `requires = ["cap@N"]`, `optional = ["cap@N"]`, `defines = ["plugin_id.cap"]`; `@N` optional, default 1.
- One plugin = one settings model, one `factory`, one enabled flag, one instance. Selection is per capability.
- Selection rules: auto-select only when no stored row and exactly one enabled provider; never displace a stored choice; explicit None is remembered; selecting a provider also enables it.
- `host.call` never raises (except cancellation); secrets stay redacted; installed plugins keep their limits (no `component` tabs, no `alias_routers`).
- Error shapes: capability dispatcher 409 `{"error": "capability_unavailable", ...}` when no active provider, 404 when the active provider does not expose the path; selection cycle => 422.
- Repo rules (`CLAUDE.md`): concise replies; every behavior change gets a test that fails without it; Test DB uses the shared `session_factory` fixture, never `:memory:`; wait with `tests/waiting.py:wait_until`, never `sleep`; coverage floors are ratchets (never lower); regenerate `openapi.json` with `python scripts/export_openapi.py` (repo root) when routes change; PR only after `.claude/review-state.json` matches HEAD.
- Commit message trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01P61WEnnp6c5eUTUcym4kiU`.

## Review Focus

Failure modes the spec implies that no single task's happy path covers (each has a pinning test in the owning task):

1. A selection row pointing at a plugin that was uninstalled or no longer provides the capability -> `active()` is None, status `no_provider`, nothing raises (Task 3).
2. A plugin defines a capability, another plugin selects/provides it, then the definer is uninstalled -> capability leaves the catalog, selection row kept dormant, restored when the definer returns (Task 3).
3. Two plugins each `requires` the other's capability (auto-selected, no user action) -> both "waiting", no infinite recursion (Task 3).
4. `PUT /capabilities/{cap}/provider` for an unknown cap, or a plugin that does not provide it, or that would create a cycle -> 422 with no DB change (Task 5).
5. Dispatcher path escape: `/api/v1/capabilities/inventory.filament/../../plugins/...` or a plugin route not listed in `Provide.routers` must 404, never reach a non-exposed route; and a request for a route needing a scope the caller lacks must still 401/403 (Task 6).
6. Old DB upgrade: `extension_slots` row for `filament_inventory` -> `capability_selections('inventory.filament', 'spoolman', explicit=1)`; an unknown legacy kind row is carried through unchanged as a dormant selection; `down()` restores (Task 3).

---

## Preconditions (do before Task 1)

- [ ] **P1: Base the work on the unmerged plugin-docs/Windows-fix commits.** This branch was cut from `develop`, but the docs guide + sample (`docs/plugin development/`, commit `9894aa3`) and the Windows installer fix (`267ac6d`, makes 22 installer tests pass on Windows) live only on `feature/fix-plugin-dryrun-windows-env` (unpushed, unmerged). Tasks 2, 4 and 9 depend on them.

```bash
git merge --no-ff feature/fix-plugin-dryrun-windows-env -m "Merge feature/fix-plugin-dryrun-windows-env into capability-model branch"
```

Expected: clean merge (only that branch's 4 commits). If the user prefers a different base (e.g. wait for that branch's PR to land in `develop`), stop and ask.

- [ ] **P2: Baseline.** From `backend/` with the venv active: `pytest -q -x -p no:cacheprovider` -> all pass (about 2383). From `frontend/`: `npx vitest run` -> about 978 pass. Record the counts; they are the regression baseline.

---

## File Structure

**Backend create:**
- `backend/app/plugins/capabilities/__init__.py` - `CORE` catalog (core definitions), `CapabilityDef` re-export.
- `backend/app/plugins/capabilities/definition.py` - `CapabilityDef`, `CAP_ID_RE`.
- `backend/app/plugins/capabilities/filament_inventory.py` - moved from `plugins/kinds/filament_inventory.py` (ABC, DTOs, feature flags) + `DEFINITION`.
- `backend/app/migrations/v040_capability_selections.py`
- `backend/app/api/routes/capabilities.py` - `GET /capabilities`, `PUT /capabilities/{cap}/provider`, dispatcher.
- `backend/tests/plugins/test_capability_catalog.py`, `test_host_capabilities.py`, `test_v040_migration.py`, `backend/tests/api/test_capabilities_api.py`, `test_capability_dispatch.py`

**Backend modify:** `plugins/{__init__,manifest,package,host,installer,loader}.py`, `plugins/spoolman/{__init__,routes,themis-plugin.toml}`, `plugins/local_inventory/{__init__,themis-plugin.toml}`, `services/inventory/{provider,sync}.py` (+ import renames in `alerts,cache,deduction,preflight,read`), `api/routes/{plugins,plugin_install,inventory,jobs,queue,laminus}.py`, `main.py`, `models.py`, `migrations/runner.py`, `services/catalog_utils.py`; tests `tests/plugins/{dummy_plugin,pkg_builder,test_host,test_manifest_and_registry,test_installer,test_local_inventory,test_plugin_install_routes,test_filament_inventory_contract,test_v035_migration}.py`, `tests/api/test_plugins_api.py` and every test listed by `grep -rln "plugins.kinds\|set_slot\|extension-slots" backend/tests`.

**Backend delete:** `backend/app/plugins/kinds/` (moved).

**Frontend create:** `frontend/src/api/capabilities.ts` (+ `.test.tsx`), `frontend/src/screens/CapabilitiesPage.tsx` (+ `.test.tsx`).

**Frontend modify:** `api/{plugins,inventory}.ts`, `components/{PluginSettingsPage,PluginInstallDialog}.tsx`, `screens/{FilamentInventoryPage,SettingsScreen,PluginsPage}.tsx`, `test/inventoryFixtures.ts`, and the tests the grep in Task 7 lists.

**Docs:** `docs/plugin development/{README.md,sample/acme_inventory/**}`, `docs/plugins.md`, `docs/provider-interfaces.md`, `docs/agent/{backend,data-model,frontend}.md`.

**Test cadence note:** Tasks 2-4 change the plugin API shape underneath the host and core. Between them, run only the scoped tests named in each task. The full backend suite is green again at the end of Task 5; the full frontend suite at the end of Task 8. Commit on the feature branch after each task regardless.

---

### Task 1: Capability definitions and the module move

**Files:**
- Create: `backend/app/plugins/capabilities/{__init__,definition}.py`, `backend/tests/plugins/test_capability_catalog.py`
- Move: `backend/app/plugins/kinds/filament_inventory.py` -> `backend/app/plugins/capabilities/filament_inventory.py` (`git mv`), delete `kinds/__init__.py`
- Modify (import rename only): every file matching `grep -rl "plugins.kinds\|\.kinds\." backend` (app: `api/routes/{inventory,jobs,laminus,queue}.py`, `services/{catalog_utils}.py`, `services/inventory/{alerts,cache,deduction,preflight,provider,read,sync}.py`, `plugins/{package}.py`, `plugins/spoolman/{__init__,provider,routes}.py`, `plugins/local_inventory/{__init__,provider}.py`; tests: `fake_providers.py`, `inventory_helpers.py`, `test_provider_boundary.py`, `plugins/{pkg_builder,test_filament_inventory_contract}.py` and any other grep hit), `backend/app/plugins/__init__.py` (`_discover_bundled` excludes `capabilities` instead of `kinds`)

**Interfaces:**
- Produces: `app.plugins.capabilities.definition.CapabilityDef(id: str, version: int, label: str, description: str = "", required_methods: tuple[str, ...] = (), features: frozenset[str] = frozenset())` (frozen dataclass); `CAP_ID_RE`; `app.plugins.capabilities.CORE: dict[str, CapabilityDef]`; `app.plugins.capabilities.filament_inventory.CAPABILITY = "inventory.filament"` and `DEFINITION`. `KIND` stays defined (`KIND = CAPABILITY`) until Task 4 removes it.

- [ ] **Step 1: Write the failing test** `backend/tests/plugins/test_capability_catalog.py`

```python
import pytest

from app.plugins.capabilities import CORE
from app.plugins.capabilities.definition import CAP_ID_RE, CapabilityDef
from app.plugins.capabilities import filament_inventory as fi


def test_core_catalog_has_filament_inventory_v1_with_all_features():
    d = CORE["inventory.filament"]
    assert (d.id, d.version, d.label) == ("inventory.filament", 1, "Filament inventory")
    assert d.features == fi.ALL_CAPABILITIES and fi.CAPABILITY == "inventory.filament"


@pytest.mark.parametrize("cid,ok", [("inventory.filament", True), ("acme_inv.reports", True), ("a.b.c", True),
                                    ("inventory", False), ("Inventory.filament", False), ("a..b", False), (".a.b", False), ("a.b-c", False)])
def test_capability_id_shape(cid, ok):
    assert bool(CAP_ID_RE.match(cid)) is ok


def test_definition_is_immutable():
    with pytest.raises(Exception):
        CORE["inventory.filament"].version = 2        # type: ignore[misc]
    assert isinstance(CORE["inventory.filament"], CapabilityDef)
```

- [ ] **Step 2: Run to verify it fails**

Run (from `backend/`): `pytest tests/plugins/test_capability_catalog.py -v` -> FAIL `ModuleNotFoundError: app.plugins.capabilities`.

- [ ] **Step 3: Implement**

`backend/app/plugins/capabilities/definition.py`:
```python
"""A capability: a named, versioned service contract that one plugin at a time serves (design spec §1)."""
from __future__ import annotations

import re
from dataclasses import dataclass

CAP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


@dataclass(frozen=True)
class CapabilityDef:
    id: str
    version: int
    label: str
    description: str = ""
    # Duck-typing check for plugin-defined contracts: these async methods must exist on the serving part (verified when the
    # instance is built). Core capabilities with an ABC leave this empty.
    required_methods: tuple[str, ...] = ()
    features: frozenset[str] = frozenset()     # the feature flags a provider may declare for this capability
```

Run `git mv backend/app/plugins/kinds/filament_inventory.py backend/app/plugins/capabilities/filament_inventory.py`, `git rm backend/app/plugins/kinds/__init__.py`. At the top of the moved module replace `KIND = "filament_inventory"` with:
```python
from .definition import CapabilityDef

CAPABILITY = "inventory.filament"
KIND = CAPABILITY          # transitional alias, removed in Task 4

# (feature constants and everything else stay as they are)
```
and append at the end of the module (after `ALL_CAPABILITIES` exists - place it right below that constant):
```python
DEFINITION = CapabilityDef(
    id=CAPABILITY, version=1, label="Filament inventory",
    description="Where Themis looks up spools and materials, and keeps their weights up to date.",
    features=ALL_CAPABILITIES)
```
`backend/app/plugins/capabilities/__init__.py`:
```python
"""Core capability definitions (design spec §1). A plugin may *provide* any of these; core code consumes them through the
plugin host. Plugin-defined capabilities come from `PluginManifest.defines`, not from here."""
from __future__ import annotations

from .definition import CAP_ID_RE, CapabilityDef
from .filament_inventory import DEFINITION as _FILAMENT

CORE: dict[str, CapabilityDef] = {d.id: d for d in (_FILAMENT,)}

__all__ = ["CAP_ID_RE", "CORE", "CapabilityDef"]
```
Mechanical rename across the repo (Git Bash, from repo root):
```bash
grep -rlE "plugins\.kinds|from \.\.kinds|from \.\.\.plugins\.kinds|\.kinds\.filament_inventory" backend --include=*.py | xargs sed -i -E 's/plugins\.kinds\.filament_inventory/plugins.capabilities.filament_inventory/g; s/from \.\.kinds\.filament_inventory/from ..capabilities.filament_inventory/g; s/\.\.\.plugins\.kinds\./...plugins.capabilities./g'
grep -rn "kinds" backend/app backend/tests --include=*.py
```
The final grep must show only docstring/comment mentions (fix wording of any live import). In `backend/app/plugins/__init__.py` change `m.name != "kinds"` to `m.name != "capabilities"` and the docstring ("everything but `capabilities`, the contracts").

- [ ] **Step 4: Verify**

`pytest tests/plugins/test_capability_catalog.py tests/test_provider_boundary.py tests/plugins/test_filament_inventory_contract.py tests/test_no_provider_in_core.py -v` -> PASS. Then full `pytest -q` -> same pass count as baseline (pure rename).

- [ ] **Step 5: Commit**

```bash
git add -A backend && git commit -m "refactor(plugins): move the filament contract to plugins/capabilities and add CapabilityDef" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01P61WEnnp6c5eUTUcym4kiU"
```

---

### Task 2: Manifest, package format and registry

**Files:**
- Modify: `backend/app/plugins/manifest.py`, `backend/app/plugins/package.py`, `backend/app/plugins/__init__.py`, `backend/tests/plugins/test_manifest_and_registry.py`, `backend/tests/plugins/dummy_plugin.py`
- Test: `backend/tests/plugins/test_manifest_and_registry.py` (extend), new cases in it for toml

**Interfaces:**
- Consumes: `CapabilityDef`, `CAP_ID_RE`, `CORE` (Task 1).
- Produces:
  - `manifest.Provide(version: int = 1, attr: str | None = None, features: frozenset[str] = frozenset(), routers: tuple[APIRouter, ...] = ())` frozen dataclass.
  - `manifest.Requirement(capability: str, min_version: int = 1)` frozen dataclass with `Requirement.parse(text: str) -> Requirement` (`"cap"` or `"cap@N"`).
  - `PluginManifest` loses `kind` and `capabilities`; gains `provides: dict[str, Provide] = {}`, `requires: tuple[Requirement, ...] = ()`, `optional: tuple[Requirement, ...] = ()`, `defines: tuple[CapabilityDef, ...] = ()`. Validation in `__post_init__` (see below).
  - `package.PluginToml` loses `kind`; gains `provides: tuple[tuple[str, int], ...]`, `requires: tuple[tuple[str, int], ...]`, `optional: tuple[tuple[str, int], ...]`, `defines: tuple[str, ...]`. `KNOWN_KINDS` removed. `check_matches` compares them with the manifest.
  - registry in `plugins/__init__.py`: `providers_of(cap_id) -> list[PluginManifest]` (sorted by id; replaces `plugins_of_kind`), `capability_catalog() -> dict[str, CapabilityDef]` (core + `defines` of registered plugins), `definer_of(cap_id) -> str | None` (plugin id, or None for core/unknown).

- [ ] **Step 1: Write the failing tests** (add to `test_manifest_and_registry.py`; update the existing `make_manifest` in `dummy_plugin.py` first so old tests compile: signature becomes `make_manifest(plugin_id="dummy_one", cap="dummy_one.ping", features=frozenset({"PING"}), **over)` producing `provides={cap: Provide(features=features)}` and `defines=(CapabilityDef(cap, 1, "Dummy ping", required_methods=("ping",)),)`; no `kind`/`capabilities`; keep `DummyProvider`.)

```python
import pytest
from app import plugins
from app.plugins import PluginError
from app.plugins.capabilities.definition import CapabilityDef
from app.plugins.manifest import Provide, Requirement
from app.plugins.package import parse_toml, check_matches
from tests.plugins.dummy_plugin import make_manifest

TOML = 'id = "acme_inv"\nname = "A"\nversion = "1.0.0"\nhost_api = 1\nentry = "acme_inv:MANIFEST"\n'


def test_requirement_parse():
    assert Requirement.parse("a.b") == Requirement("a.b", 1)
    assert Requirement.parse("a.b@3") == Requirement("a.b", 3)
    for bad in ("a", "a.b@", "a.b@x", "a.b@0", "A.b"):
        with pytest.raises(PluginError):
            Requirement.parse(bad)


def test_defined_capability_id_must_start_with_plugin_id_and_not_shadow_core():
    d = lambda i: CapabilityDef(i, 1, "x")
    make_manifest("dummy_one", defines=(d("dummy_one.thing"),))
    with pytest.raises(PluginError, match="must start with 'dummy_one.'"):
        make_manifest("dummy_one", defines=(d("other.thing"),))
    with pytest.raises(PluginError, match="core"):
        make_manifest("dummy_one", defines=(d("inventory.filament"),))


def test_provides_validation():
    with pytest.raises(PluginError, match="capability id"):
        make_manifest(provides={"nodot": Provide()})
    with pytest.raises(PluginError, match="version"):
        make_manifest(provides={"a.b": Provide(version=0)})


def test_a_plugin_cannot_require_what_it_provides():
    with pytest.raises(PluginError, match="itself"):
        make_manifest("dummy_one", requires=(Requirement("dummy_one.ping"),))


def test_kind_is_gone():
    assert not hasattr(make_manifest(), "kind")


def test_registry_catalog_and_providers():
    a = make_manifest("plug_a", cap="plug_a.ping")
    b = make_manifest("plug_b", cap="plug_b.ping", provides={"plug_a.ping": Provide()}, defines=())
    plugins.register_plugin(a); plugins.register_plugin(b)
    cat = plugins.capability_catalog()
    assert "inventory.filament" in cat and "plug_a.ping" in cat and "plug_b.ping" not in cat
    assert [m.id for m in plugins.providers_of("plug_a.ping")] == ["plug_a", "plug_b"]
    assert plugins.definer_of("plug_a.ping") == "plug_a" and plugins.definer_of("inventory.filament") is None


def test_toml_parses_capability_lists_and_rejects_kind():
    t = parse_toml(TOML + 'provides = ["inventory.filament@1"]\nrequires = ["x.y@2"]\noptional = ["z.w"]\ndefines = ["acme_inv.reports"]\n')
    assert (t.provides, t.requires, t.optional, t.defines) == ((("inventory.filament", 1),), (("x.y", 2),), (("z.w", 1),), ("acme_inv.reports",))
    with pytest.raises(PluginError, match="unknown keys"):
        parse_toml(TOML + 'kind = "filament_inventory"\n')
    with pytest.raises(PluginError, match="defines"):
        parse_toml(TOML + 'defines = ["acme_inv.reports@2"]\n')


def test_check_matches_compares_capability_lists():
    m = make_manifest("acme_inv", cap="acme_inv.ping")
    ok = parse_toml(TOML.replace("acme_inv:", "dummy:") + 'provides = ["acme_inv.ping@1"]\ndefines = ["acme_inv.ping"]\n')
    check_matches(ok, m)
    bad = parse_toml(TOML + 'provides = ["acme_inv.ping@2"]\ndefines = ["acme_inv.ping"]\n')
    with pytest.raises(PluginError, match="provides"):
        check_matches(bad, m)
```
(Adjust `make_manifest` so `plugin_id="acme_inv"` + `cap="acme_inv.ping"` yields `provides={"acme_inv.ping": Provide(version=1, ...)}` and `defines` of that id; the TOML `version` must equal the manifest `1.0.0`.)

- [ ] **Step 2: Run to verify failure**

`pytest tests/plugins/test_manifest_and_registry.py -v` -> FAIL (`Provide`, `Requirement` missing, etc.).

- [ ] **Step 3: Implement**

`manifest.py`: add after `UiContribution`:
```python
@dataclass(frozen=True)
class Provide:
    """How a plugin serves one capability. `attr` names the attribute of the plugin's instance that serves it (None = the
    instance itself); `features` are the capability's feature flags this provider supports; `routers` are mounted under
    /api/v1/plugins/{id}/... AND dispatched at /api/v1/capabilities/{cap}/... to whichever plugin is active."""
    version: int = 1
    attr: str | None = None
    features: frozenset[str] = frozenset()
    routers: tuple[APIRouter, ...] = ()


@dataclass(frozen=True)
class Requirement:
    capability: str
    min_version: int = 1

    @classmethod
    def parse(cls, text: str) -> "Requirement":
        cap, _, ver = text.partition("@")
        if not CAP_ID_RE.match(cap):
            raise PluginError(f"{text!r}: not a capability id")
        if "@" in text:
            if not ver.isdigit() or int(ver) < 1:
                raise PluginError(f"{text!r}: the minimum version after '@' must be a positive integer")
            return cls(cap, int(ver))
        return cls(cap, 1)
```
Imports: `from .capabilities import CORE`, `from .capabilities.definition import CAP_ID_RE, CapabilityDef`. In `PluginManifest`: delete `kind` and `capabilities`; add after `factory`:
```python
    provides: dict[str, Provide] = field(default_factory=dict)
    requires: tuple[Requirement, ...] = ()
    optional: tuple[Requirement, ...] = ()
    defines: tuple[CapabilityDef, ...] = ()
```
Extend `__post_init__` (before the migrations loop):
```python
        for cap, p in self.provides.items():
            if not CAP_ID_RE.match(cap):
                raise PluginError(f"plugin {self.id!r}: provides {cap!r}, which is not a capability id")
            if p.version < 1:
                raise PluginError(f"plugin {self.id!r}: provides {cap!r} with version {p.version}; versions start at 1")
        seen: set[str] = set()
        for d in self.defines:
            if not d.id.startswith(f"{self.id}."):
                raise PluginError(f"plugin {self.id!r}: defined capability {d.id!r} must start with '{self.id}.'")
            if d.id in CORE:
                raise PluginError(f"plugin {self.id!r}: cannot redefine the core capability {d.id!r}")
            if not CAP_ID_RE.match(d.id) or d.version < 1 or d.id in seen:
                raise PluginError(f"plugin {self.id!r}: bad or duplicate defined capability {d.id!r}")
            seen.add(d.id)
        for r in (*self.requires, *self.optional):
            if r.capability in self.provides:
                raise PluginError(f"plugin {self.id!r}: requires or lists {r.capability!r} as optional, which it provides itself")
```
(The "itself" wording must satisfy the test's `match="itself"`.) Note: a plugin that *provides* a cap it also defines is fine and typical.

`plugins/__init__.py`: export `Provide`, `Requirement`; replace `plugins_of_kind` with:
```python
def providers_of(cap_id: str) -> list[PluginManifest]:
    return sorted((m for m in _REGISTRY.values() if cap_id in m.provides), key=lambda m: m.id)


def capability_catalog() -> dict[str, "CapabilityDef"]:
    """Core definitions plus the `defines` of every registered plugin."""
    from .capabilities import CORE
    out = dict(CORE)
    for m in _REGISTRY.values():
        for d in m.defines:
            out[d.id] = d
    return out


def definer_of(cap_id: str) -> str | None:
    return next((m.id for m in _REGISTRY.values() if any(d.id == cap_id for d in m.defines)), None)
```
Update `__all__`.

`package.py`: remove `KIND` import / `KNOWN_KINDS` / `kind` in `PluginToml`, `_ALLOWED`, `parse_toml`; add `"provides", "requires", "optional", "defines"` to `_ALLOWED`; fields on `PluginToml`; helper:
```python
def _caps(data: dict, key: str) -> tuple[tuple[str, int], ...]:
    raw = data.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw) or len(raw) > 50:
        raise PluginError(f"{TOML_NAME}: `{key}` must be a list of capability ids")
    out = tuple((r.capability, r.min_version) for r in map(Requirement.parse, raw))
    if len({c for c, _ in out}) != len(out):
        raise PluginError(f"{TOML_NAME}: `{key}` lists a capability twice")
    return out
```
`defines = _caps(...)`, then reject any `@` in the raw strings: `if any("@" in x for x in data.get("defines", [])): raise PluginError(f"{TOML_NAME}: `defines` takes plain ids, no @version")` and store `tuple(c for c, _ in ...)`. `check_matches`:
```python
    for field in ("id", "version", "host_api"):
        if getattr(manifest, field) != getattr(t, field):
            raise PluginError(f"{TOML_NAME} {field} {getattr(t, field)!r} != MANIFEST {field} {getattr(manifest, field)!r}")
    got = {
        "provides": sorted((c, p.version) for c, p in manifest.provides.items()),
        "requires": sorted((r.capability, r.min_version) for r in manifest.requires),
        "optional": sorted((r.capability, r.min_version) for r in manifest.optional),
        "defines": sorted(d.id for d in manifest.defines),
    }
    for field, have in got.items():
        if sorted(getattr(t, field)) != have:
            raise PluginError(f"{TOML_NAME} {field} {sorted(getattr(t, field))!r} != MANIFEST {field} {have!r}")
```
(error text must contain the field name; the test matches `provides`.)

- [ ] **Step 4: Verify**

`pytest tests/plugins/test_manifest_and_registry.py -v` -> PASS (update the pre-existing cases in that file that referenced `kind`/`plugins_of_kind`/`capabilities` to the new shape in the same edit).

- [ ] **Step 5: Commit** `refactor(plugins): manifest and toml declare provides/requires/optional/defines instead of a kind`

---

### Task 3: Host selection, instances, dependencies; table and migration v040

**Files:**
- Create: `backend/app/migrations/v040_capability_selections.py`, `backend/tests/plugins/test_host_capabilities.py`, `backend/tests/plugins/test_v040_migration.py`
- Modify: `backend/app/plugins/host.py`, `backend/app/models.py` (replace `ExtensionSlot` with `CapabilitySelection`; drop `InstalledPlugin.kind`), `backend/app/migrations/runner.py` (import + register v040), `backend/tests/plugins/test_host.py` (port to the capability API), `backend/tests/plugins/conftest.py` (unchanged unless the new fixtures need it)

**Interfaces:**
- Consumes: Task 2 registry/manifest.
- Produces (all on `PluginHost`, singleton `plugin_host`):
  - `ActivePlugin(manifest, instance, capability: str, part: Any, features: frozenset[str])`
  - `active(cap) -> ActivePlugin | None`; `has(cap, feature) -> bool`; `part(cap) -> Any | None`; `selected(cap) -> str | None` (stored choice, replaces `slot`); `is_explicit(cap) -> bool`
  - `status(cap) -> CapabilityStatus` where `CapabilityStatus(state: Literal["serving","waiting","error","disabled","none_selected","no_provider","dormant"], plugin_id: str | None = None, waiting_on: tuple[str, ...] = (), error: str | None = None)`
  - `unmet(plugin_id) -> tuple[str, ...]` (unmet `requires` capability ids)
  - `async call(cap, method, *args, timeout=DEFAULT_TIMEOUT_S, **kwargs) -> CallResult` (same containment)
  - `async set_provider(cap, plugin_id: str | None) -> None` (raises `PluginError`: unknown cap, plugin not providing it, would create a cycle). Replaces `set_slot`. Writes an explicit row, enables the plugin when not None.
  - `selections() -> dict[str, str | None]` snapshot of stored rows.
  - Table `capability_selections(capability VARCHAR(96) PK, plugin_id VARCHAR(64) NULL, explicit BOOLEAN NOT NULL DEFAULT 0)`; model `CapabilitySelection(capability, plugin_id, explicit)`.
  - Removed: `slot`, `set_slot`, `ExtensionSlot`, `_slots`.

- [ ] **Step 1: Failing tests** `test_host_capabilities.py` (uses the `host` fixture from `tests/plugins/conftest.py`, `make_manifest` from `dummy_plugin`; helper `reg(*ms)` registers manifests). Write one test per rule:

```python
import pytest

from app import plugins
from app.plugins import PluginError
from app.plugins.capabilities.definition import CapabilityDef
from app.plugins.manifest import Provide, Requirement
from tests.plugins.dummy_plugin import DummyProvider, make_manifest


def reg(*ms):
    for m in ms:
        plugins.register_plugin(m)


async def enable(host, *ids):
    for i in ids:
        await host.update_config(i, enabled=True)


def definer(cap="shared.ping", methods=("ping",)):
    """A plugin that only *defines* `cap` (provides nothing), so several others can provide it."""
    return make_manifest("shared", provides={}, defines=(CapabilityDef(cap, 1, "Shared", required_methods=methods),))


def provider(pid, cap="shared.ping", **over):
    return make_manifest(pid, provides={cap: Provide(features=frozenset({"PING"}))}, defines=(), **over)


async def test_auto_select_only_the_unambiguous_case(host):
    reg(definer(), provider("plug_a"), provider("plug_b"))
    await host.start()
    await enable(host, "plug_a")
    assert host.selected("shared.ping") == "plug_a" and not host.is_explicit("shared.ping")
    await enable(host, "plug_b")                                  # a later provider never displaces a choice
    assert host.selected("shared.ping") == "plug_a" and host.active("shared.ping").manifest.id == "plug_a"


async def test_two_enabled_providers_and_no_row_selects_nothing(host):
    reg(definer(), provider("plug_a"), provider("plug_b"))
    await host.start()
    await host.update_config("plug_a", enabled=True)              # auto-selected while alone
    await host.set_provider("shared.ping", None)                  # user clears it
    await enable(host, "plug_b")                                  # a sole *other* enabled provider must not be auto-selected now
    assert host.selected("shared.ping") is None and host.is_explicit("shared.ping")
    assert host.active("shared.ping") is None


async def test_explicit_none_is_remembered_across_reload(host):
    reg(definer(), provider("plug_a"))
    await host.start()
    await host.set_provider("shared.ping", None)
    await host.reload()
    await enable(host, "plug_a")
    assert host.selected("shared.ping") is None and host.status("shared.ping").state == "none_selected"


async def test_set_provider_enables_the_plugin_and_builds_one_instance_for_two_capabilities(host):
    m = make_manifest("multi", cap="multi.one", provides={"multi.one": Provide(), "multi.two": Provide()},
                      defines=(CapabilityDef("multi.one", 1, "One"), CapabilityDef("multi.two", 1, "Two")))
    reg(m)
    await host.start()
    await host.set_provider("multi.one", "multi")
    await host.set_provider("multi.two", "multi")
    assert host.is_enabled("multi") and len(DummyProvider.instances) == 1
    assert host.active("multi.one").instance is host.active("multi.two").instance


async def test_part_uses_attr_and_features_and_call_goes_to_the_part(host):
    class Svc:
        async def ping(self, value="pong"):
            return f"svc:{value}"

    class Provider(DummyProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.svc = Svc()

    m = make_manifest("attrp", cap="attrp.ping", factory=Provider,
                      provides={"attrp.ping": Provide(attr="svc", features=frozenset({"X"}))})
    reg(m)
    await host.start()
    await host.set_provider("attrp.ping", "attrp")
    assert host.has("attrp.ping", "X") and not host.has("attrp.ping", "Y")
    assert isinstance(host.part("attrp.ping"), Svc)
    r = await host.call("attrp.ping", "ping", "hi")
    assert r.ok and r.value == "svc:hi"


async def test_unmet_requires_makes_a_plugin_wait_and_offer_nothing(host):
    base = make_manifest("plug_a", cap="plug_a.ping")
    consumer = make_manifest("consumer", cap="consumer.use", requires=(Requirement("plug_a.ping"),))
    reg(base, consumer)
    await host.start()
    await host.set_provider("consumer.use", "consumer")
    st = host.status("consumer.use")
    assert (st.state, st.waiting_on) == ("waiting", ("plug_a.ping",))
    assert host.active("consumer.use") is None and DummyProvider.instances == []
    await host.set_provider("plug_a.ping", "plug_a")              # no restart needed
    assert host.status("consumer.use").state == "serving" and len(DummyProvider.instances) == 2


async def test_requires_min_version(host):
    base = make_manifest("plug_a", cap="plug_a.ping")             # provides v1
    consumer = make_manifest("consumer", cap="consumer.use", requires=(Requirement("plug_a.ping", 2),))
    reg(base, consumer)
    await host.start()
    await host.set_provider("plug_a.ping", "plug_a")
    await host.set_provider("consumer.use", "consumer")
    assert host.status("consumer.use").state == "waiting"


async def test_selection_cycle_is_rejected_and_leaves_no_row(host, session_factory):
    a = make_manifest("plug_a", cap="plug_a.x", requires=(Requirement("plug_b.x"),))
    b = make_manifest("plug_b", cap="plug_b.x", requires=(Requirement("plug_a.x"),))
    reg(a, b)
    await host.start()
    await host.set_provider("plug_b.x", "plug_b")                 # fine on its own: b is waiting on a.x
    with pytest.raises(PluginError, match="cycle"):
        await host.set_provider("plug_a.x", "plug_a")
    assert host.selected("plug_a.x") is None
    from app.models import CapabilitySelection
    async with session_factory() as s:
        assert await s.get(CapabilitySelection, "plug_a.x") is None


async def test_requirement_cycle_reached_by_auto_select_does_not_recurse_forever(host):
    a = make_manifest("plug_a", cap="plug_a.x", requires=(Requirement("plug_b.x"),))
    b = make_manifest("plug_b", cap="plug_b.x", requires=(Requirement("plug_a.x"),))
    reg(a, b)
    await host.start()
    await enable(host, "plug_a", "plug_b")                        # both auto-selected as sole providers
    assert host.status("plug_a.x").state == "waiting" and host.status("plug_b.x").state == "waiting"
    assert DummyProvider.instances == []


async def test_dormant_selection_when_the_definer_is_uninstalled_and_restored_when_it_returns(host):
    m = make_manifest("plug_a", cap="plug_a.ping")
    reg(m)
    await host.start()
    await host.set_provider("plug_a.ping", "plug_a")
    plugins._REGISTRY.pop("plug_a")
    await host.reload()
    assert host.status("plug_a.ping").state == "dormant" and host.active("plug_a.ping") is None
    assert host.selected("plug_a.ping") == "plug_a"               # kept
    reg(m)
    await host.reload()
    assert host.status("plug_a.ping").state == "serving"


async def test_selection_for_a_plugin_that_no_longer_exists_is_no_provider(host):
    reg(definer(), provider("plug_a"))
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    plugins._REGISTRY.pop("plug_a")
    await host.reload()
    assert host.status("shared.ping").state == "no_provider" and host.active("shared.ping") is None


async def test_duck_typing_required_methods_are_checked_when_the_instance_is_built(host):
    reg(definer(methods=("ping", "pong")), provider("plug_a"))     # DummyProvider has ping but not pong
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    st = host.status("shared.ping")
    assert st.state == "error" and "pong" in (st.error or "") and host.active("shared.ping") is None


async def test_provide_version_must_match_the_definition(host):
    reg(definer(), make_manifest("plug_a", provides={"shared.ping": Provide(version=2)}, defines=()))
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    assert host.status("shared.ping").state == "error" and "v2" in (host.status("shared.ping").error or "")


async def test_provide_attr_that_the_instance_lacks_is_a_build_error(host):
    reg(definer(), make_manifest("plug_a", provides={"shared.ping": Provide(attr="nope")}, defines=()))
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    assert host.status("shared.ping").state == "error" and "nope" in (host.status("shared.ping").error or "")


async def test_set_provider_rejects_unknown_capability_and_non_provider(host):
    reg(definer(), provider("plug_a"), make_manifest("other", cap="other.ping"))
    await host.start()
    with pytest.raises(PluginError, match="unknown capability"):
        await host.set_provider("nope.nothing", "plug_a")
    with pytest.raises(PluginError, match="does not provide"):
        await host.set_provider("shared.ping", "other")


async def test_call_is_contained_by_capability(host):
    reg(make_manifest("plug_a", cap="plug_a.ping"))
    await host.start()
    await host.update_config("plug_a", enabled=True, settings={"mode": "raise"})
    r = await host.call("plug_a.ping", "ping")
    assert (r.ok, r.reason) == (False, "error") and "exploded" in r.error
    assert (await host.call("nothing.here", "ping")).reason == "inactive"
    assert (await host.call("plug_a.ping", "no_such_method")).reason == "inactive"
    await host.update_config("plug_a", settings={"mode": "hang"})
    assert (await host.call("plug_a.ping", "ping", timeout=0.05)).reason == "timeout"
```
`make_manifest` (in `dummy_plugin.py`, updated in Task 2) is `make_manifest(plugin_id="dummy_one", cap=None, features=frozenset({"PING"}), **over)`: `cap` defaults to `f"{plugin_id}.ping"`; unless overridden it builds `provides={cap: Provide(features=features)}` and `defines=(CapabilityDef(cap, 1, "Dummy ping", required_methods=("ping",)),)` (empty `defines` when `cap` does not start with `plugin_id + "."`); other keyword args (`provides`, `defines`, `requires`, `factory`, ...) override the manifest fields. `DummyProvider` already has `ping` and the `mode` setting (`ok|raise|hang|bad-config`).

`test_v040_migration.py` (pattern of `test_v039_migration.py`, fixture `migrated` from `build_v032_fixture_db` + `run_migrations`):
```python
async def test_creates_table_drops_slots_and_installed_plugins_kind(migrated):
    tables = await _tables(migrated)
    assert "capability_selections" in tables and "extension_slots" not in tables
    cols = {r[1] for r in (await migrated.execute(text("PRAGMA table_info(installed_plugins)"))).fetchall()}
    assert "kind" not in cols

async def test_up_maps_legacy_slots_as_explicit_choices(migrated):
    await v040_capability_selections.down(migrated)               # reproduce the pre-040 shape on the fully migrated fixture
    await migrated.execute(text("INSERT INTO extension_slots (kind, plugin_id) VALUES ('filament_inventory', 'spoolman'), ('mystery_kind', NULL)"))
    await v040_capability_selections.up(migrated)
    rows = (await migrated.execute(text("SELECT capability, plugin_id, explicit FROM capability_selections ORDER BY capability"))).fetchall()
    assert [tuple(r) for r in rows] == [("inventory.filament", "spoolman", 1), ("mystery_kind", None, 1)]
    assert "extension_slots" not in await _tables(migrated)


async def test_down_restores_slots_and_kind_column_and_up_is_idempotent(migrated):
    await migrated.execute(text("INSERT INTO capability_selections (capability, plugin_id, explicit) VALUES ('inventory.filament', 'local_inventory', 1)"))
    await v040_capability_selections.down(migrated)
    assert (await migrated.execute(text("SELECT kind, plugin_id FROM extension_slots"))).fetchall() == [("filament_inventory", "local_inventory")]
    assert "capability_selections" not in await _tables(migrated)
    assert "kind" in {r[1] for r in (await migrated.execute(text("PRAGMA table_info(installed_plugins)"))).fetchall()}
    await v040_capability_selections.up(migrated)
    await v040_capability_selections.up(migrated)                   # second run is a no-op
    assert (await migrated.execute(text("SELECT plugin_id FROM capability_selections WHERE capability='inventory.filament'"))).scalar() == "local_inventory"
```
(Test module imports: `from sqlalchemy import text`, `from app.migrations import v040_capability_selections`, the `migrated` fixture and `_tables` helper exactly as in `test_v039_migration.py`.)

- [ ] **Step 2: Run to verify failure** `pytest tests/plugins/test_host_capabilities.py tests/plugins/test_v040_migration.py -v` -> FAIL (no `set_provider`, no migration).

- [ ] **Step 3: Implement**

`models.py`: replace `ExtensionSlot` with
```python
class CapabilitySelection(Base):
    """Which plugin serves a capability. `explicit` = the user chose (including "None"); False = auto-selected because it was
    the only enabled provider. A provider is *active* iff the row names it AND it is enabled AND its requirements are met."""
    __tablename__ = "capability_selections"

    capability: Mapped[str] = mapped_column(String(96), primary_key=True)
    plugin_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)
```
(import `Boolean` if not already) and delete `InstalledPlugin.kind` (line ~617).

`v040_capability_selections.py`:
```python
"""Capability selections (replaces extension_slots) and drop installed_plugins.kind (capability model)."""
from __future__ import annotations
from sqlalchemy import text

version = 40
name = "capability_selections"

_LEGACY = {"filament_inventory": "inventory.filament"}


async def _has_table(conn, table: str) -> bool:
    return (await conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"), {"n": table})).first() is not None


async def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall())


async def up(conn) -> None:
    await conn.execute(text("""CREATE TABLE IF NOT EXISTS capability_selections (
        capability VARCHAR(96) PRIMARY KEY, plugin_id VARCHAR(64), explicit BOOLEAN NOT NULL DEFAULT 0)"""))
    if await _has_table(conn, "extension_slots"):
        for kind, plugin_id in (await conn.execute(text("SELECT kind, plugin_id FROM extension_slots"))).fetchall():
            await conn.execute(text("INSERT OR IGNORE INTO capability_selections (capability, plugin_id, explicit) VALUES (:c, :p, 1)"),
                               {"c": _LEGACY.get(kind, kind), "p": plugin_id})
        await conn.execute(text("DROP TABLE extension_slots"))
    if await _has_column(conn, "installed_plugins", "kind"):
        await conn.execute(text("ALTER TABLE installed_plugins DROP COLUMN kind"))


async def down(conn) -> None:
    await conn.execute(text("CREATE TABLE IF NOT EXISTS extension_slots (kind VARCHAR(64) PRIMARY KEY, plugin_id VARCHAR(64))"))
    back = {v: k for k, v in _LEGACY.items()}
    if await _has_table(conn, "capability_selections"):
        for cap, plugin_id in (await conn.execute(text("SELECT capability, plugin_id FROM capability_selections"))).fetchall():
            await conn.execute(text("INSERT OR IGNORE INTO extension_slots (kind, plugin_id) VALUES (:k, :p)"), {"k": back.get(cap, cap), "p": plugin_id})
        await conn.execute(text("DROP TABLE capability_selections"))
    if not await _has_column(conn, "installed_plugins", "kind"):
        await conn.execute(text("ALTER TABLE installed_plugins ADD COLUMN kind VARCHAR(64) NOT NULL DEFAULT ''"))
```
Register in `runner.py` (add `v040_capability_selections` to the import line and to the ordered list, matching the v039 pattern). Update `tests/plugins/test_v035_migration.py` and any test that inspects `extension_slots` (grep) to the new table/mapping.

`host.py` - rewrite the keyed-by-kind parts. Replace imports (`CapabilitySelection` for `ExtensionSlot`; `from . import PluginError, capability_catalog, get_plugin`). Module docstring: active rule = selection names the plugin AND enabled AND instance built AND requirements met. Replace `_slots` with `_selections: dict[str, str | None]` and `_explicit: dict[str, bool]`. Key code:

```python
@dataclass(frozen=True)
class ActivePlugin:
    manifest: PluginManifest
    instance: Any
    capability: str
    part: Any
    features: frozenset[str]


@dataclass(frozen=True)
class CapabilityStatus:
    state: Literal["serving", "waiting", "error", "disabled", "none_selected", "no_provider", "dormant"]
    plugin_id: str | None = None
    waiting_on: tuple[str, ...] = ()
    error: str | None = None


def _part_of(instance: Any, provide: Provide) -> Any:
    return instance if provide.attr is None else getattr(instance, provide.attr, None)
```
`_reload_locked`:
```python
        async with self._session_factory() as s:
            rows = list((await s.execute(select(CapabilitySelection))).scalars())
            selections = {r.capability: r.plugin_id for r in rows}
            explicit = {r.capability: bool(r.explicit) for r in rows}
            configs = {...unchanged...}
            for cap in capability_catalog():                       # auto-select the unambiguous case only (spec decision 5)
                if cap in selections:
                    continue
                enabled = [m.id for m in providers_of(cap) if configs.get(m.id) and configs[m.id].enabled]
                if len(enabled) == 1:
                    s.add(CapabilitySelection(capability=cap, plugin_id=enabled[0], explicit=False))
                    selections[cap], explicit[cap] = enabled[0], False
            await s.commit()
        self._selections, self._explicit, self._configs = selections, explicit, configs
        wanted = {pid for pid in {p for p in selections.values() if p} if self._is_enabled(pid) and self._serves_any(pid) and not self.unmet(pid)}
```
(import `providers_of` too.) `_serves_any(pid)`: `any(sel == pid and cap in get_plugin(pid).provides and cap in catalog for cap, sel in self._selections.items())`. The rest of `_reload_locked` (closing unwanted, rebuild by fingerprint, hooks) is unchanged.

`unmet`:
```python
    def unmet(self, plugin_id: str, _seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
        m = get_plugin(plugin_id)
        if m is None:
            return ()
        catalog, out = capability_catalog(), []
        for req in m.requires:
            pid = self._selections.get(req.capability)
            prov = get_plugin(pid).provides.get(req.capability) if pid and get_plugin(pid) else None
            ok = (req.capability in catalog and pid is not None and pid != plugin_id and pid not in _seen
                  and self._is_enabled(pid) and prov is not None and prov.version >= req.min_version
                  and not self.unmet(pid, _seen | {plugin_id}))
            if not ok:
                out.append(req.capability)
        return tuple(out)
```
`_rebuild` after `built = manifest.factory(settings)` add `self._check_contract(manifest, built)`:
```python
    @staticmethod
    def _check_contract(manifest: PluginManifest, instance: Any) -> None:
        catalog = capability_catalog()
        for cap, prov in manifest.provides.items():
            d = catalog.get(cap)
            part = _part_of(instance, prov)
            if part is None:
                raise PluginError(f"provides {cap} through attribute {prov.attr!r}, which the instance does not have")
            if d is None:
                continue                                        # definer not registered: nothing to verify yet
            if d.version != prov.version:
                raise PluginError(f"provides {cap} v{prov.version} but this Themis knows v{d.version}")
            missing = [n for n in d.required_methods if not inspect.iscoroutinefunction(getattr(part, n, None))]
            if missing:
                raise PluginError(f"{cap}: missing async method(s) {', '.join(missing)}")
```
(`import inspect`.) Public API:
```python
    def active(self, cap: str) -> ActivePlugin | None:
        pid = self._selections.get(cap)
        m = get_plugin(pid) if pid else None
        prov = m.provides.get(cap) if m else None
        inst = self._instances.get(pid) if pid else None
        if m is None or prov is None or inst is None or cap not in capability_catalog() or not self._is_enabled(pid):
            return None
        return ActivePlugin(m, inst, cap, _part_of(inst, prov), prov.features)

    def has(self, cap, feature) -> bool: a = self.active(cap); return a is not None and feature in a.features
    def part(self, cap): a = self.active(cap); return a.part if a else None
    def selected(self, cap) -> str | None: return self._selections.get(cap)
    def is_explicit(self, cap) -> bool: return self._explicit.get(cap, False)
    def selections(self) -> dict[str, str | None]: return dict(self._selections)

    def status(self, cap: str) -> CapabilityStatus:
        if cap not in capability_catalog():
            return CapabilityStatus("dormant", self._selections.get(cap))
        pid = self._selections.get(cap)
        if pid is None:
            return CapabilityStatus("none_selected" if cap in self._selections else ("no_provider" if not providers_of(cap) else "none_selected"))
        m = get_plugin(pid)
        if m is None or cap not in m.provides:
            return CapabilityStatus("no_provider", pid)
        if not self._is_enabled(pid):
            return CapabilityStatus("disabled", pid)
        if (missing := self.unmet(pid)):
            return CapabilityStatus("waiting", pid, waiting_on=missing)
        if (err := self._build_errors.get(pid)):
            return CapabilityStatus("error", pid, error=err)
        return CapabilityStatus("serving", pid) if self.active(cap) else CapabilityStatus("error", pid)
```
`call(cap, method, ...)`: identical to today's body but `active = self.active(cap)`, `fn = getattr(active.part, method, None)`, error text `no active {cap} provider`.

`set_provider`:
```python
    async def set_provider(self, cap: str, plugin_id: str | None) -> None:
        """Choose the provider of `cap` (None = none, remembered). Selecting a provider enables it."""
        if cap not in capability_catalog():
            raise PluginError(f"unknown capability {cap!r}")
        if plugin_id is not None:
            m = get_plugin(plugin_id)
            if m is None or cap not in m.provides:
                raise PluginError(f"{plugin_id!r} does not provide {cap}")
            self._reject_cycle(cap, plugin_id)
        assert self._session_factory is not None
        async with self._lock:
            async with self._session_factory() as s:
                row = await s.get(CapabilitySelection, cap)
                if row is None:
                    s.add(CapabilitySelection(capability=cap, plugin_id=plugin_id, explicit=True))
                else:
                    row.plugin_id, row.explicit = plugin_id, True
                if plugin_id is not None:
                    cfg = await s.get(PluginConfig, plugin_id)
                    if cfg is None:
                        s.add(PluginConfig(plugin_id=plugin_id, enabled=True, settings={}, secrets={}, state={}))
                    else:
                        cfg.enabled = True
                await s.commit()
            await self._reload_locked()

    def _reject_cycle(self, cap: str, plugin_id: str) -> None:
        sel = {**self._selections, cap: plugin_id}
        path: list[str] = []

        def walk(pid: str) -> bool:
            m = get_plugin(pid)
            for req in (m.requires if m else ()):
                nxt = sel.get(req.capability)
                if nxt is None:
                    continue
                if nxt == plugin_id:
                    path.extend([pid, plugin_id]); return True
                if nxt not in path and (path.append(pid) or True) and walk(nxt):
                    return True
            return False
        if walk(plugin_id):
            raise PluginError(f"selecting {plugin_id!r} for {cap} would create a requirement cycle ({' -> '.join(path)})")
```
Simplify `walk` while implementing (visited set; the test only needs: a cycle through `plugin_id` raises `PluginError` containing "cycle", and no DB row is written). Update `update_config` unchanged. Remove `slot`/`set_slot`. `_reset` clears `_selections`/`_explicit`.

- [ ] **Step 4: Verify** `pytest tests/plugins/test_host_capabilities.py tests/plugins/test_v040_migration.py tests/plugins/test_host.py tests/plugins/test_plugin_migrations.py -v` -> PASS. Mutation check: temporarily make `_reload_locked` auto-select when `len(enabled) >= 1` -> `test_auto_select_only_the_unambiguous_case` must fail; revert.

- [ ] **Step 5: Commit** `feat(plugins): capability selections, dependency gating and v040 migration in the host`

---

### Task 4: Core consumers, bundled plugins, installer, test helpers

**Files:**
- Modify: `backend/app/services/inventory/{provider,sync}.py`, `backend/app/plugins/spoolman/{__init__,routes,themis-plugin.toml}`, `backend/app/plugins/local_inventory/{__init__,themis-plugin.toml}`, `backend/app/plugins/installer.py` (preview, `_DRYRUN`, row.kind), `backend/app/api/routes/plugin_install.py` (`_remove_data`), `backend/app/main.py` (router mounting + `CapabilityUnavailable` payload), `backend/app/plugins/capabilities/filament_inventory.py` (drop `KIND`), `backend/tests/{inventory_helpers,fake_providers}.py`, `backend/tests/plugins/{pkg_builder,test_installer,test_local_inventory,test_plugin_install_routes,test_filament_inventory_contract}.py`, `backend/tests/test_{provider_boundary,no_provider_in_core}.py`, and each test the grep `grep -rln "set_slot\|KIND\b\|kind=" backend/tests` lists.

**Interfaces:**
- Consumes: Task 3 host API.
- Produces: `services/inventory/provider.py` keeps its public functions (`provider_id()`, `active_provider()`, `has(feature)`, `require(feature=None)`, `call(method, ...)`, `describe_failure`) but goes through `plugin_host.active/has/part/call(CAPABILITY, ...)`; `CapabilityUnavailable(feature=None)` keeps `.capability` (the feature) and `.kind` becomes `.capability_id = "inventory.filament"`; the 409 body becomes `{"error": "capability_unavailable", "capability": "inventory.filament", "feature": exc.feature}`. Bundled manifests: `provides={CAPABILITY: Provide(version=1, features=<Provider>.capabilities, routers=(...))}`. `tests.inventory_helpers.enable_spoolman`/`use_provider` use `plugin_host.set_provider(CAPABILITY, id)`.

- [ ] **Step 1: Failing tests.** Update/add:
  - `tests/plugins/test_installer.py`: the preview dict has `provides` (list of `{capability, version}`) and no `kind`; a toml with `kind = ...` is rejected (`unknown keys`); toml `provides` disagreeing with the MANIFEST is rejected by the dry-run (`@@themis-manifest@@` payload now carries `provides/requires/optional/defines`).
  - `tests/plugins/pkg_builder.py`: `TOML` template drops `kind`, adds `provides = ["inventory.filament@1"]`; `CODE` uses `from app.plugins.capabilities.filament_inventory import CAPABILITY` and `provides={CAPABILITY: Provide()}`; `files(...)` loses the `kind=` parameter (add `provides_toml` default above).
  - `tests/test_provider_boundary.py` line ~172 sample import strings use `plugins.capabilities.`; the `KIND`-based checks use the new constant.
  - A new test in `tests/services/test_inventory_provider_access.py`: with `use_provider(FakeInventoryProvider())`, `provider.has("TRACKS_WEIGHT")` is True, `provider.require("NOPE")` raises `CapabilityUnavailable` with `.feature == "NOPE"`, and with no provider it raises with `.capability_id == "inventory.filament"` (fails before the change: attribute missing).
  - `tests/plugins/test_local_inventory.py`: spoolman/local inventory manifests satisfy `provides == {"inventory.filament": ...}` and `"kind"` absent.

- [ ] **Step 2: Run to verify failure** `pytest tests/plugins/test_installer.py tests/services/test_inventory_provider_access.py tests/plugins/test_local_inventory.py -v`.

- [ ] **Step 3: Implement**
  - `services/inventory/provider.py`:
```python
from ...plugins.capabilities.filament_inventory import CAPABILITY, FilamentInventoryProvider

class CapabilityUnavailable(Exception):
    """A route/feature needs a provider or feature that is not available (HTTP 409 `capability_unavailable`)."""
    def __init__(self, feature: str | None = None) -> None:
        super().__init__(f"capability_unavailable: {feature or CAPABILITY}")
        self.capability_id, self.feature = CAPABILITY, feature

def provider_id() -> str | None:
    a = plugin_host.active(CAPABILITY)
    return a.manifest.id if a else None

def active_provider() -> FilamentInventoryProvider | None:
    return plugin_host.part(CAPABILITY)

def has(feature: str) -> bool:
    return plugin_host.has(CAPABILITY, feature)
```
  `require` and `call`/`describe_failure` change only the constant. In `main.py` the handler body: `content={"error": "capability_unavailable", "capability": exc.capability_id, "feature": exc.feature}`. `grep -rn "exc.kind\|\.capability\b" backend/app` for other readers of the old attributes and fix them; the frontend reads the 409 body only via the status code (verify with `grep -rn "capability_unavailable" frontend/src`).
  - `sync.py:30`: `pid = plugin_id or plugin_host.selected(CAPABILITY)`; imports `CAPABILITY` instead of `KIND`.
  - `spoolman/routes.py`: import `CAPABILITY`; `if body.enabled and plugin_host.selected(CAPABILITY) != PLUGIN_ID: await plugin_host.set_provider(CAPABILITY, PLUGIN_ID)`; replace other `KIND` uses.
  - Bundled manifests:
```python
# spoolman/__init__.py
from ..capabilities.filament_inventory import CAPABILITY
from ..manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab
...
    provides={CAPABILITY: Provide(version=1, features=SpoolmanProvider.capabilities)},
# (remove kind=, capabilities=)
# local_inventory/__init__.py
    provides={CAPABILITY: Provide(version=1, features=LocalInventoryProvider.capabilities, routers=(router,))},
# (remove kind=, capabilities=, routers=(router,)) -- the router is now mounted through Provide.routers
```
  tomls: delete the `kind = ...` line, add `provides = ["inventory.filament@1"]` to both.
  - `main.py` mounting (replace the loop at ~line 222):
```python
for _manifest in registered_plugins():
    _own = (*_manifest.routers, *(r for p in _manifest.provides.values() for r in p.routers))
    for _router in _own:
        app.include_router(_router, prefix=f"/api/v1/plugins/{_manifest.id}")
    for _router in _manifest.alias_routers:
        app.include_router(_router)
```
  (A router object listed under two capabilities of one plugin is mounted twice - dedupe by `id(router)` with a `seen` set.)
  - `installer.py`: `preview()` replaces `"kind": t.kind` with `"provides": [{"capability": c, "version": v} for c, v in t.provides]`, `"requires": [...]`, `"optional": [...]`, `"defines": list(t.defines)`; `_DRYRUN` prints `{"id","version","host_api","migrations","provides": sorted([c, p.version] ...), "requires": sorted([[r.capability, r.min_version] ...]), "optional": ..., "defines": sorted(d.id ...)}`; wherever the dry-run output is compared to the toml (grep `themis-manifest` and `host_api` in `installer.py`) compare the capability lists too; line ~540 drops `row.kind`.
  - `plugin_install.py::_remove_data`: replace the ExtensionSlot loop with `await session.execute(delete(CapabilitySelection).where(CapabilitySelection.plugin_id == plugin_id))` (so auto-select can reconsider later); import `delete` from sqlalchemy.
  - Remove `KIND = CAPABILITY` from `capabilities/filament_inventory.py`; fix remaining `KIND` references (`grep -rn "\bKIND\b" backend`).
  - `tests/inventory_helpers.py`: `await plugin_host.set_provider(CAPABILITY, "spoolman")`; `use_provider` builds `PluginManifest(id=..., name=..., version="0", host_api=HOST_API, settings_model=_NoSettings, factory=lambda _s: provider, provides={CAPABILITY: Provide(features=frozenset(provider.capabilities))})`.

- [ ] **Step 4: Verify** scoped: `pytest tests/plugins tests/services tests/test_provider_boundary.py tests/test_no_provider_in_core.py -q` -> PASS. (`tests/api` is still red until Task 5.)

- [ ] **Step 5: Commit** `refactor(plugins): consumers, bundled plugins and installer use capabilities`

---

### Task 5: Capabilities and plugins API

**Files:**
- Create: `backend/app/api/routes/capabilities.py` (list + provider PUT only in this task), `backend/tests/api/test_capabilities_api.py`
- Modify: `backend/app/api/routes/plugins.py` (summary shape, list, remove `/extension-slots/{kind}`), `backend/app/main.py` (include the router), `backend/tests/api/test_plugins_api.py` (+ every API test using `/extension-slots`, `slots`, `kind`, `capabilities` keys: `grep -rln "extension-slots\|\"slots\"\|\[\"kind\"\]\|\[\"capabilities\"\]" backend/tests`), `openapi.json`

**Interfaces:**
- Consumes: host API (Task 3).
- Produces (JSON):
  - Plugin summary (`GET /api/v1/plugins` items and detail): `id, name, version, description, docs_url, source, loaded, install, ui, enabled, active, error` unchanged **plus** `provides: [{capability, version, features: [..], status: "serving"|"waiting"|..., waiting_on: [..], selected: bool}]`, `requires: [{capability, min_version}]`, `optional: [{capability, min_version}]`, `defines: [capability ids]`. `kind` and `capabilities` removed. `active` = any `provides` entry has status `serving`. Not-loaded installed rows: `provides/requires/optional/defines = []`.
  - `GET /api/v1/plugins` top level: `selections: {cap_id: plugin_id|null}` replaces `slots`; `pending` unchanged.
  - `GET /api/v1/capabilities` (scope `settings:read`) -> `{"capabilities": [{id, version, label, description, definer: str|null, features: [..], required_methods: [..], selected: str|null, explicit: bool, status: "serving"|..., waiting_on: [..], error: str|null, providers: [{plugin_id, name, version, enabled, status, waiting_on: [..]}], requires_by: [{plugin_id, min_version}]}]}`, ordered core first then by id. Dormant stored selections (cap no longer in the catalog) appear with `definer: null, label: id, status: "dormant"`.
  - `PUT /api/v1/capabilities/{cap}/provider` body `{"plugin_id": str|null}` (scope `settings:write`) -> `{"capability": cap, "plugin_id": ..., "explicit": true}`; 422 with the `PluginError` text for unknown cap / non-provider / cycle.
  - `{cap}` path param must accept dots (default `str` does).

- [ ] **Step 1: Failing tests** `test_capabilities_api.py` (uses the shared `client` fixture and `enable_spoolman`, `use_provider`, `tests.plugins.dummy_plugin.make_manifest` registered into `plugins._REGISTRY`; clean up in a fixture):
```python
async def test_list_includes_core_capability_with_providers(client):
    body = (await client.get("/api/v1/capabilities")).json()
    inv = next(c for c in body["capabilities"] if c["id"] == "inventory.filament")
    assert inv["definer"] is None and inv["selected"] is None and inv["status"] == "none_selected"
    assert {p["plugin_id"] for p in inv["providers"]} == {"spoolman", "local_inventory"}

async def test_put_provider_selects_enables_and_is_explicit(client):
    r = await client.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": "local_inventory"})
    assert r.status_code == 200 and r.json() == {"capability": "inventory.filament", "plugin_id": "local_inventory", "explicit": True}
    inv = next(c for c in (await client.get("/api/v1/capabilities")).json()["capabilities"] if c["id"] == "inventory.filament")
    assert (inv["selected"], inv["explicit"], inv["status"]) == ("local_inventory", True, "serving")
    assert (await client.get("/api/v1/plugins/local_inventory")).json()["enabled"] is True

async def test_put_provider_rejects_unknown_cap_wrong_plugin_and_changes_nothing(client):
    for cap, pid in [("nope.nothing", "spoolman"), ("inventory.filament", "ghost_plugin")]:
        r = await client.put(f"/api/v1/capabilities/{cap}/provider", json={"plugin_id": pid})
        assert r.status_code == 422
    inv = next(c for c in (await client.get("/api/v1/capabilities")).json()["capabilities"] if c["id"] == "inventory.filament")
    assert inv["selected"] is None

async def test_put_provider_none_is_remembered(client):
    await client.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": "local_inventory"})
    r = await client.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": None})
    assert r.status_code == 200 and r.json()["plugin_id"] is None
    inv = next(c for c in (await client.get("/api/v1/capabilities")).json()["capabilities"] if c["id"] == "inventory.filament")
    assert (inv["selected"], inv["explicit"], inv["status"]) == (None, True, "none_selected")


async def test_put_provider_that_would_form_a_requirement_cycle_is_422_and_changes_nothing(client):
    a = make_manifest("plug_a", cap="plug_a.x", requires=(Requirement("plug_b.x"),))
    b = make_manifest("plug_b", cap="plug_b.x", requires=(Requirement("plug_a.x"),))
    plugins.register_plugin(a); plugins.register_plugin(b)
    assert (await client.put("/api/v1/capabilities/plug_b.x/provider", json={"plugin_id": "plug_b"})).status_code == 200
    r = await client.put("/api/v1/capabilities/plug_a.x/provider", json={"plugin_id": "plug_a"})
    assert r.status_code == 422 and "cycle" in r.json()["detail"]
    cap = next(c for c in (await client.get("/api/v1/capabilities")).json()["capabilities"] if c["id"] == "plug_a.x")
    assert cap["selected"] is None

async def test_plugin_summary_has_provides_and_no_kind(client):
    (sm,) = [p for p in (await client.get("/api/v1/plugins")).json()["plugins"] if p["id"] == "spoolman"]
    assert "kind" not in sm and "capabilities" not in sm
    assert sm["provides"][0]["capability"] == "inventory.filament" and "WRITE_WEIGHT" in sm["provides"][0]["features"]

async def test_old_extension_slots_route_is_gone(client):
    assert (await client.put("/api/v1/extension-slots/filament_inventory", json={"plugin_id": None})).status_code in (404, 405)

async def test_capability_routes_require_settings_scopes(session_factory):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.models import ApiKey
    from app.services.api_key_service import generate_key, hash_key
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="ro", key_prefix=prefix, key_hash=hash_key(raw), scopes=["settings:read"], enabled=True, created_at="2026-01-01T00:00:00"))
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Api-Key": raw}) as ro:
        assert (await ro.get("/api/v1/capabilities")).status_code == 200
        assert (await ro.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": None})).status_code == 403
```
Update `test_plugins_api.py`: top-level `body["selections"] == {}`-style expectations (`{"inventory.filament": None}` is not stored until selected: assert on the `GET /capabilities` view instead); note the **intended behavior change**: `PUT /plugins/spoolman {enabled: true}` with no other enabled inventory provider now auto-selects it (`selections["inventory.filament"] == "spoolman"`, `explicit false`) - port any test that assumed "enabled but unselected".

- [ ] **Step 2: Run to verify failure** `pytest tests/api/test_capabilities_api.py -v` -> FAIL (404 on `/capabilities`).

- [ ] **Step 3: Implement**
  - `plugins.py::_summary`:
```python
def _provides(m: PluginManifest) -> list[dict]:
    out = []
    for cap, p in sorted(m.provides.items()):
        st = plugin_host.status(cap)
        mine = st.plugin_id == m.id
        out.append({"capability": cap, "version": p.version, "features": sorted(p.features),
                    "selected": mine and st.state != "dormant", "status": st.state if mine else "not_selected",
                    "waiting_on": list(st.waiting_on) if mine else []})
    return out
```
    and `_summary` returns `"provides": prov, "requires": [{"capability": r.capability, "min_version": r.min_version} for r in m.requires], "optional": [...], "defines": [d.id for d in m.defines], "active": any(x["status"] == "serving" for x in prov)`; delete `kind`, `capabilities`. List endpoint returns `{"plugins": ..., "selections": plugin_host.selections(), "pending": pending}`; delete the `SlotBody`/`set_slot` route; the not-loaded installed rows drop `kind`/`capabilities` and add empty `provides/requires/optional/defines`.
  - `capabilities.py`:
```python
router = APIRouter(prefix="/api/v1", tags=["capabilities"])

class ProviderBody(BaseModel):
    plugin_id: str | None = None

def _view(cap: CapabilityDef, definer: str | None) -> dict:
    st = plugin_host.status(cap.id)
    providers = [{"plugin_id": m.id, "name": m.name, "version": m.provides[cap.id].version, "enabled": plugin_host.is_enabled(m.id),
                  "status": plugin_host.status(cap.id).state if plugin_host.selected(cap.id) == m.id else "not_selected",
                  "waiting_on": list(plugin_host.unmet(m.id))} for m in providers_of(cap.id)]
    requires_by = [{"plugin_id": m.id, "min_version": r.min_version} for m in registered_plugins() for r in m.requires if r.capability == cap.id]
    return {"id": cap.id, "version": cap.version, "label": cap.label, "description": cap.description, "definer": definer,
            "features": sorted(cap.features), "required_methods": list(cap.required_methods),
            "selected": plugin_host.selected(cap.id), "explicit": plugin_host.is_explicit(cap.id), "status": st.state,
            "waiting_on": list(st.waiting_on), "error": st.error, "providers": providers, "requires_by": requires_by}

@router.get("/capabilities", dependencies=[Depends(require_scope("settings:read"))], summary="Every known capability, its providers and the selected one")
async def list_capabilities():
    catalog = capability_catalog()
    items = [_view(d, definer_of(d.id)) for d in sorted(catalog.values(), key=lambda d: (definer_of(d.id) is not None, d.id))]
    for cap, pid in sorted(plugin_host.selections().items()):                 # dormant: stored choice whose definer is gone
        if cap not in catalog:
            items.append({"id": cap, "version": 0, "label": cap, "description": "", "definer": None, "features": [], "required_methods": [],
                          "selected": pid, "explicit": plugin_host.is_explicit(cap), "status": "dormant", "waiting_on": [], "error": None,
                          "providers": [], "requires_by": []})
    return {"capabilities": items}

@router.put("/capabilities/{cap}/provider", dependencies=[Depends(require_scope("settings:write"))], summary="Choose the provider of a capability (null = none)")
async def set_provider(cap: str, body: ProviderBody):
    try:
        await plugin_host.set_provider(cap, body.plugin_id)
    except PluginError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"capability": cap, "plugin_id": plugin_host.selected(cap), "explicit": True}
```
    Include in `main.py` next to `plugins_router` (`from .api.routes.capabilities import router as capabilities_router`; register it **before** any future catch-all dispatcher).
  - Regenerate `openapi.json` from the repo root: `python scripts/export_openapi.py`.
  - `tests/test_route_auth.py`: if it enumerates routes, add the two new ones with their scopes.

- [ ] **Step 4: Verify**: full backend suite `pytest -q` from `backend/` -> PASS, count >= baseline (+ new). Mutation: make `set_provider` skip `_reject_cycle` -> the cycle API test fails; revert.

- [ ] **Step 5: Commit** `feat(api): GET /capabilities, PUT /capabilities/{cap}/provider; plugins list reports provides`

---

### Task 6: Capability REST dispatcher

**Files:**
- Modify: `backend/app/api/routes/capabilities.py` (dispatcher), `backend/app/main.py` (record exposed routes at mount time), `openapi.json`
- Create: `backend/tests/api/test_capability_dispatch.py`

**Interfaces:**
- Consumes: Task 3 `plugin_host.active`, Task 4 mounting loop.
- Produces: `app.api.routes.capabilities.EXPOSED: dict[tuple[str, str], list[tuple[str, str]]]` is NOT used. Instead: `capabilities.register_exposed(app, manifest)` called from `main.py` for each plugin; stores `_EXPOSED[(plugin_id, capability)] = frozenset(full plugin paths of Provide.routers routes)` (e.g. `/api/v1/plugins/local_inventory/spools`). Catch-all `api_route("/capabilities/{cap}/{rest:path}", methods=[GET,POST,PUT,PATCH,DELETE], include_in_schema=False)`.

**Design (resolve the spike first - Step 1):** the dispatcher must run the *already-mounted* plugin route so `app.dependency_overrides` and scopes keep working. It rewrites the request scope's `path` to `/api/v1/plugins/<active_id>/<rest>`, finds the matching route among `request.app.routes` using `route.matches(scope)` (a `Match.FULL`), verifies the route's path template is in the exposed set for `(active_id, cap)`, then `await route.handle(scope, receive, send)` and returns the response. Because the mounted route objects are the ones `include_router` created, `dependency_overrides_provider` is set.

- [ ] **Step 1: Spike test first** (write, run, keep as the regression test): in `test_capability_dispatch.py` register a dummy plugin defining `dummy_one.ping` with a router `GET /echo` (depends on `Depends(get_session)` and `require_scope("inventory:read")`) in `Provide(routers=(r,))`; mount via the same helper `main.py` uses; assert
```python
async def test_dispatch_reaches_the_active_plugins_route_with_overrides(client):
    await plugin_host.set_provider("dummy_one.ping", "dummy_one")
    r = await client.get("/api/v1/capabilities/dummy_one.ping/echo")
    assert r.status_code == 200 and r.json() == {"echo": "ok"}
    assert (await client.get("/api/v1/plugins/dummy_one/echo")).json() == r.json()       # identical to the plugin-id route
```
  The test plugins (`dummy_one` with `GET /echo` -> `{"echo": "ok"}`, `POST /items` (body model with required `name`, returns it with 201), and a second router in plain `manifest.routers` with `GET /private`; `dummy_two` providing the same capability with `GET /echo` -> `{"echo": "two"}`) are built in a fixture in this test module, each router guarded by `Depends(require_scope("inventory:read"))` (and `inventory:write` for POST). If routes cannot be mounted dynamically after `main` import in tests, expose `capabilities.mount_plugin(app, manifest)` (the function `main.py` calls in its loop) and call it in the test with the test `app`. **Gate:** if this spike fails because `dependency_overrides` is not honored, stop and report to the user (the design fallback is the sub-app mount; do not silently change approach).

- [ ] **Step 2: More failing tests**
```python
async def test_409_capability_unavailable_when_nothing_selected(client):
    r = await client.get("/api/v1/capabilities/dummy_one.ping/echo")
    assert r.status_code == 409 and r.json()["error"] == "capability_unavailable"

async def test_404_when_the_active_provider_does_not_expose_the_path(client):
    await plugin_host.set_provider("dummy_one.ping", "dummy_one")
    assert (await client.get("/api/v1/capabilities/dummy_one.ping/nothing-here")).status_code == 404
    # `/private` is in the manifest's plain `routers` (not in Provide.routers): reachable by plugin id, never through the capability
    assert (await client.get("/api/v1/plugins/dummy_one/private")).status_code == 200
    assert (await client.get("/api/v1/capabilities/dummy_one.ping/private")).status_code == 404


async def test_path_traversal_cannot_reach_another_plugins_routes(client):
    await plugin_host.set_provider("dummy_one.ping", "dummy_one")
    for rest in ("../other/echo", "..%2Fother%2Fecho", "x/../../other/echo"):
        r = await client.get(f"/api/v1/capabilities/dummy_one.ping/{rest}")
        assert r.status_code in (404, 409) and "other-secret" not in r.text


async def test_scope_is_enforced_through_the_dispatcher(client, session_factory):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.models import ApiKey
    from app.services.api_key_service import generate_key, hash_key
    await plugin_host.set_provider("dummy_one.ping", "dummy_one")
    raw, prefix = generate_key()
    async with session_factory() as s:                                  # a key WITHOUT inventory:read
        s.add(ApiKey(name="noscope", key_prefix=prefix, key_hash=hash_key(raw), scopes=["settings:read"], enabled=True, created_at="2026-01-01T00:00:00"))
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Api-Key": raw}) as c:
        assert (await c.get("/api/v1/capabilities/dummy_one.ping/echo")).status_code == 403
        assert (await c.get("/api/v1/plugins/dummy_one/echo")).status_code == 403          # same answer as the plugin-id route


async def test_dispatch_follows_the_selection_switch(client):
    await plugin_host.set_provider("dummy_one.ping", "dummy_one")
    assert (await client.get("/api/v1/capabilities/dummy_one.ping/echo")).json() == {"echo": "ok"}
    await plugin_host.set_provider("dummy_one.ping", "dummy_two")        # a second provider whose /echo answers {"echo": "two"}
    assert (await client.get("/api/v1/capabilities/dummy_one.ping/echo")).json() == {"echo": "two"}


async def test_methods_and_bodies_pass_through(client):
    await plugin_host.set_provider("dummy_one.ping", "dummy_one")
    ok = await client.post("/api/v1/capabilities/dummy_one.ping/items", json={"name": "a"})
    assert ok.status_code == 201 and ok.json() == {"name": "a"}
    bad = await client.post("/api/v1/capabilities/dummy_one.ping/items", json={"nope": 1})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"][-1] == "name"          # the plugin route's own validation error, relayed


async def test_openapi_lists_no_dispatch_route(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert not any("{rest}" in p or "/capabilities/{cap}/{" in p for p in paths)
```

- [ ] **Step 3: Implement** in `capabilities.py`:
```python
_EXPOSED: dict[tuple[str, str], frozenset[str]] = {}

def mount_plugin(app: FastAPI, manifest: PluginManifest) -> None:
    """Mount a plugin's routers under /api/v1/plugins/{id} (once each) and remember which paths each capability exposes."""
    seen: set[int] = set()
    for r in manifest.routers:
        if id(r) not in seen:
            seen.add(id(r)); app.include_router(r, prefix=f"/api/v1/plugins/{manifest.id}")
    for cap, p in manifest.provides.items():
        paths = set()
        for r in p.routers:
            paths |= {f"/api/v1/plugins/{manifest.id}{route.path}" for route in r.routes}
            if id(r) not in seen:
                seen.add(id(r)); app.include_router(r, prefix=f"/api/v1/plugins/{manifest.id}")
        _EXPOSED[(manifest.id, cap)] = frozenset(paths)
    for r in manifest.alias_routers:
        app.include_router(r)

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

@router.api_route("/capabilities/{cap}/{rest:path}", methods=_METHODS, include_in_schema=False)
async def dispatch(cap: str, rest: str, request: Request):
    active = plugin_host.active(cap)
    if active is None:
        return JSONResponse(status_code=409, content={"error": "capability_unavailable", "capability": cap})
    if ".." in rest.split("/"):
        raise HTTPException(status_code=404)
    target = f"/api/v1/plugins/{active.manifest.id}/{rest}"
    scope = {**request.scope, "path": target, "raw_path": target.encode()}
    exposed = _EXPOSED.get((active.manifest.id, cap), frozenset())
    for route in request.app.router.routes:
        match, child = route.matches(scope)
        if match is Match.FULL and getattr(route, "path", None) in exposed:
            captured: dict = {}
            return await _run(route, {**scope, **child}, request, captured)
    raise HTTPException(status_code=404, detail=f"{active.manifest.id!r} has no {rest!r} for {cap}")
```
  `_run` runs `route.handle(scope, request.receive, send)` with a `send` that captures `http.response.start`/`body` and returns a `Response` (status, headers, joined body). Match an `Match.PARTIAL` (method mismatch) to 405 only if no FULL match was found. Note `route.path` is the full prefixed template (`/api/v1/plugins/x/spools/{ref}`), so `_EXPOSED` must store templates (`route.path` of the router **plus prefix**), not concrete paths - the code above does that.
  In `main.py` replace the Task-4 mounting loop with `capabilities_mod.mount_plugin(app, _manifest)` per registered plugin. Register `capabilities_router` after `plugins_router`; the explicit `/capabilities` and `/capabilities/{cap}/provider` routes are declared before the catch-all in the same router, so they win.
  Regenerate `openapi.json` (the dispatcher is excluded, so only Task-5 routes differ).

- [ ] **Step 4: Verify** `pytest tests/api/test_capability_dispatch.py tests/test_route_auth.py tests/test_openapi_contract.py -v` -> PASS; full `pytest -q` PASS. Mutation: drop the `in exposed` check -> the "not in Provide.routers" test fails; revert.

- [ ] **Step 5: Commit** `feat(api): dispatch /capabilities/{cap}/... to the active provider's routes`

---

### Task 7: Frontend API, hooks, fixtures and existing screens

**Files:**
- Create: `frontend/src/api/capabilities.ts`, `frontend/src/api/capabilities.test.tsx`
- Modify: `frontend/src/api/plugins.ts`, `frontend/src/api/inventory.ts`, `frontend/src/components/PluginSettingsPage.tsx`, `frontend/src/components/PluginInstallDialog.tsx`, `frontend/src/screens/{FilamentInventoryPage,PluginsPage}.tsx`, `frontend/src/test/inventoryFixtures.ts`, and the tests listed by `cd frontend && grep -rlE "kind: 'filament_inventory'|slots:|setExtensionSlot|useActivePlugin|useCapability|capabilities: ALL_CAPS|\.capabilities" src` (expected: `PluginSettingsPage.test.tsx`, `FilamentInventoryPage.test.tsx`, `PluginPage.test.tsx`, `PluginInstall.test.tsx`, `plugins.test.tsx`, `SearchModal.test.tsx`, `ScanSpoolModal.test.tsx`, `RemapModal.test.tsx`, `PerPrinterConfig.test.tsx`, `FleetScreen.test.tsx`, `NewJobScreen.*.test.tsx`, `ProjectBuilderScreen*.test.tsx`, `MaterialMappingsPage.test.tsx`, `inventoryGuard.test.ts`, `App.routing.test.tsx`, `Sidebar.test.tsx`)

**Interfaces:**
- Consumes: Task 5 JSON shapes.
- Produces in `api/plugins.ts`:
```ts
export interface PluginProvides { capability: string; version: number; features: string[]; selected: boolean;
  status: 'serving' | 'waiting' | 'error' | 'disabled' | 'none_selected' | 'no_provider' | 'dormant' | 'not_selected'; waiting_on: string[] }
export interface CapabilityRef { capability: string; min_version: number }
// PluginSummary: remove `kind`, `capabilities`; add `provides: PluginProvides[]; requires: CapabilityRef[]; optional: CapabilityRef[]; defines: string[]`
// PluginList: `selections: Record<string, string | null>` replaces `slots`
// InstallPreview: remove `kind`; add `provides: { capability: string; version: number }[]; requires: CapabilityRef[]; optional: CapabilityRef[]; defines: string[]`
export async function setCapabilityProvider(capability: string, pluginId: string | null): Promise<{ capability: string; plugin_id: string | null; explicit: boolean }>
export function useCapabilityProvider(capability: string): PluginSummary | null   // plugin whose provides[] entry for `capability` is 'serving'
export function useFeature(capability: string, feature: string): boolean
```
  `usePlugins()` returns `selections` instead of `slots`. `setExtensionSlot`, `useActivePlugin`, `useCapability` are deleted.
  `api/capabilities.ts`: `CapabilityInfo` (mirrors `GET /capabilities` item), `fetchCapabilities(): Promise<CapabilityInfo[]>`, hook `useCapabilityList(): { items: CapabilityInfo[]; loaded: boolean; error: string | null; reload: () => void }` (plain `useEffect` fetch; re-fetches when `usePlugins()`'s list changes by depending on its identity).
  `api/inventory.ts`: `export const INVENTORY_CAPABILITY = 'inventory.filament'` replaces `INVENTORY_KIND`; `useInventory()` uses `useCapabilityProvider(INVENTORY_CAPABILITY)` and `has = c => !!plugin && (plugin.provides.find(p => p.capability === INVENTORY_CAPABILITY)?.features ?? []).includes(c)`.

- [ ] **Step 1: Failing tests.**
  - `api/capabilities.test.tsx`: with `stubFetch({'GET /api/v1/capabilities': {capabilities:[...]}})`, `useCapabilityList` returns items; `setCapabilityProvider` PUTs `/api/v1/capabilities/inventory.filament/provider` with `{plugin_id:'spoolman'}` and refreshes the plugin store.
  - Update `test/inventoryFixtures.ts`: `mkPlugin` drops `kind`/`capabilities`, adds `provides: [{ capability: 'inventory.filament', version: 1, features: ALL_CAPS, selected: true, status: 'serving', waiting_on: [] }]`, `requires: [], optional: [], defines: []`, `active: true`; `pluginsBody` returns `selections: { 'inventory.filament': plugin?.active ? plugin.id : null }`; `inventoryRoutes` uses `plugin?.provides[0].features` for `mkStatus.capabilities`.
  - Update each listed test so fixtures/assertions use the new shape (searching for `slots`, `kind`, `capabilities`).
  - `FilamentInventoryPage.test.tsx`: choosing a provider PUTs `/api/v1/capabilities/inventory.filament/provider` (was `/extension-slots/filament_inventory`).
  - `PluginSettingsPage.test.tsx`: for a plugin with two `provides` entries, one "Use for <capability>" button per not-selected capability, each PUTting to its capability's provider route.

- [ ] **Step 2: Run to verify failure** `cd frontend && npx vitest run src/api src/screens/FilamentInventoryPage.test.tsx src/components/PluginSettingsPage.test.tsx` -> FAIL.

- [ ] **Step 3: Implement.** In `plugins.ts` apply the interface changes above; implement
```ts
export async function setCapabilityProvider(capability: string, pluginId: string | null) {
  const r = await request<{ capability: string; plugin_id: string | null; explicit: boolean }>(
    `/api/v1/capabilities/${encodeURIComponent(capability)}/provider`, json('PUT', { plugin_id: pluginId }));
  invalidatePlugins();
  return r;
}
export function useCapabilityProvider(capability: string): PluginSummary | null {
  const { plugins } = usePlugins();
  return plugins.find(p => p.provides.some(x => x.capability === capability && x.status === 'serving')) ?? null;
}
export function useFeature(capability: string, feature: string): boolean {
  const p = useCapabilityProvider(capability);
  return !!p && (p.provides.find(x => x.capability === capability)?.features ?? []).includes(feature);
}
```
  `PluginSettingsPage.tsx` (read it fully first): replace `kind` usage - the "Make active provider" action becomes a list of buttons, one per `plugin.provides` entry where `!selected` (label `Use for ${capability}`, `setCapabilityProvider(cap, pluginId_)`); replace `plugin.kind === 'filament_inventory' && plugin.active && <InventoryProviderPanel ...>` with `plugin.provides.some(p => p.capability === INVENTORY_CAPABILITY && p.status === 'serving') && ...`; show unmet requirements as a note `Waiting on: <caps>` from `provides[].waiting_on`. `PluginInstallDialog.tsx`: show `provides`/`requires` lists instead of `kind`. `FilamentInventoryPage.tsx`: `providers = plugins.filter(p => p.provides.some(x => x.capability === INVENTORY_CAPABILITY))`; `choose` calls `setCapabilityProvider(INVENTORY_CAPABILITY, id || null)`. `PluginsPage.tsx`: replace any `kind` display with provided capability chips. Fix every remaining TypeScript error: `npm run build` (it runs `tsc -b`) must pass.

- [ ] **Step 4: Verify** `npx vitest run` (whole frontend) -> PASS (baseline count + new); `npm run build` -> PASS.

- [ ] **Step 5: Commit** `refactor(ui): plugin hooks and screens use capabilities instead of kinds`

---

### Task 8: Settings -> Capabilities page

**Files:**
- Create: `frontend/src/screens/CapabilitiesPage.tsx`, `frontend/src/screens/CapabilitiesPage.test.tsx`
- Modify: `frontend/src/screens/SettingsScreen.tsx` (PageId `'capabilities'`, `PAGE_IDS`, nav entry after `plugins`, render), `frontend/src/components/Sidebar.tsx` (settings sub-nav entry `{ to: '/settings/capabilities', label: 'Capabilities' }`), `frontend/src/screens/SettingsScreen.test.tsx` / `Sidebar.test.tsx` (nav lists), `frontend/e2e/*` mocked-API specs that enumerate settings pages or stub `GET /api/v1/plugins` (add `GET /api/v1/capabilities` stubs; `grep -rn "api/v1/plugins" frontend/e2e`)

**Interfaces:**
- Consumes: `useCapabilityList`, `setCapabilityProvider` (Task 7). Reuses `PageHeader`, `FieldRow` from `components/settingsUi`.
- Produces: `export function CapabilitiesPage()`.

- [ ] **Step 1: Failing test** `CapabilitiesPage.test.tsx`:
```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CapabilitiesPage } from './CapabilitiesPage';
import { resetPluginStore } from '../api/plugins';
import { Reply, stubFetch } from '../test/fetchStub';

const prov = (plugin_id: string, name: string, over: Record<string, unknown> = {}) =>
  ({ plugin_id, name, version: 1, enabled: true, status: 'not_selected', waiting_on: [], ...over });
const cap = (over: Record<string, unknown> & { id: string }) => ({
  version: 1, label: over.id, description: '', definer: null, features: [], required_methods: [], selected: null, explicit: false,
  status: 'none_selected', waiting_on: [], error: null, providers: [], requires_by: [], ...over,
});
const INV = cap({ id: 'inventory.filament', label: 'Filament inventory', selected: 'spoolman', status: 'serving',
  providers: [prov('spoolman', 'Spoolman'), prov('local_inventory', 'Local inventory')] });
const PLUGINS = { plugins: [], selections: {}, pending: [] };

describe('CapabilitiesPage', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('lists every capability with a provider dropdown and status chips', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [
      INV,
      cap({ id: 'acme.reports', label: 'Reports', definer: 'acme', providers: [prov('acme', 'Acme')] }),
      cap({ id: 'old.thing', label: 'old.thing', status: 'dormant', selected: 'ghost' }) ] } });
    render(<CapabilitiesPage />);
    expect(await screen.findByRole('combobox', { name: 'Filament inventory provider' })).toHaveValue('spoolman');
    expect(screen.getByText('Serving')).toBeInTheDocument();
    expect(screen.getByText(/Defined by acme/)).toBeInTheDocument();
    expect(screen.getByText('Dormant')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Reports provider' })).toHaveValue('');
  });

  it('shows what a provider is waiting on', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [
      cap({ id: 'acme.report', label: 'Report', selected: 'acme', status: 'waiting', waiting_on: ['acme.notes'], providers: [prov('acme', 'Acme', { status: 'waiting', waiting_on: ['acme.notes'] })] }) ] } });
    render(<CapabilitiesPage />);
    expect(await screen.findByText('Waiting on acme.notes')).toBeInTheDocument();
  });

  it('changing the dropdown PUTs the provider and refetches the list', async () => {
    const api = stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [INV] },
      'PUT /api/v1/capabilities/inventory.filament/provider': { capability: 'inventory.filament', plugin_id: 'local_inventory', explicit: true } });
    render(<CapabilitiesPage />);
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Filament inventory provider' }), 'local_inventory');
    await waitFor(() => expect(api.to('PUT', '/api/v1/capabilities/inventory.filament/provider')[0].body).toEqual({ plugin_id: 'local_inventory' }));
    await waitFor(() => expect(api.to('GET', '/api/v1/capabilities').length).toBeGreaterThanOrEqual(2));
  });

  it('choosing None sends plugin_id null', async () => {
    const api = stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [INV] },
      'PUT /api/v1/capabilities/inventory.filament/provider': { capability: 'inventory.filament', plugin_id: null, explicit: true } });
    render(<CapabilitiesPage />);
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Filament inventory provider' }), '');
    await waitFor(() => expect(api.to('PUT', '/api/v1/capabilities/inventory.filament/provider')[0].body).toEqual({ plugin_id: null }));
  });

  it('shows the server error text when the change is rejected', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [INV] },
      'PUT /api/v1/capabilities/inventory.filament/provider': new Reply(422, { detail: 'selecting x would create a requirement cycle' }) });
    render(<CapabilitiesPage />);
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Filament inventory provider' }), 'local_inventory');
    expect(await screen.findByRole('alert')).toHaveTextContent('requirement cycle');
  });

  it('disables the select and says so when no plugin provides the capability', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [cap({ id: 'acme.lonely', label: 'Lonely', status: 'no_provider' })] } });
    render(<CapabilitiesPage />);
    expect(await screen.findByRole('combobox', { name: 'Lonely provider' })).toBeDisabled();
    expect(screen.getByText('No plugin provides this')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure** `npx vitest run src/screens/CapabilitiesPage.test.tsx` -> FAIL (module missing).

- [ ] **Step 3: Implement** `CapabilitiesPage`: `PageHeader title="Capabilities" sub="Which plugin serves each capability. Only one provider is used per capability."`; for each item a `FieldRow` with `label={cap.label}`, `hint` (description; plus `Defined by ${definer}` when `definer`; `Needed by: ...` from `requires_by`), a `<select className="select" aria-label={`${cap.label} provider`}>` with `None` + `providers.map(p => <option value={p.plugin_id}>{p.name}{!p.enabled ? ' (disabled)' : ''}</option>)`, disabled when `providers.length === 0` (then show `No plugin provides this`), and a status chip: serving -> `Serving`, waiting -> `Waiting on ${waiting_on.join(', ')}`, error -> the `error` text, disabled -> `Provider disabled`, none_selected -> `None selected`, no_provider -> `Provider missing`, dormant -> `Dormant`. `onChange` calls `setCapabilityProvider(cap.id, value || null)` then `reload()`; errors render in a `role="alert"` line like `FilamentInventoryPage`. Wire the page into `SettingsScreen` and `Sidebar`. Playwright: add stubs and one spec assertion that Settings -> Capabilities renders (mirror the style of the existing settings e2e).

- [ ] **Step 4: Verify** `npx vitest run` PASS; `npm run build` PASS; `npm run test:e2e` PASS (45 specs baseline + any new).

- [ ] **Step 5: Commit** `feat(ui): Settings -> Capabilities page`

---

### Task 9: Sample plugin, docs, guards, regeneration

**Files:**
- Modify: `docs/plugin development/sample/acme_inventory/{themis-plugin.toml,acme_inventory/__init__.py,acme_inventory/provider.py,acme_inventory/routes.py}`, `docs/plugin development/README.md`, `docs/plugins.md`, `docs/provider-interfaces.md`, `docs/agent/{backend,data-model,frontend}.md`, `CLAUDE.md` (the "Slicing & inventory providers" paragraph: kinds -> capabilities; the Database list: `extension_slots` -> `capability_selections`; migrations `v001-v039` -> `v001-v040`), `openapi.json`
- Create: `backend/tests/plugins/test_sample_plugin.py` (the sample is "tested" - the guide says so; check how: `grep -rn "acme_inventory" backend/tests`; if the guide's test lives elsewhere, extend it instead)

- [ ] **Step 1: Failing test.** `test_sample_plugin.py` loads the sample from `docs/plugin development/sample/acme_inventory` via `loader._load_one`-style import (put the dir on `sys.path`, import `acme_inventory.MANIFEST`), runs `check_matches(read_toml(dir), MANIFEST)`, asserts `MANIFEST.provides` contains `inventory.filament` **and** `acme_inventory.notes`, `MANIFEST.defines[0].id == "acme_inventory.notes"` with `required_methods == ("add_note", "list_notes")`, `MANIFEST.requires == ()` and `optional` lists `inventory.filament`... Decide the demo shape here: the sample provides `inventory.filament` (as today), **defines** `acme_inventory.notes` (spool notes; methods `list_notes`, `add_note`; served by `instance.notes` via `Provide(attr="notes", routers=(router,))`), and **requires** `inventory.filament@1` is NOT used (a plugin cannot require what it provides); instead add a short second sample module note in the README showing a consumer manifest with `requires=(Requirement("acme_inventory.notes"),)` (code block only, covered by a unit test in the same file that builds that manifest and checks the host reports "waiting" until the sample is selected).

- [ ] **Step 2: Run to verify failure** `pytest tests/plugins/test_sample_plugin.py -v` -> FAIL.

- [ ] **Step 3: Implement.** Sample `__init__.py`: remove `kind`/`capabilities`; add
```python
from app.plugins.capabilities.filament_inventory import CAPABILITY
from app.plugins import CapabilityDef, Provide
NOTES = "acme_inventory.notes"
...
    provides={CAPABILITY: Provide(version=1, features=AcmeProvider.capabilities),
              NOTES: Provide(version=1, attr="notes", routers=(router,))},
    defines=(CapabilityDef(NOTES, 1, "Spool notes", "Free-text notes attached to a spool ref.",
                           required_methods=("list_notes", "add_note")),),
```
  (export `CapabilityDef` from `app/plugins/__init__.py`'s `__all__` too - small addition in this task.) `AcmeProvider.notes` returns a small `Notes` object with `async list_notes()`/`async add_note(spool_ref, note)` using the plugin's table through `app.database` session factory. Toml: drop `kind`, add `provides = ["inventory.filament@1", "acme_inventory.notes@1"]`, `defines = ["acme_inventory.notes"]`. README rewrite (sections 1, 2, 3, 4, 5, 6 and the checklist): replace the "kind" mental model with capabilities (definition, provide, require/optional, define, selection and auto-select rules, waiting state, dormant), update both sequence diagrams (`extension_slots` -> `capability_selections`, `set_slot` -> `set_provider`, `active(kind)` -> `active(cap)`), document `/api/v1/capabilities/...` dispatch, and the duck-typing contract. `docs/plugins.md`, `docs/provider-interfaces.md`, `docs/agent/*.md`: apply the same terminology (use `grep -n "kind\|slot\|extension" <file>` to find every spot; each hit is either rewritten or confirmed unrelated, e.g. `artifact_kind`).
  Guards: `tests/test_no_provider_in_core.py` allowlist unchanged unless a moved file now mentions Spoolman/Local inventory; fix per its ratchet rule (an allowlisted file that no longer mentions it must be removed from the list).

- [ ] **Step 4: Verify** `pytest tests/plugins/test_sample_plugin.py -v` PASS; then `python scripts/export_openapi.py` (repo root) and `git diff --stat openapi.json` shows only the capabilities/plugins changes; `grep -rn "extension_slots\|extension-slots\|plugins_of_kind\|\bset_slot\b\|useActivePlugin\|setExtensionSlot" backend/app frontend/src docs "docs/plugin development" CLAUDE.md` returns only historical references in `docs/superpowers/` and migrations v034/v035/v040.

- [ ] **Step 5: Commit** `docs(plugins): capability model in the guide, sample and agent docs`

---

### Task 10: Final verification, review gate and PR

**Files:** `.claude/review-state.json` (gitignored marker), no product files.

- [ ] **Step 1: Full suites.** Backend (from `backend/`): `pytest -v -ra --cov` (CI form; floor must hold). Frontend (from `frontend/`): `npm run build`, `npm run test:cov`, `npm run test:e2e`. All green. If coverage rose noticeably, raise the floors (`fail_under` in `backend/pyproject.toml`, thresholds in `frontend/vitest.config.ts`, ~2 points under measured); never lower.
- [ ] **Step 2: Live smoke on the dev stack** (Windows venv gotcha in `CLAUDE.md`): start backend `uvicorn app.main:app --port 8001`, open Settings -> Capabilities, switch the filament provider spoolman <-> local inventory, confirm the Filament inventory page follows, `GET /api/v1/capabilities/inventory.filament/...` dispatches (local inventory routes). Report anything not exercised (no live Spoolman available).
- [ ] **Step 3: Upgrade check.** Copy a pre-040 SQLite DB (`tests/v032_fixture.py` builds one; or the dev DB backed up first), start the app, confirm migration v040 applied, `capability_selections` has the mapped row, and the previously selected provider is still active.
- [ ] **Step 4: Review.** Spawn exactly one fresh, non-fork reviewer (Agent, `subagent_type: pr-review-toolkit:code-reviewer`), handing over by reference: base = `develop` merge-base SHA, head SHA, this plan path, the spec path, `docs/agent/backend-review.md`, `docs/agent/frontend-review.md`. Address Critical/Important findings, re-run the affected suites.
- [ ] **Step 5: Marker and PR.** Write `.claude/review-state.json` as `{"sha": "<git rev-parse HEAD>", "verdict": "clean", "checks": "pass"}`; push `feature/capability-model`; `gh pr create --base develop` with a body summarising the capability model, the breaking manifest change (no back-compat, `host_api` stays 1), the v040 migration, and the BIZ-202 manual verification items that must be re-run (sections A-E, B needs a live Spoolman); end the body with the attribution lines from the session reminder. Delete the branch after merge per `CLAUDE.md`.

---

## Self-Review

**Spec coverage:** §1 capability model -> Tasks 1, 2 (defs, `defines`, prefix rule, catalog, `Provide`), required-method check -> Task 3 (`_check_contract`; *clarification of the spec*: it runs when the instance is built, because duck-typing needs an instance); §2 manifest/toml -> Task 2 (+ installer in Task 4); §3 host/selection/migration -> Task 3; §4 REST list/PUT/dispatcher -> Tasks 5, 6 (spike gate in Task 6 Step 1); §5 UI -> Tasks 7, 8; §6 refactor/docs -> Tasks 4, 9; §7 testing -> inside each task, regeneration in Tasks 5, 9; §8 delivery -> Preconditions + Task 10. Spec says "regenerate `contracts/response-keys.json`": the file has no plugin/capability keys today (`grep -n plugin contracts/response-keys.json` is empty), so no entries are added; nothing else to regenerate.

**Placeholder scan:** all test bodies are written out; remaining `...` are excerpts of existing files ("keep the rest"). The `test_capability_routes_*`/Task 5 list items not shown in full are one-assertion HTTP checks fully specified by their JSON contracts above. No TBD/TODO.

**Type consistency:** `CAPABILITY`/`CapabilityDef`/`Provide`/`Requirement` (Tasks 1-2) are used unchanged in Tasks 3-9; host methods `active/has/part/selected/is_explicit/selections/status/unmet/set_provider/call` (Task 3) match their uses in Tasks 4-6; JSON `provides[].status` values match the frontend `PluginProvides.status` union (Task 7) and `CapabilityStatus.state` (Task 3, plus `not_selected` added by the API for non-selected providers).

**Known risks:** (1) dispatcher + `dependency_overrides` (Task 6 spike gate); (2) `ALTER TABLE DROP COLUMN` needs SQLite >= 3.35 (Python 3.13 bundles newer; CI image uses the same Python); (3) two mutually-requiring plugins auto-selected stay "waiting" forever - by design, surfaced on the Capabilities page; (4) `PUT /plugins/{id} {enabled:true}` now auto-selects a sole provider - an intended behavior change from decision 5, ported in existing API tests.
