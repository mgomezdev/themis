# BIZ-356 Project items: 3MF plates, 3MF objects and gcode × qty Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** A project item is one printable unit × qty: an STL, a 3MF **plate** (bundle of its objects), a 3MF **object**, or a **gcode** plate, each with a packing rule (Split / Together / Solo / Locked; gcode is fixed "as sliced").

**Architecture:** `project_items` gains `kind` (`stl` | `3mf_object` | `3mf_plate` | `gcode`), `source_ref` (object id or plate number), `pack_rule` (`split` | `together` | `solo` | `locked`) and `source_hash` (the file's content hash when picked; a 3MF item whose file changed must be re-picked). Generation per group: packable items (everything but Locked plates and gcode) go through the neutral `ProjectConstructor` with `ConstructInput(object_id | plate, rule)`; the Laminus constructor sends those to `POST /api/v1/pack/mixed` (laminus#45, feature `pack.mixed`), plain split STLs keep `/pack`. Locked plates and gcode become **fixed jobs**: one job per copy on the source file's plate. BIZ-364's requeue goes through the same planner, so a failed Locked/gcode item gets a new fixed job. `GET /files/{id}/objects` lists a 3MF's objects through the constructor (`list_objects`, Laminus `/projects/inspect`).

**Spec:** design record § Items; Linear BIZ-356; Laminus `docs/laminus-api-for-agents.md` § Mixed multi-plate pack.

## Global Constraints
- STL items: kind `stl`, rule `split` | `solo` (Together/Locked mean nothing for one mesh); default `split`.
- 3MF object: `split` | `solo`. 3MF plate: `split` | `together` | `solo` | `locked`. gcode: no rule (stored `locked`).
- Manifest counts: a bundle's per-plate count is complete copies = instances ÷ parts per copy (parts per copy = manifest `requested` ÷ qty).
- Core names no provider file format: object listing and the mixed pack stay behind the constructor contract.
- Known Laminus gap (laminus-dev fixing): plates mixing multi-instance 3MF objects may fail slicing with Orca -52 → those jobs block, never fail.

## Review Focus
1. A project with all four kinds generates: packed plate(s) + one fixed job per Locked/gcode copy, each item's qty and rule kept. → test.
2. A 3MF item whose file content changed since it was picked → 422 "re-pick". → test.
3. Legacy STL-only projects generate exactly as before (plain `/pack`, round-robin/manifest). → existing tests.
4. A Together plate bundle on the manifest → per-plate count is copies, not objects. → test.
5. A failed Locked/gcode item requeues as a new fixed job (BIZ-364 path). → test.
