# BIZ-360 Print groups with fixed-bed packing Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** A project's items belong to ordered print groups; each group says which printers may take its jobs (BIZ-357 targets), with which process profile per target ("Printer default" = BIZ-358), and the bed envelope it packs for. Generation runs group by group, so queue order follows group order.

**Architecture:** `project_groups` (ordered, one `is_default` per project, `envelope_x/y` NULL = auto = smallest covered bed, `mix_colours` stored for the colour rule) + `project_group_targets` (per group: `target_key` = the shared selection key `<printer id>` / `model:<uuid>` / `any`, `process_profile`, `""` = printer default) + `project_items.group_id`. `services/project_groups.py` owns default-group creation, target resolution and envelope fit. `_generate_project` loops groups → filament asks → packs. Legacy callers (no saved targets on the Default Group) keep working: the request's `eligible_*` + `process_preset` apply to the Default Group.

**Spec:** design record § Groups / § Queue ("Queue order = group order"); Linear BIZ-360; mockup Groups.

## Global Constraints
- Default Group: created with the project (and by migration for existing ones), renamable, never deletable; an empty group adds nothing.
- Deleting another group moves its items to the Default Group.
- Fixed bed: an explicit printer is eligible only if its bed contains the envelope (x ≥ ex and y ≥ ey). Model / Any-printer targets materialize as today; a plate that doesn't fit a claiming printer fails the BIZ-355 fit check and is rescued elsewhere.
- Deferred (documented on the issue): Solo/Locked/gcode items (need BIZ-356), the pool strategy (BIZ-362), and the colour toggle's mixing behaviour (stored; packing stays one pack per ask until multi-material items exist).

## Review Focus
1. Two groups, different process profiles → each job's configs carry its group's profile and only its group's printers. → test.
2. Group order → all of group 1's jobs queue before group 2's. → test.
3. Envelope larger than a selected printer's bed → that printer gets no config. → test.
4. Legacy generate (no groups touched, `eligible_printer_ids` + `process_preset` in the body) → identical result to before. → existing tests stay green.
5. Deleting a group moves its items to the Default Group; deleting the Default Group is refused. → test.

### Task 1: Schema + groups API
Models, migration v059 (create both tables, add `project_items.group_id`, backfill a Default Group per project and assign its items), `services/project_groups.py` (`ensure_default_group`), routes in `api/routes/project_groups.py`: `GET/POST /projects/{id}/groups`, `PATCH/DELETE /projects/{id}/groups/{gid}` (name, envelope, mix_colours, targets full replace), `PUT /projects/{id}/groups/order`; `PUT /projects/{id}/items/{iid}` accepts `group_id`; items serialize `group_id`. contracts + openapi.

### Task 2: Generate by group
Resolve each group's targets (explicit printers filtered by envelope; models; any) and envelope; pack per ask per group in group order; configs/targets carry the target's process profile; response jobs carry `group_id`.

### Task 3: Frontend groups editor
ProjectDetailScreen "Print groups" card: per group name, `PrinterTargetPicker`, per-target process select (Printer default + presets compatible with the target's printers), envelope (Auto / custom X × Y), add / delete / move; item rows get a group select. Generate posts no eligibility (groups are saved). ProjectBuilder keeps its legacy picker (BIZ-363 reworks the builder).

### Task 4: Docs, checks, review, PR.
