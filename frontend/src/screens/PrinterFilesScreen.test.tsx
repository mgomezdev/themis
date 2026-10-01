import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrinterFilesScreen } from './PrinterFilesScreen';
import { stubFetch, Reply } from '../test/fetchStub';

const file = (over: object) => ({
  id: 'x', name: 'x', size: 1024, modified_at: '2025-10-01T00:00:00+00:00', is_dir: false, metadata: null, printable: true, ...over,
});

const ATLAS = {
  printer_id: 1, printer_name: 'Atlas', error: null, can_delete: true, can_download: true,
  files: [
    file({ id: 'sub', name: 'sub', is_dir: true, printable: false, size: 0 }),
    file({ id: 'part.gcode', name: 'part.gcode', size: 2048, metadata: { estimated_seconds: 3720, filament_grams: 3.54 } }),
    file({ id: 'model.3mf', name: 'model.3mf', size: 3 * 1024 * 1024 }),
    file({ id: 'readme.txt', name: 'readme.txt', printable: false }),
  ],
};
const BOREALIS = { printer_id: 2, printer_name: 'Borealis', error: 'Printer not connected', can_delete: false, can_download: false, files: [] };
const CIRRUS = {
  printer_id: 3, printer_name: 'Cirrus', error: null, can_delete: false, can_download: false,
  files: [file({ id: 'c.gcode', name: 'c.gcode' }), file({ id: 'c.3mf', name: 'c.3mf' })],
};

let confirm: ReturnType<typeof vi.spyOn>;
beforeEach(() => { confirm = vi.spyOn(window, 'confirm').mockReturnValue(true); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const section = (id: number) => within(screen.getByTestId(`printer-files-${id}`));

describe('PrinterFilesScreen', () => {
  it('lists every printer with its files, size, print time and filament, and surfaces a printer error', async () => {
    stubFetch({ 'GET /api/v1/printers/files/all': { printers: [ATLAS, BOREALIS, CIRRUS] } });
    render(<PrinterFilesScreen />);

    expect(await screen.findByTestId('printer-files-1')).toBeTruthy();
    const atlas = section(1);
    expect(atlas.getByText('part.gcode')).toBeTruthy();
    expect(atlas.getByText('2.0 KB')).toBeTruthy();
    expect(atlas.getByText('1h 2m · 3.5 g')).toBeTruthy();                   // 3720 s, 3.54 g
    expect(atlas.getByText('3.0 MB')).toBeTruthy();
    expect(section(2).getByText('Printer not connected')).toBeTruthy();
    expect(section(3).getByText('c.gcode')).toBeTruthy();
  });

  it('only offers the actions the printer and file allow', async () => {
    stubFetch({ 'GET /api/v1/printers/files/all': { printers: [ATLAS, CIRRUS] } });
    render(<PrinterFilesScreen />);
    await screen.findByTestId('printer-files-1');

    expect(section(1).getByRole('button', { name: 'Print part.gcode' })).toBeTruthy();
    expect(section(1).getByRole('button', { name: 'Delete part.gcode' })).toBeTruthy();
    expect(section(1).getByRole('button', { name: 'Add model.3mf to library' })).toBeTruthy();
    expect(section(1).queryByRole('button', { name: 'Add part.gcode to library' })).toBeNull();     // gcode can't go in the library
    expect(section(1).queryByRole('button', { name: 'Print readme.txt' })).toBeNull();               // not printable
    expect(section(1).queryByRole('button', { name: 'Add readme.txt to library' })).toBeNull();
    expect(section(1).getByRole('button', { name: 'Delete readme.txt' })).toBeTruthy();                // anything can be deleted
    expect(section(3).queryByRole('button', { name: /Delete|library/ })).toBeNull();                 // no delete/download caps
    expect(section(3).getByRole('button', { name: 'Print c.3mf' })).toBeTruthy();
  });

  it('filters files by name across printers but keeps directories', async () => {
    const user = userEvent.setup();
    stubFetch({ 'GET /api/v1/printers/files/all': { printers: [ATLAS, CIRRUS] } });
    render(<PrinterFilesScreen />);
    await screen.findByTestId('printer-files-1');

    await user.type(screen.getByLabelText('Filter files'), 'model');

    expect(section(1).getByText('model.3mf')).toBeTruthy();
    expect(section(1).queryByText('part.gcode')).toBeNull();
    expect(section(1).getByRole('button', { name: /sub/ })).toBeTruthy();
    expect(section(3).getByText('No files match the filter.')).toBeTruthy();
  });

  it('prints only after confirmation, and reports the result', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      'GET /api/v1/printers/files/all': { printers: [ATLAS] },
      'POST /api/v1/printers/1/files/print': { ok: true },
    });
    render(<PrinterFilesScreen />);
    await screen.findByTestId('printer-files-1');

    confirm.mockReturnValueOnce(false);
    await user.click(section(1).getByRole('button', { name: 'Print part.gcode' }));
    expect(calls.some(c => c.method === 'POST')).toBe(false);

    await user.click(section(1).getByRole('button', { name: 'Print part.gcode' }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('bypasses the queue'));
    await waitFor(() => expect(calls.find(c => c.method === 'POST')?.body).toEqual({ file_id: 'part.gcode' }));
    expect((await screen.findByRole('status')).textContent).toBe('Started part.gcode — Atlas');
  });

  it('shows the server\'s reason when a print is refused', async () => {
    const user = userEvent.setup();
    stubFetch({
      'GET /api/v1/printers/files/all': { printers: [ATLAS] },
      'POST /api/v1/printers/1/files/print': new Reply(409, { detail: 'The previous plate has not been cleared; mark the printer ready first' }),
    });
    render(<PrinterFilesScreen />);
    await screen.findByTestId('printer-files-1');

    await user.click(section(1).getByRole('button', { name: 'Print part.gcode' }));

    expect((await screen.findByRole('alert')).textContent).toMatch(/previous plate has not been cleared/);
  });

  it('deletes after confirmation and re-reads that printer\'s directory', async () => {
    const user = userEvent.setup();
    let listings = 0;
    const { calls } = stubFetch({
      'GET /api/v1/printers/files/all': { printers: [ATLAS] },
      'DELETE /api/v1/printers/1/files?file_id=part.gcode': { ok: true },
      'GET /api/v1/printers/1/files?directory=%2F': () => {
        listings++;
        return { printer_id: 1, directory: '/', can_delete: true, can_download: true, files: ATLAS.files.filter(f => f.id !== 'part.gcode') };
      },
    });
    render(<PrinterFilesScreen />);
    await screen.findByTestId('printer-files-1');

    await user.click(section(1).getByRole('button', { name: 'Delete part.gcode' }));

    await waitFor(() => expect(section(1).queryByText('part.gcode')).toBeNull());
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('cannot be undone'));
    expect(listings).toBe(1);
    expect(calls.filter(c => c.method === 'DELETE')).toHaveLength(1);
  });

  it('copies a .3mf into the library', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      'GET /api/v1/printers/files/all': { printers: [ATLAS] },
      'POST /api/v1/printers/1/files/to-library': { id: 9, original_filename: 'model.3mf' },
    });
    render(<PrinterFilesScreen />);
    await screen.findByTestId('printer-files-1');

    await user.click(section(1).getByRole('button', { name: 'Add model.3mf to library' }));

    await waitFor(() => expect(calls.find(c => c.url.endsWith('/to-library'))?.body).toEqual({ file_id: 'model.3mf' }));
    expect((await screen.findByRole('status')).textContent).toBe('Added model.3mf to the library — Atlas');
  });

  it('navigates into a directory and back up', async () => {
    const user = userEvent.setup();
    stubFetch({
      'GET /api/v1/printers/files/all': { printers: [ATLAS] },
      'GET /api/v1/printers/1/files?directory=sub': { printer_id: 1, directory: 'sub', can_delete: true, can_download: true,
        files: [file({ id: 'sub/inner.gcode', name: 'inner.gcode' })] },
      'GET /api/v1/printers/1/files?directory=%2F': { printer_id: 1, directory: '/', can_delete: true, can_download: true, files: ATLAS.files },
    });
    render(<PrinterFilesScreen />);
    await screen.findByTestId('printer-files-1');

    await user.click(section(1).getByRole('button', { name: /sub/ }));
    expect(await section(1).findByText('inner.gcode')).toBeTruthy();
    expect(section(1).getByText('/sub')).toBeTruthy();

    await user.click(section(1).getByRole('button', { name: /Up/ }));
    expect(await section(1).findByText('part.gcode')).toBeTruthy();
  });

  it('says so when no printer can list files', async () => {
    stubFetch({ 'GET /api/v1/printers/files/all': { printers: [] } });
    render(<PrinterFilesScreen />);
    expect(await screen.findByTestId('printer-files-empty')).toBeTruthy();
  });

  it('survives a malformed response and a failed load', async () => {
    stubFetch({ 'GET /api/v1/printers/files/all': {} });
    const { unmount } = render(<PrinterFilesScreen />);
    expect(await screen.findByTestId('printer-files-empty')).toBeTruthy();
    unmount();

    stubFetch({ 'GET /api/v1/printers/files/all': new Reply(500, { detail: 'boom' }) });
    render(<PrinterFilesScreen />);
    expect((await screen.findByRole('alert')).textContent).toMatch(/Could not load printer files: boom/);
  });
});
