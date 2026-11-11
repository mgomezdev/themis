import { describe, expect, it } from 'vitest';
import { buildStlTree, filesInView, isPickableModel, normalizeFolder } from './stlTree';
import type { FolderNode } from '../data/types';

const f = (original_filename: string, folder = '', missing = false) => ({ original_filename, folder, missing });

/** Every node's path, depth-first, keyed by its chain of names — so a wrong `path` is visible at a glance. */
function paths(node: FolderNode, prefix = ''): Record<string, string> {
  const here = prefix ? `${prefix}>${node.name}` : node.name;
  return Object.values(node.children).reduce(
    (acc, child) => ({ ...acc, ...paths(child, here) }),
    { [here]: node.path } as Record<string, string>,
  );
}

describe('normalizeFolder', () => {
  it.each([
    ['', ''],
    ['/', ''],
    ['a', 'a'],
    ['/a', 'a'],
    ['a/', 'a'],
    ['/a//b/', 'a/b'],
    ['a/b/c', 'a/b/c'],
  ])('%j -> %j', (input, expected) => {
    expect(normalizeFolder(input)).toBe(expected);
  });
});

describe('isPickableModel', () => {
  it.each([
    [f('part.stl'), true],
    [f('PART.STL'), true],
    [f('plate.3mf'), true],
    [f('Sliced.GCODE'), true],
    [f('part.stl', '', true), false],      // missing on disk
    [f('notes.txt'), false],
    [f('part.stl.zip'), false],
    [f('stl'), false],
    [f('part.gcode.3mf'), true],          // a pre-sliced 3MF is a 3MF file
  ])('%j -> %s', (file, expected) => {
    expect(isPickableModel(file)).toBe(expected);
  });
});

describe('buildStlTree', () => {
  it('is a bare "All files" root when every file is at the top level', () => {
    expect(buildStlTree([f('a.stl'), f('b.stl', '/')])).toEqual({ name: 'All files', path: '', count: 0, children: {} });
  });

  it('nests folders and gives each node its full path', () => {
    expect(paths(buildStlTree([f('1', 'Toys/Cars'), f('2', 'Toys'), f('3', 'Tools')]))).toEqual({
      'All files': '',
      'All files>Toys': 'Toys',
      'All files>Toys>Cars': 'Toys/Cars',
      'All files>Tools': 'Tools',
    });
  });

  it('merges files that share a folder into one node', () => {
    const tree = buildStlTree([f('1', 'Toys'), f('2', 'Toys'), f('3', 'Toys/Cars'), f('4', 'Toys/Cars')]);
    expect(Object.keys(tree.children)).toEqual(['Toys']);
    expect(Object.keys(tree.children.Toys.children)).toEqual(['Cars']);
  });

  it('gives a folder that repeats an ancestor\'s name its own path, not the ancestor\'s', () => {
    expect(paths(buildStlTree([f('1', 'a/x/a'), f('2', 'b/b/b')]))).toEqual({
      'All files': '',
      'All files>a': 'a',
      'All files>a>x': 'a/x',
      'All files>a>x>a': 'a/x/a',
      'All files>b': 'b',
      'All files>b>b': 'b/b',
      'All files>b>b>b': 'b/b/b',
    });
  });

  it('ignores leading, trailing and doubled slashes', () => {
    expect(paths(buildStlTree([f('1', '/a//b/')]))).toEqual({
      'All files': '', 'All files>a': 'a', 'All files>a>b': 'a/b',
    });
  });
});

describe('filesInView', () => {
  const FILES = [
    f('Root.stl', ''),
    f('Ball.stl', 'Toys'),
    f('Car.stl', '/Toys/Cars'),
    f('Slash.stl', 'Tools/'),
    f('Bolt.stl', 'Tools'),
  ];
  const names = (out: { original_filename: string }[]) => out.map(x => x.original_filename);

  it('shows every file at the root', () => {
    expect(names(filesInView(FILES, '', ''))).toEqual(names(FILES));
  });

  it('shows only the files directly in the selected folder, not its subfolders', () => {
    expect(names(filesInView(FILES, 'Toys', ''))).toEqual(['Ball.stl']);
    expect(names(filesInView(FILES, 'Toys/Cars', ''))).toEqual(['Car.stl']);
  });

  it('matches a selected folder however the file\'s folder is slashed', () => {
    expect(names(filesInView(FILES, 'Tools', ''))).toEqual(['Slash.stl', 'Bolt.stl']);
  });

  it('searches names case-insensitively across all folders, ignoring the selected one', () => {
    expect(names(filesInView(FILES, 'Tools', 'CAR'))).toEqual(['Car.stl']);
    expect(names(filesInView(FILES, '', 'l.stl'))).toEqual(['Ball.stl']);
  });

  it('trims the search, and treats a blank one as no search', () => {
    expect(names(filesInView(FILES, '', '  bolt '))).toEqual(['Bolt.stl']);
    expect(names(filesInView(FILES, 'Toys', '   '))).toEqual(['Ball.stl']);
  });

  it('is empty for a folder with no files of its own', () => {
    expect(filesInView(FILES, 'Nope', '')).toEqual([]);
  });
});
