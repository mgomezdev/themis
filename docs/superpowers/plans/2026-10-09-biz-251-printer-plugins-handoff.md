# Handoff: BIZ-251 Printer vendors as plugins

Branch `feature/biz-251-printer-plugins` off `develop`. One PR into `develop`. Follow CLAUDE.md (workflow, tests, review gate, contracts/openapi regen). Read first: `docs/printer-interface.md`, `docs/plugins.md`, `docs/provider-interfaces.md`, `docs/agent/`, `backend/app/plugins/host.py`, `backend/app/plugins/spoolman/` (reference plugin). Use `themis-planning`; write the plan to `docs/superpowers/plans/`.

Linear: BIZ-251 (full decisions in its description). Slices of BIZ-250 (routed mode), BIZ-249 (bus), BIZ-262 (registry). Context: BIZ-264, BIZ-248.

## Goal
Each vendor (Bambu, Elegoo Centauri, Snapmaker U1, mock) becomes a plugin. Core imports no vendor module and names no plugin id. Existing behavior unchanged.

## Decisions
- **Routed capability mode** (`printer.client`): all enabled printer plugins active; each call goes to the provider bound on the printer. `exclusive` (inventory) unchanged.
- **Typing:** provider contract = `typing.Protocol` + pydantic DTOs. `RoutedCapability[P]` handle; `host.call_for(cap, binding, lambda p: p.m(...)) -> CallResult[R]` (no string method names). Validate manifest `provides` against the Protocol at load. Declarations and events = pydantic.
- **Printer row:** `plugin_id + manufacturer_id + model_id` (replaces `printer_type`). User picks mfr -> model (-> connection method if several plugins offer it); system derives the provider. Plugin disabled -> printer dormant. Plugin removed -> printer orphaned; model dormant while referenced.
- **Bambu:** the plugin offers **all Bambu models** in the dropdown. The current implementation was tested against a P1S, which is only relevant to the migration (below), not to what the plugin supports.
- **Migration:** existing `bambu` rows -> Bambu P1S (the tested model); `elegoo_centauri` -> Elegoo Centauri; `snapmaker_extended` -> Snapmaker U1 (Extended); `mock` -> mock plugin. Keep connection settings.
- **Mock plugin:** a normal plugin that can be **disabled**. Disabled in production so it doesn't appear as a fleet option; enabled for dev/test.
- **Plugins declare** manufacturers/models (+ optional bed size, toolheads, capability overrides, connection fields), including a custom/generic entry. Minimal registry only; no UUIDs or enabled subsets (BIZ-262 later).
- **Events:** minimal in-process async pub/sub in core (typed names/payloads, no persistence, no versioned envelope; BIZ-249 later). Replace `on_print_complete` / `on_state_change` / `on_ams_change` (wired in `main.py`, ~L125). Plugin owns alarm code -> text (`alarm_codes.py`, `alarms.py` move out) and publishes neutral alarm events that core handles.
- **Camera:** plugin produces the feed (incl. ffmpeg/RTSP: `camera_proxy.py`, `get_ffmpeg_executable`) behind a fleet-card / camera-wall contract (`snapshot()`, `open_stream()` JPEG frames, limits). Core keeps the generic `camera_hub.py`. Remove vendor camera code from `api/routes/cameras.py` and the ffmpeg check in `api/routes/printers.py`.
- **Discovery:** `discover_host` + SSDP matchers move into the plugin interface; core (`discovery.py`, `discovery_net.py`) iterates plugins.
- **Remap (`services/snapmaker/remap.py`, `paint_remap.py`):** owned by the **slicer** side, not the printer plugin. Expose it as a slicing-provider method (e.g. `apply_tool_mapping(3mf, mapping)`) on the `SlicingProvider` interface; the Snapmaker printer plugin must not import it. The code stays in Themis for this PR and the Laminus adapter delegates to it. Moves to Laminus multi-material (BIZ-248) later. This supersedes the earlier "core upload path" interim choice.
- **Bundling:** all vendor plugins bundled; mock bundled but disabled by default in production.
- **Frontend:** keep the current add-printer flow; dropdowns fed from plugin-declared types (`FleetScreen.tsx`, `/printers/types`).
- Remove vendor branches from `api/routes/printers.py` (`_require_capability`, files, jog, temps, camera, upload): use capability flags instead.

## Acceptance
- `tests/test_provider_boundary.py` / `test_no_provider_in_core.py` extended: core imports no vendor client.
- Contract test: two fake routed printer providers coexist; each printer's calls reach only its provider; inventory exclusive tests stay green.
- Fake vendor plugin addable with zero core change; a plugin not satisfying the Protocol is rejected at load.
- Mock plugin disabled -> not in fleet dropdowns; enabled -> usable in tests.
- Virtual-printer suites (`tests/virtual_printers/`, `test_verification_suite_against_virtual_printers.py`) run against plugin-loaded clients.
- Events flow via the bus; alarms: plugin -> bus -> core.
- Migration tests for existing rows; dormant/removed behavior tested.
- Update `contracts/response-keys.json`, regenerate `openapi.json`, raise coverage floors if they grew, sync `docs/agent/` (`themis-docs-sync`).
- Review per CLAUDE.md (one fresh reviewer; write `.claude/review-state.json` before PR).

## Out of scope
UUID registry and enabled subsets, bus durability/versioning, choose-one/fan-out modes, Laminus multi-material capability, new vendors (BIZ-148/149/150).
