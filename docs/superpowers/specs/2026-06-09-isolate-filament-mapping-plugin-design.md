# Isolate Filament→Tool Mapping Behind the Printer Interface — Design Spec (Sub-project C)

**Date:** 2026-06-09
**Status:** Approved (pending spec review)
**Branch:** `worktree-multi-material-tool-mapping`

## Goal & scope

Move the **model-filament → physical-tool mapping operation out of the generic slicer layer and behind
the `AbstractPrinterClient` interface**, so vendor-specific (and AGPL-sensitive) slice manipulation is
isolated per printer. This is a **refactor of Project 2 + Sub-projects A/B with no behavior change** —
the same gcode is produced; only *where the code lives* changes.

**Two motivations:**
1. **Licensing isolation.** `paint_remap.py` reimplements OrcaSlicer's (AGPL) `TriangleSelector` paint
   codec. Containing it (and all OrcaSlicer-3MF-routing code) in a clearly-delineated Snapmaker plugin
   keeps the core vendor-agnostic and the AGPL-derived code in one isolable place. *(Not legal advice —
   recommend a real review if Themis is distributed/commercial; a file format is generally not
   copyrightable, but code derived by reading AGPL source is the gray case worth isolating.)*
2. **Architecture.** Filament mapping is inherently per-vendor: Snapmaker rewrites the sliceable 3MF at
   **slice time**; Bambu uses its **AMS-mapping API at print time** (`ams_mapping` already in
   `StartPrintOptions`). The generic slicer should not bake in one vendor's approach.

**In scope:** the `remap_sliceable_3mf` ABC hook; a `services/snapmaker/` plugin holding `paint_remap.py`
+ the 3MF routing rewrite; reverting `mesh_3mf_builder` to vendor-agnostic; the slice seam that invokes
the hook; relocating tests. **Out of scope:** any functional change to routing/UI/queue/gating; Bambu
multi-filament `ams_mapping` (its print-time path already exists for single-filament and is unchanged).

## Current state (to refactor)

- `slicer_service.slice()` builds a prepared 3MF via `mesh_3mf_builder.build_sliceable_3mf(..., tool_index,
  filament_map)` / `stl_to_3mf(..., tool_index)`, which apply the routing rewrite (object-`extruder`
  injection for `tool_index`; `paint_remap` + per-object `extruder` for `filament_map`).
- `SliceRequest` carries `tool_index` + `filament_map`.
- The queue (`_run_slice_and_print`) holds the client (`get_client(printer_id)`) before slicing, and
  realizes Bambu's mapping at print time (`StartPrintOptions.ams_mapping` → `start_print`).

## Architecture & data flow

### 1. The ABC hook — `AbstractPrinterClient.remap_sliceable_3mf`
```python
def remap_sliceable_3mf(self, sliceable_3mf: Path, *,
                        tool_index: int | None = None,
                        filament_map: list | None = None) -> None:
    """Rewrite the prepared sliceable 3MF IN PLACE so the model's filament(s) route
    to the chosen physical tool(s). Default: no-op — vendors that realize the mapping
    elsewhere (e.g. Bambu at print time) leave this alone."""
```
Default implementation: `pass`. Called on the prepared 3MF after generic build, before OrcaSlicer runs.

### 2. The Snapmaker plugin — `backend/app/services/snapmaker/`
New package (`__init__.py`):
- **`paint_remap.py`** — moved verbatim from `app/services/paint_remap.py` (the AGPL-sensitive
  TriangleSelector codec). Imports updated.
- **`remap.py`** — `remap_3mf(sliceable_3mf: Path, *, tool_index=None, filament_map=None) -> None`: the
  routing rewrite currently inside `mesh_3mf_builder` (object-`extruder` injection for the single-tool
  case; `paint_remap` of `3D/*.model` + per-object `extruder` remap for the multi case), now operating on
  an already-built prepared 3MF (works for both STL-derived and 3MF-derived prepared files, since it
  reads/writes the prepared zip's `model_settings.config` + `3D/*.model`).
- `SnapmakerExtendedClient.remap_sliceable_3mf(...)` overrides the ABC hook → calls `remap_3mf(...)`.

### 3. `mesh_3mf_builder` reverts to vendor-agnostic
`build_sliceable_3mf` and `stl_to_3mf` **drop** the `tool_index`/`filament_map` parameters and all the
routing-rewrite branches + helpers (`_model_settings_with_extruder`, `_patch_model_settings_extruder`,
`_object_ids_from_model`, `_patch_model_settings_filament_map`, the `paint_remap` calls). They go back to:
embed `project_settings.config` + preserve/passthrough geometry + the geometry-only recovery drop. The
moved helpers live in `services/snapmaker/remap.py`.

### 4. The slice seam — generic transform hook on `SliceRequest`
`SliceRequest` **drops** `tool_index`/`filament_map` and gains:
```python
    prepare_hook: Callable[[Path], None] | None = None  # applied to the prepared 3MF before slicing
```
`slice()` calls `if req.prepare_hook: req.prepare_hook(prepared)` after each `build_sliceable_3mf`/
`stl_to_3mf` and before `_run` (so it re-applies on the geometry-only recovery rebuild too). The slicer
knows nothing about routing or vendors — it applies an opaque prepared-3MF transform.

`queue_engine._run_slice_and_print` (which already has `client`, `tool_index`, `filament_map`) binds:
```python
    prepare_hook = None
    if config and (config.tool_index is not None or config.filament_map):
        prepare_hook = lambda p: client.remap_sliceable_3mf(
            p, tool_index=config.tool_index, filament_map=config.filament_map)
    req = SliceRequest(..., prepare_hook=prepare_hook)
```
(Capture `tool_index`/`filament_map`/`client` into locals to avoid late-binding/session issues.) The
multi-material `filament_presets` (loaded-slot profiles) selection stays in the queue (vendor-agnostic
preset choice); only the 3MF *rewrite* moves to the client.

### 5. Bambu / Elegoo
Inherit the no-op `remap_sliceable_3mf`. Bambu continues to realize mapping at print time via
`ams_mapping` (unchanged). No vendor's routing code lives in the core anymore.

## Behavior invariance
Same inputs → same prepared 3MF → same gcode. The refactor is a pure relocation + indirection: the queue
already computed `tool_index`/`filament_map`; now it hands the client a hook instead of passing them into
the generic builder. The single-tool (Project 2) and multi (Sub-B) outputs are byte-identical to before.

## Testing
- **Relocate** `tests/services/test_paint_remap.py` → `tests/services/snapmaker/test_paint_remap.py`
  (import path `app.services.snapmaker.paint_remap`); same assertions (byte-exact round-trip on the
  fixture).
- **Move** the `tool_index`/`filament_map` rewrite tests from `test_mesh_3mf_builder.py` to a new
  `tests/services/snapmaker/test_remap.py` (now calling `services.snapmaker.remap.remap_3mf` on a prepared
  3MF). `test_mesh_3mf_builder.py` keeps only the generic-build tests (and must stay green — the
  `build_sliceable_3mf` output with no routing is byte-identical to the old `tool_index=None` path).
- **New** `test_remap_hook.py`: a Snapmaker client's `remap_sliceable_3mf` rewrites a prepared 3MF's paint
  + object extruder; Bambu/Elegoo clients' hook is a no-op (prepared 3MF unchanged).
- **Slice seam:** a `slicer_service` test that `prepare_hook` is invoked on the prepared 3MF before
  `_run` (mock `_run`); `prepare_hook=None` path unchanged.
- **e2e** (`test_filament_map_e2e.py`): update to drive the slice through the Snapmaker hook (or call
  `services.snapmaker.remap.remap_3mf` then slice) — still asserts the remapped fixture slices (exit 0)
  and reroutes. Keep it as the functional proof.
- Full backend suite + frontend unchanged (no frontend changes in C).

## File structure
**Create:** `backend/app/services/snapmaker/__init__.py`, `…/paint_remap.py` (moved), `…/remap.py`;
`backend/tests/services/snapmaker/` (moved/added tests).
**Modify:** `backend/app/services/abstract_printer_client.py` (`remap_sliceable_3mf` default),
`backend/app/services/snapmaker_client.py` (override), `backend/app/services/mesh_3mf_builder.py` (revert),
`backend/app/services/slicer_service.py` (`SliceRequest.prepare_hook`; call it),
`backend/app/services/queue_engine.py` (bind the hook).
**Delete:** `backend/app/services/paint_remap.py` (moved into the plugin).
**Docs:** `themis-docs-sync` (printers.md: routing is now a vendor hook; the Snapmaker plugin holds the
OrcaSlicer-format code).

## Sequencing
1. Create `services/snapmaker/` (move `paint_remap.py`; add `remap.py` with the rewrite logic moved from
   `mesh_3mf_builder`); relocate their tests — all green.
2. Add the ABC hook + Snapmaker override.
3. Revert `mesh_3mf_builder`; add `SliceRequest.prepare_hook` + slice() call; bind in the queue.
4. Fix up tests (mesh now generic; seam + hook tests; e2e through the hook).
5. Docs.
After C: the branch (Project 2 + A + B + C) is ready to merge.
