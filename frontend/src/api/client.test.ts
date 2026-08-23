import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiFetch, setForbiddenHandler, setUnauthorizedHandler } from './client';

beforeEach(() => {
  vi.resetAllMocks();
  setUnauthorizedHandler(null);
  setForbiddenHandler(null);
});

describe('apiFetch', () => {
  it('calls forbiddenHandler on 403 with JSON detail', async () => {
    const handler = vi.fn();
    setForbiddenHandler(handler);

    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Missing required scope' }), { status: 403 })));

    const resp = await apiFetch('/api/test');

    expect(resp.status).toBe(403);
    expect(handler).toHaveBeenCalledWith('Missing required scope');
  });

  it('calls forbiddenHandler with generic message when response has no detail', async () => {
    const handler = vi.fn();
    setForbiddenHandler(handler);

    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({}), { status: 403 })));

    const resp = await apiFetch('/api/test');

    expect(resp.status).toBe(403);
    expect(handler).toHaveBeenCalledWith("This API key doesn't have permission to do that.");
  });

  it('calls forbiddenHandler with generic message when response is not valid JSON', async () => {
    const handler = vi.fn();
    setForbiddenHandler(handler);

    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('not json', { status: 403 })));

    const resp = await apiFetch('/api/test');

    expect(resp.status).toBe(403);
    expect(handler).toHaveBeenCalledWith("This API key doesn't have permission to do that.");
  });

  it('does not call handler if no handler registered', async () => {
    // No handler registered
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'test' }), { status: 403 })));

    const resp = await apiFetch('/api/test');

    expect(resp.status).toBe(403);
    // Should not throw
  });
});
