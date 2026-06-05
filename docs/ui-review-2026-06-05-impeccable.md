# Themis UI Review (impeccable critique) — 2026-06-05

**Scope:** whole frontend (`frontend/src`) — shell (Sidebar/Topbar), shared components (`ui.tsx`), design tokens (`styles/app.css`), and all screens (Queue, NewJob, EditJob, JobDetail, Orders, NewOrder, Fleet, Printers, Files, Settings).
**Audience framing (authoritative):** operator/admin tool for expert 3D-print-farm operators who already know the task. Density, clarity, low cognitive load, consistency, and efficiency are the goals. Not a direct-to-consumer marketing app, so it is **not** judged on marketing polish, hero sections, or "delight."
**Method:** two independent assessments per the impeccable critique flow — (A) isolated design-director review reading the source, (B) deterministic pattern detector (`npx impeccable --json`). No live-browser overlay was run (no browser automation in this environment); evaluation is from source, where styles are inline + `app.css` classes.

---

## Design Health Score

| # | Heuristic | Score | Key issue |
|---|-----------|------:|-----------|
| 1 | Visibility of system status | 3 | Strong live telemetry/progress/status pills + inline saving/connecting states. Gap: lists/fleet have no loading vs empty distinction; several topbar/fleet buttons have no handler (dead controls = zero feedback). |
| 2 | Match system ↔ real world | 4 | Speaks operator fluently ("Ready for new work", "Claim next from queue", grams/layers/nozzle/bed/chamber). Jargon (Spoolman, OrcaSlicer profile) defined inline. |
| 3 | User control & freedom | 3 | Cancel/back present; deletes are two-step. Gaps: no Esc-to-close on modals (scrim-click only); no undo after cancel/unblock/remove; new-job state lost on navigation. |
| 4 | Consistency & standards | 2 | System exists but applied unevenly: 3 header patterns (17/20/22px), 3 toggle implementations, 2 destructive-confirm patterns (inline two-step vs `window.confirm`), heavy one-off inline `style={}` where `.card`/`.btn`/`.pill` exist → spacing/border drift screen-to-screen. |
| 5 | Error prevention | 3 | Genuinely good: override-loss modal, filament-slot warnings, disabled submit until plate complete, two-step deletes, type-constrained file accept. Gap: free-text folder/filament inputs, numeric interval not validated on bad paste. |
| 6 | Recognition vs recall | 3 | Mostly visible (eligibility chips, loaded-filament swatches, step checkmarks). Cost: icon-only buttons (wrench=edit, **copy glyph = "Edit settings"**, camera=snapshot, x=collapse) rely on `title` tooltips; copy-icon-means-edit is a recall trap. |
| 7 | Flexibility & efficiency | 2 | An expert tool with almost no accelerators: no global search/command palette (despite `.kbd`/`.search` CSS sitting unused), no keyboard shortcuts, no bulk actions. Density/accent tokens exist but no UI sets them. |
| 8 | Aesthetic & minimalist | 3 | Dense and purposeful. Minor over-decoration: progress bars carry glow + inset border + 3-stop gradient; status dots have colored glow; expanded printer card stacks accent-glow + border + cue border. |
| 9 | Error recovery | 3 | Slice-failure panel (raw per-printer error + "Edit slicer settings" jump) is excellent. Gap: many hooks `.catch(console.error)` and render an empty list, so "backend down" looks like "nothing here." |
| 10 | Help & documentation | 3 | Strong inline help (per-field hints, Spoolman "first time?" callout, step subtext). No searchable help, but inline is the right call for this audience. |
| **Total** | | **29 / 40** | **"Good" (upper band).** Foundations are solid; the two 2s (Consistency, Flexibility) are where the work is. |

---

## Anti-Patterns Verdict

**Does this look AI-generated? No — and that is a genuine finding, not flattery.**

**LLM assessment:** None of the absolute bans are present. No gradient text, no decorative glassmorphism (the only `backdrop-filter` uses are functional: topbar blur, modal scrims), no hero-metric block, no identical endless card grid, no modal-as-first-thought (modals reserved for real interrupts). The one near-miss, `.tbl tbody tr.selected { box-shadow: inset 3px 0 0 var(--accent) }` and the nav active 3px stub, are **state cues on transient/active elements**, not permanent colored left-borders on every card — the legitimate carve-out, not slop. Domain-real detail betrays a human author: the fan icon spins at a period inverse to duty cycle, telemetry shows value-over-target, the camera fallback fabricates a printer silhouette + scanlines in pure CSS, and the override modal diffs baked-3MF settings against selected presets. The dark-navy + amber theme is a **considered token system** (4-step bg ramp, 3-step borders, 4-step text ramp, real `[data-density]`/`[data-accent]` variable systems), not a "tools look cool dark" reflex. Honest caveat: the aesthetic is *familiar* (Linear/Vercel-adjacent), well-executed convention rather than novel — which is the correct, low-risk choice for an operator tool.

**Deterministic scan:** `npx impeccable --json frontend/src/screens` returned `[]` (clean, no findings across its 25 patterns). The detector and the LLM review **agree**: no AI-slop tells.

**Visual overlays:** not run (no browser automation available this session). Re-run `$impeccable critique` with a live dev server for in-browser overlays if desired.

---

## Cognitive Load: 3 of 8 fail — **moderate** (address soon)

- **FAIL — Chunking:** the New-Job active-plate panel stacks Source → Eligible printers → per-printer config (print profile + filament, ×N) → Order → Job name in one scroll. With 2 printers selected that is ~8 decision surfaces in one card.
- **FAIL — Minimal choices at a decision point:** the expanded printer card header exposes ~7 controls at once (StatusPill, Ready-for-work, Queue on/off, Snapshot, Edit, Collapse, + Pause/Stop/Resume below). Fleet rows are a 7-column grid.
- **FAIL — Working memory / context:** new-job config is not persisted; navigating to the "+ New order" button offered *inside* the new-job flow wipes the in-progress config.
- **Passes:** single focus per screen, proximity grouping, clear size/weight/color hierarchy, plate-tab sequencing, progressive disclosure (accordions, expand-on-click, conditional per-printer config).

Worst decision points by option count: New-Job active plate w/ 2+ printers (8+ inputs), expanded printer card header (7 actions), Fleet rows (7 columns), Spoolman-connected settings (~10 fields, borderline but the FieldRow rhythm keeps it scannable).

---

## What's Working (keep these)

1. **Slice-failure recovery (QueueScreen).** Failed jobs show an inline red strip *and* a detail panel that fetches and renders the raw per-printer `slice_error` in a scrollable mono block plus a one-click "Edit slicer settings" jump. Strongest single piece of UX here.
2. **Override-loss prevention modal (NewJobScreen).** Diffs settings baked into the uploaded 3MF against the selected printer/preset, shows a key → from → to table, and warns on filament-slot shortfall before committing. Real error-prevention that respects operator intent.
3. **Density/accent token architecture (app.css).** `[data-density]` retunes the full spacing scale + row height; `[data-accent]` is a clean 4-variable swap. The infrastructure is correct and cheap; it just needs a control wired to it.

---

## Priority Issues

**[P1] Dead / placeholder controls erode trust in system status.**
"Resync" (App.tsx), Fleet "Sync now", Queue "Sort"/"Material", Fleet "Snapshot", "Claim next from queue" have no handlers. In an operator tool, a button that does nothing while you are trying to reconcile a stuck printer reads as broken. **Fix:** wire them, or remove them; at minimum disable with a tooltip until built. (Several of these overlap the known mockup-audit "looks done but does nothing" list.) → `$impeccable harden`, then `$impeccable distill`.

**[P1] Consistency drift across the primary screen archetypes.**
Three header components (`SectionHeader` 17px / `PageHeader` 20px / Fleet inline 22px), three toggle implementations, two destructive-confirm patterns, and pervasive inline `style={}` doing work the token classes already cover. This is the lowest heuristic score and the difference between "one product" and "competent screens that do not quite match." **Fix:** collapse to one Header, one Toggle, one ConfirmButton; lint-ban inline spacing/border styles that a class covers. → `$impeccable audit` then `$impeccable distill`.

**[P1] Status conveyed by color with weak / absent non-color redundancy (accessibility).**
StatusPill has a text label (good) but the dot is the only differentiator between same-toned states (`queued`/`waiting`/`offline` all map to grey; `printing`/`in_progress` both blue). Worse: the **queue-off cue and the awaiting-plate-clear cue are both an amber border** — two different critical meanings, one color. A colorblind operator cannot tell "held out of queue" from "needs plate cleared." **Fix:** give each cue a distinct icon/shape or text token, not just border color. → `$impeccable colorize` (+ `$impeccable clarify`).

**[P2] No expert accelerators in an expert tool.**
Zero keyboard shortcuts, no command palette (the `.kbd`/`.search` CSS is built and unused), no bulk actions. The power-user is the *primary* persona and the tool under-serves them. **Fix:** a `⌘K` palette on the existing search styling; j/k list nav + Enter to open; bulk-select on queue and fleet. → `$impeccable adapt`.

**[P2] New-job configuration is not navigation/crash-safe.**
All plate/printer config lives in component state; navigating to "+ New order" (offered inside the flow) or refreshing wipes it. **Fix:** persist the draft to `sessionStorage` keyed by `uploaded_file_id`; restore on return. → `$impeccable harden`.

**[P2] Silent fetch failures render as empty states.**
`useQueue`/`useOrders`/profile hooks `.catch(console.error)` and fall through to an empty list. An operator cannot tell "no jobs" from "backend unreachable" — dangerous in a farm context. **Fix:** distinguish loading vs empty vs error; show a load-error banner with retry. → `$impeccable harden`.

**[P3] Decorative glow stacking on progress/status.**
Progress bar = gradient + glow + inset border; status dots = colored glow; expanded card = triple shadow/border. Minor visual-noise tax on an otherwise minimal system. **Fix:** drop the progress glow and dot glow; keep one elevation cue per surface. → `$impeccable polish`.

---

## Persona Red Flags

**Alex — Power User (primary):** no global search/`⌘K` despite the CSS being built for it; no keyboard nav or Enter-to-open on queue/fleet; no bulk ops (cancel N jobs, flip queue-on across the fleet is one-at-a-time); density/accent prefs exist in tokens but no control sets them, and `data-density="balanced"` is hardcoded **and is not a defined density key** (only `dense`/`roomy` exist), so the dense layout Alex wants is unreachable.

**Sam — Accessibility-Dependent (color is load-bearing here):** two distinct critical states share one amber border; icon-only buttons depend on `title` tooltips; likely-failing contrast on `--text-4 #475472` over `--bg-2 #0e1a31` (used for `.tag-key`/`.nav-section-label`/dim metadata) and borderline `--text-3 #6b81a4` on the 11px muted captions that carry real info (folder names, "remaining", targets); custom toggles have `aria-checked` but no consistent `:focus-visible` ring on the many bare-`<button style={}>` elements.

**Riley — Stress Tester:** new-job draft lost on refresh/navigation; dead Sort/Material/Resync buttons "appear to work but do not"; `blockReason` hard-truncated at 90 chars with no expand-in-place (full reason only in the side panel); empty-state copy ("Nothing here", "No tags match") is indistinguishable from a load failure.

---

## Minor Observations

- `data-density="balanced"` (App.tsx) matches no `[data-density]` rule, so it silently falls back to `:root` defaults — dead/misleading. The whole collapse-sidebar CSS (`data-nav="collapsed/hidden"`) is built but never triggered.
- `plateName` ternary in QueueScreen has identical branches (`plate ? 'Plate X' : 'Plate X'`) — dead logic; the queue card always shows "Plate N" with no model/file name, hurting scannability of the highest-traffic screen.
- `DisplayJob.material` is hardcoded `'—'` and `eligiblePrinters` to `[]` in QueueScreen, so the Material/Eligible rows and the Material filter can never populate from real data. (Matches the earlier mockup-audit "queue detail not fully wired" gap.)
- `import { JOBS } from '../data/mock'` still drives the expanded printer card's "Current job" parts list in FleetScreen — mock data leaking into an otherwise API-wired screen.
- Topbar `actions` are undefined for `/jobs/:id`, `/jobs/:id/edit`, `/orders/:id/edit`, so those screens lose their top-right contextual actions; breadcrumbs are static strings, not links.
- `.video.live::before { content: "● LIVE" }` shows on every non-offline tile including the CSS-faked feed when no camera is configured, implying a stream that may not exist.

---

## Questions to Consider

1. Density and accent theming are fully built in tokens but unreachable in the UI. Is a Settings → Appearance control planned, and was `data-density="balanced"` meant to be `"dense"`? Wiring this is nearly free and directly serves the expert audience.
2. For a keyboard-first operator tool, is the absence of any shortcut / command-palette layer a deliberate scope cut or an oversight? The unused `.kbd`/`.search` styles suggest it was intended.
3. The queue cards omit model/file name and real material (hardcoded `'—'`). Backend-data gap or design choice? It currently makes the highest-traffic screen the least scannable, which conflicts with the density-and-clarity priority.

---

## Recommended Actions (prioritized)

Calibrated to the operator-tool framing. Suggested order:

1. **`$impeccable harden`** — wire-or-remove dead controls (Resync/Sort/Material/Snapshot/Claim-next), distinguish loading vs empty vs error states, and persist the new-job draft. Highest trust + safety payoff.
2. **`$impeccable audit`** then **`$impeccable distill`** — unify the three header/toggle/confirm patterns and replace inline `style={}` with the existing token classes. Fixes the lowest score (consistency).
3. **`$impeccable colorize`** + **`$impeccable clarify`** — add non-color redundancy to status cues (the two amber meanings, the same-toned pills), fix the weakest text-contrast pairs, add `:focus-visible` rings.
4. **`$impeccable adapt`** — the expert-efficiency layer: `⌘K` command palette on the existing search styling, j/k + Enter list nav, bulk actions on queue/fleet; wire a density/accent control.
5. **`$impeccable layout`** — reduce the New-Job active-plate density (collapse per-printer config behind a per-printer disclosure) and trim the 7-control card header to a primary action + overflow.
6. **`$impeccable polish`** — final pass: drop the stacked progress/dot glows, tidy the `data-density="balanced"` bug and dead `plateName` ternary.

> Note: several findings (dead Sort/Material/Snapshot/Resync, queue `material='—'`/`eligiblePrinters=[]`, Fleet `JOBS` mock) are the same "looks built but isn't" items from the earlier mockup-vs-implementation audit. They are unbuilt *features* surfacing as UX debt, not pure styling issues; treat them as wire-or-remove decisions.
