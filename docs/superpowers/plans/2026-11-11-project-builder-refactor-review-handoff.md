# Handoff: independent check of the ProjectBuilderScreen refactor

**For:** a fresh agent (no prior context) asked to double-check this refactor.
**Repo:** `mgomezdev/themis`. Read `CLAUDE.md` first (communication style, testing rules, PR gate).

## Prompt to paste into the new session

> You are reviewing a behavior-preserving refactor of `frontend/src/screens/ProjectBuilderScreen.tsx` in the
> Themis repo. The plan is `docs/superpowers/plans/2026-11-11-project-builder-refactor.md`; this file
> (`docs/superpowers/plans/2026-11-11-project-builder-refactor-review-handoff.md`) is your checklist.
> Find the refactor commits with
> `git log --oneline -- frontend/src/screens/ProjectBuilderScreen.tsx frontend/src/lib/projectDraft.ts frontend/src/lib/stlTree.ts`
> and diff each step against its parent. Don't trust the author's summary or the tests: check the code against
> the pre-refactor version (`git show <base>:frontend/src/screens/ProjectBuilderScreen.tsx`). Work through every
> section below, run the commands, and report findings ranked Critical / Important / Minor, each with file:line
> and a concrete failing scenario. Don't fix anything unless asked; if you're asked to fix, follow CLAUDE.md's
> workflow (a failing test first, then the fix). Be extremely concise.

## What changed (step 1)

| New | Holds | Moved from |
|---|---|---|
| `frontend/src/lib/stlTree.ts` | `normalizeFolder`, `isPickableStl`, `buildStlTree`, `stlFilesInView` | `buildStlTree` + the inline STL filter/search |
| `frontend/src/components/StlFolderTree.tsx` | `StlFolderTree` (was `FolderRow`), copied unchanged | screen |
| `frontend/src/lib/projectDraft.ts` | `LocalItem/LocalLink/LocalPart/RemovedSnapshot/ProjectDraft`, `askOf`, `unitCostValue`, `newLocalId`, `draftSnapshot` (was positional `computeSnap`), `projectToDraft` (was the load-effect body), `GENERATE_ERRORS`, `parseGenerateError` | screen |

Two **intended** behavior changes, both bug fixes:
1. `buildStlTree` used `parts.indexOf(part)` for a node's path, so a repeated folder name (`a/x/a`) got the
   ancestor's path → selecting it showed the wrong files. It now uses the loop index.
2. The tree normalized folders (`split('/').filter(Boolean)`), but the file filter only stripped a leading `/`, so
   files in `Tools/` (trailing slash) never showed under `Tools`. Both now go through `normalizeFolder`.

Anything else that behaves differently is a defect.

## Checklist

### A. Behavior equivalence (the main job)
- [ ] `draftSnapshot` emits the **same JSON shape, key order and values** as the old `computeSnap` for the same
      state. Old call sites passed `price`/`customerId` implicitly through default params bound to component
      state: confirm the new call passes the *current* values everywhere (`currentSnap`, the load effect,
      `handleSave`).
- [ ] `handleSave` now does `setCleanSnap(currentSnap)` (the render-time value its closure captured) instead
      of recomputing after `await saveProject()`. Confirm the old code also used the closure's (pre-await)
      state, so the two are equivalent, and that nothing mutates draft state while the save is in flight.
- [ ] `projectToDraft` vs the old load effect, field by field: `customer` precedence (linked `customer_name`
      wins only when `customer_id != null`), `orderType` forced to `customer` when linked, `?? ''` / `?? false`
      defaults, `price: 0` → `'0'`, `unit_cost: null` → `''`, `label: null` → `''`, `links`/`parts` missing → `[]`,
      and items carrying exactly the four ask fields (via `askOf`) plus ids, quantity, sort_order.
- [ ] `isDraft` (`p.stage === 'draft'`) and `serverItems` still come from the raw project, not the draft.
- [ ] `stlFilesInView`: search trims and lowercases; a blank search falls back to the folder filter; root `''`
      shows everything; a folder shows only its *direct* files.
- [ ] `StlFolderTree` matches the old `FolderRow` exactly: diff the JSX (`git show <base>:…` against the new file).
- [ ] No leftover dead code or duplicate helper in the screen (`grep -n "computeSnap\|FolderRow\|_lid" …`).

### B. Tests — are they real?
- [ ] `ProjectBuilderScreen.test.tsx` and `ProjectBuilderScreen.generate.test.tsx` are **unchanged** from base
      (`git diff <base> -- <those files>` is empty).
- [ ] `ProjectBuilderScreen.draft.test.tsx` was written as characterization: check out base, copy that file in,
      run it. Expect **exactly two** failures (the two bug-fix tests: "repeats an ancestor's name" and
      "trailing slash"). Any other failure means it isn't characterizing the old behavior.
- [ ] Mutation spot-check (CLAUDE.md: a test you can't make fail isn't a test). For each, mutate, run
      `npx vitest run src/lib src/components/StlFolderTree src/screens/ProjectBuilderScreen`, and confirm a
      failure, then revert:
      - `stlTree.ts`: `parts.slice(0, i + 1)` → `indexOf` version; drop `normalizeFolder` in the filter; drop `.trim()`.
      - `projectDraft.ts`: drop `price` or a part's `sid` from the snapshot; drop the `isCustomer` guard;
        `customerId != null &&` removed; `p.price != null` → truthy check.
      - screen: delete `setCleanSnap(currentSnap)` in `handleSave`.
      The author ran 17 such mutations and 16 failed. The survivor, the `if (childKeys.length)` guard on
      `setOpen` in `StlFolderTree`, makes no visible difference (toggling a leaf changes nothing), so it's an
      equivalent mutant. Try your own mutations as well.
- [ ] Look for weak assertions: tests that only check "didn't throw" or a status code, `toBeTruthy` on
      something that's always truthy, or helpers whose selectors could match nothing and pass vacuously
      (e.g. `pickerFiles()` returning `[]` when the expected value is also `[]`).
- [ ] Are there gaps? In particular: dirty tracking for customer-type projects (switching type with a customer
      selected), and `onHold` / due date at screen level.

### C. Gates
Run from `frontend/`:
```bash
npm install
```
```bash
npm run test:cov
```
```bash
npm run build
```
- [ ] All green. Coverage at or above the floors in `vitest.config.ts`. The floors were raised in this change,
      not lowered; check the diff.
- [ ] Check `docs/agent/frontend.md` against the code: the `StlFolderTree` row and the `ProjectBuilderScreen`
      note.

### D. Later steps (when they land)
Same method for each: per-step diff, existing screen tests unchanged, new unit tests falsifiable.
- **Step 2 `syncProject`:** request **order** and bodies identical to the old `saveProject` for the create path,
  the edit path, deleted links/parts, and unit cost (omitted on create when blank, `null` on update when cleared).
- **Step 3 hooks / reducer:** undo restores the row at its original index **and** removes its id from the
  deleted list. A second removal replaces the first snapshot and restarts the 6 s timer. The timer is cleared
  on unmount (the old code doesn't do this; flag it if it's still missing).
- **Step 4 JSX split:** no prop drilling of setters that bypass the reducer; labels, `title`s and
  placeholders unchanged, because the tests select by them.

## Report format
```
Critical: …
Important: …
Minor: …
Verified OK: A1, A2, … (one line)
```
