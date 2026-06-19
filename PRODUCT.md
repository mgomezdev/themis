# Product

## Register

product

## Users

The person who owns and runs the print farm: a solo operator or small team lead checking job status, dispatching prints, and clearing plates throughout the workday from a desk or shop floor. They are technically capable, hands-on, and return to the dashboard frequently in short bursts rather than sitting in it for hours.

## Product Purpose

Themis is a print farm management dashboard for fleets of FDM 3D printers. It manages the job queue, slices files via OrcaSlicer, dispatches jobs to available printers, tracks live printer state, and handles failure recovery. Success looks like: the operator can see everything at a glance, act immediately when something needs attention, and trust the queue to run itself when nothing does.

## Brand Personality

Electric precision. Pulled from Night City's corporate intranet — chrome yellow on asphalt dark, holo cyan for data, red-orange for danger. The interface is direct and charged, not decorative. CP2077 game UI and Edgerunners (anime) are the visual references; the tabletop game is not. The functional sensibility remains: no ceremony, no glitch decoration, no scanlines for flavor — just the palette and the confidence.

## Anti-references

- Generic SaaS dashboards: cream backgrounds, rounded modals, Stripe-clone card grids
- Enterprise bloat: overly structured sidebars, excessive section headers, felt-by-committee layouts

## Design Principles

1. **Operational clarity first.** Status reads at a glance. Every pixel earns its place in a working session, not a presentation.
2. **Calm confidence.** Clear signals, not alarms. The interface doesn't create anxiety; it reduces it.
3. **Earned density.** Compact enough to see the whole farm at once; never cramped or illegible.
4. **No ceremony.** Skip the chrome. No hero zones mid-session, no modal for what an inline action handles.
5. **Instrument, not app.** Feels crafted for this specific job, not adapted from a general-purpose dashboard template.

## Accessibility & Inclusion

WCAG AA minimum. Dark theme is intentional (shop floor ambient, screen-dominant environment). Support reduced motion via `prefers-reduced-motion`. Color-blind-safe status signals: never rely on hue alone — pair color with shape, label, or icon.
