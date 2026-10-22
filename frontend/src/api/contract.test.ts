/// <reference types="vite/client" />
// Contract check: every backend URL the frontend calls must exist in the committed openapi.json
// (the backend CI regenerates and diffs that file, so it is the source of truth for routes).
import { describe, expect, it } from 'vitest';
import openapiRaw from '../../../openapi.json?raw';

const SPEC = JSON.parse(openapiRaw) as { paths: Record<string, Record<string, unknown>> };

const METHODS = ['get', 'post', 'put', 'patch', 'delete'];

/** Calls that are intentionally not in openapi.json. Keep this list empty unless a route is genuinely absent. */
const ALLOWLIST: Record<string, string> = {
  // schema tabs call routes the plugin itself named at runtime (`GET /plugins/{id}/ui/{tab}` says which), so no fixed path exists
  'plugins.ts: GET /api/v1/plugins/{}/{}': 'plugin-owned routes named by a tab schema',
};

const norm = (path: string) => path.replace(/\{[^}]+\}/g, '{}');

const specOperations = new Set(
  Object.entries(SPEC.paths).flatMap(([path, ops]) =>
    Object.keys(ops).filter(m => METHODS.includes(m)).map(m => `${m.toUpperCase()} ${norm(path)}`)),
);

interface Call { file: string; method: string; path: string; raw: string }

/** Pull every `/api/v1/...` URL literal (string or template, including `${BASE}/...`) out of one module. */
export function extractCalls(file: string, source: string): Call[] {
  const bases = new Map<string, string>();
  for (const m of source.matchAll(/const\s+([A-Z_]+)\s*=\s*['"`](\/api\/v1[^'"`]*)['"`]/g)) bases.set(m[1], m[2]);

  const calls: Call[] = [];
  const literal = /`((?:\$\{[A-Z_]+\}|\/api\/v1)[^`]*)`|'(\/api\/v1[^']*)'|"(\/api\/v1[^"]*)"/g;
  for (const m of source.matchAll(literal)) {
    const before = source.slice(Math.max(0, (m.index ?? 0) - 40), m.index ?? 0);
    if (/const\s+[A-Z_]+\s*=\s*$/.test(before)) continue; // the BASE constant's own definition is not a call
    let raw = m[1] ?? m[2] ?? m[3];
    raw = raw.replace(/^\$\{([A-Z_]+)\}/, (_, name: string) => bases.get(name) ?? `\${${name}}`);
    if (!raw.startsWith('/api/v1')) continue;
    // An interpolation glued to the path (`/files${qs}`) is a query-string suffix, not a path segment.
    const path = raw.split('?')[0].replace(/([^/])\$\{[\s\S]*$/, '$1').replace(/\$\{[^}]*\}/g, '{}').replace(/\/+$/, '');
    // The HTTP method is whatever the same statement passes as options (default GET).
    const rest = source.slice((m.index ?? 0) + m[0].length);
    const statement = rest.slice(0, rest.search(/;\s*\n|\n\s*\n|\nexport /) === -1 ? 400 : rest.search(/;\s*\n|\n\s*\n|\nexport /));
    const method = statement.match(/method:\s*['"](\w+)['"]/)?.[1]
      ?? statement.match(/\b(?:json|jsonInit|jsonRequest)\(\s*['"](\w+)['"]/)?.[1]
      ?? 'GET';
    calls.push({ file, method: method.toUpperCase(), path, raw });
  }
  return calls;
}

const apiModules = Object.entries(import.meta.glob('./*.ts', { query: '?raw', import: 'default', eager: true }) as Record<string, string>)
  .filter(([file]) => !file.endsWith('.test.ts'))
  .map(([file, source]) => ({ file: file.replace('./', ''), source }));

describe('extractCalls (the scanner itself)', () => {
  it('resolves ${BASE}, template params, query strings and inline methods', () => {
    const src = [
      "const BASE = '/api/v1/things';",
      'export const a = () => request(`${BASE}/${id}/pause?x=${y}`, { method: \'POST\' });',
      "export const b = () => request('/api/v1/other');",
      "export const c = () => request<X>(`/api/v1/things/${id}`, json('PUT', body));",
    ].join('\n');

    expect(extractCalls('t.ts', src).map(c => `${c.method} ${c.path}`)).toEqual([
      'POST /api/v1/things/{}/pause', 'GET /api/v1/other', 'PUT /api/v1/things/{}',
    ]);
  });
});

describe('frontend → backend URL contract', () => {
  const calls = apiModules.flatMap(m => extractCalls(m.file, m.source));

  it('finds a realistic number of calls (guards against the scanner silently matching nothing)', () => {
    expect(calls.length).toBeGreaterThan(80);
  });

  it('every call the frontend makes exists in openapi.json with that method', () => {
    const missing = calls
      .filter((c: Call) => !specOperations.has(`${c.method} ${norm(c.path)}`))
      .map((c: Call) => `${c.file}: ${c.method} ${c.path}`)
      .filter((line: string) => !(line in ALLOWLIST));

    expect([...new Set(missing)]).toEqual([]);
  });
});
