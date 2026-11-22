# Mobile responsive journeys implementation plan

## Goal

Make every audited Themis journey usable at Android phone widths without changing the established electric-precision visual language. Preserve dense desktop layouts while adapting hierarchy, controls, and data presentation below the content-driven 1024px, 768px, and 600px breakpoints.

## Responsive decisions

- Stack primary/secondary workflows at 768px so order summaries and file navigation remain in normal document flow.
- Replace action-heavy tables with mobile cards; keep desktop tables unchanged. Analytics becomes per-printer metric cards because comparison rows otherwise require horizontal scanning.
- Use a compact settings selector on phones and retain the scrolling tab strip on tablets.
- Hold the camera wall to two columns on phones. This preserves wall density while keeping tiles roughly 160px wide on a 360px viewport; desktop column preferences remain unchanged.
- Standardize touch affordances at a minimum 44px while retaining the existing smaller visual tracks/icons inside them.
- Keep duplicate page-level actions in the content on desktop, but hide their redundant copies on phones when the same action is already in the app header.
- Keep Tailscale trust deployment-specific: the local review deployment uses an uncommitted Compose override that admits only its isolated relay peer (`172.30.253.3/32`) via `THEMIS_LOCAL_NETWORKS`. Tailscale users get keyless admin without changing repository defaults or globally trusting the shared CGNAT range and forwarded headers.

## Implementation

1. Refactor shared settings fields, toggles, tabs, touch targets, and bottom navigation labels.
2. Repair New Order, Files, Printer Files, Camera Wall, Project Builder, Settings/Tags/API Keys, and Analytics layouts.
3. Remove mobile-only duplicated headings/actions identified in Queue, Fleet, and New Job.
4. Add focused component/style tests for new responsive hooks and semantic mobile markup.
5. Run frontend type/style/unit checks and backend auth tests.
6. Build and deploy the branch with Docker Compose, verify health and Tailscale-local session behavior, then push the branch without opening a pull request.

## Verification matrix

- Widths: 360x800, 412x915, 768x1024, and desktop regression at 1440x900.
- Journeys: queue/new job, fleet/wall, files/printer files, projects/new, orders/new, analytics, and all settings pages.
- Checks: no horizontal viewport overflow, no clipped text/actions, normal-flow summaries, visible form labels, 44px targets, keyboard focus, and readable bottom navigation.
- Auth: a request sent through the isolated Tailscale relay reaches Themis from the explicitly trusted `172.30.253.3/32` peer and resolves to local admin without a password.
