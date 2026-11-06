import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FileDropZone } from './FileDropZone';
import { Reply, stubFetch } from '../test/fetchStub';

const file = (name: string) => new File(['x'], name);
const filesDrag = (...files: File[]) => ({ dataTransfer: { files, types: ['Files'], dropEffect: 'none' } });

function renderZone(onUploaded = vi.fn()) {
  render(<FileDropZone onUploaded={onUploaded} label="Drop to add"><p>page body</p></FileDropZone>);
  return { zone: screen.getByTestId('file-drop-zone'), onUploaded };
}

describe('FileDropZone', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uploads each supported dropped file to the library and hands it on; names the ones it skipped', async () => {
    const uploaded = { id: 7, original_filename: 'bracket.stl', kind: 'stl' };
    const api = stubFetch({ 'POST /api/v1/files/upload': uploaded });
    const { zone, onUploaded } = renderZone();

    fireEvent.drop(zone, filesDrag(file('bracket.stl'), file('notes.txt')));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(uploaded));
    expect(onUploaded).toHaveBeenCalledTimes(1);
    const sent = api.to('POST', '/api/v1/files/upload');
    expect(sent).toHaveLength(1);
    expect(((sent[0].body as FormData).get('file') as File).name).toBe('bracket.stl');
    expect((await screen.findByRole('alert')).textContent).toBe('Not a .3mf, .stl or .gcode file: notes.txt');
  });

  it('shows the drop overlay only while files are dragged over, and ignores drags without files', async () => {
    const api = stubFetch({});
    const { zone, onUploaded } = renderZone();

    fireEvent.dragEnter(zone, { dataTransfer: { types: ['text/plain'], files: [] } });   // the page's own item drag
    expect(screen.queryByText('Drop to add')).toBeNull();
    fireEvent.drop(zone, { dataTransfer: { types: ['text/plain'], files: [] } });

    fireEvent.dragEnter(zone, filesDrag(file('a.3mf')));
    expect(screen.getByText('Drop to add')).toBeTruthy();
    fireEvent.dragLeave(zone, filesDrag(file('a.3mf')));
    expect(screen.queryByText('Drop to add')).toBeNull();

    await act(async () => {});
    expect(api.calls).toEqual([]);
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('reports a failed upload or a failed hand-off with the file name and keeps going', async () => {
    stubFetch({
      'POST /api/v1/files/upload': (c: { body: unknown }) => {
        const name = ((c.body as FormData).get('file') as File).name;
        return name === 'bad.3mf' ? new Reply(413, { detail: 'File too large' }) : { id: 9, original_filename: name, kind: '3mf' };
      },
    });
    const onUploaded = vi.fn(async () => { throw new Error('only models can be packed'); });
    const { zone } = renderZone(onUploaded);

    fireEvent.drop(zone, filesDrag(file('bad.3mf'), file('plate.gcode')));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('bad.3mf: File too large');
    expect(alert.textContent).toContain('plate.gcode: only models can be packed');
    expect(onUploaded).toHaveBeenCalledTimes(1);
  });
});
