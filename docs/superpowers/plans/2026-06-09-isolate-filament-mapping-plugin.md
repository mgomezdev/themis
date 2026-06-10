# Isolate Filament→Tool Mapping Behind the Printer Interface — Implementation Plan (Sub-project C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Move the model-filament→tool slice rewrite out of the generic slicer into a Snapmaker plugin behind an `AbstractPrinterClient` hook — pure refactor, identical gcode.

**Architecture:** New `app/services/snapmaker/` package holds `paint_remap.py` (the AGPL-sensitive TriangleSelector codec, moved) + `remap.py` (`remap_3mf(prepared, tool_index, filament_map)`, the routing rewrite moved from `mesh_3mf_builder`, now operating on an already-built prepared 3MF). `AbstractPrinterClient.remap_sliceable_3mf` (default no-op) is overridden by Snapmaker. `mesh_3mf_builder` reverts to vendor-agnostic; `SliceRequest` carries an opaque `prepare_hook`; the queue binds it to the client's hook.

**Tech Stack:** Python/FastAPI/pytest.

**Spec:** `docs/superpowers/specs/2026-06-09-isolate-filament-mapping-plugin-design.md`. **Branch:** `worktree-multi-material-tool-mapping` (worktree `C:\Users\mgome\Documents\projects\themis\.claude\worktrees\multi-material-tool-mapping`).

## Conventions
- Work ONLY in the worktree; absolute paths. Backend venv at `backend\.venv`. Tests: `cd backend && backend\.venv\Scripts\python.exe -m pytest <path> -v`.
- **Behavior-invariant:** at every task boundary the full backend suite must stay green. The `e2e` test (`tests/services/test_filament_map_e2e.py`) is the functional anchor — it must keep passing (remapped fixture slices + reroutes).
- Commit after each task; do NOT push. TDD where adding behavior; for pure moves, relocate the existing tests + keep them green.

## Model tuning
**T1** Haiku (mechanical move). **T2, T3, T4, T5** Sonnet (the rewrite-on-prepared-3mf adaptation, the cutover, docs).

## File structure
- Create `backend/app/services/snapmaker/__init__.py`, `…/paint_remap.py` (moved), `…/remap.py`.
- Create `backend/tests/services/snapmaker/__init__.py` (if the test dir needs it — match sibling convention), `…/test_paint_remap.py` (moved), `…/test_remap.py`.
- Modify `abstract_printer_client.py`, `snapmaker_client.py`, `mesh_3mf_builder.py`, `slicer_service.py`, `queue_engine.py`, `tests/services/test_mesh_3mf_builder.py`, `tests/services/test_filament_map_e2e.py`.
- Delete `backend/app/services/paint_remap.py`.

---

## Task 1: Move `paint_remap.py` into the `snapmaker` plugin

**Model: Haiku.** Mechanical relocation.

**Files:** Create `backend/app/services/snapmaker/__init__.py` (empty), move `backend/app/services/paint_remap.py` → `backend/app/services/snapmaker/paint_remap.py`; move `backend/tests/services/test_paint_remap.py` → `backend/tests/services/snapmaker/test_paint_remap.py`.

- [ ] **Step 1: Move the files**

```
cd C:\Users\mgome\Documents\projects\themis\.claude\worktrees\multi-material-tool-mapping
mkdir backend\app\services\snapmaker
type nul > backend\app\services\snapmaker\__init__.py
git mv backend/app/services/paint_remap.py backend/app/services/snapmaker/paint_remap.py
mkdir backend\tests\services\snapmaker
git mv backend/tests/services/test_paint_remap.py backend/tests/services/snapmaker/test_paint_remap.py
```
Check whether `backend/tests/services/` has an `__init__.py`; if it does, add `backend/tests/services/snapmaker/__init__.py` too (match the convention so the test is collected).

- [ ] **Step 2: Fix imports**

- In `backend/tests/services/snapmaker/test_paint_remap.py`: change `from app.services.paint_remap import ...` → `from app.services.snapmaker.paint_remap import ...`.
- In `backend/app/services/mesh_3mf_builder.py`: change `from . import paint_remap as _paint_remap` → `from .snapmaker import paint_remap as _paint_remap` (temporary — removed in Task 4).
- Grep for any other importer: `grep -rn "import paint_remap\|from .paint_remap\|services.paint_remap" backend/app backend/tests` and fix each to the new path.

- [ ] **Step 3: Run — confirm green**

`cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/snapmaker/test_paint_remap.py -v` → all pass (byte-exact round-trip on the fixture). Then `... -m pytest -q` → green (no import errors).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(snapmaker): move paint_remap into services/snapmaker plugin package"
```

---

## Task 2: `services/snapmaker/remap.py` — `remap_3mf` on a prepared 3MF

**Model: Sonnet.** Move the routing rewrite out of `mesh_3mf_builder` into a standalone function that operates on an already-built prepared 3MF (read-modify-write the zip).

**Files:** Create `backend/app/services/snapmaker/remap.py`, `backend/tests/services/snapmaker/test_remap.py`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/snapmaker/test_remap.py
import zipfile
from app.services.snapmaker.paint_remap import encode_nodes, decode_nodes
from app.services.snapmaker.remap import remap_3mf


def _prepared(tmp_path, *, paint=None, object_extruder="1", with_model_settings=True):
    p = tmp_path / "prepared.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        if paint is not None:
            z.writestr("3D/Objects/o.model", f'<model><triangle paint_color="{paint}"/></model>')
        z.writestr("Metadata/project_settings.config", "{}")
        if with_model_settings:
            z.writestr("Metadata/model_settings.config",
                       f'<?xml version="1.0"?>\n<config><object id="1">'
                       f'<metadata key="extruder" value="{object_extruder}"/></object></config>')
    return p


def test_remap_3mf_tool_index_sets_object_extruder(tmp_path):
    p = _prepared(tmp_path, object_extruder="1")
    remap_3mf(p, tool_index=2)                       # tool 2 -> extruder 3
    with zipfile.ZipFile(p) as z:
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    assert 'key="extruder" value="3"' in ms


def test_remap_3mf_filament_map_remaps_paint_and_object(tmp_path):
    painted = encode_nodes(("L", 3))                 # one triangle on filament 1 (use the codec's node form)
    p = _prepared(tmp_path, paint=painted, object_extruder="1")
    remap_3mf(p, filament_map=[{"model_filament": 1, "tool_index": 2}])  # filament1 -> tool2 (ext3)
    with zipfile.ZipFile(p) as z:
        obj = z.read("3D/Objects/o.model").decode("utf-8")
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    import re
    pc = re.search(r'paint_color="([^"]+)"', obj).group(1)
    assert decode_nodes(pc) == ("L", 5)              # filament1 -> extruder3 -> state 5
    assert 'key="extruder" value="3"' in ms


def test_remap_3mf_noop_when_both_none(tmp_path):
    p = _prepared(tmp_path, object_extruder="2")
    before = p.read_bytes()
    remap_3mf(p)                                     # no tool_index, no filament_map
    assert p.read_bytes() == before                  # untouched
```
(Adjust `encode_nodes`/`decode_nodes` node form to whatever the codec actually exposes — read `snapmaker/paint_remap.py`; the point is: paint for filament-1 leaf, after remapping filament 1→tool 2, decodes to the extruder-3 state.)

- [ ] **Step 2: Run — confirm FAIL** (`...snapmaker/test_remap.py` → no module `remap`).

- [ ] **Step 3: Implement `backend/app/services/snapmaker/remap.py`**

Move these from `mesh_3mf_builder.py` (verbatim) into `remap.py`: the helpers `_model_settings_with_extruder`, `_object_ids_from_model`, `_patch_model_settings_extruder`, `_patch_model_settings_filament_map`, the `_3D_MODEL_RE` regex, and `from .paint_remap import ...` (note: `remap.py` is INSIDE `snapmaker/`, so import is `from .paint_remap import remap_paint_color` or `from . import paint_remap`). Then add:

```python
import zipfile
from pathlib import Path

def remap_3mf(prepared_3mf: Path, *, tool_index: int | None = None,
              filament_map: list | None = None) -> None:
    """Rewrite a prepared sliceable 3MF IN PLACE so the model's filament(s) route to
    the chosen physical tool(s). No-op if both args are None/empty. Snapmaker-only."""
    use_map = bool(filament_map)
    if tool_index is None and not use_map:
        return
    prepared_3mf = Path(prepared_3mf)
    with zipfile.ZipFile(prepared_3mf) as zin:
        names = zin.namelist()
        entries = {n: zin.read(n) for n in names}
    model_xml = entries.get("3D/3dmodel.model", b"")
    src_ms = entries.get("Metadata/model_settings.config", b"")
    mapping = {e["model_filament"]: e["tool_index"] for e in (filament_map or [])}

    tmp = prepared_3mf.with_suffix(".remap.3mf")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            if name == "Metadata/model_settings.config":
                continue  # re-emitted below
            if use_map and _3D_MODEL_RE.match(name):
                import re
                txt = data.decode("utf-8")
                txt = re.sub(r'paint_color="([^"]+)"',
                             lambda m: f'paint_color="{remap_paint_color(m.group(1), mapping)}"', txt)
                zout.writestr(name, txt.encode("utf-8"))
            else:
                zout.writestr(name, data)
        if use_map:
            ms = (_patch_model_settings_filament_map(src_ms, mapping) if src_ms
                  else _model_settings_with_extruder(_object_ids_from_model(model_xml) or ["1"], 1))
        else:
            ext = tool_index + 1
            ms = (_patch_model_settings_extruder(src_ms, ext) if src_ms
                  else _model_settings_with_extruder(_object_ids_from_model(model_xml) or ["1"], ext))
        zout.writestr("Metadata/model_settings.config", ms)
    tmp.replace(prepared_3mf)
```
(Use `remap_paint_color` imported from the sibling `paint_remap`. This mirrors the exact branches of the old `build_sliceable_3mf` routing — paint rewrite for the map; object-extruder patch/create for both paths — but reading the prepared zip rather than the source.)

- [ ] **Step 4: Run — PASS + full suite.** New tests pass; `... -m pytest -q` green. (`mesh_3mf_builder` still has its own copy of this logic — that's fine for now; both green.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/snapmaker/remap.py backend/tests/services/snapmaker/test_remap.py
git commit -m "feat(snapmaker): remap_3mf() applies tool routing to a prepared 3MF"
```

---

## Task 3: ABC hook + Snapmaker override

**Model: Sonnet.**

**Files:** Modify `backend/app/services/abstract_printer_client.py`, `backend/app/services/snapmaker_client.py`. Test: `backend/tests/services/snapmaker/test_remap_hook.py` (create).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/snapmaker/test_remap_hook.py
import zipfile
from app.services.snapmaker_client import SnapmakerExtendedClient
from app.services.elegoo_centauri_client import ElegooCentauriClient


def _prepared(tmp_path):
    p = tmp_path / "prepared.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/project_settings.config", "{}")
        z.writestr("Metadata/model_settings.config",
                   '<?xml version="1.0"?>\n<config><object id="1">'
                   '<metadata key="extruder" value="1"/></object></config>')
    return p


def test_snapmaker_hook_remaps(tmp_path):
    c = SnapmakerExtendedClient(ip_address="1.2.3.4")
    p = _prepared(tmp_path)
    c.remap_sliceable_3mf(p, tool_index=2)
    with zipfile.ZipFile(p) as z:
        assert 'value="3"' in z.read("Metadata/model_settings.config").decode("utf-8")


def test_non_snapmaker_hook_is_noop(tmp_path):
    c = ElegooCentauriClient(ip_address="1.2.3.4")
    p = _prepared(tmp_path)
    before = p.read_bytes()
    c.remap_sliceable_3mf(p, tool_index=2)
    assert p.read_bytes() == before
```

- [ ] **Step 2: Run — confirm FAIL** (`remap_sliceable_3mf` missing).

- [ ] **Step 3: Implement**

In `abstract_printer_client.py`, add a default no-op method (near the other overridable methods like `orca_export_args`):
```python
    def remap_sliceable_3mf(self, sliceable_3mf, *, tool_index=None, filament_map=None) -> None:
        """Rewrite the prepared sliceable 3MF in place to route the model's filament(s)
        to the chosen physical tool(s). Default: no-op (vendors that realize mapping
        elsewhere, e.g. at print time)."""
        return None
```
(Add `from pathlib import Path` if you want to type-hint `sliceable_3mf: Path`; not required.)

In `snapmaker_client.py`, add the override on `SnapmakerExtendedClient`:
```python
    def remap_sliceable_3mf(self, sliceable_3mf, *, tool_index=None, filament_map=None) -> None:
        from .snapmaker.remap import remap_3mf
        remap_3mf(sliceable_3mf, tool_index=tool_index, filament_map=filament_map)
```

- [ ] **Step 4: Run — PASS + full suite.**

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/abstract_printer_client.py backend/app/services/snapmaker_client.py backend/tests/services/snapmaker/test_remap_hook.py
git commit -m "feat(printers): remap_sliceable_3mf ABC hook; Snapmaker override, others no-op"
```

---

## Task 4: Cutover — `prepare_hook` seam + revert `mesh_3mf_builder` + queue

**Model: Sonnet.** The atomic switch from the old (generic-builder) routing to the new (vendor-hook) routing. Routing must work via the hook AT THE SAME TIME the old path is removed, so the suite stays green.

**Files:** Modify `backend/app/services/slicer_service.py`, `backend/app/services/mesh_3mf_builder.py`, `backend/app/services/queue_engine.py`, `backend/tests/services/test_mesh_3mf_builder.py`, `backend/tests/services/test_filament_map_e2e.py`.

- [ ] **Step 1: `SliceRequest.prepare_hook` + `slice()` applies it**

In `slicer_service.py`:
- `SliceRequest`: REMOVE `tool_index` and `filament_map`; ADD `prepare_hook: "Callable[[Path], None] | None" = None` (import `from typing import Callable` and `from pathlib import Path` if not present).
- In `slice()`, change the three build call sites to drop `tool_index`/`filament_map`, and after EACH build (STL, primary, recovery), before `_run`, apply the hook:
```python
        if Path(req.source_3mf).suffix.lower() == ".stl":
            stl_to_3mf(req.source_3mf, config, prepared)
            if req.prepare_hook: req.prepare_hook(prepared)
            return self._run(prepared, req, out_dir)

        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=False)
        if req.prepare_hook: req.prepare_hook(prepared)
        try:
            return self._run(prepared, req, out_dir)
        except SliceError as primary_err:
            logger.warning("Slice failed for job %s; retrying geometry-only: %s", req.job_id, primary_err)

        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=True)
        if req.prepare_hook: req.prepare_hook(prepared)
        return self._run(prepared, req, out_dir)
```

- [ ] **Step 2: Revert `mesh_3mf_builder` to vendor-agnostic**

In `mesh_3mf_builder.py`:
- `build_sliceable_3mf`: remove the `tool_index` and `filament_map` params and ALL the routing logic (the `use_filament_map`/`tool_index` branches, the paint `re.sub`, the model_settings re-emit). It becomes: copy entries (dropping `_DROPPED`/`_REPLACED`, plus `_MODEL` only when `geometry_only`), write `project_settings.config`. (I.e. the original pre-Project-2 behavior: preserve `model_settings.config` unless `geometry_only`.)
- `stl_to_3mf`: remove the `tool_index`/`filament_map` params and the model_settings re-emit branch — back to writing just `[Content_Types]`, `_rels`, `3dmodel.model`, `project_settings.config`.
- DELETE the now-unused helpers `_model_settings_with_extruder`, `_object_ids_from_model`, `_patch_model_settings_extruder`, `_patch_model_settings_filament_map`, the `_3D_MODEL_RE`, and the `from .snapmaker import paint_remap` import (they now live in `snapmaker/remap.py`). Let `noUnusedLocals`-equivalent (pyflakes/the tests) catch stragglers; run `grep` to confirm no remaining references in this file.

- [ ] **Step 3: Queue binds the hook**

In `queue_engine.py` `_run_slice_and_print`: capture `tool_index`/`filament_map`/`client` into locals (before the session closes), build the hook, and pass it. Replace the `SliceRequest(...)` construction's `tool_index=...`/`filament_map=...` args with `prepare_hook=...`:
```python
            cfg_tool_index = config.tool_index if config else None
            cfg_filament_map = config.filament_map if config else None
        # ... after the session block, with `client = self._mgr.get_client(printer_id)` available:
        prepare_hook = None
        if client is not None and (cfg_tool_index is not None or cfg_filament_map):
            prepare_hook = (lambda p, c=client, ti=cfg_tool_index, fm=cfg_filament_map:
                            c.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm))
        req = SliceRequest(
            ...,                               # existing fields MINUS tool_index/filament_map
            prepare_hook=prepare_hook,
        )
```
(Read the current `_run_slice_and_print` to place these correctly — `client` is fetched at ~line 251; capture `cfg_tool_index`/`cfg_filament_map` inside the session block where `config` is live. The multi-material `filament_presets` selection STAYS in the queue, unchanged.)

- [ ] **Step 4: Update tests**

- `tests/services/test_mesh_3mf_builder.py`: DELETE the tests that pass `tool_index=`/`filament_map=` to `build_sliceable_3mf`/`stl_to_3mf` (those moved to `snapmaker/test_remap.py` in Task 2). KEEP the generic-build tests (preserve model_settings; geometry_only drops it; project_settings swapped) — they must pass unchanged (the no-routing output is byte-identical to the old `tool_index=None` path).
- `tests/services/test_filament_map_e2e.py`: it currently calls `build_sliceable_3mf(..., filament_map=...)`. Change it to build generic then apply the Snapmaker remap: `build_sliceable_3mf(src, cfg, prepared)` then `from app.services.snapmaker.remap import remap_3mf; remap_3mf(prepared, filament_map=...)` (identity = `remap_3mf(prepared)` / no-op). Keep the assertion: the remapped fixture slices (exit 0) and tool usage differs from identity.
- Add a slicer-seam test `tests/services/test_slice_prepare_hook.py`: `slice()` calls `req.prepare_hook(prepared)` before `_run` (mock `_run`, `_build_config`, and the builder; assert the hook ran with the prepared path); `prepare_hook=None` path doesn't error.

- [ ] **Step 5: Run — full suite + e2e green**

`cd backend && backend\.venv\Scripts\python.exe -m pytest -q` → green. Explicitly run `... tests/services/test_filament_map_e2e.py -v` (slices; a few min) → still passes (remap reroutes, no crash). This proves the cutover preserved behavior.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/slicer_service.py backend/app/services/mesh_3mf_builder.py backend/app/services/queue_engine.py backend/tests/services/test_mesh_3mf_builder.py backend/tests/services/test_filament_map_e2e.py backend/tests/services/test_slice_prepare_hook.py
git commit -m "refactor(slice): route filament mapping through the printer hook; mesh_3mf_builder vendor-agnostic"
```

---

## Task 5: Docs sync

**Model: Sonnet.** Run `themis-docs-sync`. Update `docs/agent/printers.md` (filament→tool routing is now a vendor operation behind `remap_sliceable_3mf`; the Snapmaker plugin `services/snapmaker/` owns the OrcaSlicer paint/extruder format incl. the AGPL-sensitive `paint_remap`; `mesh_3mf_builder` is vendor-agnostic; the slice applies an opaque `SliceRequest.prepare_hook`), `docs/agent/backend.md` (the `services/snapmaker/` package; `SliceRequest.prepare_hook`). Note the AGPL-isolation rationale tersely. Commit `docs(agent): sync for filament-mapping plugin isolation`.

---

## Final verification
`cd backend && backend\.venv\Scripts\python.exe -m pytest -q` green (incl. e2e). `grep -rn "tool_index\|filament_map" backend/app/services/mesh_3mf_builder.py backend/app/services/slicer_service.py` shows the routing is gone from the generic layer (only `prepare_hook` remains in slicer_service). `grep -rn "paint_remap" backend/app/services` shows it only under `services/snapmaker/`.

## Self-review notes (author)
- **Spec coverage:** ABC hook (T3), snapmaker plugin w/ paint_remap moved (T1) + remap_3mf (T2), mesh revert + prepare_hook seam + queue bind (T4), test relocation + e2e through the hook (T2/T4), docs (T5). All spec sections mapped.
- **Behavior invariance:** T2 reproduces the exact old branches on the prepared 3MF; T4 removes the old path AND wires the new one atomically; the e2e + the byte-identical generic-build assertion are the guards. The queue still selects loaded-slot `filament_presets` (vendor-agnostic); only the 3MF rewrite moved.
- **Type/name consistency:** `remap_sliceable_3mf(sliceable_3mf, *, tool_index, filament_map)` identical on ABC + Snapmaker + the bound hook; `remap_3mf(prepared, *, tool_index, filament_map)` in the plugin; `SliceRequest.prepare_hook: Callable[[Path], None] | None`. `paint_remap` import path `app.services.snapmaker.paint_remap` everywhere after T1.
- **Late-binding:** the queue lambda captures `client`/`tool_index`/`filament_map` as default args (avoids closure-over-loop and detached-`config` access in the executor thread).
