// frontend/src/api/tags.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getTags, createTag } from './tags';

beforeEach(() => vi.restoreAllMocks());

describe('tags api', () => {
  it('getTags fetches the list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{ id: 1, name: 'PLA', color: '#fff', category: 'Material', usage_count: 2 }]),
        { status: 200 })));
    const tags = await getTags();
    expect(tags[0].name).toBe('PLA');
    expect(tags[0].usage_count).toBe(2);
  });

  it('createTag posts JSON', async () => {
    const f = vi.fn(async () =>
      new Response(JSON.stringify({ id: 9, name: 'PETG', color: '#0af', category: '', usage_count: 0 }),
        { status: 201 }));
    vi.stubGlobal('fetch', f);
    const t = await createTag({ name: 'PETG', color: '#0af', category: '' });
    expect(t.id).toBe(9);
    expect(f).toHaveBeenCalledWith('/api/v1/tags', expect.objectContaining({ method: 'POST' }));
  });
});
