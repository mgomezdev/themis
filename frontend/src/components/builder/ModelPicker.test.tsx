import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ModelPicker } from './ModelPicker';
import type { LibraryFile } from '../../data/types';

const file = (id: number, original_filename: string, folder = '', missing = false) =>
  ({ id, original_filename, folder, missing }) as LibraryFile;
const FILES = [file(1, 'Root.stl'), file(2, 'Deep.stl', 'x/y'), file(3, 'Gone.stl', '', true), file(4, 'Plate.3mf'), file(5, 'notes.txt')];
/** The picker with its folder/search held by a parent, as the screen does. */
function Picker({ onAdd }: { onAdd: (f: LibraryFile) => void }) {
  const [selectedFolder, setSelectedFolder] = useState('');
  const [search, setSearch] = useState('');
  return <ModelPicker files={FILES} onAdd={onAdd} selectedFolder={selectedFolder} setSelectedFolder={setSelectedFolder}
                      search={search} setSearch={setSearch} />;
}
const listed = () => screen.queryAllByTitle(/^Add /).map(b => b.textContent);

describe('ModelPicker', () => {
  it('lists only pickable models and adds the clicked one', async () => {
    const onAdd = vi.fn();
    render(<Picker onAdd={onAdd} />);
    expect(listed()).toEqual(['Root.stl', 'Deep.stl', 'Plate.3mf']);
    await userEvent.click(screen.getByTitle('Add Deep.stl'));
    expect(onAdd).toHaveBeenCalledWith(FILES[1]);
  });

  it('narrows to a folder, saying so when it has no files of its own', async () => {
    render(<Picker onAdd={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'x' }));
    expect(listed()).toEqual([]);
    expect(screen.getByText('No STL files here')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'y' }));
    expect(listed()).toEqual(['Deep.stl']);
  });

  it('searches every folder, hiding the tree, and says when nothing matches', async () => {
    render(<Picker onAdd={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'x' }));
    await userEvent.type(screen.getByPlaceholderText('Search files…'), 'root');
    expect(listed()).toEqual(['Root.stl']);
    expect(screen.queryByRole('button', { name: 'All files' })).toBeNull();
    await userEvent.type(screen.getByPlaceholderText('Search files…'), 'zz');
    expect(screen.getByText('No matching STL files')).toBeInTheDocument();
  });
});
