// The plate map is a neutral component: it knows a bed, labelled footprints and caller-supplied states, nothing about
// what the objects are. BIZ-404 (closeout) and BIZ-396 (skip objects) convert their own data into a Plate.
import { describe, expect, it } from 'vitest';

const sources = import.meta.glob('./*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const prod = Object.entries(sources).filter(([p]) => !/\.test\.tsx?$/.test(p));
const imports = (text: string) => [...text.matchAll(/^\s*import[^'"]*['"]([^'"]+)['"]/gm)].map(m => m[1]);
// Words that would mean a Themis domain concept leaked in. A comment saying "no job" is a leak too: keep it neutral.
const DOMAIN = /\b(jobs?|parts?|printers?|slicers?|plugins?|spools?|filaments?|gcode|laminus|queue|projects?|customers?)\b/i;

describe('plate map boundary', () => {
  it('has production files to check', () => {
    expect(prod.map(([p]) => p).sort()).toEqual(['./PlateMap.tsx', './layout.ts']);
  });

  it.each(prod)('%s imports only react and its own folder', (_p, text) => {
    for (const spec of imports(text)) expect(spec === 'react' || spec.startsWith('./'), spec).toBe(true);
  });

  it.each(prod)('%s names no Themis domain concept', (_p, text) => {
    expect(text.match(DOMAIN)?.[0] ?? null).toBeNull();
  });

  it('the guard catches a leak', () => {
    expect(imports("import { useJob } from '../../api/jobs';")).toEqual(['../../api/jobs']);
    expect(DOMAIN.test('maps an object to its part')).toBe(true);
  });
});
