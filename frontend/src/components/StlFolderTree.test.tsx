import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StlFolderTree } from './StlFolderTree';
import { Icons } from './icons';
import type React from 'react';
import { buildStlTree } from '../lib/stlTree';

const TREE = buildStlTree([{ folder: 'Toys/Cars' }, { folder: 'Toys/Balls' }, { folder: 'Tools' }]);
const row = (name: string) => screen.queryByRole('button', { name });
const rowNames = () => screen.getAllByRole('button').map(b => b.textContent);

function renderTree(selected = '') {
  const onSelect = vi.fn();
  render(<StlFolderTree node={TREE} depth={0} selected={selected} onSelect={onSelect} />);
  return onSelect;
}

describe('StlFolderTree', () => {
  it('opens the root by default with its folders sorted, subfolders closed', () => {
    renderTree();
    expect(rowNames()).toEqual(['All files', 'Tools', 'Toys']);
  });

  it('selects a folder by its path and toggles its subfolders open and closed', async () => {
    const onSelect = renderTree();
    await userEvent.click(row('Toys')!);
    expect(onSelect).toHaveBeenLastCalledWith('Toys');
    expect(rowNames()).toEqual(['All files', 'Tools', 'Toys', 'Balls', 'Cars']);

    await userEvent.click(row('Cars')!);
    expect(onSelect).toHaveBeenLastCalledWith('Toys/Cars');

    await userEvent.click(row('Toys')!);
    expect(rowNames()).toEqual(['All files', 'Tools', 'Toys']);
  });

  it('selects a leaf folder without changing what is shown', async () => {
    const onSelect = renderTree();
    await userEvent.click(row('Tools')!);
    expect(onSelect).toHaveBeenLastCalledWith('Tools');
    expect(rowNames()).toEqual(['All files', 'Tools', 'Toys']);
  });

  it('collapses the root when clicked, and selects it (path "")', async () => {
    const onSelect = renderTree('Tools');
    await userEvent.click(row('All files')!);
    expect(onSelect).toHaveBeenLastCalledWith('');
    expect(rowNames()).toEqual(['All files']);
  });

  it('shows an open/closed chevron only on folders with subfolders', async () => {
    const markup = (icon: React.ReactElement) => render(<>{icon}</>).container.innerHTML;
    const OPEN = markup(Icons.chevD);
    const CLOSED = markup(Icons.chevR);
    renderTree();
    const chevron = (name: string) => row(name)!.querySelector('span')!.innerHTML;
    expect(chevron('All files')).toBe(OPEN);
    expect(chevron('Toys')).toBe(CLOSED);
    expect(chevron('Tools')).toBe('');
    await userEvent.click(row('Toys')!);
    expect(chevron('Toys')).toBe(OPEN);
  });

  it('highlights only the selected folder', () => {
    renderTree('Tools');
    const icon = (name: string) => (row(name)!.querySelectorAll('span')[1] as HTMLElement).style.color;
    expect(icon('Tools')).toBe('var(--accent-hi)');
    expect(icon('Toys')).toBe('var(--text-3)');
    expect(row('Tools')!.style.color).toBe('var(--accent)');
    expect(row('Toys')!.style.color).toBe('var(--text-1)');
    expect(row('All files')!.style.color).toBe('var(--text-1)');
  });

  it('indents each level', async () => {
    renderTree();
    await userEvent.click(row('Toys')!);
    expect(row('All files')!.style.paddingLeft).toBe('8px');
    expect(row('Toys')!.style.paddingLeft).toBe('22px');
    expect(row('Cars')!.style.paddingLeft).toBe('36px');
  });
});
