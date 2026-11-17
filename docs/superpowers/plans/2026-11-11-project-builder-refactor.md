# ProjectBuilderScreen refactor

**Status:** all four steps done, **on develop's Builder** (tabs, print groups, 3MF/gcode items, BIZ-356/360/363): screen 1169 → ~370 lines.
The first pass was written against `main`'s older screen; `develop` was merged in and every step redone on top of it
(commits after the merge). Where develop had already fixed a bug found in the first pass (repeat-save duplicates,
retry re-deletes, a stale "Save" after Queue), only the extraction remains; the step notes below say which fixes are
new on develop.

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

## Step 1 — pure logic out of the screen

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

## Step 1 on develop
`lib/stlTree.ts` (`isPickableModel`: stl/3mf/gcode), `lib/projectDraft.ts` (LocalItem with kind/source_ref/pack_rule/
group_id, multi-item undo snapshot, `draftSnapshot(draft, deletedItemIds)` = develop's `computeSnap` incl. `del`),
`components/StlFolderTree.tsx` (develop's FolderRow verbatim). **Fixes (still present on develop):** the repeated
folder name path and the trailing-slash folder filter.

## Step 2 — `syncProject` (`src/api/projectSync.ts`)
`syncProject(projectId | null, draft, deleted, onEvent)` replaces the screen's `saveProject`: same order and bodies
(pack rule; group only when set; kind/source on create). Develop already stamped new ids and dropped deletes inline;
that is now `onEvent`. A reply without a numeric id stays unstamped, as before.

## Step 3 — reducer + hooks
`lib/projectBuilderState.ts` (pure `builderReducer`; item ops incl. `removeItems`, `moveItems`, `setItemsRule` (only
where the kind allows it), `setItemsQuantity`, `setItemsAsk`, `groupsListed` (dead group → Default Group), `promoted`),
`components/builder/useProjectDraft.ts`, `components/useAutoDismiss.ts`. Groups, Review, tabs and bulk-ask state stay
in the screen. **Fixes (new on develop):** undoing a removal whose delete was already saved PUT to the deleted id
(items, links, parts) — it now re-creates the row; a removal made *during* a save was dropped when it finished (the
lists were cleared wholesale) — only successful deletes are dropped now; a slow load for a project the Builder moved
off is ignored; toast timers are cleared on unmount.

## Step 4 — JSX split
`components/builder/`: `ProjectMetaFields`, `ProjectLinksEditor`, `ProjectPartsEditor`, `ModelPicker` (owns
folder/search state), `ItemRow`, `BulkFilamentCard`, `QueueResult` (`QueueSuccess`/`QueueError`), `Toasts`. Markup
moved verbatim: the step-3 and step-4 screens were rendered on the same project (Items with a selection, Details after a
removal, Review) and their `innerHTML` compared equal once local-id-derived test ids are normalized.

## Review
Each step: one fresh reviewer pass against `docs/agent/frontend-review.md`. Independent second-opinion
handoff: `docs/superpowers/plans/2026-11-11-project-builder-refactor-review-handoff.md`.
