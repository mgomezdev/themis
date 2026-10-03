import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FilesScreen } from './FilesScreen';

// Slicing cache in the library (BIZ-195/196): version badge + "sliced from" chip, the kind filter, and deleting a
// model that has cached versions.

const base = {
  relative_path: 'x', folder: '/Prints', size_bytes: 100, plate_count: 1, uploaded_at: 't', missing: false,
  tags: [], thumbnail_url: null, plate_thumbnails: [],
};
const MODEL = { ...base, id: 1, original_filename: 'benchy.3mf', kind: '3mf', sliced_version_count: 2, sliced_version: null };
const CACHED = {
  ...base, id: 2, original_filename: 'benchy PETG.gcode.3mf', kind: 'gcode_3mf', sliced_version_count: 0,
  sliced_version: {
    id: 7, source_file_id: 1, source_filename: 'benchy.3mf', plate_number: 1, machine_preset: 'Bambu Lab P1S 0.4',
    process_preset: '0.20mm', filament_presets: ['Generic PETG'], filament_type: 'PETG', filament_color: '#000000',
  },
};
const VERSIONS = [{
  id: 7, file_id: 2, name: 'benchy PETG.gcode.3mf', kind: 'gcode_3mf', plate_number: 1,
  machine_preset: 'Bambu Lab P1S 0.4', process_preset: '0.20mm', filament_presets: ['Generic PETG'],
  filament_type: 'PETG', filament_color: '#000000', bed_type: null, overrides: {}, estimated_seconds: 60,
  filament_grams: 2, created_at: 't', source_changed: false, stale: false, stale_reasons: [], printable_now: true,
}];

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function stub(extra: Handler = () => undefined) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const hit = extra(url, init);
    if (hit) return hit;
    if (url.startsWith('/api/v1/tags')) return new Response('[]', { status: 200 });
    if (url.startsWith('/api/v1/files/dirs')) return new Response(JSON.stringify(
      { name: 'All files', path: '', count: 2, children: {} }), { status: 200 });
    if (url.startsWith('/api/v1/files/1/sliced-versions')) return new Response(JSON.stringify(VERSIONS), { status: 200 });
    if (url.startsWith('/api/v1/files')) return new Response(JSON.stringify([MODEL, CACHED]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const calls = (m: ReturnType<typeof stub>, method: string) =>
  m.mock.calls.filter(c => ((c[1] as RequestInit | undefined)?.method ?? 'GET') === method).map(c => c[0] as string);

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => vi.unstubAllGlobals());

describe('FilesScreen — cached sliced versions', () => {
  it('badges a model with its version count and chips a cached file with its source model', async () => {
    stub();
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);

    expect((await screen.findByTestId('sliced-badge')).textContent).toBe('2 sliced versions');
    expect(screen.getByTestId('sliced-from-chip').textContent).toBe(
      'Sliced from benchy.3mf · Bambu Lab P1S 0.4 · PETG #000000');
  });

  it('opens the model and lists its versions from the badge', async () => {
    const api = stub();
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);

    fireEvent.click(await screen.findByTestId('sliced-badge'));

    const list = await screen.findByTestId('sliced-versions');
    expect(within(list).getByText('benchy PETG.gcode.3mf')).toBeTruthy();
    expect(calls(api, 'GET')).toContain('/api/v1/files/1/sliced-versions');
  });

  it('filters the library by kind', async () => {
    const api = stub();
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await screen.findByTestId('sliced-badge');

    fireEvent.change(screen.getByLabelText('File kind'), { target: { value: 'sliced' } });

    await waitFor(() => expect(calls(api, 'GET').some(u => u.startsWith('/api/v1/files?') && u.includes('kind=sliced'))).toBe(true));
    fireEvent.change(screen.getByLabelText('File kind'), { target: { value: 'all' } });
    await waitFor(() => {
      const gets = calls(api, 'GET').filter(u => u.startsWith('/api/v1/files?') || u === '/api/v1/files');
      expect(gets[gets.length - 1]).not.toContain('kind=');
    });
  });

  it.each([
    ['Delete model and versions', 'delete'],
    ['Delete model, keep versions as gcode', 'keep'],
  ])('asks what to do with the versions (once — no extra confirm), then %s', async (button, choice) => {
    const confirm = vi.fn(() => true);
    vi.stubGlobal('confirm', confirm);
    const api = stub((url, init) => {
      if (init?.method !== 'DELETE') return undefined;
      if (url === '/api/v1/files/1') return new Response(JSON.stringify({ detail: {
        message: 'has versions', versions: [{ id: 7, file_id: 2, name: 'benchy PETG.gcode.3mf', folder: '/Prints' }],
      } }), { status: 409 });
      return new Response(JSON.stringify({ deleted: 1, deleted_versions: [] }), { status: 200 });
    });
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    fireEvent.click(await screen.findByText('benchy.3mf'));
    fireEvent.click(await screen.findByRole('button', { name: /^Delete$/ }));

    const dialog = await screen.findByRole('dialog', { name: 'Delete model with sliced versions' });
    expect(within(dialog).getByText('benchy PETG.gcode.3mf')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: button }));

    await waitFor(() => expect(calls(api, 'DELETE')).toEqual(['/api/v1/files/1', `/api/v1/files/1?versions=${choice}`]));
    expect(confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Delete model with sliced versions' })).toBeNull());
  });

  it('Escape backs out of the choice and deletes nothing more', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    const api = stub((_url, init) => init?.method === 'DELETE'
      ? new Response(JSON.stringify({ detail: { versions: [{ id: 7, file_id: 2, name: 'v', folder: '/' }] } }), { status: 409 })
      : undefined);
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    fireEvent.click(await screen.findByText('benchy.3mf'));
    fireEvent.click(await screen.findByRole('button', { name: /^Delete$/ }));

    const dialog = await screen.findByRole('dialog');
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    fireEvent.keyDown(window, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls(api, 'DELETE')).toEqual(['/api/v1/files/1']);
  });

  it('a bulk delete skips models with versions and says so', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    const alert = vi.fn();
    vi.stubGlobal('alert', alert);
    const api = stub((url, init) => {
      if (init?.method !== 'DELETE') return undefined;
      return url === '/api/v1/files/1'
        ? new Response(JSON.stringify({ detail: { versions: [{ id: 7, file_id: 2, name: 'v', folder: '/' }] } }), { status: 409 })
        : new Response(JSON.stringify({ deleted: 2, deleted_versions: [] }), { status: 200 });
    });
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await screen.findByTestId('sliced-badge');
    fireEvent.click(screen.getByLabelText('Select benchy.3mf'));
    fireEvent.click(screen.getByLabelText('Select benchy PETG.gcode.3mf'));

    fireEvent.click(screen.getByRole('button', { name: /^Delete$/ }));

    await waitFor(() => expect(alert).toHaveBeenCalled());
    expect(String(alert.mock.calls[0][0])).toContain('have cached sliced versions');
    expect(String(alert.mock.calls[0][0])).toContain('benchy.3mf');
    expect(calls(api, 'DELETE').sort()).toEqual(['/api/v1/files/1', '/api/v1/files/2']);   // the other one went
  });
});
