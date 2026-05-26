# Themis Frontend Stub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all screens from the Claude Design mockup as a working stub React/TypeScript frontend. All data is mocked — no live API calls. GitHub enhancement issues capture the functional wiring work.

**Architecture:** Port design JSX files (`docs/design/themis/project/`) to TypeScript React. Replace `window.xxx` globals with ES imports. React Router v6 handles navigation. Vitest + React Testing Library for component tests. Design CSS ported verbatim (minus font @import, replaced with Google Fonts link in index.html).

**Tech Stack:** React 19 + TypeScript + Vite + React Router v6 + Vitest + @testing-library/react

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `frontend/src/styles/app.css` | Ported design CSS |
| Create | `frontend/src/data/types.ts` | TypeScript interfaces for all data shapes |
| Create | `frontend/src/data/mock.ts` | Ported mock data (PRINTERS, JOBS, ORDERS, etc.) |
| Create | `frontend/src/data/helpers.ts` | fmtTime, fmtClock, matColor, matTypeBg/Fg/Border |
| Create | `frontend/src/components/icons.tsx` | Icon + Icons map |
| Create | `frontend/src/components/ui.tsx` | All shared UI primitives |
| Create | `frontend/src/components/Sidebar.tsx` | App sidebar nav |
| Create | `frontend/src/components/Topbar.tsx` | Top bar with search + actions |
| Create | `frontend/src/screens/QueueScreen.tsx` | Job queue screen |
| Create | `frontend/src/screens/FleetScreen.tsx` | Fleet dashboard |
| Create | `frontend/src/screens/OrdersScreen.tsx` | Orders list |
| Create | `frontend/src/screens/NewJobScreen.tsx` | New job wizard |
| Create | `frontend/src/screens/NewOrderScreen.tsx` | New order intake |
| Create | `frontend/src/screens/FilesScreen.tsx` | Model library |
| Create | `frontend/src/screens/FilamentsScreen.tsx` | Filament catalog |
| Create | `frontend/src/screens/PrintersScreen.tsx` | Printer management |
| Create | `frontend/src/screens/SettingsScreen.tsx` | Settings (7 sub-pages) |
| Modify | `frontend/src/App.tsx` | React Router app shell |
| Modify | `frontend/index.html` | Add Google Fonts link |
| Create | `frontend/vitest.config.ts` | Test runner config |
| Create | `frontend/src/test/setup.ts` | Testing library setup |

---

## Task 1: Project Foundation

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Modify: `frontend/index.html`

- [ ] **Step 1: Install dependencies**

```bash
cd frontend
npm install react-router-dom@^6
npm install --save-dev vitest @vitest/coverage-v8 @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
```

Expected: `package.json` now has `react-router-dom` in dependencies and vitest packages in devDependencies.

- [ ] **Step 2: Write `vitest.config.ts`**

```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
});
```

- [ ] **Step 3: Write `src/test/setup.ts`**

```typescript
import '@testing-library/jest-dom';
```

- [ ] **Step 4: Add `test` script to `package.json`**

Add to the `"scripts"` block:
```json
"test": "vitest",
"test:run": "vitest run"
```

- [ ] **Step 5: Add Google Fonts to `index.html`**

Add inside `<head>` before the closing tag:
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
```

Also change the title to `<title>Themis</title>`.

- [ ] **Step 6: Verify test runner**

```bash
cd frontend
echo "import { describe, it, expect } from 'vitest'; describe('smoke', () => { it('passes', () => expect(1).toBe(1)); });" > src/test/smoke.test.ts
npm run test:run -- src/test/smoke.test.ts
```

Expected: 1 test passes.

Remove smoke test: `del src\test\smoke.test.ts` (Windows) or `rm src/test/smoke.test.ts`

- [ ] **Step 7: Commit**

```bash
cd frontend
git add package.json vitest.config.ts src/test/setup.ts index.html
git commit -m "feat(frontend): add test infrastructure + Google Fonts"
```

---

## Task 2: CSS + Font Variables

**Files:**
- Create: `frontend/src/styles/app.css`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Copy design CSS**

Copy `docs/design/themis/project/styles.css` to `frontend/src/styles/app.css`.

Then find and replace the `@import url(...)` font lines at the top with nothing (fonts are loaded via index.html). The CSS references `var(--font-sans)` and `var(--font-mono)` — keep those CSS variable definitions but they now point to Geist (already done in the :root block of the CSS).

Verify the CSS file has the `:root` block with `--font-sans: 'Geist', ...` and `--font-mono: 'Geist Mono', ...`.

- [ ] **Step 2: Import CSS in `main.tsx`**

Replace the contents of `frontend/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/app.css';
import App from './App.tsx';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 3: Verify CSS loads**

```bash
cd frontend && npm run dev
```

Open `http://localhost:5173` — background should be dark navy (`#040d1a` or similar), not white. Kill the dev server.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/styles/app.css frontend/src/main.tsx
git commit -m "feat(frontend): port design CSS"
```

---

## Task 3: Type Definitions + Mock Data

**Files:**
- Create: `frontend/src/data/types.ts`
- Create: `frontend/src/data/mock.ts`
- Create: `frontend/src/data/helpers.ts`
- Create: `frontend/src/data/types.test.ts`

- [ ] **Step 1: Write failing type smoke test**

Create `frontend/src/data/types.test.ts`:

```typescript
import { describe, it, expectTypeOf } from 'vitest';
import type { Printer, Job, Order, Filament, FileEntry } from './types';

describe('data types', () => {
  it('Printer has required fields', () => {
    expectTypeOf<Printer>().toHaveProperty('id');
    expectTypeOf<Printer>().toHaveProperty('status');
    expectTypeOf<Printer>().toHaveProperty('capabilities');
  });
  it('Job has eligiblePrinters', () => {
    expectTypeOf<Job>().toHaveProperty('eligiblePrinters');
  });
});
```

Run: `npm run test:run -- src/data/types.test.ts`
Expected: FAIL (types not defined yet).

- [ ] **Step 2: Write `src/data/types.ts`**

```typescript
export interface Material {
  name: string;
  type: string;
  color: string;
}

export interface Printer {
  id: string;
  name: string;
  nickname: string;
  model: string;
  badge: string;
  buildVolume: string;
  capabilities: string[];
  chamber: boolean;
  status: 'printing' | 'idle' | 'paused' | 'error' | 'offline' | 'claiming';
  progress: number;
  timeRemaining: number;
  timeElapsed: number;
  layer: { now: number; total: number } | null;
  nozzleTemp: number;
  bedTemp: number;
  chamberTemp: number | null;
  material: Material;
  currentJobId: string | null;
  accent: string;
  note?: string;
}

export interface OrderPart {
  id: string;
  name: string;
  qty: number;
  printed: number;
  material: string;
  est: number;
  thumbColor: string;
}

export interface Order {
  id: string;
  type: 'customer' | 'internal';
  customer: string;
  title: string;
  placed: string;
  due: string;
  status: 'queued' | 'in_progress' | 'partial' | 'complete' | 'hold';
  notes: string;
  parts: OrderPart[];
}

export interface JobPart {
  orderId: string;
  partId: string;
  qty: number;
}

export interface Job {
  id: string;
  plateName: string;
  status: 'printing' | 'queued' | 'complete' | 'paused' | 'error';
  printerId: string | null;
  eligiblePrinters: string[];
  actualPrinter?: string;
  material: string;
  parts: JobPart[];
  estTime: number;
  elapsed: number;
  progress: number;
  layer?: { now: number; total: number };
  priority: number;
  sliced: boolean;
  note?: string;
  completedAt?: string;
}

export interface FilamentProfile {
  printerId: string;
  name: string;
  nozzle: string;
  bedTemp: number;
  hotendTemp: number;
  layerHeight: number;
  notes: string;
}

export interface PurchaseLink {
  vendor: string;
  url: string;
}

export interface Filament {
  id: string;
  name: string;
  manufacturer: string;
  type: string;
  subtype: string;
  color: string;
  colorName: string;
  diameter: number;
  dryTemp: number;
  purchaseLinks: PurchaseLink[];
  profiles: FilamentProfile[];
  notes: string;
  favorite?: boolean;
}

export interface ProcessPreset {
  id: string;
  printerId: string;
  name: string;
  nozzle: string;
  layerHeight: number;
  infill: number;
  walls: number;
  speed: string;
  description: string;
}

export interface Tag {
  id: string;
  name: string;
  color: string;
  category: string;
}

export interface FileEntry {
  id: string;
  name: string;
  size: string;
  parts: number;
  updated: string;
  thumbColor: string;
  folder: string;
  tags: string[];
}

export type StatusKey =
  | 'printing' | 'queued' | 'waiting' | 'claiming' | 'slicing'
  | 'paused' | 'error' | 'offline' | 'idle' | 'ready' | 'complete'
  | 'hold' | 'in_progress' | 'partial';
```

- [ ] **Step 3: Verify types test passes**

```bash
npm run test:run -- src/data/types.test.ts
```

Expected: 2 tests pass.

- [ ] **Step 4: Write `src/data/helpers.ts`**

```typescript
export function fmtTime(mins: number | null | undefined): string {
  if (mins == null) return '—';
  if (mins < 1) return '<1m';
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

export function fmtClock(mins: number | null | undefined): string {
  if (mins == null) return '--:--';
  const h = Math.floor(mins / 60);
  const m = Math.floor(mins % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export function matColor(material: string): string {
  const m = material.toLowerCase();
  if (m.includes('pa-cf') || m.includes('pacf')) return '#94a3b8';
  if (m.includes('petg')) return '#67e8f9';
  if (m.includes('abs')) return '#a78bfa';
  if (m.includes('asa')) return '#f87171';
  if (m.includes('tpu')) return '#f87171';
  if (m.includes('pc') && !m.includes('pacf')) return '#fbbf24';
  if (m.includes('pla')) return '#60a5fa';
  return '#475472';
}

export function matTypeBg(t: string): string {
  switch (t) {
    case 'PLA': return 'rgba(96,165,250,0.12)';
    case 'PETG': return 'rgba(103,232,249,0.12)';
    case 'PA-CF': return 'rgba(148,163,184,0.14)';
    case 'ABS': return 'rgba(167,139,250,0.14)';
    case 'ASA': return 'rgba(244,114,182,0.12)';
    case 'PC': return 'rgba(251,191,36,0.10)';
    case 'TPU': return 'rgba(248,113,113,0.14)';
    default: return 'rgba(148,163,184,0.10)';
  }
}

export function matTypeFg(t: string): string {
  switch (t) {
    case 'PLA': return '#93c5fd';
    case 'PETG': return '#7dd3fc';
    case 'PA-CF': return '#cbd5e1';
    case 'ABS': return '#c4b5fd';
    case 'ASA': return '#f9a8d4';
    case 'PC': return '#fcd34d';
    case 'TPU': return '#fca5a5';
    default: return 'var(--text-2)';
  }
}

export function matTypeBorder(t: string): string {
  switch (t) {
    case 'PLA': return 'rgba(96,165,250,0.30)';
    case 'PETG': return 'rgba(103,232,249,0.30)';
    case 'PA-CF': return 'rgba(148,163,184,0.30)';
    case 'ABS': return 'rgba(167,139,250,0.30)';
    case 'ASA': return 'rgba(244,114,182,0.30)';
    case 'PC': return 'rgba(251,191,36,0.25)';
    case 'TPU': return 'rgba(248,113,113,0.30)';
    default: return 'var(--border-1)';
  }
}
```

- [ ] **Step 5: Write `src/data/mock.ts`**

Port `docs/design/themis/project/data.jsx` verbatim to TypeScript.
Replace the `const` declarations (no `window.assign` at the end) and add types:

```typescript
import type { Printer, Order, Job, Filament, ProcessPreset, Tag, FileEntry } from './types';
import type { Material } from './types';

export const PRINTERS: Printer[] = [ /* ... paste from data.jsx PRINTERS array ... */ ];
export const ORDERS: Order[] = [ /* ... paste from data.jsx ORDERS array ... */ ];
export const JOBS: Job[] = [ /* ... paste from data.jsx JOBS array ... */ ];
export const FILAMENTS: Filament[] = [ /* ... paste from data.jsx FILAMENTS array ... */ ];
export const PROCESS_PRESETS: ProcessPreset[] = [ /* ... paste from data.jsx PROCESS_PRESETS array ... */ ];
export const TAGS: Tag[] = [ /* ... paste from data.jsx TAGS array ... */ ];
export const FILES: FileEntry[] = [ /* ... paste from data.jsx FILES array ... */ ];

export function getPrinter(id: string): Printer | undefined {
  return PRINTERS.find(p => p.id === id);
}
export function getOrder(id: string): Order | undefined {
  return ORDERS.find(o => o.id === id);
}
export function getPart(orderId: string, partId: string) {
  const o = getOrder(orderId);
  return o ? o.parts.find(p => p.id === partId) : undefined;
}
```

**NOTE:** Copy the actual array contents from `docs/design/themis/project/data.jsx` lines 8–170 (PRINTERS through FILES). Remove `const MATERIALS` (not needed — material colors handled by `matColor`). Remove `const SETTINGS` for now (handled per-screen). Remove `const PROCESS_PRESETS` entry `chamber` (rename the non-unique id `proc-ecc-04-chamber` which conflicts — keep as-is, it's a unique id string, just ensure no JS object key collision).

- [ ] **Step 6: Write helpers test**

Create `frontend/src/data/helpers.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { fmtTime, fmtClock, matColor } from './helpers';

describe('fmtTime', () => {
  it('formats hours and minutes', () => expect(fmtTime(90)).toBe('1h 30m'));
  it('formats minutes only', () => expect(fmtTime(45)).toBe('45m'));
  it('formats hours only', () => expect(fmtTime(120)).toBe('2h'));
  it('handles null', () => expect(fmtTime(null)).toBe('—'));
});

describe('fmtClock', () => {
  it('zero pads', () => expect(fmtClock(65)).toBe('01:05'));
  it('handles null', () => expect(fmtClock(null)).toBe('--:--'));
});

describe('matColor', () => {
  it('returns blue for PLA', () => expect(matColor('PLA')).toBe('#60a5fa'));
  it('returns slate for PA-CF', () => expect(matColor('PA-CF')).toBe('#94a3b8'));
  it('returns cyan for PETG', () => expect(matColor('PETG')).toBe('#67e8f9'));
});
```

Run: `npm run test:run -- src/data/helpers.test.ts`
Expected: All pass.

- [ ] **Step 7: Write mock data smoke test**

Create `frontend/src/data/mock.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { PRINTERS, JOBS, ORDERS, FILAMENTS, FILES } from './mock';

describe('mock data', () => {
  it('has 3 printers', () => expect(PRINTERS).toHaveLength(3));
  it('has jobs with eligiblePrinters', () => {
    JOBS.forEach(j => expect(Array.isArray(j.eligiblePrinters)).toBe(true));
  });
  it('has orders with parts', () => {
    ORDERS.forEach(o => expect(o.parts.length).toBeGreaterThan(0));
  });
  it('has filaments with profiles', () => {
    FILAMENTS.forEach(f => expect(Array.isArray(f.profiles)).toBe(true));
  });
  it('has files with folders', () => {
    FILES.forEach(f => expect(typeof f.folder).toBe('string'));
  });
});
```

Run: `npm run test:run -- src/data/mock.test.ts`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/data/
git commit -m "feat(frontend): add TypeScript types, mock data, and helpers"
```

---

## Task 4: Shared UI Components

**Files:**
- Create: `frontend/src/components/icons.tsx`
- Create: `frontend/src/components/ui.tsx`
- Create: `frontend/src/components/icons.test.tsx`
- Create: `frontend/src/components/ui.test.tsx`

- [ ] **Step 1: Write failing icon test**

Create `frontend/src/components/icons.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Icons } from './icons';

describe('Icons', () => {
  it('renders queue icon as SVG', () => {
    const { container } = render(<>{Icons.queue}</>);
    expect(container.querySelector('svg')).toBeTruthy();
  });
  it('has all required icons', () => {
    const required = ['queue', 'fleet', 'printer', 'orders', 'files', 'settings', 'plus', 'x'];
    required.forEach(k => expect(Icons).toHaveProperty(k));
  });
});
```

Run: fails (no icons.tsx yet).

- [ ] **Step 2: Write `src/components/icons.tsx`**

Port `docs/design/themis/project/components.jsx` lines 8–57 (Icon component + Icons object):

```tsx
import React from 'react';

interface IconProps {
  d?: string;
  paths?: string[];
  children?: React.ReactNode;
  size?: number;
  stroke?: number;
  [key: string]: unknown;
}

export function Icon({ d, paths, children, size = 18, stroke = 1.6, ...rest }: IconProps) {
  return (
    <svg className="ico" width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth={stroke}
         strokeLinecap="round" strokeLinejoin="round" {...rest}>
      {d && <path d={d} />}
      {paths && paths.map((p, i) => <path key={i} d={p} />)}
      {children}
    </svg>
  );
}

export const Icons = {
  queue:    <Icon paths={["M3 6h14","M3 12h14","M3 18h10","M21 6l-2 2 2 2","M21 18l-2-2 2-2"]} />,
  fleet:    <Icon paths={["M4 4h7v7H4z","M13 4h7v7h-7z","M4 13h7v7H4z","M13 13h7v7h-7z"]} />,
  printer:  <Icon paths={["M6 9V3h12v6","M6 18H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2","M6 14h12v8H6z"]} />,
  orders:   <Icon paths={["M8 2v4","M16 2v4","M3 8h18","M5 6h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z","M9 14l2 2 4-4"]} />,
  files:    <Icon paths={["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z","M14 2v6h6","M9 13h6","M9 17h4"]} />,
  settings: <Icon paths={["M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z","M19.4 14.6l1.6.9-2 3.4-1.8-.6a8 8 0 0 1-1.6.9l-.3 1.9h-4l-.3-1.9a8 8 0 0 1-1.6-.9l-1.8.6-2-3.4 1.6-.9a8 8 0 0 1 0-1.8L4.6 12l2-3.4 1.8.6a8 8 0 0 1 1.6-.9l.3-1.9h4l.3 1.9c.6.2 1.1.5 1.6.9l1.8-.6 2 3.4-1.6.9c.1.6.1 1.2 0 1.8z"]} />,
  search:   <Icon paths={["M21 21l-4.3-4.3","M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z"]} />,
  plus:     <Icon paths={["M12 5v14","M5 12h14"]} />,
  play:     <Icon paths={["M6 4l14 8-14 8V4z"]} />,
  pause:    <Icon paths={["M7 4h3v16H7z","M14 4h3v16h-3z"]} />,
  stop:     <Icon paths={["M5 5h14v14H5z"]} />,
  alert:    <Icon paths={["M12 9v4","M12 17h.01","M10.3 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"]} />,
  check:    <Icon paths={["M20 6 9 17l-5-5"]} />,
  x:        <Icon paths={["M18 6 6 18","M6 6l12 12"]} />,
  chevR:    <Icon paths={["M9 6l6 6-6 6"]} />,
  chevD:    <Icon paths={["M6 9l6 6 6-6"]} />,
  chevU:    <Icon paths={["M6 15l6-6 6 6"]} />,
  chevL:    <Icon paths={["M15 6l-6 6 6 6"]} />,
  drag:     <Icon paths={["M9 5h.01","M15 5h.01","M9 12h.01","M15 12h.01","M9 19h.01","M15 19h.01"]} stroke={3} />,
  more:     <Icon paths={["M12 12h.01","M19 12h.01","M5 12h.01"]} stroke={3} />,
  clock:    <Icon paths={["M12 6v6l4 2","M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z"]} />,
  layers:   <Icon paths={["M12 2 2 7l10 5 10-5-10-5z","M2 17l10 5 10-5","M2 12l10 5 10-5"]} />,
  filter:   <Icon paths={["M22 3H2l8 9.5V19l4 2v-8.5L22 3z"]} />,
  sort:     <Icon paths={["M3 6h13","M3 12h9","M3 18h5","M17 9l3-3 3 3","M20 6v12","M17 15l3 3 3-3"]} />,
  link:     <Icon paths={["M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7","M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"]} />,
  thermo:   <Icon paths={["M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4 4 0 1 0 5 0z"]} />,
  spool:    <Icon paths={["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z","M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12z","M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"]} />,
  camera:   <Icon paths={["M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2v11z","M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"]} />,
  refresh:  <Icon paths={["M21 12a9 9 0 0 1-15.36 6.36L3 16","M3 12a9 9 0 0 1 15.36-6.36L21 8","M21 3v5h-5","M3 21v-5h5"]} />,
  bell:     <Icon paths={["M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9","M13.7 21a2 2 0 0 1-3.4 0"]} />,
  external: <Icon paths={["M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6","M15 3h6v6","M10 14L21 3"]} />,
  arrowR:   <Icon paths={["M5 12h14","M12 5l7 7-7 7"]} />,
  user:     <Icon paths={["M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2","M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"]} />,
  upload:   <Icon paths={["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4","M17 8l-5-5-5 5","M12 3v12"]} />,
  copy:     <Icon paths={["M9 9h11a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-1","M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"]} />,
  trash:    <Icon paths={["M3 6h18","M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2","M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6","M10 11v6","M14 11v6"]} />,
  panel:    <Icon paths={["M3 3h18v18H3z","M9 3v18"]} />,
} as const;

export type IconKey = keyof typeof Icons;
```

- [ ] **Step 3: Run icon test**

```bash
npm run test:run -- src/components/icons.test.tsx
```

Expected: Both pass.

- [ ] **Step 4: Write failing UI component tests**

Create `frontend/src/components/ui.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusPill, Progress, Swatch, MaterialChip, Card, SectionHeader, Empty, Kv } from './ui';
import { Icons } from './icons';

describe('StatusPill', () => {
  it('renders printing status', () => {
    render(<StatusPill status="printing" />);
    expect(screen.getByText('Printing')).toBeTruthy();
  });
  it('renders custom label', () => {
    render(<StatusPill status="queued" label="Custom" />);
    expect(screen.getByText('Custom')).toBeTruthy();
  });
});

describe('Progress', () => {
  it('renders without crashing', () => {
    const { container } = render(<Progress value={50} />);
    expect(container.querySelector('.progress')).toBeTruthy();
  });
});

describe('Kv', () => {
  it('renders key and value', () => {
    render(<Kv k="Due" v={<span>2026-05-28</span>} />);
    expect(screen.getByText('DUE')).toBeTruthy();
    expect(screen.getByText('2026-05-28')).toBeTruthy();
  });
});

describe('Card', () => {
  it('renders children', () => {
    render(<Card>hello</Card>);
    expect(screen.getByText('hello')).toBeTruthy();
  });
});

describe('Empty', () => {
  it('renders title and sub', () => {
    render(<Empty title="No files" sub="Try again" icon={Icons.files} />);
    expect(screen.getByText('No files')).toBeTruthy();
    expect(screen.getByText('Try again')).toBeTruthy();
  });
});
```

Run: fails (no ui.tsx yet).

- [ ] **Step 5: Write `src/components/ui.tsx`**

Port `docs/design/themis/project/components.jsx` lines 59–247, converting to TypeScript. Key additions:

1. Add `Kv` component (used in screen-orders.jsx but not in components.jsx):

```tsx
export function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="col">
      <div className="tag-key">{k.toUpperCase()}</div>
      <div style={{ marginTop: 4 }}>{v}</div>
    </div>
  );
}
```

2. Import from local data files instead of `window.xxx`:

```tsx
import { PRINTERS } from '../data/mock';
import type { StatusKey } from '../data/types';
```

3. `StatusPill` uses `StatusKey` type for `status` prop.

4. `PrinterBadge` and `EligibilityChips` use imported `PRINTERS` instead of `window.PRINTERS`.

5. Export all components.

Full file structure:

```tsx
import React from 'react';
import { Icons } from './icons';
import { PRINTERS } from '../data/mock';
import type { StatusKey } from '../data/types';
import { fmtClock } from '../data/helpers';

export function StatusPill({ status, label }: { status: StatusKey; label?: string }) {
  const map: Record<string, [string, string]> = {
    printing:    ['info',   'Printing'],
    queued:      ['idle',   'Queued'],
    waiting:     ['idle',   'Waiting'],
    claiming:    ['accent', 'Claiming'],
    slicing:     ['accent', 'Slicing'],
    paused:      ['warn',   'Paused'],
    error:       ['err',    'Error'],
    offline:     ['idle',   'Offline'],
    idle:        ['ok',     'Idle'],
    ready:       ['ok',     'Ready'],
    complete:    ['ok',     'Complete'],
    hold:        ['warn',   'On hold'],
    in_progress: ['info',   'In progress'],
    partial:     ['warn',   'Partial'],
  };
  const [cls, txt] = map[status] ?? ['idle', status];
  return <span className={`pill ${cls}`}><span className="dot" />{label ?? txt}</span>;
}

export function Progress({ value = 0, tone, large }: { value?: number; tone?: string; large?: boolean }) {
  const cls = tone === 'warn' ? 'warn' : tone === 'err' ? 'err' : '';
  return (
    <div className={`progress ${cls} ${large ? 'lg' : ''}`}>
      <div className="bar" style={{ '--p': `${Math.max(0, Math.min(100, value))}%` } as React.CSSProperties} />
    </div>
  );
}

export function Swatch({ color, large }: { color: string; large?: boolean }) {
  return <span className={`swatch ${large ? 'lg' : ''}`} style={{ '--c': color } as React.CSSProperties} />;
}

export function MaterialChip({ material, color }: { material: string; color: string }) {
  return (
    <span className="row gap-2" style={{ fontSize: 12 }}>
      <Swatch color={color} />
      <span>{material}</span>
    </span>
  );
}

export function VideoTile({ live = true, status, time }: { live?: boolean; status?: StatusKey; time?: number }) {
  return (
    <div className={`video ${live ? 'live' : ''}`}>
      <div className="feed-scene" />
      <div className="feed-noise" />
      {time != null && <div className="feed-time mono">{fmtClock(time)}</div>}
      {status && (
        <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 3 }}>
          <StatusPill status={status} />
        </div>
      )}
    </div>
  );
}

export function Card({ children, className = '', ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`card ${className}`} {...rest}>{children}</div>;
}

export function PrinterBadge({ printerId }: { printerId: string }) {
  const p = PRINTERS.find(pr => pr.id === printerId);
  if (!p) return null;
  return <span className="elig on" title={p.name}>{p.badge}</span>;
}

export function EligibilityChips({ ids }: { ids: string[] }) {
  return (
    <span className="row gap-2" style={{ flexWrap: 'wrap' }}>
      {PRINTERS.map(p => (
        <span key={p.id} className={`elig ${ids.includes(p.id) ? 'on' : 'off'}`} title={p.name}>
          {p.badge}
        </span>
      ))}
    </span>
  );
}

export function SectionHeader({ title, sub, actions }: { title: React.ReactNode; sub?: string; actions?: React.ReactNode }) {
  return (
    <div className="row between" style={{ marginBottom: 14 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 600, letterSpacing: '-0.01em' }}>{title}</h2>
        {sub && <div className="muted small" style={{ marginTop: 2 }}>{sub}</div>}
      </div>
      {actions && <div className="row gap-2">{actions}</div>}
    </div>
  );
}

export function Empty({ title, sub, icon }: { title: string; sub?: string; icon: React.ReactNode }) {
  return (
    <div className="col" style={{ alignItems: 'center', padding: '60px 20px', color: 'var(--text-3)' }}>
      <div style={{ width: 44, height: 44, borderRadius: 12, background: 'var(--bg-3)',
                    display: 'grid', placeItems: 'center', marginBottom: 12, color: 'var(--text-3)' }}>
        {icon}
      </div>
      <div style={{ fontSize: 14, color: 'var(--text-2)', fontWeight: 500 }}>{title}</div>
      {sub && <div className="small muted" style={{ marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

export function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="col">
      <div className="tag-key">{k.toUpperCase()}</div>
      <div style={{ marginTop: 4 }}>{v}</div>
    </div>
  );
}
```

- [ ] **Step 6: Run UI tests**

```bash
npm run test:run -- src/components/ui.test.tsx
```

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/icons.tsx frontend/src/components/ui.tsx \
        frontend/src/components/icons.test.tsx frontend/src/components/ui.test.tsx
git commit -m "feat(frontend): add shared UI components and icons"
```

---

## Task 5: App Shell (Sidebar + Topbar + Routing)

**Files:**
- Create: `frontend/src/components/Sidebar.tsx`
- Create: `frontend/src/components/Topbar.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/components/Sidebar.test.tsx`

- [ ] **Step 1: Write failing sidebar test**

Create `frontend/src/components/Sidebar.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from './Sidebar';

describe('Sidebar', () => {
  it('renders nav items', () => {
    render(
      <MemoryRouter initialEntries={['/queue']}>
        <Sidebar queueCount={3} ordersOpen={2} />
      </MemoryRouter>
    );
    expect(screen.getByText('Job queue')).toBeTruthy();
    expect(screen.getByText('Fleet')).toBeTruthy();
    expect(screen.getByText('Orders')).toBeTruthy();
  });
  it('shows queue count badge', () => {
    render(
      <MemoryRouter initialEntries={['/queue']}>
        <Sidebar queueCount={5} ordersOpen={0} />
      </MemoryRouter>
    );
    expect(screen.getByText('5')).toBeTruthy();
  });
});
```

Run: fails.

- [ ] **Step 2: Write `src/components/Sidebar.tsx`**

Port `docs/design/themis/project/components.jsx` lines 193–241 to TypeScript React Router:

```tsx
import React from 'react';
import { NavLink } from 'react-router-dom';
import { Icons } from './icons';

interface SidebarProps {
  queueCount: number;
  ordersOpen: number;
}

export function Sidebar({ queueCount, ordersOpen }: SidebarProps) {
  const items = [
    { to: '/queue',     label: 'Job queue',   icon: Icons.queue,    count: queueCount },
    { to: '/fleet',     label: 'Fleet',       icon: Icons.fleet },
    { to: '/orders',    label: 'Orders',      icon: Icons.orders,   count: ordersOpen },
    { to: '/files',     label: 'Files',       icon: Icons.files },
    { to: '/filaments', label: 'Filaments',   icon: Icons.spool },
    { to: '/printers',  label: 'Printers',    icon: Icons.printer },
  ];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" />
        <div className="brand-name">themis<span className="dim">.farm</span></div>
      </div>

      <div className="nav-section">
        <div className="nav-section-label">Workshop</div>
        {items.map(it => (
          <NavLink key={it.to} to={it.to}
                   className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            {it.icon}
            <span className="label">{it.label}</span>
            {it.count != null && it.count > 0 && <span className="count num">{it.count}</span>}
          </NavLink>
        ))}
      </div>

      <div className="nav-section">
        <div className="nav-section-label">Account</div>
        <NavLink to="/settings"
                 className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          {Icons.settings}
          <span className="label">Settings</span>
        </NavLink>
      </div>

      <div className="footer">
        <div className="user-chip">
          <div className="avatar">LR</div>
          <div className="user-meta">
            <div className="name">Lev Romero</div>
            <div className="sub">Workshop · 3 printers</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
```

- [ ] **Step 3: Write `src/components/Topbar.tsx`**

```tsx
import React from 'react';
import { Icons } from './icons';

interface TopbarProps {
  title: string;
  crumbs?: string[];
  actions?: React.ReactNode;
  search?: boolean;
}

export function Topbar({ title, crumbs = [], actions, search = true }: TopbarProps) {
  return (
    <div className="topbar">
      <div className="row gap-2" style={{ alignItems: 'center' }}>
        {crumbs.map((c, i) => <span key={i} className="crumb">{c}</span>)}
        <h1>{title}</h1>
      </div>
      <div className="spacer" />
      {search && (
        <div className="search">
          {React.cloneElement(Icons.search, { size: 14 })}
          <input placeholder="Search jobs, orders, parts…" />
          <span className="kbd">⌘K</span>
        </div>
      )}
      {actions}
      <button className="btn icon ghost" title="Notifications">{Icons.bell}</button>
    </div>
  );
}
```

- [ ] **Step 4: Rewrite `src/App.tsx`**

```tsx
import React, { useMemo } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { Topbar } from './components/Topbar';
import { Icons } from './components/icons';
import { JOBS, ORDERS } from './data/mock';

// Lazy-loaded screens (stub)
import { QueueScreen }     from './screens/QueueScreen';
import { FleetScreen }     from './screens/FleetScreen';
import { OrdersScreen }    from './screens/OrdersScreen';
import { NewJobScreen }    from './screens/NewJobScreen';
import { NewOrderScreen }  from './screens/NewOrderScreen';
import { FilesScreen }     from './screens/FilesScreen';
import { FilamentsScreen } from './screens/FilamentsScreen';
import { PrintersScreen }  from './screens/PrintersScreen';
import { SettingsScreen }  from './screens/SettingsScreen';

function AppShell() {
  const queueCount = useMemo(() => JOBS.filter(j => j.status === 'queued').length, []);
  const ordersOpen = useMemo(() => ORDERS.filter(o => o.status !== 'complete').length, []);
  const location = useLocation();

  // Map path → topbar config
  const screenConfig: Record<string, { title: string; crumbs: string[]; actions?: React.ReactNode }> = {
    '/queue':     { title: 'Job queue',     crumbs: ['Workshop'],
                    actions: <><button className="btn sm">{Icons.refresh} Resync</button>
                               <button className="btn primary sm" onClick={() => void 0}>{Icons.plus} New job</button></> },
    '/queue/new': { title: 'New job',       crumbs: ['Workshop', 'Job queue'] },
    '/fleet':     { title: 'Fleet',         crumbs: ['Workshop'],
                    actions: <button className="btn sm">{Icons.refresh} Resync</button> },
    '/orders':    { title: 'Orders',        crumbs: ['Workshop'],
                    actions: <button className="btn primary sm">{Icons.plus} New order</button> },
    '/orders/new':{ title: 'New order',     crumbs: ['Workshop', 'Orders'] },
    '/files':     { title: 'Model library', crumbs: ['Workshop'],
                    actions: <button className="btn primary sm">{Icons.upload} Upload</button> },
    '/filaments': { title: 'Filament library', crumbs: ['Workshop'],
                    actions: <><button className="btn sm">{Icons.refresh} Sync vendor prices</button>
                               <button className="btn primary sm">{Icons.plus} Add filament</button></> },
    '/printers':  { title: 'Printers',      crumbs: ['Workshop'] },
    '/settings':  { title: 'Settings',      crumbs: [] },
  };

  const path = '/' + location.pathname.split('/').filter(Boolean).slice(0, 2).join('/');
  const cfg = screenConfig[path] ?? screenConfig['/queue'];

  return (
    <div className="app" data-nav="expanded">
      <Sidebar queueCount={queueCount} ordersOpen={ordersOpen} />
      <div className="main">
        <Topbar title={cfg.title} crumbs={cfg.crumbs} actions={cfg.actions} />
        <div className="content" data-density="balanced">
          <Routes>
            <Route path="/"             element={<Navigate to="/queue" replace />} />
            <Route path="/queue"        element={<QueueScreen />} />
            <Route path="/queue/new"    element={<NewJobScreen />} />
            <Route path="/fleet"        element={<FleetScreen />} />
            <Route path="/orders"       element={<OrdersScreen />} />
            <Route path="/orders/new"   element={<NewOrderScreen />} />
            <Route path="/files"        element={<FilesScreen />} />
            <Route path="/filaments"    element={<FilamentsScreen />} />
            <Route path="/printers"     element={<PrintersScreen />} />
            <Route path="/settings/*"   element={<SettingsScreen />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
```

- [ ] **Step 5: Create stub screen files**

Before tests can pass, create minimal stub exports for all 9 screens so the App compiles:

```bash
for screen in QueueScreen FleetScreen OrdersScreen NewJobScreen NewOrderScreen FilesScreen FilamentsScreen PrintersScreen SettingsScreen; do
  echo "import React from 'react'; export function ${screen}() { return <div className=\"col\" style={{padding:24}}><div className=\"muted\">${screen} — stub</div></div>; }" > src/screens/${screen}.tsx
done
```

On Windows PowerShell:
```powershell
$screens = 'QueueScreen','FleetScreen','OrdersScreen','NewJobScreen','NewOrderScreen','FilesScreen','FilamentsScreen','PrintersScreen','SettingsScreen'
foreach ($s in $screens) {
  "import React from 'react'; export function ${s}() { return React.createElement('div', {className:'col', style:{padding:24}}, React.createElement('div', {className:'muted'}, '${s} stub')); }" | Out-File -Encoding utf8 "src/screens/${s}.tsx"
}
```

- [ ] **Step 6: Run sidebar test**

```bash
npm run test:run -- src/components/Sidebar.test.tsx
```

Expected: Both pass.

- [ ] **Step 7: Verify app renders in browser**

```bash
npm run dev
```

Open http://localhost:5173 — should show the app shell with sidebar, topbar, and "stub" content. All nav links should work. Kill dev server.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/Topbar.tsx \
        frontend/src/components/Sidebar.test.tsx frontend/src/App.tsx \
        frontend/src/screens/
git commit -m "feat(frontend): add app shell with router, sidebar, topbar, and screen stubs"
```

---

## Task 6: Queue Screen

**Files:**
- Modify: `frontend/src/screens/QueueScreen.tsx`
- Create: `frontend/src/screens/QueueScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-queue.jsx`

- [ ] **Step 1: Write failing queue screen test**

Create `frontend/src/screens/QueueScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueueScreen } from './QueueScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

describe('QueueScreen', () => {
  it('renders summary stats', () => {
    render(<QueueScreen />, { wrapper });
    expect(screen.getByText(/Active/i)).toBeTruthy();
    expect(screen.getByText(/Queued/i)).toBeTruthy();
  });
  it('shows job cards', () => {
    render(<QueueScreen />, { wrapper });
    // Printing jobs appear
    expect(screen.getAllByText(/Printing/i).length).toBeGreaterThan(0);
  });
  it('filter chips are clickable', async () => {
    const user = userEvent.setup();
    render(<QueueScreen />, { wrapper });
    const queuedBtn = screen.getByRole('button', { name: /queued/i });
    await user.click(queuedBtn);
    // After clicking queued filter, only queued jobs shown
    expect(screen.queryByText('Active')).toBeNull();
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `QueueScreen.tsx`**

Port `docs/design/themis/project/screen-queue.jsx` to TypeScript.

Key changes from the design:
- Replace `window.JOBS`, `window.PRINTERS`, etc. with imports from `../data/mock`
- Replace `window.fmtTime`, `window.fmtClock` with imports from `../data/helpers`
- Replace `window.StatusPill`, `window.Progress`, etc. with imports from `../components/ui`
- Replace `window.Icons` with imports from `../components/icons`
- Replace `window.EligibilityChips`, `window.MaterialChip`, `window.SectionHeader`, `window.VideoTile`, `window.Empty` with imports from `../components/ui`
- Remove all `window.xxx` and `Object.assign(window, ...)` calls
- Add TypeScript types for state and props

The screen consists of:
1. Summary stats row (Active/Queued/Done/Est. remaining counts from JOBS array)
2. Filter chips: All / Active / Queued / Done (filter JOBS by status)
3. Job card variants: `compact`, `rich`, `strip` — render based on `queueCard` prop (default `rich`)
4. Selected job detail panel: slide-in right panel showing job parts + eligible printers + actions

For the stub: card variant selector uses local state (default `rich`). Detail panel opens on card click.

The `useNavigate` hook from react-router-dom is available if "New job" button is used.

- [ ] **Step 3: Run queue test**

```bash
npm run test:run -- src/screens/QueueScreen.test.tsx
```

Expected: All pass.

- [ ] **Step 4: Verify in browser**

```bash
npm run dev
```

Navigate to `/queue`. Verify: stats row visible, job cards rendered, filter chips change the list, clicking a card opens the detail panel. Kill dev server.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/QueueScreen.tsx frontend/src/screens/QueueScreen.test.tsx
git commit -m "feat(frontend): implement queue screen stub"
```

---

## Task 7: Fleet Screen

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx`
- Create: `frontend/src/screens/FleetScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-fleet.jsx`

- [ ] **Step 1: Write failing fleet test**

Create `frontend/src/screens/FleetScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { FleetScreen } from './FleetScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('FleetScreen', () => {
  it('renders printer tiles', () => {
    render(<FleetScreen />, { wrapper });
    // All 3 printers should appear
    expect(screen.getAllByText(/Atlas|Forge|Iris/).length).toBeGreaterThan(0);
  });
  it('layout toggle works', async () => {
    const user = userEvent.setup();
    render(<FleetScreen />, { wrapper });
    const listBtn = screen.getByTitle(/list/i);
    await user.click(listBtn);
    // Table row visible after switching to list layout
    expect(screen.getByRole('table')).toBeTruthy();
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `FleetScreen.tsx`**

Port `docs/design/themis/project/screen-fleet.jsx` to TypeScript.

Key changes:
- Import data + helpers + components from local modules
- `layout` state: `'grid' | 'list' | 'focus'` (default `grid`)
- `expanded` state: printer id or null (which card is expanded)
- Grid layout: renders `PrinterTile` per printer; click to expand `PrinterExpandedCard`
- List layout: renders `FleetList` (table rows)
- Focus layout: renders `FleetFocus` (hero + strip rail)
- `VideoTile` renders the animated placeholder (no real camera)
- Camera section in expanded card shows `VideoTile` with a caption: "Live camera — stub, see GitHub issue #xx"

For the layout toggle icons: use `Icons.fleet` (grid), `Icons.filter` (list), `Icons.panel` (focus) — or add a note that these are placeholder icon choices.

- [ ] **Step 3: Run fleet test**

```bash
npm run test:run -- src/screens/FleetScreen.test.tsx
```

Expected: Both pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/screens/FleetScreen.tsx frontend/src/screens/FleetScreen.test.tsx
git commit -m "feat(frontend): implement fleet screen stub"
```

---

## Task 8: Orders Screen

**Files:**
- Modify: `frontend/src/screens/OrdersScreen.tsx`
- Create: `frontend/src/screens/OrdersScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-orders.jsx`

- [ ] **Step 1: Write failing orders test**

Create `frontend/src/screens/OrdersScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { OrdersScreen } from './OrdersScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('OrdersScreen', () => {
  it('renders order list', () => {
    render(<OrdersScreen />, { wrapper });
    expect(screen.getByText('Vela Robotics')).toBeTruthy();
  });
  it('filter chips filter orders', async () => {
    const user = userEvent.setup();
    render(<OrdersScreen />, { wrapper });
    const internalBtn = screen.getByRole('button', { name: /internal/i });
    await user.click(internalBtn);
    expect(screen.queryByText('Vela Robotics')).toBeNull();
  });
  it('expanding an accordion shows parts table', async () => {
    const user = userEvent.setup();
    render(<OrdersScreen />, { wrapper });
    const firstAccordion = screen.getAllByRole('button').find(b => b.textContent?.includes('ORD-'));
    if (firstAccordion) {
      await user.click(firstAccordion);
      expect(screen.getByText(/parts breakdown/i)).toBeTruthy();
    }
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `OrdersScreen.tsx`**

Port `docs/design/themis/project/screen-orders.jsx` to TypeScript.

Key changes:
- `matColor` imported from `../data/helpers`
- `Kv` imported from `../components/ui`
- The `OrderDetail` component (list+detail layout) is also in this file — include it
- `window.JOBS` → imported `JOBS`

The screen has two sub-components also exported in the design file:
- `OrderRow` — compact card used in the side-by-side list+detail layout
- `OrderDetail` — full detail panel

Include all of these in `OrdersScreen.tsx` as non-exported (local) components.

- [ ] **Step 3: Run orders test + commit**

```bash
npm run test:run -- src/screens/OrdersScreen.test.tsx
git add frontend/src/screens/OrdersScreen.tsx frontend/src/screens/OrdersScreen.test.tsx
git commit -m "feat(frontend): implement orders screen stub"
```

---

## Task 9: New Job Screen

**Files:**
- Modify: `frontend/src/screens/NewJobScreen.tsx`
- Create: `frontend/src/screens/NewJobScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-new-job.jsx`

- [ ] **Step 1: Write failing new-job test**

Create `frontend/src/screens/NewJobScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewJobScreen } from './NewJobScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('NewJobScreen', () => {
  it('renders dropzone', () => {
    render(<NewJobScreen />, { wrapper });
    expect(screen.getByText(/Drop a .3mf or .stl/i)).toBeTruthy();
  });
  it('shows "Add jobs" button disabled before file', () => {
    render(<NewJobScreen />, { wrapper });
    const btn = screen.getByRole('button', { name: /add.*job/i });
    expect(btn).toBeDisabled();
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `NewJobScreen.tsx`**

Port `docs/design/themis/project/screen-new-job.jsx` to TypeScript.

Key changes:
- `onCancel` and `onCreate` props use `useNavigate` internally (navigate to `/queue`)
- `PROCESS_PRESETS`, `FILAMENTS`, `PRINTERS`, `ORDERS` from `../data/mock`
- File input: accepts `.3mf` and `.stl`; when dropped, calls `readPlatesFromFilename()` mock
- `readPlatesFromFilename` is the same mock logic from the design — port it verbatim
- `eligibleForMaterial` logic ported verbatim
- No actual file upload happens (stub)
- `onCreate` logs the job config to console and navigates to `/queue`

The `darken(hex)` helper can go into `helpers.ts` as an export.

- [ ] **Step 3: Run new-job test + commit**

```bash
npm run test:run -- src/screens/NewJobScreen.test.tsx
git add frontend/src/screens/NewJobScreen.tsx frontend/src/screens/NewJobScreen.test.tsx
git commit -m "feat(frontend): implement new-job screen stub"
```

---

## Task 10: New Order Screen

**Files:**
- Modify: `frontend/src/screens/NewOrderScreen.tsx`
- Create: `frontend/src/screens/NewOrderScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-new-order.jsx`

- [ ] **Step 1: Write failing new-order test**

Create `frontend/src/screens/NewOrderScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewOrderScreen } from './NewOrderScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('NewOrderScreen', () => {
  it('renders order type selector', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.getByText('Customer order')).toBeTruthy();
    expect(screen.getByText('Internal project')).toBeTruthy();
  });
  it('renders parts table', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.getByText('Arm bracket — L')).toBeTruthy();
  });
  it('add row button works', async () => {
    const user = userEvent.setup();
    render(<NewOrderScreen />, { wrapper });
    const rowsBefore = screen.getAllByRole('row').length;
    await user.click(screen.getAllByRole('button', { name: /add part|add row/i })[0]);
    expect(screen.getAllByRole('row').length).toBeGreaterThan(rowsBefore);
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `NewOrderScreen.tsx`**

Port `docs/design/themis/project/screen-new-order.jsx` to TypeScript.

Key changes:
- `onCancel` uses `useNavigate` to go to `/orders`
- "Create order & queue jobs" button: stub — logs form state, navigates to `/orders`
- `matColor` and `EligibilityChips` imported from local modules
- `fmtTime` from `../data/helpers`

- [ ] **Step 3: Run new-order test + commit**

```bash
npm run test:run -- src/screens/NewOrderScreen.test.tsx
git add frontend/src/screens/NewOrderScreen.tsx frontend/src/screens/NewOrderScreen.test.tsx
git commit -m "feat(frontend): implement new-order screen stub"
```

---

## Task 11: Files Screen

**Files:**
- Modify: `frontend/src/screens/FilesScreen.tsx`
- Create: `frontend/src/screens/FilesScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-files-printers.jsx` (lines 1–438)

- [ ] **Step 1: Write failing files test**

Create `frontend/src/screens/FilesScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { FilesScreen } from './FilesScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('FilesScreen', () => {
  it('renders file grid', () => {
    render(<FilesScreen />, { wrapper });
    expect(screen.getByText('vr_arm_bracket_L.3mf')).toBeTruthy();
  });
  it('renders folder tree', () => {
    render(<FilesScreen />, { wrapper });
    expect(screen.getByText('All files')).toBeTruthy();
  });
  it('tag filter reduces file count', async () => {
    const user = userEvent.setup();
    render(<FilesScreen />, { wrapper });
    const paButton = screen.getByRole('button', { name: /PA-CF/i });
    await user.click(paButton);
    // PA-CF tag should filter down the list
    expect(screen.queryByText('northbeam_cradle.3mf')).toBeNull();
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `FilesScreen.tsx`**

Port the `FilesScreen` section from `docs/design/themis/project/screen-files-printers.jsx` (lines 9–438) to TypeScript.

Key changes:
- `FILES` from `../data/mock`
- `shade()` helper — add to `helpers.ts` as an export
- `buildFolderTree()` — keep as a local function

- [ ] **Step 3: Run files test + commit**

```bash
npm run test:run -- src/screens/FilesScreen.test.tsx
git add frontend/src/screens/FilesScreen.tsx frontend/src/screens/FilesScreen.test.tsx
git commit -m "feat(frontend): implement files screen stub"
```

---

## Task 12: Filaments Screen

**Files:**
- Modify: `frontend/src/screens/FilamentsScreen.tsx`
- Create: `frontend/src/screens/FilamentsScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-filaments.jsx`

- [ ] **Step 1: Write failing filaments test**

Create `frontend/src/screens/FilamentsScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { FilamentsScreen } from './FilamentsScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('FilamentsScreen', () => {
  it('renders summary stats', () => {
    render(<FilamentsScreen />, { wrapper });
    expect(screen.getByText('Spools tracked')).toBeTruthy();
  });
  it('renders filament rows', () => {
    render(<FilamentsScreen />, { wrapper });
    expect(screen.getByText('PolyTerra Charcoal')).toBeTruthy();
  });
  it('type filter works', async () => {
    const user = userEvent.setup();
    render(<FilamentsScreen />, { wrapper });
    const petgBtn = screen.getByRole('button', { name: /PETG/i });
    await user.click(petgBtn);
    expect(screen.queryByText('NinjaFlex Signal Red')).toBeNull();
  });
  it('clicking a row opens detail panel', async () => {
    const user = userEvent.setup();
    render(<FilamentsScreen />, { wrapper });
    const firstRow = screen.getByText('PolyTerra Charcoal').closest('.card');
    if (firstRow) await user.click(firstRow);
    expect(screen.getByText('Print profiles')).toBeTruthy();
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `FilamentsScreen.tsx`**

Port `docs/design/themis/project/screen-filaments.jsx` to TypeScript.

Key changes:
- `FILAMENTS`, `PRINTERS`, `getPrinter` from `../data/mock`
- `matTypeBg`, `matTypeFg`, `matTypeBorder` from `../data/helpers`
- `SpoolSwatch` component: port the radial-gradient spool design verbatim
- `FavoriteToggle` component: local state for favorites (stub — does not persist)
- Purchase links: render as `<a>` tags (they open real vendor URLs, which is fine)

- [ ] **Step 3: Run filaments test + commit**

```bash
npm run test:run -- src/screens/FilamentsScreen.test.tsx
git add frontend/src/screens/FilamentsScreen.tsx frontend/src/screens/FilamentsScreen.test.tsx
git commit -m "feat(frontend): implement filaments screen stub"
```

---

## Task 13: Printers Screen

**Files:**
- Modify: `frontend/src/screens/PrintersScreen.tsx`
- Create: `frontend/src/screens/PrintersScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-files-printers.jsx` (lines 440–692)

- [ ] **Step 1: Write failing printers test**

Create `frontend/src/screens/PrintersScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PrintersScreen } from './PrintersScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('PrintersScreen', () => {
  it('renders printer table', () => {
    render(<PrintersScreen />, { wrapper });
    expect(screen.getByText('Atlas')).toBeTruthy();
    expect(screen.getByText('Forge')).toBeTruthy();
  });
  it('shows Add printer button', () => {
    render(<PrintersScreen />, { wrapper });
    expect(screen.getByRole('button', { name: /add printer/i })).toBeTruthy();
  });
  it('clicking Add printer shows wizard step 1', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    expect(screen.getByText('Pick a printer model')).toBeTruthy();
  });
  it('wizard advances to step 2', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    await user.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText(/How should we talk/i)).toBeTruthy();
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `PrintersScreen.tsx`**

Port the `PrintersScreen` + `PrinterAddForm` sections from `docs/design/themis/project/screen-files-printers.jsx` (lines 440–692).

Key changes:
- `PRINTERS` from `../data/mock`
- "Finish" button in step 3 is a stub — logs form state, returns to printer list
- "Test connection" button in step 2 is a stub — shows a success status after 1 second (no real network call)

- [ ] **Step 3: Run printers test + commit**

```bash
npm run test:run -- src/screens/PrintersScreen.test.tsx
git add frontend/src/screens/PrintersScreen.tsx frontend/src/screens/PrintersScreen.test.tsx
git commit -m "feat(frontend): implement printers screen stub"
```

---

## Task 14: Settings Screen

**Files:**
- Modify: `frontend/src/screens/SettingsScreen.tsx`
- Create: `frontend/src/screens/SettingsScreen.test.tsx`

**Reference:** `docs/design/themis/project/screen-settings.jsx`

- [ ] **Step 1: Write failing settings test**

Create `frontend/src/screens/SettingsScreen.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('SettingsScreen', () => {
  it('renders General page by default', () => {
    render(<SettingsScreen />, { wrapper });
    expect(screen.getByText('Workshop name')).toBeTruthy();
  });
  it('nav items are all visible', () => {
    render(<SettingsScreen />, { wrapper });
    expect(screen.getByText('Tags')).toBeTruthy();
    expect(screen.getByText('Notifications')).toBeTruthy();
    expect(screen.getByText('Spoolman')).toBeTruthy();
  });
  it('clicking Notifications nav item shows notifications page', async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /notifications/i }));
    expect(screen.getByText('Job completed')).toBeTruthy();
  });
});
```

Run: fails.

- [ ] **Step 2: Implement `SettingsScreen.tsx`**

Port `docs/design/themis/project/screen-settings.jsx` to TypeScript.

Key changes:
- Settings state is local React state (no persistence to backend — stub)
- All `window.SETTINGS.*` replaced with `useState` initialized from inline defaults matching the design's `SETTINGS` constant
- All 7 sub-pages: General, Tags, Print defaults, Spoolman, Notifications, Data & backup, About
- Tags page: mutates local TAGS array (copy it in from mock data into local state)
- Spoolman: "Test connection" is a simulated stub (setTimeout for 1.1s, then sets connected state)
- Data & backup: export/import buttons are stubs (log to console)
- `SettingsIcons` defined locally in the file (they're unique to this screen)
- `Toggle`, `Segmented`, `FieldRow`, `PageHeader` as local component functions in this file
- Sub-page navigation: use URL hash or local state — use local state (not React Router) to keep settings self-contained

- [ ] **Step 3: Run settings test + commit**

```bash
npm run test:run -- src/screens/SettingsScreen.test.tsx
git add frontend/src/screens/SettingsScreen.tsx frontend/src/screens/SettingsScreen.test.tsx
git commit -m "feat(frontend): implement settings screen stub"
```

---

## Task 15: Run Full Test Suite + Final Verification

**Files:** No new files.

- [ ] **Step 1: Run all tests**

```bash
cd frontend && npm run test:run
```

Expected: All tests pass. Note any failures and fix them before proceeding.

- [ ] **Step 2: Build for production**

```bash
npm run build
```

Expected: Build completes with no errors. Output in `frontend/dist/`.

- [ ] **Step 3: Start dev server and do a full walkthrough**

```bash
npm run dev
```

Verify each route:
- `/queue` — stats, job cards, filter chips, detail panel on click
- `/fleet` — printer tiles, expand one, layout toggle
- `/orders` — accordion list, expand one, parts table
- `/queue/new` — dropzone, file drop simulation (drag a real 3mf from Finder/Explorer)
- `/orders/new` — form fields, add/remove parts, suggested plates
- `/files` — folder tree, file grid, tag filter
- `/filaments` — stats, filter, detail panel
- `/printers` — table, add wizard 3 steps
- `/settings` — all 7 sub-pages

Kill dev server after walkthrough.

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix(frontend): address issues from full UI walkthrough"
```

---

## Task 16: Create GitHub Enhancement Issues

**Files:** None (uses gh CLI).

Create GitHub issues for all functional wiring work captured during stub implementation. Each issue links to the relevant design file(s) in `docs/design/themis/project/`.

- [ ] **Step 1: Verify gh CLI and repo**

```bash
gh auth status
gh repo view --json nameWithOwner -q .nameWithOwner
```

Expected: Authenticated and repo visible.

- [ ] **Step 2: Create Queue issues**

```bash
gh issue create \
  --title "feat: wire job queue to live API (CRUD + reorder)" \
  --label "enhancement,frontend" \
  --body "## Summary
Wire the queue screen to the backend REST API.

**Stub location:** \`frontend/src/screens/QueueScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-queue.jsx\`

## Scope
- Fetch jobs from \`GET /api/jobs\` on mount and after mutations
- Drag-to-reorder: \`PATCH /api/jobs/{id}\` to update priority
- Job actions: start, pause, cancel, delete via existing job API
- 'New job' button navigates to /queue/new (already wired to route)
- Progress and status should update via WebSocket (see issue: WebSocket real-time updates)

## API endpoints already exist
See \`backend/app/api/routes/jobs.py\` for available endpoints."

gh issue create \
  --title "feat: wire new-job screen to file upload + queue API" \
  --label "enhancement,frontend" \
  --body "## Summary
The new-job screen is currently a stub — file drop is handled client-side with mock plate parsing.

**Stub location:** \`frontend/src/screens/NewJobScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-new-job.jsx\`

## Scope
- File drop: POST to \`POST /api/files/upload\` (already implemented)
- Plate parsing: use response from upload API (\`plates\` field in 3MF response)
- Eligible printers: derive from backend printer capabilities vs. required material
- Process presets + filament profiles: fetch from \`GET /api/profiles/orca-presets\` and \`GET /api/printers/{id}/filament-profiles\`
- Job creation: \`POST /api/jobs\` with \`file_id\`, \`eligible_printer_ids\`, \`process_preset_id\`, \`filament_profile_id\`"
```

- [ ] **Step 3: Create Fleet + Camera issues**

```bash
gh issue create \
  --title "feat: wire fleet screen to live printer telemetry via WebSocket" \
  --label "enhancement,frontend" \
  --body "## Summary
Fleet tiles currently show static mock data. Wire to live printer state via WebSocket.

**Stub location:** \`frontend/src/screens/FleetScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-fleet.jsx\`

## Scope
- Connect to \`ws://localhost:8000/ws\` on mount
- Update printer state (status, progress, temps, layer) from printer status events
- Reconnect on disconnect
- Initial state from \`GET /api/printers\`"

gh issue create \
  --title "feat: wire camera feed in fleet screen to backend camera proxy" \
  --label "enhancement,frontend" \
  --body "## Summary
The VideoTile component in the fleet screen's expanded printer card shows an animated placeholder. Wire to real camera feed.

**Stub location:** \`frontend/src/screens/FleetScreen.tsx\` (VideoTile usage)
**Backend:** \`backend/app/api/routes/printers.py\` — \`GET /api/printers/{id}/camera\`

## Scope
- Replace \`VideoTile\` placeholder with \`<img>\` or \`<video>\` streaming from \`/api/printers/{id}/camera\`
- Show VideoTile placeholder while camera is loading or unavailable
- Handle 404 (no camera) and 503 (not connected / ffmpeg unavailable) gracefully"
```

- [ ] **Step 4: Create Orders issues**

```bash
gh issue create \
  --title "feat: wire orders screen to order CRUD API" \
  --label "enhancement,frontend" \
  --body "## Summary
Orders screen shows static mock data. Wire to backend.

**Stub location:** \`frontend/src/screens/OrdersScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-orders.jsx\`

## Scope
- Fetch orders from \`GET /api/orders\`
- Order status updates via API
- Clone order action
- Add part to order action
- Part progress should reflect actual job completion (cross-reference JOBS)"

gh issue create \
  --title "feat: wire new-order screen to order creation API" \
  --label "enhancement,frontend" \
  --body "## Summary
New order form is a stub that logs to console and navigates away.

**Stub location:** \`frontend/src/screens/NewOrderScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-new-order.jsx\`

## Scope
- Submit: \`POST /api/orders\` with customer, title, due, notes, parts[]
- After creation: navigate to /orders with the new order expanded
- 'Import from .3mf' button: open file picker, upload via \`POST /api/files/upload\`, parse parts from 3MF metadata"
```

- [ ] **Step 5: Create Files, Filaments, Printers issues**

```bash
gh issue create \
  --title "feat: wire files screen to file upload + library API" \
  --label "enhancement,frontend" \
  --body "## Summary
Files screen shows static mock data from \`mock.ts\`.

**Stub location:** \`frontend/src/screens/FilesScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-files-printers.jsx\`

## Scope
- Fetch files from \`GET /api/files\`
- Upload button: POST to \`/api/files/upload\`
- Folder tree: derive from file paths returned by API
- Tag filter: client-side (tags stored on file records)
- Clicking a file: show detail panel with thumbnail from \`GET /api/files/{id}/thumbnail\`"

gh issue create \
  --title "feat: wire filaments screen to filament CRUD API" \
  --label "enhancement,frontend" \
  --body "## Summary
Filaments screen shows static mock data. Needs backend storage and API.

**Stub location:** \`frontend/src/screens/FilamentsScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-filaments.jsx\`

## Scope
- Backend: add \`filaments\` table + CRUD routes (\`GET/POST/PATCH/DELETE /api/filaments\`)
- Fetch from API on mount
- Add filament form (inline or modal)
- Edit/archive filament
- Favorite toggle: persist to backend
- Print profiles: editable per-printer config stored with filament record"

gh issue create \
  --title "feat: wire printers screen to printer CRUD + connection test API" \
  --label "enhancement,frontend" \
  --body "## Summary
Printers screen table shows mock data. Add wizard is a stub.

**Stub location:** \`frontend/src/screens/PrintersScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-files-printers.jsx\`

## Scope
- Fetch printers from \`GET /api/printers\`
- Add printer wizard step 2: 'Test connection' — \`POST /api/printers/test-connection\` with IP + access code
- Add printer finish: \`POST /api/printers\` with model, nickname, connection type, IP, capabilities
- Edit printer: \`PATCH /api/printers/{id}\`
- Enable/disable toggle: \`PATCH /api/printers/{id}\` (enabled field)"
```

- [ ] **Step 6: Create Settings + Global issues**

```bash
gh issue create \
  --title "feat: persist settings to backend (general, print defaults, notifications)" \
  --label "enhancement,frontend" \
  --body "## Summary
Settings page stores all state in-memory (lost on refresh).

**Stub location:** \`frontend/src/screens/SettingsScreen.tsx\`
**Design reference:** \`docs/design/themis/project/screen-settings.jsx\`

## Scope
- Backend: add \`settings\` table or JSON file storage
- \`GET /api/settings\` — fetch on app load
- \`PATCH /api/settings/{section}\` — save section on 'Save changes' click
- Tags: \`GET/POST/PATCH/DELETE /api/tags\` (currently only in memory in the stub)
- Data export: wire export buttons to backend endpoints"

gh issue create \
  --title "feat: implement Spoolman integration (settings + filament sync)" \
  --label "enhancement,frontend" \
  --body "## Summary
Spoolman settings page is a UI stub — test connection does not make real network calls.

**Stub location:** \`frontend/src/screens/SettingsScreen.tsx\` (SpoolmanPage)
**Design reference:** \`docs/design/themis/project/screen-settings.jsx\`

## Scope
- Backend: \`POST /api/settings/spoolman/test-connection\` — proxies to Spoolman API
- \`POST /api/settings/spoolman/sync-now\` — triggers manual sync
- Sync filament inventory from Spoolman on connect
- Deduct grams on job completion if enabled"

gh issue create \
  --title "feat: implement global search (⌘K) across jobs, orders, files, printers" \
  --label "enhancement,frontend" \
  --body "## Summary
Search bar in topbar is a placeholder input with no functionality.

**Stub location:** \`frontend/src/components/Topbar.tsx\`
**Design reference:** \`docs/design/themis/project/components.jsx\` (Topbar)

## Scope
- ⌘K / Ctrl+K opens a search modal
- Search across: job names, order IDs/customers, file names, printer nicknames
- Navigate to result on selection
- Backend: \`GET /api/search?q=\` or implement client-side over cached data"

gh issue create \
  --title "feat: WebSocket real-time updates for printer status and job progress" \
  --label "enhancement,frontend" \
  --body "## Summary
All screens currently show static mock data. Printer status, job progress, and temps should update in real-time.

**Backend:** WebSocket hub at \`ws://localhost:8000/ws\` already implemented
**Affected screens:** Fleet (printer tiles), Queue (job progress), any screen showing printer status

## Scope
- Add a React context or Zustand store for live printer/job state
- Connect to \`/ws\` on app mount, reconnect on disconnect
- Update printer and job state from incoming messages
- Queue screen: animate progress bars, update status pills live
- Fleet screen: animate temps, progress, layer counters live"
```

- [ ] **Step 7: Commit (no file changes, just tracking)**

```bash
git commit --allow-empty -m "chore: created GitHub enhancement issues for functional wiring"
```

---

## Self-Review

**Spec coverage:**
- All 9 screens from the design are covered: Queue, Fleet, Orders, New Job, New Order, Files, Filaments, Printers, Settings ✓
- App shell with routing ✓
- Shared UI components (all from components.jsx + the missing `Kv`) ✓
- Mock data layer with TypeScript types ✓
- GitHub issues for functional wiring ✓

**Placeholder scan:**
- Task 2 mock.ts says "paste from data.jsx" — this is intentional direction (the array contents are hundreds of lines; the subagent reads the file). Not a placeholder — it's a file reference. ✓
- All implementation tasks reference the design file + call out non-obvious changes. ✓

**Type consistency:**
- `StatusKey` type used consistently across StatusPill, VideoTile, and screen usages ✓
- `Printer.status` is `StatusKey` subset ✓
- `Job.status` is `StatusKey` subset ✓
- `fmtTime`, `matColor`, `matTypeBg/Fg/Border` all defined in helpers.ts before they're used in screens ✓
- `Kv` defined in ui.tsx, used in OrdersScreen ✓
- `shade()` needed by FilesScreen — add to helpers.ts in Task 3 (Step 4) ✓

**Missing from spec check:**
- `darken(hex)` used in NewJobScreen → add to helpers.ts note in Task 9 ✓
- `shade(hex, amt)` used in FilesScreen → add to helpers.ts note in Task 3 ✓
- `EligibilityChips` used in NewOrderScreen → exported from ui.tsx ✓
- `matColor` used in OrdersScreen and NewOrderScreen → defined in helpers.ts ✓
