# Print groups and pooled packing: design decisions

Epic: **BIZ-354** (sub-issues BIZ-355 … BIZ-365; follow-up BIZ-353).
Mockup: [`mockups/2026-11-01-print-groups-pooled-packing.html`](mockups/2026-11-01-print-groups-pooled-packing.html), a static page to open in a browser.

This is the record of what was agreed while workshopping the idea. It is not a delivery plan: each sub-issue gets
its own plan in `docs/superpowers/plans/` when work starts. Where this file and the mockup disagree, this file wins.

## Problem (verified in code, 2026-11-01)

- Laminus production slices run `orcaslicer --slice N --arrange 1` over the **whole** multi-plate 3MF
  (`laminus/app/main.py` `run_orcaslicer_task`). Orca re-packs every plate for the target bed, so "plate N" can contain
  different objects on different printers: the on/off-plate drift. With `--arrange 0` alone, Orca places plates by bed
  stride, so objects land between plates on a different bed.
- `generate_project` (`backend/app/api/routes/projects.py`) packs STL-only items for the **smallest** eligible bed, and
  writes `project_item_quantities` as a round-robin guess (`qty // num_plates`), not the real plate contents. Outcome
  reporting (`PUT /jobs/{id}/outcome`, `job_item_failures`) builds on that guess.
- Generate takes one `process_preset` for every eligible printer and model. There is no printer-level default process profile.

## Decisions

### Items
- An item is **one printable unit × qty** (qty ≥ 1): STL file · 3MF plate (bundle of every object on the source plate) ·
  3MF object · gcode plate (`.gcode` or plate N of a `.gcode.3mf`).
- Gcode items are never repacked. They are eligible per `file_machine_eligibility` and need no group settings.
- Filament: pre-filled from the file, overridable with the existing ask rules. STLs are set by the user.
- Packing rule per item: **Split** (default) · **Together** (bundle on one plate) · **Solo** (alone on its plate, e.g. vase
  mode / avoiding z-hop marks) · **Locked** (source plate as authored, centred for the bed).

### Groups
- Every project has a **Default Group** (can be renamed, not deleted, empty = adds nothing to the queue). New items land in it.
- Group = eligible printers + process profile per eligible entry + packing strategy + colour rule (+ optional max parts per plate).
- Eligible printers are **opt-in** badges, the same picker as New Job: yellow = a group of printers (Any printer / Any
  printer of a model), blue = a specific printer. "Any printer" is new (New Job gets it too).
- Process profile per badge, or **Printer default**: a new per-printer default process profile, set next to the
  machine profile it gets from its model.
- Packing strategy:
  - **Now, for one bed size** (options 1–3 from the original idea): pack once for an envelope. Eligible = every
    selected printer whose bed **contains** it (fits within, not same size; X, Y **and** Z).
  - **When a printer picks it up** (option 4, pool): see Queue.
- Queue order = group order (drag the badges).

### Colour
- Multi-material items pack with items whose materials are the same or a subset of theirs.
- Different single-colour items share a plate only when a multicolour item already forces swaps, or the printer is a
  toolchanger. This avoids a purge every layer on single-nozzle AMS printers.
- Per-printer **Single colour only** flag: the printer takes any loaded colour but never a multicolour plate.

### Queue (pools)
- A pool is one queue entry, not a job. A claim filters the remaining parts to what the printer can take, asks the
  slicer for the **next plate** (Orca arrange, plate 1 = the most parts that fit), reserves those parts and creates a
  normal job (normal lifecycle, cancel↔stop, plate-clear).
- **Fall-through:** wrong filament, nothing fits, pool mid-pack for another printer, pack error, or a waived part
  means skip to the next queue entry. The pool keeps its place, and the project may stay at the top of the queue.
  (Today a `slice_failed` config blocks and stops the scan; that rule stays for ordinary jobs only.)
- The slicer returns a **manifest** of parts per plate. Per-part counts replace the round-robin guess everywhere.
- No plate preview before printing for pools (accepted).

### Results and failures
- Plate clear becomes **Print results**: per part, Succeeded / Failed counts (default all succeeded), with
  "Nothing failed" / "Whole plate failed" quick fills. Bundles are reported part by part.
- Failed parts are restored automatically: to the pool or fixed-bed set; as a new job for Solo, Locked and gcode.
  A locked plate reprints **only the failed objects**, in place.
- **Retry limit** (Themis-wide, Settings → Print defaults, default 2): failures of a part on *different* printers →
  **held** for Reprint / Drop.
- **Waiver** (Themis-wide "waive after", default 2): failures of a part on the *same* printer → that printer is waived for
  that part. A waiver belongs to the **part itself** (file content hash + plate/object), across all projects. It is cleared
  by the user on the printer page; completing maintenance does not clear it.
- Per printer: **flag at N waived parts** (Fleet warning: wash the plate / do maintenance) and **stop taking jobs at N
  waived parts** (0 = disabled, the default).
- **Test print** a waived part from the printer page: sent at once if the printer is idle with no queue job, otherwise
  the printer stops claiming queue work and the test is its next print. A good result offers Clear waiver.

### UI (Project Builder)
- Section tabs Details · Items · Groups · Review with counts and a "to fix" badge; Back/Next; Save draft.
- Group banner: All · Default Group · groups · "+ New group". The selected badge filters the list, rows carry their
  group colour, and rows drag onto badges. All is hidden on the Groups tab (disable it instead only if hiding adds
  real complexity).
- Collapsible per-file sections, row/section checkboxes with bulk actions.
- Review checks link to the tab that fixes them; queueing is blocked until none are open.

## Deferred
- BIZ-353: make a project from held parts; recombining partial Together bundles across jobs.
- Max *hours* per plate (needs a slice to measure); only a max *parts* cap is in scope.
