/// <reference types="vite/client" />
// Architecture guard (BIZ-202 §6), frontend half: UI code must not name a specific inventory provider. A screen branches on
// the capabilities the active provider declares (`useInventory().has(...)`, `useCapability`), never on a plugin id.
//
// A ratchet like the backend's: the files below are the ones that still mention it. A new file naming a provider fails; so
// does an allowlisted file that no longer does (delete it from the list — the allowlist only ever shrinks).
import { describe, expect, it } from 'vitest';

const sources = import.meta.glob('./**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

const SPOOLMAN = /spoolman/i;
const LOCAL_INVENTORY = /local[_-]?inv(entory)?/i;

// Paths relative to src/. Why each remains:
const SPOOLMAN_ALLOWLIST: Record<string, string> = {
  'api/printers.ts': 'the legacy slot key (`spoolman_spool_id`) a slot still carries; removed with the backend dual-write (BIZ-221)',
  'api/laminus.ts': 'response-contract keys the backend still names (`spoolman_filaments`, `spoolman_error`)',
  'components/RemapModal.tsx': 'renders those same laminus response keys',
  'api/apiKeys.ts': 'the legacy `spoolman:*` API-key scopes, still granted and honoured',
  'plugins/registry.ts': 'maps the bundled Spoolman plugin id to its component tab and its old URLs',
};
// Local inventory is an ordinary plugin: nothing outside its own pages may name it.
const LOCAL_ALLOWLIST: Record<string, string> = {};

const isTest = (path: string) => /\.test\.tsx?$/.test(path) || path.startsWith('./test/');
const prod = Object.entries(sources).filter(([path]) => !isTest(path)).map(([path, text]) => [path.replace(/^\.\//, ''), text] as const);

const violations = (files: readonly (readonly [string, string])[], pattern: RegExp, allow: Record<string, string>) =>
  files.filter(([path, text]) => pattern.test(text) && !(path in allow)).map(([path]) => path);
const stale = (files: readonly (readonly [string, string])[], pattern: RegExp, allow: Record<string, string>) =>
  Object.keys(allow).filter(path => !files.some(([p, text]) => p === path && pattern.test(text)));

describe('UI code does not name an inventory provider', () => {
  it('only the allowlisted files mention Spoolman', () => {
    expect(violations(prod, SPOOLMAN, SPOOLMAN_ALLOWLIST)).toEqual([]);
  });

  it('the allowlist only shrinks', () => {
    expect(stale(prod, SPOOLMAN, SPOOLMAN_ALLOWLIST)).toEqual([]);
  });

  it('nothing names Local inventory outside its own pages', () => {
    expect(violations(prod, LOCAL_INVENTORY, LOCAL_ALLOWLIST)).toEqual([]);
    expect(stale(prod, LOCAL_INVENTORY, LOCAL_ALLOWLIST)).toEqual([]);
  });

  it('scans the whole source tree (guards against the glob silently matching nothing)', () => {
    expect(prod.length).toBeGreaterThan(100);
    expect(prod.some(([p]) => p === 'api/inventory.ts')).toBe(true);
  });

  it('the guard can fail: a planted reference is caught, and a stale entry is reported', () => {
    const files = [['screens/New.tsx', 'const x = useSpoolmanThing();'], ['api/clean.ts', 'export {}']] as const;
    expect(violations(files, SPOOLMAN, {})).toEqual(['screens/New.tsx']);
    expect(violations(files, SPOOLMAN, { 'screens/New.tsx': 'x' })).toEqual([]);
    expect(stale(files, SPOOLMAN, { 'api/clean.ts': 'x' })).toEqual(['api/clean.ts']);
  });
});
