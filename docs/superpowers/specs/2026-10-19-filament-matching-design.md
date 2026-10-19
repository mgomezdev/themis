# Filament matching: job asks and printer slots

Date: 2026-10-19. Status: agreed with the product owner; implementation tracked in BIZ-312. Decision log and the questions behind it:
comments on BIZ-307.

Supersedes the matching rules in `2026-06-15-filament-constraint-and-3mf-overrides-design.md` (constraint modes),
`2026-06-17-per-color-filament-assignment-design.md` (per-color catalog assignment) and `2026-06-17-spool-picker-slot-config-design.md`
(what a slot stores). Those documents stay as history; where they disagree with this one, this one wins. Gradients and multicolor
filaments are out of scope (BIZ-311); this spec treats color as one hex value.

## Goal

One provider-neutral rule for "can this printer print this job's filament", used for single-filament jobs and for every model color of a
multi-material job. It must work with Spoolman, Local inventory, or no inventory at all.

## The tuple

A filament is described by four parts: **manufacturer**, **type**, **subtype**, **color**.

| Part | Meaning | Comparison |
|---|---|---|
| manufacturer | the maker ("Bambu Lab", "Acme"). The inventory contract already calls it `vendor` | case-insensitive |
| type | base material: PLA, PETG, ABS, TPU... | case-insensitive |
| subtype | variant of the type: `standard` (plain), `plus` ("+"), `silk`, `cf`, `gf`, `hf`, others a provider reports | case-insensitive |
| color | one hex value | `#` and case ignored, otherwise exact (no tolerance) |

* **Type and subtype are two separate fields everywhere** (inventory contract, job asks, slots). A provider plugin translates its own
  provider's strings into the two fields and back; core never parses a combined string. How Spoolman splits "PLA+" is Spoolman's
  decision, inside the Spoolman plugin.
* **Plain filament has the explicit subtype `standard`.** There is no empty subtype on a spool or slot.
* A suffix a provider reports that Themis does not know is kept as the subtype text (compared like any name).
* **"Undefined" is stored as absent (NULL), never as the string `"any"`.** `"any"` is a real subtype value (the subtype wildcard) and
  must not be confused with an unset part. This differs from today's columns, whose server default is the string `"any"`.

## The job's ask

A job's ask is a tuple. Each part is either **defined** or **undefined**.

* An **undefined** part matches anything.
* **Subtype is required whenever type is defined.** The user may set it to `any` to say "I don't care", which matches every subtype.
  `any` is the only wildcard value; there is no wildcard for the other parts (leave them undefined).
* A job with every part undefined is "defer": it matches any loaded slot.

### What the job stores

The **tuple only**. Picking a material from an inventory catalog just fills the four parts; the job does not keep the material ref or the
provider id. Consequences: matching is identical for every provider, an ask survives a provider switch or an edited/deleted material,
and the tuple can be typed by hand when no inventory exists.

### Multi-material jobs

Each model color has its own ask (same tuple, same rules), or is pinned to a tool slot by index. A pinned slot is not matched; it must
simply be loaded. Per-color assignment is provider-neutral: choosing a material fills the tuple, and the tuple can also be entered by
hand, so it never depends on one provider's integer id.

## The slot

A slot stores **its own tuple**: manufacturer, type, subtype, color. It is filled from any of these sources:

* the material of the spool the operator picked for the slot (copied when picked),
* printer telemetry (RFID/AMS/tool data), translated by the printer plugin into the tuple,
* hand entry in the slot editor.

**Hardware wins.** Where a printer reports its slots (AMS, Snapmaker tool data), each (re)connect replaces the slot's type and color with
what the hardware reports and keeps the Themis-owned keys (the bound spool, the filament profile). So a copy of a picked spool's
material is only the starting value; telemetry overrides it.

**Conflict.** A slot is in conflict when it has a bound spool whose material tuple differs from the slot's tuple in any part that is
known on both sides (an unknown part, such as a manufacturer the hardware does not report, is not a difference). While a slot is in
conflict:

* it is **skipped for matching**: it satisfies no ask, and a job pinned to its tool index is ineligible on that printer. The printer's
  other slots still match;
* the UI flags it ("bound spool differs from what the printer reports") so the operator can rebind, correct, or unbind the spool;
* the job detail reasons say so (for example "T1: bound spool conflicts with what the printer reports").

Deduction still follows the bound spool (see Deduction). Hand-entered slots never conflict, because the operator sets the tuple and the
binding together.

Matching reads **only the slot's tuple**, never the bound spool's current material.

* The slot may also hold a **spool ref** (provider-namespaced). It is used only for weight deduction (see below). After a provider
  switch the tuple still matches and the ref is shown as stale.
* The **filament profile for slicing** is chosen by the operator per slot. Local inventory has no profile links.
* A tool-changer placeholder for an empty tool (`empty: true`, no type) is not loaded: it satisfies no defined ask and is not "any slot".
  A hand-entered slot with a blank type that is not `empty: true` is still a slot: it satisfies an all-undefined ask, and fails any ask
  that defines a type.
* A slot is **unbound** when it has no spool ref.

## Matching

`slot satisfies ask` iff every defined part of the ask is satisfied by the slot:

* manufacturer: the slot's manufacturer is known and equal. **An unknown manufacturer fails a manufacturer ask.**
* type: equal.
* subtype: ask is `any`, or equal.
* color: equal.

An unbound slot is matched the same way, on the fields it has: the missing spool ref plays no part. An undefined part of the ask is
always satisfied, including against an unknown slot value. Whatever feeds a slot (spool pick, telemetry, hand
entry) always supplies a subtype; plain filament is `standard`.

### Choosing a slot

The lowest slot index that satisfies the ask. For a multi-material job each model color is resolved independently, so **two model colors
may resolve to the same slot** (no error; the print simply has no change between them).

### Printer eligibility

A printer is eligible for a job's filament iff every ask (the single ask, or every model color's ask) is satisfied by some slot that is
not in conflict, and every pinned slot is loaded and not in conflict. A printer with **no slots on record** is ineligible for any ask that is not fully undefined, and the reason it shows is "no filament
configured".

This is the same check for single-filament and multi-material jobs.

### When no printer matches

The job **stays queued**. It is never blocked or failed because of filament alone. The job detail shows, per candidate printer, why it
does not match (for example "T1: PLA standard red; wanted PLA silk red", "no filament configured", "T2 is empty"). Loading the right
filament makes the job claimable on the next queue cycle.

A filament removed **after** the eligibility check passed (before or during slicing) is still a slice failure: the config is
`slice_failed`, the job `blocked`, recoverable with unblock, as in `CLAUDE.md` (Slicing failure recovery).

## Deduction

Weight is deducted from the **spool bound to the slot that printed** (per used slot for multi-material jobs). If that slot has no bound
spool, nothing is deducted, the job shows that no spool was tracked, and the operator is warned so they can bind a spool and correct the
weight. The job's own ask never selects a spool.

## Provider switch

Jobs hold tuples and slots hold tuples, so nothing needs rewriting. Slot spool refs of the inactive provider are left alone and shown as
stale (the existing rule that rows of an inactive provider are left alone).

## Migration of existing data

Today's ask columns hold a single type string and a color, where blank or the string `"any"` means "no constraint"
(`_is_any_filament_ask`). They exist on `job_printer_configs`, `job_model_targets` and `project_items`, in `filament_map` entries
(`filament_type`/`filament_color`), and in the order parts API (`filament_type`/`filament_color`).

* **Job asks, targets, project items, order parts, `filament_map` entries:**
  * legacy type blank or `any`: type, subtype, and manufacturer are all undefined;
  * legacy type set: type = the base type parsed from the string, subtype = `any`, manufacturer undefined;
  * legacy color blank or `any`: color undefined; otherwise unchanged.
* This is **at least as broad** as before, not identical: a legacy `PLA` ask used to match only a slot whose type string was exactly
  `PLA`, and now also matches PLA+, silk, CF and so on; a legacy `PLA+` ask becomes PLA / `any`, broader still. The old rule with a type
  set and color `any` compared against an empty slot color; that quirk is dropped (color is simply undefined).
* **Slots** (`Printer.loaded_filaments`): the existing type string is split with the shared suffix rules ("PLA+" to PLA/plus,
  "PLA-CF" to PLA/cf, "PLA Silk" to PLA/silk, plain to PLA/standard); manufacturer unknown.
* Provider material strings are split by the provider plugin, not by this migration.
* **Legacy columns** `material_provider`, `material_ref` and `filament_id` on those tables are no longer read for matching. Dropping
  them is a separate cleanup; until then they stay as history.

## Consequences for the implementer (derived from the rules above, not separately decided)

* `queue_engine` currently blocks a job on a filament mismatch (`_filament_mismatch` leads to `_block_job`). That path goes away for
  filament-only mismatches: the job stays queued and the reasons are shown. Jobs already `blocked` for a filament reason need to return
  to `queued` (a migration or the first queue cycle after deploy); the exact mechanism is the implementer's, and it must not touch jobs
  blocked for other reasons.
* `_resolve_filament_map` currently raises when two model filaments resolve to one slot. That check is removed. How a `filament_map`
  with two entries on the same tool index is passed to the slicer (Laminus/Orca `multi_presets`) must be verified against the slicing
  provider; if it cannot express that, say so in the PR and raise it as an open question, do not reintroduce the error silently.
* Field and column names are **not fixed by this spec.** The inventory contract needs a subtype field and a decision on whether
  `material` is renamed to mean only the base type (`MaterialDraft`, `MATERIAL_FIELDS`, the Local plugin's `local_inv_materials`
  table via a plugin migration); job-ask columns and the slot dict keys (today `type`, `color`, `name`, `filament_id`) need the same.
  The implementer chooses the names and records them in the PR. Constraint: undefined is NULL/absent (see The tuple).

## Examples

Slots: T0 = Acme / PLA / standard / #FF0000, T1 = Acme / PLA / silk / #FF0000, T2 = (no manufacturer) / PETG / standard / #00FF00.

| Ask (manufacturer, type, subtype, color) | Matches | Why |
|---|---|---|
| (none, none, none, none) | T0 | defer: lowest loaded slot |
| (none, PLA, any, #ff0000) | T0 | subtype `any`, lowest index |
| (none, PLA, silk, #FF0000) | T1 | subtype must be equal |
| (none, PLA, standard, #FF0000) | T0 | all parts equal; lowest index |
| (acme, pla, standard, FF0000) | T0 | names case-insensitive, `#` ignored |
| (Bambu Lab, PLA, any, #FF0000) | none | manufacturer differs |
| (Acme, PETG, standard, #00FF00) | none | T2's manufacturer is unknown |
| (none, PETG, standard, #00FF00) | T2 | manufacturer undefined |
| (none, PLA, any, #FF0001) | none | color is exact |

Two-color job asking (none, PLA, standard, #FF0000) twice: both colors resolve to T0.

Other cases: a slot whose bound spool is PLA / silk / #FF0000 while the printer reports PLA / standard / #FF0000 is in conflict and
matches nothing, whatever the ask; the printer's other slots still match. A printer with no slots and any ask that defines a part: ineligible ("no filament configured"). An empty tool placeholder
(`empty: true`) never matches. A pinned slot T3 that is an empty placeholder: the printer is ineligible. The same slot T0 with no bound
spool matches exactly as above; only deduction differs.

## Out of scope

* Gradients, transitions, multicolor (BIZ-311), and color tolerance.
* Local inventory profile links.

Considered and not adopted: choosing a slot by remaining weight, an operator-pinned choice among several matches, operator confirmation
for printers without slot data.

## Implementation notes (BIZ-312; names the spec left open, and decisions)

* **Inventory contract:** `InvMaterial.material` keeps its name but now means the base type only; a new `subtype` field sits beside it
  (`MaterialDraft` and `MATERIAL_FIELDS` too). Local stores two columns (plugin migration v002 splits existing strings); Spoolman splits
  its single `material` text with the shared suffix rules in its own plugin.
* **Job asks:** new nullable columns `ask_manufacturer/ask_type/ask_subtype/ask_color` on `job_printer_configs`, `job_model_targets`,
  `project_items` (migration v053; the old `filament_type/color`, `filament_id`, `material_provider/ref` columns stay as history, unread).
  API/JSON names are `filament_manufacturer/filament_type/filament_subtype/filament_color` (also on `filament_map` entries and order parts).
* **Slot dict:** `manufacturer`, `type` (base), `subtype`, `color`; `reported: true` marks what hardware telemetry replaced (only those slots
  can be in conflict). Spool binding stays `inventory`.
* **Claim-time plan:** `job_printer_configs.slot_plan` stores the chosen slots; slicing and deduction follow it. A planned slot emptied
  before slicing is a slice failure (blocked, recoverable).
* **Head of line:** a job the printer cannot supply is skipped for that printer (it stays queued); every other block still holds the line.
* **Many colours on one slot:** Themis' own routing (`snapmaker/remap.remap_3mf`, dict-based) already handles several model filaments on
  one tool, so only the "two colours, one slot" error was removed; no slicing-provider (Laminus) change is needed.
* **Migration of blocked jobs:** v053 returns jobs `blocked` with one of the old filament block reasons to `queued`; other blocks are untouched.
* **Bambu manufacturer:** the Bambu plugin decodes its own RFID data (`plugins/bambu/rfid.py`): a tag (non-zero `tray_uuid`/`tag_uid`)
  plus the material code (`tray_info_idx`) looked up in a plugin-owned table of RFID spool makers; no tag or an unknown maker = unknown
  manufacturer, never a guess (BIZ-320). The table is an assumption to confirm on hardware. Snapmaker U1 reports its tool `filament_vendor` ("Generic"/"NONE" = unknown); Elegoo reports no filament data.
* **Blends:** `split_type` keeps a blend of two base materials whole ("PC-ABS" = type `PC-ABS`, subtype `standard`), not PC with an "abs"
  subtype; the frontend `splitType` mirrors it (BIZ-324).
* **`reported`:** hardware-owned — API slot writes drop a client-supplied value and keep a stored `true`; only the telemetry merge sets it (BIZ-322).
* **Rollback:** v053 `down()` writes each ask back into the legacy `filament_type`/`filament_color` (subtype folds into the type string:
  any/standard dropped, plus -> "PLA+", other -> "PLA-silk"; manufacturer is lost) and the map / order-part keys before dropping the columns (BIZ-326).
* **Deduction:** per used slot from the plan; several slots take their share from the per-extruder breakdown (extruder index = tool index);
  a slot with no bound spool deducts nothing, flags the job (`deduction_note`) and emits `inventory.no_spool_tracked`.

## Open

* Non-hex colour text (until BIZ-311): decided — it matches only an identical text, compared case-insensitively (the colour key strips a
  leading `#` and lower-cases whatever is there).
