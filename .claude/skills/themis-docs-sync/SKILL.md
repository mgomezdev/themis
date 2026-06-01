---
name: themis-docs-sync
description: Use after making changes to the Themis codebase (or before a commit/PR) to update the docs/agent reference set so it keeps matching the code — reconciles routes, services, screens, schema, printer protocols, and conventions against what actually changed.
---

# Themis Docs Sync

Keep `docs/agent/` matching the code. Run after implementing a change to Themis, or as a pre-commit
pass. The reference docs are only useful if they're true — this skill reconciles them against the diff.

**Announce at start:** "Using themis-docs-sync to update the agent reference docs against the changes."

## Principle

The **code is the source of truth**; the docs are a derived map. This skill propagates code → docs,
never the reverse. Only edit `docs/agent/*` (and, when a human-facing doc is clearly stale, flag it —
don't silently rewrite `README.md`/`CLAUDE.md` unless asked).

## Process

1. **Get the diff.** `git diff` (unstaged) + `git diff --staged`, or diff against the branch point
   (`git diff main...HEAD`). If invoked right after a specific change, scope to those files.

2. **Map changed files → owning doc** (mirror of the planning ownership table):

   | Changed path | Update |
   |---|---|
   | `app/api/routes/*.py` | `backend.md` routes table (+ `recipes.md` if the route shape is novel) |
   | `app/services/*.py` | `backend.md` services table / key flows; `printers.md` if vendor/slicing |
   | `app/models.py`, `app/database.py` (`_migrate`) | `data-model.md` (tables, columns, JSON shapes); note new `_migrate` guards |
   | `app/services/*_client.py`, `printer_client_factory.py` | `printers.md` (contract, vendor specifics, registry) |
   | `frontend/src/screens/*` | `frontend.md` screens table |
   | `frontend/src/api/*` | `frontend.md` api-clients table |
   | `frontend/src/App.tsx` | `frontend.md` routing section |
   | new invariant / dev-env trap / build rule | `conventions.md` |
   | any new "how to do X" pattern | `recipes.md` |
   | new subsystem / lifecycle change / extension point | `README.md` (system-in-one-screen, lifecycle, extension points) |

3. **Reconcile each affected doc.** For every changed symbol, endpoint, table, column, screen, or
   protocol detail:
   - Added → add a terse row/line (file:symbol pointer, one fact per line).
   - Renamed/moved → update the pointer.
   - Removed → delete the stale line.
   - Behavior changed → fix the description; update the lifecycle/flow if the spine changed.
   Keep the house style: dense, table-heavy, `file.py:symbol` pointers, no prose padding.

4. **Audit-sweep for drift the diff doesn't show.** Quickly confirm the touched doc sections still match
   reality (a change can invalidate a neighboring claim). Spot-check counts in `README.md`/tables
   ("16 services", "11 screens", "8 tables") if files were added/removed.

5. **Cross-link.** If the change adds a new extension point, ensure `README.md`'s extension-points list
   and `recipes.md` both reference it.

6. **Report.** List which docs changed and the key facts updated. If you found a doc claim the code
   contradicts but couldn't confidently fix, flag it explicitly rather than guessing.

## What NOT to do

- Don't add narrative/tutorial prose — these docs are a reference for an LLM, optimized for lookup.
- Don't document transient state (a specific bug, a one-off debug session) — only durable architecture,
  contracts, invariants, and "where to change X".
- Don't update the human-facing docs (`docs/architecture/index.html`, `README.md`, `CLAUDE.md`) as part
  of routine sync unless the change makes them outright wrong — flag those for the user instead.
- Don't let the index counts/lifecycle in `README.md` go stale when files are added or removed.

## Red flags

- A new route/service/screen/table/vendor with no corresponding doc edit → you missed step 2.
- A `_migrate` guard added but `data-model.md` not updated → schema doc is now lying.
- Editing code-of-truth to match a doc → backwards; the code wins, fix the doc.
