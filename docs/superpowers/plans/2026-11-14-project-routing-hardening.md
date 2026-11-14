# Project print-routing review and hardening (2026-11-14)

## What the code does today
`POST /projects/{id}/generate` groups the project's STL items by filament ask, packs each group ONCE (Laminus `/pack`,
OrcaSlicer arrange) for the smallest bed among the eligible printers, stores the multi-plate 3MF, and queues one job per
plate. Each job can go to any eligible printer; the queue engine hands the first matching queued job to whichever printer
is idle with a clear plate. `PUT /jobs/{id}/outcome` records per-item pass/fail for a printed plate.

## Findings
| # | Finding | Status |
|---|---|---|
| 1 | Per-plate part counts were a guess (item quantity split evenly over the plates), not what the packer placed: outcome review and progress were wrong whenever plates are uneven | fixed |
| 2 | A part the packer could not place (too large for the bed) was not detected: the project silently came out short | fixed (422, when the pack records its layout) |
| 3 | More than 50 parts in one filament group could not be generated (one upload per copy, Laminus caps uploads at 50) | fixed (`counts`, 500 parts; old Laminus: clear error) |
| 4 | No way to reprint only what failed: generate always queued the full quantity again | fixed (`quantities: "remaining"`, UI checkbox) |
| 5 | An outcome could be recorded for a job that never printed (queued, printing, cancelled), counting its parts as made | fixed (409) |
| 6 | Unknown eligible printer ids were ignored: packed for a default 256 mm bed, jobs nothing could take | fixed (422) |
| 7 | "Any printer of model X" with no such printer yet packed for the default bed, not the model's | fixed (registry bed) |
| 8 | A slice failure arriving after a cancel turned the cancelled job back into `blocked` | fixed |
| 9 | Packing is static: one layout for the smallest bed, decided at generate time. Larger printers print under-filled plates; nothing is re-packed for the printer that is actually free | open (design, below) |
| 10 | Nothing holds a printer with maintenance due, or one that keeps failing, out of the queue; nothing holds the remaining plates of a part that keeps failing | open (design, below) |
| 11 | Pack height is a fixed 250 mm (printers have no stored Z): a tall part is only caught at slice time | open |
| 12 | Generate with no eligible printers/models queues jobs nothing can take (they block as "no match") | open, by design ("generate without dispatch") |
| 13 | Legacy `projects.machine_uuid` pack mode sends an empty filament list Laminus rejects; unreachable through the API | open (dead path) |

## Changes
- Laminus: `/pack` takes `counts` (copies per file; total capped by `PACK_MAX_INSTANCES`, default 500) and names each object
  after its upload; features `pack.counts`, `pack.names`.
- Themis `SlicingProvider.pack_models(paths, counts=, labels=)`; the Laminus client sends counts when the sidecar
  advertises them and otherwise one upload per copy (max 50, explicit error beyond).
- Generate reads the pack's plate layout (`plate_object_names`) and stores real per-plate quantities; `estimated` fallback
  for a pack that does not record it; 422 on unplaced parts before that filament group is stored or queued (groups commit
  one by one: an earlier group's jobs stay queued; retry with `quantities: "remaining"`). Generates are serialised per project.
- `quantities: "remaining"`; outcome 409 guard; printer-id validation; model registry beds; conditional slice-failure block.

## Proposed next steps (not built: product decisions)
1. **Late packing** (finding 9). Keep a per-project pool of parts still to make; when a printer frees up, pack the pool for
   THAT printer's bed, queue plate 1 for it and return the rest to the pool. Failed plates and failed parts go back to the
   pool by construction. Cost: one pack call per claim (cacheable by pool content + bed), and plate previews only exist
   once a printer claims. A cheaper intermediate step: pack once per distinct bed size and let each printer take plates of
   its own size.
2. **Printer hold** (finding 10). `printers.hold_reason`: set after N consecutive failed prints (setting, default 2) or,
   optionally, when a maintenance item is overdue; the claim skips a held printer, `printer.held` event + notification,
   released from the Fleet screen. Today the only brake is the plate-clear gate (one human click per failed plate).
3. **Part hold** (finding 10). When an item accumulates N failed plates/parts, block the project's remaining jobs holding it
   ("part X failed N times — review") until the user releases or edits the item.
