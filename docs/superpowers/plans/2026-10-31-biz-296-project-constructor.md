# BIZ-296 Laminus `3mf.construct` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native, main session — CLAUDE.md). Steps use `- [ ]`.

**Goal:** Project generation packs models through the neutral `3mf.construct` capability (`ProjectConstructor` / `resolve_construct`) served by the Laminus plugin, and `SlicingProvider` loses `ARRANGE`/`PACK_MODELS`.

**Architecture:** New core capability `3mf.construct` (exclusive) whose plugin part exposes `project_constructor()`. `LaminusProjectConstructor` wraps `LaminusSidecarClient` pack-by-bed. `routes/projects.py::_generate_project` resolves it via `get_project_constructor()` + `resolve_construct`, builds a `TargetPrinter` from the smallest eligible bed, and calls `construct()` in a worker thread with a timeout and `CancelToken`.

**Tech stack:** FastAPI, httpx (sync, worker thread), pytest.

**Spec:** Linear BIZ-296; `docs/architecture/laminus-contract.md` §1, §2, D2, §8.

## Global constraints
- Core names no vendor; core never imports `services/providers/laminus` (boundary tests).
- `bed_mm` required (all > 0) else `ProviderError(UNSUPPORTED_TARGET)`; `model_uuid` optional and informational.
- Anything the constructor can't preserve → `ProviderError(UNSUPPORTED_INPUT)`.
- Pack recipe hash (`{"items", "pack": {"mode": "geometry", "bed": [x, y]}}`) unchanged: existing packs stay reusable.
- Blocking provider calls only via `asyncio.to_thread`.

## Decisions
- `ConstructRequest.inputs` becomes `tuple[ConstructInput, ...]`; `ConstructInput(path: Path, quantity: int, ref: str)` replaces `slicing.PackItem` (moved to the contract; sidecar client takes it). Quantity/ref are needed for one-upload-per-file packing.
- Laminus constructor accepts **STL only** until laminus-dev confirms `/pack` preserves 3MF modifiers/painted supports/plates; `.3mf` input → `UNSUPPORTED_INPUT`. `/api/v1/arrange` has no Themis caller and no bed parameter: `SlicingProvider.arrange`, adapter `arrange`, client `arrange` are removed.
- HTTP mapping in generate: `NO_PROVIDER` → 422 `"No project builder is configured — enable a plugin that provides 3mf.construct (Settings -> Plugins); it is required for generation"`; `DEFAULT_UNAVAILABLE` → 503 `"The project builder is unavailable: <status.detail>"`; `ProviderError` TIMEOUT → 504 (existing text), UNSUPPORTED_INPUT/UNSUPPORTED_TARGET → 422 `str(err)`, others → 502 `"Project builder error during generation: <message>"`.
- `TargetPrinter(bed_mm=(min x, min y, 250.0), model_uuid=<the one model shared by every bed printer, else None>)`.

## Review focus
1. Two projects items pointing at the same STL → still one input, summed quantity (existing test, re-pointed).
2. Provider answered READY but `construct` raises TIMEOUT → 504, no file/job rows left behind.
3. Provider status NOT_READY → 503, `construct` never called.
4. Second filament group fails after first committed → hooks/partial behaviour unchanged (test_project_hooks).
5. Cancel token set before the call → CANCELLED, no output file left in `output_dir`.

---

### Task 1: Contract + capability + Laminus constructor

**Files:** modify `backend/app/services/providers/contract.py`, `slicing.py`, `laminus/adapter.py`, `laminus/sidecar_client.py`, `laminus/connection.py`, `app/plugins/laminus/__init__.py`, `themis-plugin.toml`, `app/plugins/capabilities/__init__.py`; create `app/plugins/capabilities/three_mf_construct.py`, `services/providers/laminus/constructor.py`, `services/providers/construction.py`; tests `tests/services/test_laminus_constructor.py`, update `test_laminus_contract.py`, `test_slicing_provider_contract.py`, `test_laminus_sidecar_client.py`, `tests/plugins/test_laminus_plugin.py`.

**Produces:**
- `contract.ConstructInput(path, quantity, ref)`; `ConstructRequest(inputs: tuple[ConstructInput, ...], target)`.
- `capabilities.three_mf_construct.CAPABILITY = "3mf.construct"`, mode `exclusive`, protocol `ConstructPlugin.project_constructor(url=None)`.
- `LaminusProjectConstructor(client_factory, *, version)`: `identity` (`provider_id="laminus"`), `status()` (same mapping as the producer), `construct(request, output_dir, *, timeout, cancel) -> Artifact(PROJECT_3MF)` writing `output_dir/"project.3mf"`.
- `construction.get_project_constructor() -> ProjectConstructor | None` (host's active `3mf.construct` part → `project_constructor()`).

- [ ] Tests (fail first): bed with a 0 dimension → UNSUPPORTED_TARGET and no HTTP call; `.3mf` input → UNSUPPORTED_INPUT; cancelled token → CANCELLED, no HTTP call; cancel set during the pack → CANCELLED, no file left; httpx `ReadTimeout` → TIMEOUT; 503 health → status NOT_READY; pack 500 → FAILED with the detail; happy path posts `bed_x/bed_y/bed_z` + `items` with ref-prefixed names and returns an Artifact whose file holds the bytes; plugin manifest lists `3mf.construct` as `exclusive`; `get_project_constructor()` returns a Laminus constructor once the plugin is configured, None when unconfigured.
- [ ] Implement; remove `ARRANGE`, `PACK_MODELS`, `arrange`, `pack_models`, `PackItem` from `SlicingProvider`/adapter/fakes; client `pack_stls(items, bed_x, bed_y, bed_z, timeout)`; drop `pack_stls_by_uuid` and client `arrange` (no callers).
- [ ] `pytest tests/services tests/plugins -q` green.

### Task 2: Migrate project generation

**Files:** modify `backend/app/api/routes/projects.py` (`_pack_stored_models` → `_construct_stored_models`, `_generate_project`), `tests/fake_providers.py` (`fake_packer` → a `FakeProjectConstructor` that writes scripted bytes / raises scripted errors and records `ConstructRequest`s), tests `test_projects_api.py`, `test_pack_on_store.py`, `test_project_hooks.py`, `test_slice_cache_api.py`, `test_projects_children.py`.

**Consumes:** Task 1 names. Patch point: `app.api.routes.projects.get_project_constructor`.

- [ ] Tests: NO_PROVIDER → 422 text above; NOT_READY → 503 and no `construct` call; TIMEOUT → 504; FAILED → 502; UNSUPPORTED_INPUT → 422; request carries `TargetPrinter((256.0, 256.0, 250.0), None)` with no printers and the shared model uuid when all eligible printers share one; one input per library file with summed quantity and ref = file id.
- [ ] Implement; full backend suite green; `python scripts/export_openapi.py` (502/503 response docs) and commit the diff.

### Task 3: Real Orca fixtures + docs

**Files:** `backend/tests/fixtures/orca/` (pack output from laminus-dev, two-plate `.gcode.3mf`), test `tests/services/test_orca_fixtures.py`; docs `docs/architecture/laminus-contract.md` §8, `docs/provider-interfaces.md`, `docs/plugins.md`, `docs/agent/backend.md`, `docs/architecture/3mf-ownership.md` row.

- [ ] Tests: `plate_numbers()` on the pack fixture == `[1, 2]`; on the gcode.3mf the plate thumbnails (`Metadata/plate_N.png`) are read by the existing plate/thumbnail extractor; record whether pack output embeds thumbnails (assert the observed fact).
- [ ] Docs updated; commit.
