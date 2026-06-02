---
name: themis-planning
description: Use when planning or scoping a change to the Themis print-farm manager — turns a feature request into a concrete, file-level change plan by reading the docs/agent reference set instead of re-deriving the architecture from source.
---

# Themis Planning

Produce a precise, file-level implementation plan for a Themis change by grounding it in the
`docs/agent/` reference set. The goal: never re-analyze the whole codebase from scratch — load the
relevant reference docs, locate the exact files/symbols/flows, verify them against current code, then
write the plan.

**Announce at start:** "Using themis-planning to scope this change from the agent reference docs."

## Why this exists

`docs/agent/` is an LLM-facing map maintained specifically so feature work starts from facts, not from a
cold re-read of 16 services + 11 screens. Trust it to *navigate*, but the **code wins** — verify every
symbol/path you'll touch before committing it to the plan.

## Process

1. **Load the index.** Read `docs/agent/README.md` first. It has the system-in-one-screen, the job
   lifecycle, the doc-ownership table, and the extension-points list.

2. **Classify the request** against the extension points / recipes:
   - new printer vendor · new API route · new screen · new table/column · queue/print behavior ·
     new `/ws` event · per-printer print option · cross-cutting (several of these).

3. **Load only the docs the task touches** (per the ownership table):
   - `backend.md` — routes, services, queue/slice/print flow, WS hub
   - `frontend.md` — screens, `api/` clients, routing, conventions
   - `styling.md` — design tokens, no-framework CSS, `ui.tsx` components, status→pill map (any visual change)
   - `data-model.md` — tables, JSON column shapes, derived fields
   - `printers.md` — vendor contract, Bambu/Elegoo protocols, AMS, slicing
   - `recipes.md` — the step-by-step cookbook for the matched shape
   - `conventions.md` — always skim (invariants + dev-env traps)

4. **Verify before planning.** For each file/symbol the recipe names, confirm it still exists and
   matches (Grep/Read the actual code). If a doc contradicts the code, the **code wins** — note the
   drift so it can be synced later (mention running `themis-docs-sync`).

5. **Check invariants** in `conventions.md` that the change could violate (blocked-vs-failed,
   awaiting_plate_clear, no-migration-tool `_migrate` guard, filament-ask-vs-profile, per-printer flags
   from `self._*`, head-of-line queue, cancel↔stop). Call out any the plan must respect.

6. **Hand off to the planning skill.** This skill scopes; it does not write the final task-by-task
   plan. Invoke `writing-plans` (and then `subagent-driven-development` for execution) with the scoped
   context — the touched files, the recipe steps, the invariants, and any verified symbol drift —
   already gathered. Don't make the planner re-derive what you just confirmed.

## Output of this skill (before handing to writing-plans)

A scoping brief containing:
- **Change shape** (which extension point/recipe).
- **Files to create/modify**, each with the specific symbol/function and why (verified against code).
- **Invariants in play** and how the change respects them.
- **Doc drift found** (if any) → flag for `themis-docs-sync`.
- **Open questions** for the user (only genuine forks; pick sensible defaults otherwise).

## Red flags

- Planning from memory of the codebase instead of loading the docs → load them.
- Citing a file/symbol from a doc without verifying it exists → verify.
- Skipping `conventions.md` → you'll miss an invariant or the Store-Python/`tsc -b` traps.
- Writing the full task-by-task plan here → that's `writing-plans`' job; this skill only scopes.
