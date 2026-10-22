import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FilesScreen } from './FilesScreen';

// Machine eligibility of pre-sliced G-code in the library (BIZ-263).

const base = {
  relative_path: 'x', folder: '/Prints', size_bytes: 100, plate_count: 1, uploaded_at: 't', missing: false,
  tags: [], thumbnail_url: null, plate_thumbnails: [], sliced_version_count: 0, sliced_version: null,
};
const MODEL = { ...base, id: 1, original_filename: 'benchy.3mf', kind: '3mf', eligibility: null };
const LEGACY = { ...base, id: 2, original_filename: 'old.gcode', kind: 'gcode', eligibility: { known: false, model_uuids: [] } };
const KNOWN = { ...base, id: 3, original_filename: 'known.gcode', kind: 'gcode', eligibility: { known: true, model_uuids: ['x1'] } };
const X1 = { id: 'x1', plugin_id: 'a', manufacturer_id: 'a', manufacturer_name: 'Acme', model_id: 'x1', display_name: 'X1', bed_mm: [256, 256],
  toolheads: 1, enabled: true, dormant: false, dormant_reason: null, printer_count: 0 };

function stub(files: unknown[], uploaded?: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });
    if (init?.method === 'POST' && url === '/api/v1/files/upload') return json(uploaded);
    if (url.startsWith('/api/v1/tags')) return json([]);
    if (url.startsWith('/api/v1/files/dirs')) return json({ name: 'All files', path: '', count: 1, children: {} });
    if (url === '/api/v1/printer-models') return json([X1]);
    if (/\/api\/v1\/files\/\d+\/eligibility$/.test(url)) {
      const parts = url.split('/');
      const id = Number(parts[parts.length - 2]);
      return json(id === 3 ? { known: true, models: [{ model_uuid: 'x1', source: 'target', display_name: 'X1', manufacturer_name: 'Acme' }] }
                           : { known: false, models: [] });
    }
    if (url.startsWith('/api/v1/files')) return json(files);
    return json([]);
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe('FilesScreen — machine eligibility', () => {
  it('shows the eligibility editor for a pre-sliced file (unknown flagged) and not for a model file', async () => {
    stub([MODEL, LEGACY]);
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);

    fireEvent.click(await screen.findByText('old.gcode'));
    expect((await screen.findByRole('status')).textContent).toMatch(/Unknown/);

    fireEvent.click(await screen.findByText('benchy.3mf'));
    await waitFor(() => expect(screen.queryByTestId('file-eligibility')).toBeNull());
  });

  it('shows the recorded models of a known file with no unknown warning', async () => {
    stub([KNOWN]);
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);

    fireEvent.click(await screen.findByText('known.gcode'));

    expect(await screen.findByLabelText('Acme X1')).toBeChecked();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('uploading a pre-sliced file opens its drawer so the printers it is for can be recorded straight away', async () => {
    stub([MODEL], LEGACY);
    const { container } = render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await screen.findByText('benchy.3mf');
    stub([MODEL, LEGACY], LEGACY);                                   // after the upload the list includes the new file

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['G28'], 'old.gcode')] } });

    expect((await screen.findByTestId('file-eligibility'))).toBeTruthy();
    expect((await screen.findByRole('status')).textContent).toMatch(/Unknown/);
  });
});
