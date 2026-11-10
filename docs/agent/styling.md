# Styling Reference

How Themis is styled. **There is no CSS framework** — no Tailwind, CSS-modules, styled-components, or
Sass. Styling is a single global stylesheet of design tokens + hand-written utility/component classes,
applied via `className` strings, with `style={{ '--token' }}`/inline `style` for one-offs. Match this —
don't introduce a framework or per-component CSS files.

- **The stylesheet**: `frontend/src/styles/app.css`. Imported once (via `main.tsx`/`App`).
- **The guard**: `frontend/src/styleTokens.test.ts` fails on a `var(--token)` that `app.css` does not define, on a
  `var(--token, #hex)` fallback, and on a new hex / `rgba()` / `oklch()` literal in component code (a ratchet: `LITERAL_BUDGET` only goes down). It does not
  see named colours (`'white'`) or the files listed in its `PALETTE_FILES`.
- **Shared styled components**: `frontend/src/components/ui.tsx`. Use these before writing new markup.

## Design tokens (CSS variables, `:root` in app.css)

Everything references tokens — never hard-code a hex/px when a token exists.

| Group | Tokens | Notes |
|---|---|---|
| Backgrounds | `--bg-0`…`--bg-4` | 0 = page (near-black) → 4 = hover/selected |
| Borders | `--border-1`…`--border-3` | low→high contrast |
| Text | `--text-1`…`--text-4` | 1 = primary → 4 = dim |
| Accent | `--accent`, `--accent-hi`, `--accent-lo`, `--accent-glow` | chrome yellow default; **themeable** (see below) |
| Status | `--ok`/`--warn`/`--err`/`--info` each with `*-bg` (fill) and `*-border` (outline); `--warn-border-strong`/`--err-border-strong`; `--idle` + `--idle-bg`; `--link` | drive `.pill`, `.progress`, `.notice`. A tinted box is `background: var(--err-bg); border: 1px solid var(--err-border)` — never an `rgba()` |
| Overlay | `--scrim` (modal backdrop / plate behind text on a camera feed), `--thumb-fallback` (file with no thumbnail) | |
| Type | `--font-sans` (IBM Plex Sans), `--font-mono` (IBM Plex Mono) | base body 13.5px |
| Spacing | `--pad-1`…`--pad-6`, `--row-h`, `--card-pad`, `--gap-stack` | **density-dependent** (see below) |
| Radii | `--r-1`(0)…`--r-5`(4px) | square-cornered look; read the values in `:root` |
| Shadow | `--shadow-1` (subtle), `--shadow-2` (elevated/modal) | |
| Sidebar | `--sb-collapsed` (64px), `--sb-expanded` (232px), runtime `--sb-w` | |

### Runtime theming (data-attributes on ancestors, no JS recompute)
- **Density** — `[data-density="dense"|"roomy"]` overrides the spacing tokens (`--pad-*`, `--row-h`,
  `--card-pad`, `--gap-stack`). Default (no attr) is the comfortable middle. `.content[data-density]`
  also adjusts page padding. So size with tokens and a component auto-responds to density.
- **Accent** — `[data-accent="azure"|"cyan"|"indigo"|"amber"|"mint"]` swaps the `--accent*` set. Use
  `var(--accent)` (not a literal amber) so accent theming works.
- **Nav** — `.app[data-nav="collapsed"|"hidden"]` drives `--sb-w` (sidebar width) + hides labels.

## Component & utility class vocabulary (app.css)

Reach for these before inventing classes. Composable via space-separated `className`.

| Class | Purpose |
|---|---|
| `.pill` + tone `.ok/.warn/.err/.info/.idle/.accent` (with `.dot`) | status chip — **use `<StatusPill>`**, don't hand-roll |
| `.progress` (+`.warn`/`.err`/`.lg`, inner `.bar`) | progress bar — **use `<Progress>`** |
| `.btn` (+`.primary`/`.ghost`/`.sm`/`.icon`, inner `.ico`) | buttons; `:disabled` dims it, so new code needs no inline `opacity` (a few older call sites still set one, which wins) |
| `.notice` + tone `.ok/.warn/.err/.info` | inline message box (failed save, warning above a form) |
| `.card` (+`.flat`/`.raised`) | panel surface — **use `<Card>`** |
| `.input`, `.textarea`, `.select`, `.label` | form controls |
| `.tbl` (`th`/`td`, `tr.selected`, hover) | data tables |
| `.video` (+`.live`), `.feed-scene`, `.swatch`(+`.lg`) | camera tile, color swatch |
| `.elig`(+`.on`/`.off`), `.tweaks-panel`, `.tag-key`, `.live-dot`, `.shimmer-bar` | domain bits |
| **Layout/util**: `.app`, `.main`, `.sidebar`, `.topbar`, `.content`, `.screen-grid`, `.layout-main-sidebar` | shells |
| `.row`(+`.between`), `.col`, `.divider` | flex helpers |
| `.muted`, `.tiny`, `.small`, `.mono`, `.num` | text helpers (`.num` = tabular mono figures) |
| `.gap-1`…`.gap-6`, `.wrap`, `.grow`, `.min-0`, `.shrink-0`, `.w-full`, `.nowrap`, `.text-right`, `.text-err`/`.text-warn`/`.text-ok`, `.fw-500`/`.fw-600` | one-property helpers (end of `app.css`). Use these, not `style={{ flex: 1 }}` / `style={{ color: 'var(--err)' }}`. `.row` already centres its items: don't add `alignItems: 'center'` |

## Shared components (`components/ui.tsx`) — prefer these over raw markup

`StatusPill({status, label?})`, `Progress({value, tone?, large?})`, `Swatch({color, large?})`,
`MaterialChip({material, color})`, `VideoTile(...)`, `Card(...HTMLAttributes)`,
`PrinterBadge({printerId})`, `EligibilityChips({ids})`, `SectionHeader({title, sub?, actions?})`,
`Empty({title, sub?, icon})`, `Kv({k, v})`. Icons: `components/icons.tsx` (`Icons.*`).

## Status → visual mapping (the `StatusKey` ↔ pill-tone bridge)

`StatusPill` (`ui.tsx`) maps a `StatusKey` to a `.pill` tone class + label via an internal `map`. Current
keys (`data/types.ts`): `printing→info, queued/waiting/offline→idle, claiming/slicing→accent,
paused/hold/partial→warn, error→err, idle/ready/complete→ok, in_progress→info`.
- `StatusKey` (`data/types.ts`) is the **styled** status set. Job statuses (`backend.md`) can exceed it
  (`blocked`, `failed`, `cancelled`, `uploading`) → cast `as StatusKey`/`as never` at the call site; an
  unmapped status falls through to `['idle', status]` (grey pill, raw text). **If you add a job/order
  status that should render styled, add it to `StatusKey` AND the `StatusPill` map** — otherwise it
  shows up as a grey pill with the raw key.

## Responsive / touch (BIZ-156)

- One breakpoint does the work: `@media (max-width: 1024px)` in `app.css` swaps the sidebar for the bottom
  bar (`BottomNav` in `App.tsx`; destinations without a slot live in its **More** sheet, `MORE_NAV_ITEMS`),
  enforces ≥44px `.btn`/inputs/`.settings-tab`/`.layout-toggle` targets, and stacks `.fleet-expanded` and the
  open-job panel (`.queue-grid[data-selected]`). Inline `gridTemplateColumns` can't be overridden by a media
  query — put multi-column layouts in a class (see `.fleet-expanded`, `.queue-grid`).
- Use `minmax(0, 1fr)`, never bare `1fr`, for a grid track holding wide content (a bare `1fr` can't shrink
  below its min-content and widens the whole page — the original Queue bug). Use `.row.wrap` for toolbars.
- `useMediaQuery(query)` (`components/useMediaQuery.ts`) for JS-level switches (Fleet hides the 7-column Rows
  layout at ≤768px). `e2e/responsive.spec.ts` asserts no sideways scroll + 44px targets at 375/768/1024px.
- PWA: `public/manifest.webmanifest` + `public/icons/*` (copied to `dist/`, served by `register_spa`), linked
  from `index.html`. No service worker (installable for home-screen launch; no offline mode).

## Recipes

- **Style a new element** → compose existing utility/component classes + tokens; only add a new class in
  `app.css` if nothing fits. Use `<Card>`, `<StatusPill>`, `<Progress>`, `.btn`, `.row/.col/.gap-*`.
- **Colours that are data, not chrome** (material palette, group colours, the unknown-filament swatch `UNKNOWN_COLOR` /
  `NEW_SLOT_COLOR` in `data/helpers.ts`) live in the files listed in `PALETTE_FILES` in `styleTokens.test.ts`; import the
  constant, don't retype the hex.
- **Add a token** → define in `:root` in `app.css`; if it should react to density/accent, add the
  override under the matching `[data-density]`/`[data-accent]` block too.
- **Add a styled status** → extend `StatusKey` (`data/types.ts`) + the `StatusPill` `map` (`ui.tsx`);
  pick an existing tone (`ok/warn/err/info/idle/accent`).
- **New accent theme** → add a `[data-accent="x"]` block setting the four `--accent*` vars.

## Gotchas

- No framework → no utility-class autocomplete/purge; class names are the ones in `app.css`, that's it. `styleTokens.test.ts` fails on a
  `className` word that `app.css` does not define (it reads `className="…"` and the fixed words of a template literal; a
  class chosen in an expression, `className={on ? 'a' : 'b'}`, is not checked).
- One global stylesheet → class names are global; prefix domain-specific classes to avoid collisions.
- Build/type-check is still `npm run build` (`tsc -b`); CSS isn't type-checked — a typo'd class name
  and a typo'd `var(--token)` used to no-op silently; `styleTokens.test.ts` now catches both.
