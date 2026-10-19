/// <reference types="vite/client" />
// Architecture guard (BIZ-301), frontend half: shared UI never branches on a plugin id. A screen asks what the active provider
// declares (`useInventory().has(...)`, `useFeature`, a plugin's `ui` / `provides`), never "is this plugin X?". Display strings
// that merely contain a vendor name are fine; a comparison against an id is not. `inventoryGuard.test.ts` covers inventory naming.
//
// No exceptions today. If one is ever needed it goes in ALLOWLIST with the reason, and a stale entry fails the test.
import { describe, expect, it } from 'vitest';

const sources = import.meta.glob('./**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

// The bundled plugins' ids (backend/app/plugins/*/themis-plugin.toml). Kept in step by `test_the_frontend_guard_lists_every_bundled_plugin`.
const BUNDLED_PLUGIN_IDS = ['bambu', 'elegoo_centauri', 'snapmaker', 'moonraker', 'mock', 'laminus', 'spoolman',
  'local_inventory', 'notify_ntfy', 'notify_discord', 'notify_email'];
const ID = BUNDLED_PLUGIN_IDS.join('|');
// `x.plugin_id === 'bambu'`, `'bambu' !== id`, and a comparison of anything called plugin id / provider id with any literal.
const PATTERNS = [
  new RegExp(String.raw`(===|!==|==|!=)\s*['"\x60](${ID})['"\x60]`),
  new RegExp(String.raw`['"\x60](${ID})['"\x60]\s*(===|!==|==|!=)`),
  /(plugin_?[iI]d|provider_?[iI]d)\s*(===|!==|==|!=)\s*['"`]/,
  /\[['"`](bambu|elegoo_centauri|snapmaker|moonraker|laminus|spoolman|local_inventory|notify_ntfy|notify_discord|notify_email)['"`]\]/,
];

const ALLOWLIST: Record<string, string> = {};

const isTest = (path: string) => /\.test\.tsx?$/.test(path) || path.startsWith('./test/');
const prod = Object.entries(sources).filter(([path]) => !isTest(path)).map(([path, text]) => [path.replace(/^\.\//, ''), text] as const);

const hits = (files: readonly (readonly [string, string])[], allow: Record<string, string>) =>
  files.filter(([path, text]) => PATTERNS.some(p => p.test(text)) && !(path in allow)).map(([path]) => path);

describe('UI code does not branch on a plugin id', () => {
  it('no source file compares with a bundled plugin id or a plugin/provider id literal', () => {
    expect(hits(prod, ALLOWLIST)).toEqual([]);
  });

  it('every allowlist entry is still needed', () => {
    expect(Object.keys(ALLOWLIST).filter(path => !prod.some(([p, text]) => p === path && PATTERNS.some(re => re.test(text))))).toEqual([]);
  });

  it('scans the whole source tree', () => {
    expect(prod.length).toBeGreaterThan(100);
    expect(prod.some(([p]) => p === 'screens/PluginPage.tsx')).toBe(true);
  });

  it('can fail: planted comparisons are caught, labels and ordinary ids are not', () => {
    const bad = (code: string) => hits([['screens/New.tsx', code]], {});
    expect(bad("if (plugin.id === 'bambu') {}")).toEqual(['screens/New.tsx']);
    expect(bad("const x = 'snapmaker' !== printer.plugin_id;")).toEqual(['screens/New.tsx']);
    expect(bad("if (p.plugin_id == 'whatever') {}")).toEqual(['screens/New.tsx']);
    expect(bad("const f = FLAGS['spoolman'];")).toEqual(['screens/New.tsx']);
    expect(bad("const label = 'Bambu Lab'; if (a.id === 'row-1') {}")).toEqual([]);
  });
});
