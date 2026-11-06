import { act, createEvent, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FileDropZone } from './FileDropZone';
import { Reply, stubFetch } from '../test/fetchStub';

const file = (name: string) => new File(['x'], name);
const filesDrag = (...files: File[]) => ({ dataTransfer: { files, types: ['Files'], dropEffect: 'none' } });
const alertText = () => screen.getByRole('alert').textContent;

function renderZone(onUploaded = vi.fn()) {
  render(<FileDropZone onUploaded={onUploaded} label="Drop to add"><p data-testid="child">page body</p></FileDropZone>);
  return { zone: screen.getByTestId('file-drop-zone'), onUploaded };
}

describe('FileDropZone', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uploads each supported dropped file to the library and hands it on; names the ones it skipped', async () => {
    const uploaded = { id: 7, original_filename: 'bracket.stl', kind: 'stl' };
    const api = stubFetch({ 'POST /api/v1/files/upload': uploaded });
    const { zone, onUploaded } = renderZone();

    const drop = createEvent.drop(zone, filesDrag(file('bracket.stl'), file('notes.txt')));
    fireEvent(zone, drop);
    expect(drop.defaultPrevented).toBe(true);                       // the browser doesn't open the file

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(uploaded));
    expect(onUploaded).toHaveBeenCalledTimes(1);
    const sent = api.to('POST', '/api/v1/files/upload');
    expect(sent).toHaveLength(1);
    expect(((sent[0].body as FormData).get('file') as File).name).toBe('bracket.stl');
    await waitFor(() => expect(alertText()).toBe('Not a .3mf, .stl or .gcode file: notes.txt'));
  });

  it('keeps the overlay while the drag crosses children and ignores drags without files', async () => {
    const api = stubFetch({});
    const { zone, onUploaded } = renderZone();
    const child = screen.getByTestId('child');

    fireEvent.dragEnter(zone, { dataTransfer: { types: ['text/plain'], files: [] } });   // the page's own item drag
    expect(screen.queryByText('Drop to add')).toBeNull();
    fireEvent.drop(zone, { dataTransfer: { types: ['text/plain'], files: [] } });

    fireEvent.dragEnter(zone, filesDrag(file('a.3mf')));
    fireEvent.dragEnter(child, filesDrag(file('a.3mf')));           // entering a child before leaving the parent
    fireEvent.dragLeave(zone, filesDrag(file('a.3mf')));
    expect(screen.getByText('Drop to add')).toBeTruthy();           // still over the child
    fireEvent.dragLeave(child, filesDrag(file('a.3mf')));
    expect(screen.queryByText('Drop to add')).toBeNull();

    await act(async () => {});
    expect(api.calls).toEqual([]);
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('refuses a second drop while an upload is still running', async () => {
    let release!: () => void;
    stubFetch({ 'POST /api/v1/files/upload': { id: 1, original_filename: 'a.stl', kind: 'stl' } });
    const onUploaded = vi.fn(() => new Promise<void>(r => { release = r; }));
    const { zone } = renderZone(onUploaded);

    fireEvent.drop(zone, filesDrag(file('a.stl')));
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Uploading a.stl…'));
    fireEvent.drop(zone, filesDrag(file('b.stl')));
    expect(alertText()).toBe('Wait for the current upload to finish, then drop again');

    await act(async () => release());
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe(''));
    expect(onUploaded).toHaveBeenCalledTimes(1);
  });

  it('swallows a file dropped elsewhere in the window so the browser stays on the page', () => {
    renderZone();
    const stray = createEvent.drop(window, filesDrag(file('a.stl')));
    fireEvent(window, stray);
    expect(stray.defaultPrevented).toBe(true);
  });

  it('reports a failed upload or a failed hand-off with the file name and keeps going', async () => {
    stubFetch({
      'POST /api/v1/files/upload': (c: { body: unknown }) => {
        const name = ((c.body as FormData).get('file') as File).name;
        return name === 'bad.3mf' ? new Reply(413, { detail: 'File too large' }) : { id: 9, original_filename: name, kind: '3mf' };
      },
    });
    const onUploaded = vi.fn(async () => { throw new Error('422 {"detail": "only models can be packed"}'); });
    const { zone } = renderZone(onUploaded);

    fireEvent.drop(zone, filesDrag(file('bad.3mf'), file('plate.gcode')));

    await waitFor(() => expect(alertText()).toBe('bad.3mf: File too large · plate.gcode: only models can be packed'));
    expect(onUploaded).toHaveBeenCalledTimes(1);
  });
});
