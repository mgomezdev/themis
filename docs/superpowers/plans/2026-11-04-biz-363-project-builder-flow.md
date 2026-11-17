# BIZ-363 Project Builder flow Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** The Project Builder becomes a tabbed flow (Details · Items · Groups · Review) with a group banner (filter, drag-to-group, drag to reorder), per-file item sections with bulk edit, and a Review tab whose checks, X/Y/Z fit included, gate "Queue project".

**Spec:** `docs/superpowers/specs/2026-11-01-print-groups-pooled-packing-design.md` (branch `claude/multi-plate-3mf-repacking-s6nakq`) § UI, plus mockup screens 1, 1b, 2, 3. Fit-check scope decided with the user: full X/Y/Z.

**Architecture:**
- Bed height: `printers.bed_z_mm` (nullable, v064). Effective Z = the printer's value, else the machine profile's `printable_height_mm` from the slicing catalog (when the provider reports it), else unknown. Editable in the printer editor next to the bed X/Y.
- Part size: `ProjectConstructor.measure(path, selection) -> list[(x, y, z) | None]` (contract default: unsupported, so the check says sizes are unknown). Laminus: `POST /api/v1/projects/measure` (laminus-dev). Sizes are cached in `part_sizes(part_key PK, x, y, z, measured_at)`, keyed by `part_waivers.part_key` (content hash · kind · ref), so a changed file is re-measured.
- Review: `GET /api/v1/projects/{id}/review` -> `{checks: [{level: "error"|"note"|"ok", tab: "details"|"items"|"groups", message, group_id, item_id}], open}` where `open` = number of errors. Checks:
  - a draft project can't queue (details);
  - a group with items but no eligible printers (groups);
  - "Printer default" on a target that covers a printer with no default process profile (groups, BIZ-358 deferral);
  - fit: a part (non-gcode) that fits no covered printer's bed in X/Y (either XY rotation) and Z is an error (items). Fitting only some printers is a note. An unknown size or Z is a note;
  - colour: no covered printer has the item's filament loaded is a note (items);
  - gcode: no eligible printer is an error, otherwise an ok line naming the printers.
- `POST /projects/{id}/items` accepts `group_id`, so new rows keep the group chosen on the Items tab.
- Frontend:
  - `ProjectBuilderScreen` keeps its state and adds `tab`, with `BuilderTabs` (counts + Review "N to fix") and a Back/Next bar.
  - Details tab: the metadata fields, links and parts.
  - Items tab: the file tree plus `GroupBanner` and `ItemSections`, i.e. collapsible per-file sections, By file / By group, Collapse all, row/section checkboxes and the `BulkBar`.
  - Groups tab: `GroupBanner` without All, with the selected group's `PrintGroupsCard` row. Dragging badges reorders the groups.
  - Review tab: the checks and "Queue project" (generate), disabled while `open > 0`.
  - Groups and Review need a saved project: on a new project they offer "Save draft".
  - Drag-and-drop is native HTML5: rows onto a badge set `group_id`, and dropping on "+ New group" creates a group and moves the rows there.

## Global Constraints
- Nothing typed is lost when switching tabs (one component, one state).
- Core names no vendor; `measure` goes through the provider contract.
- Review never calls the provider on the event loop (worker thread), and an unmeasurable part is a note, never an error.

## Review Focus
1. A part too big for every eligible printer (X/Y or Z) → Review error, Queue disabled. → test.
2. A part that fits some printers → note, Queue enabled. → test.
3. "Printer default" on a printer without a default → error naming the printer and group. → test.
4. Rows dragged onto a badge move group; onto "+ New group" create one. → FE test.
5. Bulk bar applies to the checked rows only (filtered view respected). → FE test.
