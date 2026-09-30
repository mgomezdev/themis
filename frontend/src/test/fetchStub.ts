import { vi } from 'vitest';

/** One recorded request. `body` is the parsed JSON body (or the raw body when it isn't JSON). */
export interface Call { method: string; url: string; body: unknown }

/** Return `new Reply(status, body)` from a route to simulate an error response. */
export class Reply {
  constructor(public status: number, public body: unknown = null) {}
}

type Route = unknown | ((call: Call) => unknown);

/**
 * Stub global fetch with routes keyed `"METHOD /exact/url"` (query string included as the app sent it).
 * A route is a static JSON body, a `Reply`, or a function of the recorded call returning either.
 * Unmatched requests reject loudly so a missing route can't silently pass a test.
 */
export function stubFetch(routes: Record<string, Route>) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    let body: unknown = init?.body;
    if (typeof body === 'string') { try { body = JSON.parse(body); } catch { /* keep raw */ } }
    const call: Call = { method, url, body };
    calls.push(call);
    const route = routes[`${method} ${url}`];
    if (route === undefined) return Promise.reject(new Error(`unmocked fetch: ${method} ${url}`));
    const result = typeof route === 'function' ? (route as (c: Call) => unknown)(call) : route;
    const reply = result instanceof Reply ? result : new Reply(200, result);
    const ok = reply.status >= 200 && reply.status < 300;
    const resp = {
      ok, status: reply.status, statusText: ok ? 'OK' : 'Error',
      json: () => Promise.resolve(reply.body),
      text: () => Promise.resolve(typeof reply.body === 'string' ? reply.body : JSON.stringify(reply.body)),
      clone: () => resp,
    };
    return Promise.resolve(resp);
  }));
  return {
    calls,
    /** Recorded calls matching method and exact url. */
    to: (method: string, url: string) => calls.filter(c => c.method === method.toUpperCase() && c.url === url),
  };
}
