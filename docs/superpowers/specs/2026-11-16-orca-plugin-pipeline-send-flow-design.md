# Orca → Themis plugin: pipeline-attached send flow (BIZ-151)

Status: draft for review. Supersedes the "panel-only" send flow in BIZ-151 / BIZ-379–383 where they differ.

## Reference flow

The target UX is the **Set & Send / u1control** pattern for the Snapmaker U1 (found: Set & Send, u1control; the
"SnapSend" plugin named in u1control's credits could not be located, so this spec is modelled on those two).
Observed behaviour:

1. The plugin is added to the **printer's process preset** (Others → Slicing Pipeline Plugin → Add plugin).
   Presets without it are untouched; presets with it get the flow.
2. User presses **Print** / **Send** as usual. The plugin runs in the slicing pipeline for that print.
3. Before anything leaves Orca, a **pop-up** shows what will be sent, pre-filled with smart defaults. User edits, then confirms.
   Pop-up appears only when a choice is needed ("Automagic"); an **Expert** setting forces it every time.
4. Nothing is sent if the target is not ready; errors say what to fix.

Themis adopts this shape. Opting a printer in = adding the plugin to that preset. No separate "Themis" button workflow.

## Flow

```
Slice → [pipeline: psGCodePostProcess] capture gcode + config snapshot
      → Print/Send pressed
      → Themis Send dialog (prefilled, editable)
      → Confirm → POST /api/v1/slicer-sends  (send_id, kind, job|project…)
      → result toast: "Sent as job #118 (queued) · Open in Themis"
```

Default mode is derived from the preset, not chosen up front:

| Situation | Default mode | Why |
|---|---|---|
| Preset mapped to a Themis printer model, user pressed Print | 1 G-code for model | Orca already sliced for that model |
| Preset has no Themis mapping / user switches tab | 2 Geometry | Themis slices per target |
| User has preset overrides vs system parent | 3 Geometry + overrides | Overrides are the diff |

The user can switch mode in the dialog; mode only changes which sections are active.

## Dialog (examples)

### Example A — Mode 1, defaults accepted (Automagic: dialog skipped)

Printer preset `Snapmaker U1 0.4 nozzle`, previously sent once, last choices remembered.

```
Dialog suppressed (Automagic). Toast:
  Sent plate 1 to Themis · job #118 · queued · any "Snapmaker U1"   [Open] [Undo]
Payload:
  upload file bytes unchanged → uploaded_file_id 42
  POST /slicer-sends {"send_id":"7c1e…","kind":"job",
    "job":{"uploaded_file_id":42,"plate_number":1,"model_targets":[{"model_uuid":"<U1>"}]}}
```

Automagic skips the dialog only when: mapping resolved unambiguously, no unsupported overrides, not a re-send, and
Themis reachable. Otherwise the dialog opens.

### Example B — Mode 2, dialog opens (new project, unknown model)

```
┌ Send to Themis ───────────────────────────────┐
│ Mode      ( ) G-code   (•) Geometry   ( ) +Overrides │
│ Destination (•) New job  ( ) Project: [Shelf brackets ▾] [+ New] │
│ Needs bed  210 × 210 × 95 mm   (from plate hull)   [Use] [Ignore] │
│ Filaments  PLA Black · PLA White       [Use] [Ignore]            │
│ Presets    0.20mm Standard @U1         [Use] [Ignore]            │
│ Printers   any eligible                                          │
│                              [Cancel]  [Send]                    │
└────────────────────────────────────────────────┘
```

Result: `kind:"job"`, `min_bed_mm {x:210,y:210,z:95}`, 3MF uploaded, `model_targets` empty (any eligible).
Ignored sections send nothing — identical to an internally created job with those fields empty.

### Example C — Mode 3, overrides + preference

The dialog lists the preset's diff against its system parent, each row checkable and targetable:

```
Overrides (3 differ from system "0.20mm Standard @U1")
 [x] sparse_infill_density   15% → 30%      target: [This model ▾]
 [x] wall_loops               2  → 3        target: [This model ▾]
 [ ] outer_wall_speed        60 → 45        target: [All]           ⚠ not supported by Themis — will be ignored
Preference  1. Printer "U1-Bench"   2. Model "Snapmaker U1"   [x] Only these
```

Payload: `model_targets[0].overrides {"sparse_infill_density":"30%","wall_loops":3}`,
`printer_preference {"entries":[<U1-Bench id>,"<U1 model uuid>"],"strict":true}`.
Unsupported keys (not in the Themis curated contract) are shown with a warning and default to unchecked; the user
may force Ignore/keep, never silently dropped.

### Example D — Re-send (update)

Same project file + plate sent before, job #118 still queued:

```
Previously sent: job #118 (queued)
 (•) Update #118 (replaces file/targets, re-queues)   ( ) Send as new job   ( ) Add as project item
```

If #118 started printing, `GET /slicer-sends/{id}` returns `can_update:false`; the Update option is disabled with
"#118 is printing" and Send as new is preselected. Retry after a lost response reuses the same `send_id` → 200, no duplicate.

### Example E — Errors (with fix)

| Condition | Panel text |
|---|---|
| 401 | "API key rejected. Settings → Themis → paste a valid key." |
| 403 scope | "Key `ci-key` lacks `jobs:write`. Add it in Themis → Settings → API keys." |
| unreachable | "Can't reach http://themis:8000. Check URL / network, then Retry." (file kept) |
| unknown model | "Preset 'X' maps to no Themis model. Pick one: [list]" |
| oversize | "File is 612 MB; Themis limit is 500 MB." |

## Components

| Part | Responsibility | Runs on |
|---|---|---|
| `SlicingPipeline` step at `psGCodePostProcess` | copy `ctx.gcode_path` + config snapshot to plugin data dir; **no UI, no G-code edits** | slicing worker thread |
| Send coordinator | after capture, hand off to UI thread; decide Automagic vs dialog; wait for confirm/cancel | UI thread |
| Dialog | prefill, edit, preview payload | UI thread |
| Payload builders | footprint (hull of plate parts), preset diff, payload assembly — pure Python, unit-tested without Orca | any |
| Send ledger (plugin config) | (project key, plate) → `send_id`, kind, Themis id/URL | any |
| Settings | URL, API key, Test connection (`/auth/me?include_identity=true`), Automagic/Expert toggle | UI thread |

## Settings

- **Automagic** (default): dialog only when a decision is needed. **Expert**: dialog always.
- Remember last choices per printer preset (destination, Use/Ignore per section, preference list).
- Optional: "Send automatically after slicing" is out of scope (always requires Print/Send intent).

## What does not change (backend)

All required endpoints exist on develop (#198, #199): `POST /slicer-sends`, `GET /slicer-sends/{id}`, `min_bed_mm`,
target `overrides`, `printer_preference`. No backend work is required by this flow. Optional later nicety: a
`GET /printer-models` mapping hint to prefill model from `printer_model`.

## Open questions / spikes (must resolve before building)

1. **Dialog from the pipeline.** The pipeline step runs on the worker thread and host UI calls there deadlock (BIZ-151 notes). Need to confirm how Set & Send / u1control show their pop-up on Print: marshal to the UI thread and block the worker on a result, or intercept at a different seam. Spike with a trivial plugin on Orca 2.5.0.
2. **Does Print/Send (print-host path) run `psGCodePostProcess`?** Notes say it runs on Export G-code and Send to print host. Confirm for Bambu `.gcode.3mf` presets.
3. **Cancel semantics.** If the user cancels the dialog, does Orca's own Print/Send continue? (Set & Send says it only intercepts the U1 print host.) Decide whether Themis leaves the original action alone.
4. **Plate geometry in Print flow.** Mode 2/3 need `export_3mf_copy()` (UI thread, 3D view must exist); confirm it is callable from the coordinator at confirm time.
5. **Preset mapping source.** Where mapping Orca printer preset → Themis model lives (plugin config vs Themis `slicer-profile` mappings via `/laminus/catalog`).

## Impact on Linear

- BIZ-379: add the pipeline-attach install step + Automagic/Expert settings.
- BIZ-380 (mode 1): becomes the first dialog-capable path (Examples A, D).
- BIZ-381/382/383: reuse the same dialog (Examples B, C, D).
- New spike story for open questions 1–3 before BIZ-380 proceeds.

## Test plan (no Orca needed)

- Payload builders: footprint hull (rotated/overlapping parts), preset diff (unsupported keys flagged), payload for each of Examples A–C.
- Coordinator: Automagic suppresses on resolved mapping, opens on first send / re-send / unsupported override; Expert always opens.
- Ledger: replay keeps `send_id`; `can_update:false` disables Update.
- Error mapping: each row of Example E from a faked HTTP layer.
