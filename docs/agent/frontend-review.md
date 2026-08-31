# Frontend Review Guide

Checklist for reviewing a frontend change (or the frontend half of a cross-cutting one) before it's
considered done. Read `frontend.md` and `styling.md` first if you haven't — this doc assumes that
context and doesn't repeat it. These are systemic gotchas specific to this codebase's shape, not
generic advice.

## 1. Contract fidelity with the backend

This codebase has no schema-sharing or codegen between backend and frontend — every shared contract is
kept in sync by hand and can drift silently, with both sides' own tests still passing (each side tests
against what it assumes the other does, not what the other actually does). Known hand-synced spots:
`api/apiKeys.ts`'s `SCOPES` (mirrors `app/auth.py`), `Sidebar.tsx`'s `settingsSubItems` (mirrors
`SettingsScreen.tsx`'s own nav).

When a screen or hook reads a field from an API response, grep the backend route that actually returns
it and confirm the key matches byte-for-byte — don't trust a plan or a negotiated contract, verify it
fresh. If you touch a Settings sub-page or anything scope-related, check the two hand-synced spots
above for drift too.

## 2. Filament identity

Multiple distinct "filament ID" spaces exist across the stack — a Spoolman filament (catalog spec) ID,
a Spoolman spool (physical instance) ID, an OrcaSlicer filament preset name, a Bambu AMS tray code, and
the eligibility "ask" (type/color) are all different things that happen to live near each other in the
data model. When a component reads or writes anything filament-related, confirm which one it actually
has rather than assuming from the field name alone — see `backend-review.md` §1 for the full inventory.

## 3. State fidelity when loading saved config

When a `useEffect` loads a saved config from the API into local form state, load it as-is. Don't
silently substitute a "nicer" default for an empty array, `null`, or `false` the backend returns on
purpose — that misrepresents what's actually saved, and an unmodified Save afterward would silently
overwrite the real value with the substituted default. If the backend has a genuine "not configured"
state, it should be distinguishable in the response from a real, deliberately-empty saved value; don't
collapse both to the same UI default.

## 4. Type safety — real build, not `tsc --noEmit`

`npx tsc --noEmit` checks nothing here (root `tsconfig.json` is references-only). Use `npm run build`
(`tsc -b && vite build`) or `npx tsc -b`. TS strict + `noUnusedLocals`/`noUnusedParameters` — an unused
import fails the build, not just a lint warning.

## 5. Reuse before adding a new pattern

`SettingsScreen.tsx` has accumulated several pages that already solve common shapes: a
save-button-with-"Saved"-pill flow, an event-checkbox list, a per-item test button. Before hand-rolling
a new version of any of these, check whether the existing one can be reused or lightly generalized
(e.g. take a list as a prop instead of hardcoding it) rather than copy-pasting the shape again — same
for `api/*.ts` fetch-wrapper patterns.

## 6. Test doubles must match the real backend shape

A mock encoding what you *think* the API returns, written from the same mental model as the code under
test, will pass even when both are wrong in the same way. When mocking a backend response, prefer
copying the actual shape from the backend route/model or an existing correct fixture over hand-typing
one from memory.

## 7. Styling/UX conventions

No CSS framework — compose from `app.css` tokens and the shared `components/ui.tsx` primitives
(`Card`, `StatusPill`, `Progress`, ...). See `styling.md` for the full vocabulary and the `StatusKey`→
pill-tone mapping. A new styled status needs an entry there, not an ad-hoc inline color.

## 8. Tests

TDD: a test that failed for the right reason before the fix, for every behavior change. `npx vitest
run` full suite green and `npm run build` clean before calling anything done. If a screen consumes a
new/changed API field, check whether `e2e/mock-api.ts`'s canned data needs the same update — it's a
separate fixture source from the unit test mocks and can drift independently.
