import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StlPicker } from './StlPicker';
import type { LibraryFile } from '../../data/types';

const file = (id: number, original_filename: string, folder = '', missing = false) =>
  ({ id, original_filename, folder, missing }) as LibraryFile;
const FILES = [file(1, 'Root.stl'), file(2, 'Deep.stl', 'x/y'), file(3, 'Gone.stl', '', true), file(4, 'Plate.3mf')];
const listed = () => screen.queryAllByTitle(/^Add /).map(b => b.textContent);

describe('StlPicker', () => {
  it('lists only pickable STLs and adds the clicked one', async () => {
    const onAdd = vi.fn();
    render(<StlPicker files={FILES} onAdd={onAdd} />);
    expect(listed()).toEqual(['Root.stl', 'Deep.stl']);
    await userEvent.click(screen.getByTitle('Add Deep.stl'));
    expect(onAdd).toHaveBeenCalledWith(FILES[1]);
  });

  it('narrows to a folder, saying so when it has no files of its own', async () => {
    render(<StlPicker files={FILES} onAdd={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'x' }));
    expect(listed()).toEqual([]);
    expect(screen.getByText('No STL files here')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'y' }));
    expect(listed()).toEqual(['Deep.stl']);
  });

  it('searches every folder, hiding the tree, and says when nothing matches', async () => {
    render(<StlPicker files={FILES} onAdd={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'x' }));
    await userEvent.type(screen.getByPlaceholderText('Search files…'), 'root');
    expect(listed()).toEqual(['Root.stl']);
    expect(screen.queryByRole('button', { name: 'All files' })).toBeNull();
    await userEvent.type(screen.getByPlaceholderText('Search files…'), 'zz');
    expect(screen.getByText('No matching STL files')).toBeInTheDocument();
  });
});
