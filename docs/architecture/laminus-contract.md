# Laminus integration contract (BIZ-248)

Epic BIZ-265. Builds on `3mf-ownership.md` (BIZ-259). Code: `backend/app/services/providers/contract.py` (neutral DTOs, ABCs, pure
resolver, cache key); proof: `backend/tests/services/test_laminus_contract.py` (fakes in `tests/fake_providers.py`).
**Additive only: no existing path uses the contract yet.** Implementations: BIZ-281 (FormatProvider), BIZ-260 (settings), BIZ-256 (storage).

## 1. Capabilities

| Capability | Mode (BIZ-250) | ABC | Does |
|---|---|---|---|
| `3mf.construct` | exclusive: one active provider | `ProjectConstructor` | arrange/combine `model_3mf`/`stl` inputs into one `project_3mf` for a target |
| `gcode.produce` | choose-one: many enabled | `GcodeProducer` | turn one model input + target + settings into `gcode` / `gcode_3mf` |

Pre-sliced G-code (`gcode`, `gcode_3mf`) is **pass-through and needs no provider**: dispatch needs only the offline reader (3mf-ownership §3) and the
file's eligible model UUIDs (BIZ-263). Printer-provider identity and slicer-provider identity are unrelated; a printer never requires a slicer.
Laminus = one adapter implementing both ABCs (today's `SlicingProvider` is wrapped, not replaced, until BIZ-260 migrates callers).

## 2. Methods (all sync; call from a worker thread, never the event loop)

```
identity -> ProviderIdentity                       # local, no network
status(timeout=2.0) -> ProviderStatus              # never raises for "unusable"
ProjectConstructor.construct(ConstructRequest, output_dir, *, timeout=630, cancel=None) -> Artifact
GcodeProducer.supports_target(TargetPrinter) -> bool           # local/cached, no network
GcodeProducer.profiles(kind, TargetPrinter) -> Sequence[Profile]
GcodeProducer.curated_settings() -> tuple[CuratedSetting, ...]
GcodeProducer.produce(ProduceRequest, output_dir, *, timeout=630, cancel=None) -> Artifact
```
`compatible_presets` stays on `SlicingProvider` (needs a catalog); `profiles()` is its neutral successor on `gcode.produce`.
Offline inspection (`parse_estimates`, `inspect_overrides`, `curated_settings`) is the `FormatProvider` of BIZ-259/BIZ-281, never a network call.

**DTOs** (frozen dataclasses): `ProviderIdentity(provider_id, version, engine_id?, engine_version?, build?)`, `ProviderStatus(state, identity?, detail)`,
`TargetPrinter(bed_mm, model_uuid?, toolheads)`, `FilamentChoice(slot, material, profile?, color_hex?)`, `SliceSettings(process_profile?, filaments,
overrides, tool_index?, filament_map?)`, `Profile(name, kind, compatible_models)`, `ConstructRequest(inputs, target)`, `ProduceRequest(source,
input_kind, plate, target, settings, want, pin_engine_version?)`, `Artifact(path, kind, produced_by)`, and the BIZ-259 inspection DTOs
`PlateEstimate`, `SettingChange`, `SlotWarning`, `OverrideInspection` (`has_findings` derived), `CuratedSetting`. BIZ-281 imports these from `contract.py`.

## 3. File kinds

| Kind | Construct in | Produce in | Produce out |
|---|---|---|---|
| `model_3mf` (Orca/Bambu layout) | yes | yes | `gcode`, `gcode_3mf` |
| `stl` | yes | yes | `gcode`, `gcode_3mf` |
| `gcode`, `gcode_3mf` | no | no (`unsupported_input`) | pass-through |

Construct out: `project_3mf`. Preserving plates, instances, modifiers, painted supports and per-object settings through `construct` is a provider
guarantee (BIZ-236/240); an operation it can't preserve raises `unsupported_input`.

## 4. Decisions

### D1. Settings ownership: **A, Themis-owned neutral settings** (chosen)
`SliceSettings` is persisted by Themis (job and printer config) as plain values: a process-profile **name**, per-slot `FilamentChoice` (material + profile
**name**), and `overrides` restricted to keys the provider lists in `curated_settings()`. Providers advertise choices (`profiles`, `curated_settings`);
they never own stored values or a settings UI. Refs/UUIDs are resolved from names at call time (as today).

| | A. Themis-owned neutral | B. Plugin-owned opaque |
|---|---|---|
| Job/printer persistence | one schema in Themis, survives provider swap/removal (dormant, not lost) | blob per provider; swap loses/invalidates it; migration per plugin |
| Cache identity | Themis fingerprints known fields | needs a provider-supplied fingerprint; Themis can't verify it covers everything |
| Compatibility checks | `Profile.compatible_models` + `supports_target` checked before dispatch | only the provider knows; failure surfaces at slice time |
| Inventory to filament profile | inventory `profile_links` map to `FilamentChoice.profile` in core | core can't read the blob; per-plugin mapping UI |
| Cost | neutral vocabulary limits exotic engine knobs to the curated list | full engine power, UI per plugin |

Rationale: choose-one slicing + dormant-provider rules (BIZ-250) need settings that outlive any one provider, and cache/eligibility need Themis to
see the inputs. **Explicitly NOT decided: opaque plugin-owned settings UI, and NAS/S3 storage.** Extending A with provider-specific advanced keys is
possible only via `curated_settings()` (still Themis-stored); anything beyond needs its own decision. BIZ-260 moves Laminus *connection* config
(URL, timeouts) behind plugin settings; that is not slice settings. BIZ-256 must treat `Artifact.path` as local until it decides otherwise.

### D2. Construction target: **both, with `bed_mm` mandatory and `model_uuid` optional**
Themis always supplies bed dimensions (registry value, or the user's for a `custom` model, BIZ-262), so the provider never looks models up.
`model_uuid` (the BIZ-262 core UUID) is for profile mapping, eligibility and cache identity; it is None only for printer-less work (library pack by bed).
Bed-only would lose profile selection; UUID-only would break custom models and force providers to hold a model table.

### D3. Format reading
As BIZ-259: reading held files is offline (`FormatProvider` for slicer vocabulary; generic core reader for layout). Construction/slicing is online-only.

## 5. Identity, readiness, errors

`ProviderIdentity.provider_id` is the stable plugin/registry id (not a URL). `engine_id`/`engine_version` name the engine actually used (BIZ-243
pinning: `ProduceRequest.pin_engine_version`; an unavailable pinned version fails `invalid_settings`). `build` is a finer hash recorded on the
artifact and is part of the cache key (§7).

`status()` returns `ready | not_ready` (answered, still starting/building catalog) `| unreachable` (incl. not configured). Queue preflight uses `status(2)`.
`ProviderError(code)`: `not_ready`, `unreachable`, `unsupported_input`, `unsupported_target`, `invalid_settings`, `failed`, `timeout`, `cancelled`.
Themis effects (unchanged semantics): slice `failed`/`timeout` -> job `blocked` + `slice_failed` on that printer config (rescuable on another printer);
`unsupported_*`/`invalid_settings` -> that printer ineligible; `not_ready`/`unreachable` -> wait (no state change).

**Timeout/cancellation.** Every long call takes `timeout` (default 630 s, incl. internal polling; health 2 s) and raises `timeout`. `CancelToken` is
set by core when the job is cancelled or the printer stopped (CLAUDE.md cancel/stop link); the provider polls it between round-trips, aborts remote
work best-effort, removes partial output, raises `cancelled`. A cancel never marks a job `failed`.

## 6. Queue-pull resolution (`resolve_work`, per pulling printer)

| Case | Outcome |
|---|---|
| `gcode`/`gcode_3mf`, target model in file's eligible set | `pass_through`; **no provider consulted or required, even if all are down** |
| ...target model not in set / set empty or unknown | `blocked` `model_ineligible` / `eligibility_unknown` (unknown is never a match) |
| model input, `preferred` set (per-printer/job) | preferred must be enabled, then eligible (accepts the kind, `supports_target`), then `ready` (checked in that order); else `blocked` `preference_dormant` / `preference_ineligible` / `preference_unavailable`. **Never falls back** |
| model input, no preference | capability default (selection row), checked enabled+`ready` first, then eligible: -> `blocked` `default_unavailable` / `default_ineligible`; no default set -> `no_provider` |
| no provider enabled, or enabled but no default and no preference | `blocked` `no_provider`; the default/preferred provider exists but does not fit this printer/input -> `default_ineligible` / `preference_ineligible` (never silently another provider). Job stays queued; surfaced to the user |
| multiple target printers (`resolve_job`) | each printer resolved independently with its own model and preference; claimable by any printer whose outcome is not `blocked`; one blocked printer never blocks others |
| `3mf.construct` (`resolve_construct`) | the single active provider if `ready` -> `proceed`; else `blocked` `no_provider`, or `default_unavailable` (reason reused for the one active provider) |

Matches `PluginHost.resolve` outcomes (BIZ-250); wiring `resolve_work` to the host and the queue is BIZ-260's job, not this issue's.

## 7. Cache key (`contract.cache_key`)

sha256 of canonical JSON of: `provider` id; `engine` id, `engine_version` (the request's pin, else the provider-reported version) and `build`
(`ProviderIdentity.build`), each **omitted when unknown**; source content hash; plate; target `model_uuid`, `bed_mm`, `toolheads`;
`SliceSettings.fingerprint()` (process profile, per-slot material + profile name, overrides, tool index, filament map); artifact kind.
Excluded on purpose: filament colour (preview only), paths, timestamps, `identity.version` (adapter version, not engine).

This is a **new key format**, not a superset of `slice_cache.CacheKeyInputs`: rows saved under the old keys will simply miss after migration and
be re-sliced (or the migrating issue may map them explicitly). Note the old key deliberately left engine version/build out and tracked them as
staleness; this contract makes them part of identity, so an engine upgrade is a miss, not a stale hit.
