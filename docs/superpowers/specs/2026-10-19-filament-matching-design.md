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
| manufacturer | the maker ("Bambu Lab", "Acme"). The inventory contract calls it `vendor` | case-insensitive, trimmed |
| type | base material: PLA, PETG, ABS, TPU... | case-insensitive, trimmed |
| subtype | variant of the type: `standard` (plain), `plus` ("+"), `silk`, `cf`, `gf`, `hf`, others a provider reports | case-insensitive, trimmed |
| color | one hex value | `#` and case ignored, otherwise exact (no tolerance) |

* **Type and subtype are two separate fields everywhere** (inventory contract, job asks, slots). A provider plugin translates its own
  provider's strings into the two fields and back; core never parses a combined string. How Spoolman splits "PLA+" is Spoolman's
  decision, inside the Spoolman plugin.
* **Plain filament has the explicit subtype `standard`.** There is no empty subtype on a spool or slot.
* A suffix a provider reports that Themis does not know is kept as the subtype text (compared like any name).

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
simply be loaded. Per-color assignment is provider-neutral: the picker offers the active inventory's materials (or free entry), never an
integer id of one provider.

## The slot

A slot stores **its own tuple**: manufacturer, type, subtype, color. It is filled from, in order of what is available:

1. the material of the spool the operator picked for the slot (copied at pick time),
2. printer telemetry (RFID/AMS/tool data), translated by the printer plugin into the tuple,
3. hand entry in the slot editor.

Matching reads **only the slot's tuple**, never the bound spool's current material.

* The slot may also hold a **spool ref** (provider-namespaced). It is used only for weight deduction (see below). After a provider
  switch the tuple still matches and the ref is shown as stale.
* The **filament profile for slicing** is chosen by the operator per slot. Local inventory has no profile links; Spoolman's links may
  pre-fill the choice.
* A tool-changer placeholder for an empty tool (`empty: true`, no type) is not loaded.
* A slot is **unbound** when it has no spool ref.

## Matching

`slot satisfies ask` iff every defined part of the ask is satisfied by the slot:

* manufacturer: the slot's manufacturer is known and equal. **An unknown manufacturer fails a manufacturer ask.**
* type: equal.
* subtype: ask is `any`, or equal.
* color: equal.

An undefined part is always satisfied, including against an unknown slot value. Whatever feeds a slot (spool pick, telemetry, hand
entry) always supplies a subtype; plain filament is `standard`.

### Choosing a slot

The lowest slot index that satisfies the ask. For a multi-material job each model color is resolved independently, so **two model colors
may resolve to the same slot** (no error; the print simply has no change between them).

### Printer eligibility

A printer is eligible for a job's filament iff every ask (the single ask, or every model color's ask) is satisfied by some slot, and
every pinned slot is loaded. A printer with **no slots on record** is ineligible for any ask that is not fully undefined.

This is the same check for single-filament and multi-material jobs.

### When no printer matches

The job **stays queued**. It is never blocked or failed because of filament alone. The job detail shows, per candidate printer, why it
does not match (for example "T1: PLA standard red; wanted PLA silk red", "no filament configured", "T2 is empty"). Loading the right
filament makes the job claimable on the next queue cycle.

A filament removed **after** the eligibility check passed (before or during slicing) is still a slice failure: the config is
`slice_failed`, the job `blocked`, recoverable with unblock, as in `CLAUDE.md` (Slicing failure recovery).

## Deduction

Weight is deducted from the **spool bound to the slot that printed** (per used slot for multi-material jobs). If that slot has no bound
spool, nothing is deducted, the job shows "no spool tracked", and the operator gets a warning notification so they can bind a spool and
correct the weight. The job's own ask never selects a spool.

## Provider switch

Jobs hold tuples and slots hold tuples, so nothing needs rewriting. Slot spool refs of the inactive provider are left alone and shown as
stale (the existing rule that rows of an inactive provider are left alone).

## Migration of existing data

* **Job asks and project items** (`filament_type`, `filament_color`): type = base type parsed from the existing string; subtype =
  `any`; manufacturer undefined; color unchanged. They keep matching what they matched before.
* **Slots** (`Printer.loaded_filaments`): the existing type string is split with the shared suffix rules ("PLA+" to PLA/plus,
  "PLA-CF" to PLA/cf, "PLA Silk" to PLA/silk, plain to PLA/standard); manufacturer unknown.
* Provider material strings are split by the provider plugin, not by this migration.

## Examples

Slots: T0 = Acme / PLA / standard / #FF0000, T1 = Acme / PLA / silk / #FF0000, T2 = (no manufacturer) / PETG / standard / #00FF00.

| Ask (manufacturer, type, subtype, color) | Matches | Why |
|---|---|---|
| (none, none, none, none) | T0 | defer: lowest loaded slot |
| (none, PLA, any, #ff0000) | T0 | subtype `any`, lowest index |
| (none, PLA, silk, #FF0000) | T1 | subtype must be equal |
| (none, PLA, standard, #FF0000) | T0 | |
| (acme, pla, standard, FF0000) | T0 | names case-insensitive, `#` ignored |
| (Bambu Lab, PLA, any, #FF0000) | none | manufacturer differs |
| (Acme, PETG, standard, #00FF00) | none | T2's manufacturer is unknown |
| (none, PETG, standard, #00FF00) | T2 | manufacturer undefined |
| (none, PLA, any, #FF0001) | none | color is exact |

Two-color job asking (none, PLA, standard, #FF0000) twice: both colors resolve to T0.

## Out of scope

* Gradients, transitions, multicolor (BIZ-311), and color tolerance.
* Choosing a slot by remaining weight, or an operator-pinned choice among several matches.
* Operator confirmation for printers without slot data.
* Local inventory profile links.

## Open

* How a non-hex color text behaves until BIZ-311: propose a default in the implementing PR (suggest: it matches only an identical text,
  compared case-insensitively).
