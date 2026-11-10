/// <reference types="vite/client" />
// Style guard: colours live in one place. `styles/app.css` `:root` defines the design tokens (docs/agent/styling.md);
// components reference them as `var(--token)`. CSS is not type-checked, so three mistakes are otherwise silent:
//   1. `var(--border)` for a token that does not exist: the declaration is dropped and the element loses its border.
//   2. `var(--ok, #22c55e)`: a second, different definition of the colour that hides mistake 1 behind an off-palette value.
//   3. A new `rgba(239,68,68,0.12)` literal next to the `--err-bg` token that already says the same thing.
// 1 and 2 are forbidden outright. 3 is a ratchet: the literals left in component code are counted and the count may only
// go down. Lower LITERAL_BUDGET when you remove some; never raise it (add a token, or a named constant in an allowed file).
import { describe, expect, it } from 'vitest';
import css from './styles/app.css?raw';

const sources = import.meta.glob('./**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

const isTest = (path: string) => /\.test\.tsx?$/.test(path) || path.startsWith('./test/');
const prod = Object.entries(sources).filter(([path]) => !isTest(path)).map(([path, text]) => [path.replace(/^\.\//, ''), text] as const);

const defined = (text: string) => new Set([...text.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map(m => m[1]));
const referenced = (text: string) => [...text.matchAll(/var\(\s*(--[a-z0-9-]+)/gi)].map(m => m[1]);
// A component may set a custom property inline for its own class to read (`style={{ '--p': '40%' }}`).
const setInline = (text: string) => [...text.matchAll(/['"](--[a-z0-9-]+)['"]\s*:/gi)].map(m => m[1]);

const undefinedVars = (files: readonly (readonly [string, string])[], stylesheet: string) => {
  const known = defined(stylesheet);
  files.forEach(([, text]) => setInline(text).forEach(v => known.add(v)));
  const out: string[] = [];
  for (const [path, text] of [...files, ['styles/app.css', stylesheet] as const]) {
    for (const v of new Set(referenced(text))) if (!known.has(v)) out.push(`${path}: ${v}`);
  }
  return out.sort();
};

const FALLBACK = /var\(\s*--[a-z0-9-]+\s*,\s*(#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|oklch\()/i;
const literalFallbacks = (files: readonly (readonly [string, string])[]) => files.filter(([, text]) => FALLBACK.test(text)).map(([path]) => path);

// Files whose job is to hold colour data, not to style the app's chrome.
const PALETTE_FILES: Record<string, string> = {
  'data/helpers.ts': 'material palette (matColor / matType*) and the named fallback swatches',
  'api/fleet.ts': 'per-printer-type accent',
  'components/builder/GroupBanner.tsx': 'the colours a print group can be given',
  'components/StlPreview.tsx': 'canvas renderer: shades each triangle from its normal',
  'screens/SettingsScreen.tsx': 'the swatch picker offered for a material type',
  'screens/FilesScreen.tsx': 'per-plate fallback colours for a file with no filament data',
};
// `'#1a73e8'` / `"#fff"` / `rgba(…)` / `oklch(…)` as a string in component code. `&#8203;` entities and `#123` ids in prose do not match.
const LITERAL = /['"`]#[0-9a-f]{3,8}['"`]|rgba?\(\s*[\d$]|hsla?\(\s*\d|oklch\(\s*\d/gi;
const literalCount = (files: readonly (readonly [string, string])[]) =>
  files.filter(([path]) => !(path in PALETTE_FILES)).reduce((n, [, text]) => n + (text.match(LITERAL)?.length ?? 0), 0);

// Measured 2026-11-09 after the tone/scrim tokens replaced the status tints. What is left is mostly drop shadows and
// white-on-thumbnail overlays; turn those into tokens and lower this.
const LITERAL_BUDGET = 34;

describe('style tokens', () => {
  it('every var(--token) used in the source is defined in app.css (or set inline by a component)', () => {
    expect(undefinedVars(prod, css)).toEqual([]);
  });

  it('no var() carries a literal colour fallback', () => {
    expect(literalFallbacks(prod)).toEqual([]);
  });

  it('colour literals in component code stay within the budget', () => {
    expect(literalCount(prod)).toBeLessThanOrEqual(LITERAL_BUDGET);
  });

  it('the budget is tight: lower LITERAL_BUDGET when literals are removed', () => {
    expect(LITERAL_BUDGET - literalCount(prod)).toBeLessThan(5);
  });

  it('every palette file exists and still holds a colour literal', () => {
    const stale = Object.keys(PALETTE_FILES).filter(path => !prod.some(([p, text]) => p === path && (text.match(LITERAL)?.length ?? 0) > 0));
    expect(stale).toEqual([]);
  });

  it('scans the whole source tree and the stylesheet', () => {
    expect(prod.length).toBeGreaterThan(100);
    expect(defined(css).has('--err-border')).toBe(true);
  });

  it('can fail: a planted undefined var, fallback and literal are each caught', () => {
    const planted = [['x.tsx', `<div style={{ border: '1px solid var(--border)', color: 'var(--ok, #22c55e)', background: 'rgba(239,68,68,0.12)' }} />`]] as const;
    expect(undefinedVars(planted, ':root { --ok: green; }')).toEqual(['x.tsx: --border']);
    expect(literalFallbacks(planted)).toEqual(['x.tsx']);
    expect(literalCount(planted)).toBe(1);
    // …and the things that must not trip it.
    const fine = [['y.tsx', `<div style={{ '--p': '40%', color: 'var(--ok)' } as React.CSSProperties}>Job #101 &#8203; {'#' + id}</div>`]] as const;
    expect(undefinedVars(fine, ':root { --ok: green; } .bar { width: var(--p, 0%); }')).toEqual([]);
    expect(literalFallbacks(fine)).toEqual([]);
    expect(literalCount(fine)).toBe(0);
  });
});
