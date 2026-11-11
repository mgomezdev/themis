# ProjectBuilderScreen refactor

**Goal:** break `frontend/src/screens/ProjectBuilderScreen.tsx` (990 lines, ~25 `useState`, save/sync,
undo, dirty tracking and ~450 lines of JSX in one component) into tested units, with **no user-visible
behavior change** except the two bug fixes listed in Step 1.

**Ground rules (every step)**
- One PR per step. Existing screen tests (`ProjectBuilderScreen.test.tsx`,
  `ProjectBuilderScreen.generate.test.tsx`) must stay green **without edits** — they are the regression net.
- Characterization tests for any screen behavior the step touches are added *before* the move, and must pass
  on the pre-refactor code.
- Every extracted unit gets its own test file. Each test must be falsifiable (mutate the line → it fails).
- Coverage of the moved code must not drop; global floors in `vitest.config.ts` never lowered.
- Checks: `npx vitest run`, `npm run test:cov`, `npm run build` (`tsc -b`).

## Step 1 — pure logic out of the screen (this PR)

**Create**
- `src/lib/stlTree.ts`
  - `normalizeFolder(folder)` — strip leading/trailing/duplicate `/` (`'/a//b/'` → `'a/b'`).
  - `isPickableStl(f)` — `.stl` (case-insensitive) and not `missing` (unchanged rule).
  - `buildStlTree(files)` — moved. **Fix:** a node's `path` used `parts.indexOf(part)`, which returns the
    *first* occurrence, so a repeated segment (`a/x/a`) got path `a` instead of `a/x/a` → selecting it
    showed the wrong files. Now uses the loop index.
  - `stlFilesInView(stlFiles, selectedFolder, search)` — moved filter. **Fix:** the tree normalized folder
    paths (split + drop empties) but the filter only stripped a leading `/`, so a file in `a/b/` (trailing
    slash) never showed when its tree node `a/b` was selected. Both now use `normalizeFolder`.
- `src/components/StlFolderTree.tsx` — `FolderRow` moved verbatim, exported as `StlFolderTree`.
- `src/lib/projectDraft.ts`
  - types `LocalItem`, `LocalLink`, `LocalPart`, `RemovedSnapshot`, `ProjectDraft`; helpers `askOf`,
    `unitCostValue`, `newLocalId`.
  - `draftSnapshot(draft)` — replaces positional `computeSnap(n, c, ot, oh, dd, no, its, lks, prts, pr?, cid?)`
    (11 args, two silently defaulted from component state). Same JSON shape.
  - `projectToDraft(project)` — the load-effect mapping (linked account ⇒ customer type; linked account name
    wins; nulls → `''`; missing links/parts → `[]`).
  - `GENERATE_ERRORS`, `parseGenerateError` — moved.

**Tests**
- `src/lib/stlTree.test.ts`, `src/lib/projectDraft.test.ts`, `src/components/StlFolderTree.test.tsx`.
- Screen characterization (new `ProjectBuilderScreen.draft.test.tsx`, written first, green on old code):
  edit-mode dirty tracking (no Save on load → appears on edit → hidden after reverting → hidden after save),
  folder navigation, undo of a removed part item / link / non-printed part.

## Step 2 — `syncProject` into `api/projects.ts`
Move `saveProject()`'s create-or-diff logic to `syncProject(projectId | null, draft, deleted)`; test request
order/bodies directly with `stubFetch` (create path, edit path, deleted links/parts, unit-cost omission vs
`null` clear). Screen keeps only `setSaving`/navigate.

## Step 3 — hooks
`useUndoRemoval()` (timer + snapshot; spy on `setTimeout`, don't fake timers around `waitFor`) and
`useProjectDraft(projectId)` (`useReducer`: load/add/update/remove/undo/markClean, exposes `isDirty`).
Reducer unit-tested as a pure function; hook tested with `renderHook`.

## Step 4 — JSX split
`ProjectMetaFields`, `ProjectLinksEditor`, `ProjectPartsEditor`, `ProjectItemsTable`, `GeneratePanel`,
`SavedToast`/`UndoToast`. Each gets a component test; screen target ≈150–200 lines of wiring.

## Review
Each step: one fresh reviewer pass against `docs/agent/frontend-review.md`. Independent second-opinion
handoff: `docs/superpowers/plans/2026-11-11-project-builder-refactor-review-handoff.md`.
