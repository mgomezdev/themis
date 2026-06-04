# Styling Reference

How Themis is styled. **There is no CSS framework** — no Tailwind, CSS-modules, styled-components, or
Sass. Styling is a single global stylesheet of design tokens + hand-written utility/component classes,
applied via `className` strings, with `style={{ '--token' }}`/inline `style` for one-offs. Match this —
don't introduce a framework or per-component CSS files.

- **The stylesheet**: `frontend/src/styles/app.css` (~650 lines). Imported once (via `main.tsx`/`App`).
- **Shared styled components**: `frontend/src/components/ui.tsx`. Use these before writing new markup.

## Design tokens (CSS variables, `:root` in app.css)

Everything references tokens — never hard-code a hex/px when a token exists.

| Group | Tokens | Notes |
|---|---|---|
| Backgrounds | `--bg-0`…`--bg-4` | 0 = page (deepest navy) → 4 = hover/selected |
| Borders | `--border-1`…`--border-3` | low→high contrast |
| Text | `--text-1`…`--text-4` | 1 = primary → 4 = dim |
| Accent | `--accent`, `--accent-hi`, `--accent-lo`, `--accent-glow` | amber default; **themeable** (see below) |
| Status | `--ok`/`--warn`/`--err`/`--idle`/`--info` (+ `*-bg` translucent fills) | drive `.pill`, `.progress` |
| Type | `--font-sans` (Geist), `--font-mono` (Geist Mono) | base body 13.5px, `font-feature-settings 'ss01','cv11'` |
| Spacing | `--pad-1`…`--pad-6`, `--row-h`, `--card-pad`, `--gap-stack` | **density-dependent** (see below) |
| Radii | `--r-1`(4px)…`--r-5`(24px) | |
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
| `.btn` (+`.primary`/`.ghost`/`.sm`/`.icon`, inner `.ico`) | buttons |
| `.card` (+`.flat`/`.raised`) | panel surface — **use `<Card>`** |
| `.input`, `.textarea`, `.select`, `.label` | form controls |
| `.tbl` (`th`/`td`, `tr.selected`, hover) | data tables |
| `.video` (+`.live`), `.feed-scene`, `.swatch`(+`.lg`) | camera tile, color swatch |
| `.elig`(+`.on`/`.off`), `.tweaks-panel`, `.tag-key`, `.live-dot`, `.shimmer-bar` | domain bits |
| **Layout/util**: `.app`, `.main`, `.sidebar`, `.topbar`, `.content`, `.screen-grid`, `.layout-main-sidebar` | shells |
| `.row`(+`.between`), `.col`, `.gap-2`…`.gap-6`, `.divider` | flex helpers |
| `.muted`, `.tiny`, `.small`, `.mono`, `.num` | text helpers (`.num` = tabular mono figures) |

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

## Recipes

- **Style a new element** → compose existing utility/component classes + tokens; only add a new class in
  `app.css` if nothing fits. Use `<Card>`, `<StatusPill>`, `<Progress>`, `.btn`, `.row/.col/.gap-*`.
- **Add a token** → define in `:root` in `app.css`; if it should react to density/accent, add the
  override under the matching `[data-density]`/`[data-accent]` block too.
- **Add a styled status** → extend `StatusKey` (`data/types.ts`) + the `StatusPill` `map` (`ui.tsx`);
  pick an existing tone (`ok/warn/err/info/idle/accent`).
- **New accent theme** → add a `[data-accent="x"]` block setting the four `--accent*` vars.

## Gotchas

- No framework → no utility-class autocomplete/purge; class names are the ones in `app.css`, that's it.
- One global stylesheet → class names are global; prefix domain-specific classes to avoid collisions.
- Build/type-check is still `npm run build` (`tsc -b`); CSS isn't type-checked — a typo'd class name
  silently no-ops. Verify against `app.css`.
