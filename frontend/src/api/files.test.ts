// frontend/src/api/files.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getFiles, uploadLibraryFile } from './files';

beforeEach(() => vi.restoreAllMocks());

describe('files api', () => {
  it('getFiles builds the query string', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
    vi.stubGlobal('fetch', f);
    await getFiles({ folder: '/Customers', tags: ['PLA', 'structural'], sort: 'name' });
    const url = (f.mock.calls[0] as unknown[])[0] as string;
    expect(url).toContain('folder=%2FCustomers');
    expect(url).toContain('tags=PLA');
    expect(url).toContain('tags=structural');
    expect(url).toContain('sort=name');
  });

  it('uploadLibraryFile posts FormData with folder', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ id: 1 }), { status: 201 }));
    vi.stubGlobal('fetch', f);
    await uploadLibraryFile(new File(['x'], 'a.stl'), '/Customers/Vela');
    const call = f.mock.calls[0] as unknown[];
    const init = call[1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
  });
});
