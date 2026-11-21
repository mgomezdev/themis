import { beforeEach, expect, it, vi } from 'vitest';
import { apiFetch } from './client';
import { fetchCatalog, fetchFileOrigin, materializeEntry, syncSource } from './library';
vi.mock('./client', () => ({ apiFetch: vi.fn() }));
const fetch = vi.mocked(apiFetch);
beforeEach(() => { vi.resetAllMocks(); });
it('uses concrete encoded paths and preserves request methods', async () => {
  fetch.mockImplementation(async () => new Response('{}'));
  await fetchCatalog(100); await syncSource('source/a'); await materializeEntry('entry/a'); await fetchFileOrigin(42);
  expect(fetch.mock.calls).toEqual([
    ['/api/v1/library/entries?limit=100&offset=100', undefined],
    ['/api/v1/library/sources/source%2Fa/sync', { method: 'POST' }],
    ['/api/v1/library/entries/entry%2Fa/materialize', { method: 'POST' }],
    ['/api/v1/library/files/42/origin', undefined],
  ]);
});
it.each(['null', '[]', '{"detail":[]}', 'not JSON'])('handles an error body without a string detail: %s', async body => {
  fetch.mockResolvedValue(new Response(body, { status: 502 }));
  await expect(fetchCatalog()).rejects.toThrow('Library source request failed');
});
it('shows the backend safe error detail', async () => {
  fetch.mockResolvedValue(new Response('{"detail":"Enable this source first"}', { status: 409 }));
  await expect(syncSource('source')).rejects.toThrow('Enable this source first');
});
