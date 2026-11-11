// The Project builder's STL picker: which library files it offers, the folder tree it shows them in,
// and which files are in view for the selected folder / search.
import type { FolderNode, LibraryFile } from '../data/types';

/** A folder path in canonical form: no leading, trailing or repeated `/` (`'/a//b/'` → `'a/b'`; root is `''`). */
export function normalizeFolder(folder: string): string {
  return folder.split('/').filter(Boolean).join('/');
}

/** Files the builder can add as a part: an `.stl` (any case) that still exists on disk. */
export function isPickableStl(f: Pick<LibraryFile, 'original_filename' | 'missing'>): boolean {
  return f.original_filename.toLowerCase().endsWith('.stl') && !f.missing;
}

/** The folder tree of `files`, rooted at "All files" (path `''`). Each node's `path` is its canonical folder path. */
export function buildStlTree(files: Pick<LibraryFile, 'folder'>[]): FolderNode {
  const root: FolderNode = { name: 'All files', path: '', count: 0, children: {} };
  for (const f of files) {
    const parts = normalizeFolder(f.folder).split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, i) => {
      if (!node.children[part]) {
        node.children[part] = { name: part, path: parts.slice(0, i + 1).join('/'), count: 0, children: {} };
      }
      node = node.children[part];
    });
  }
  return root;
}

/**
 * The files listed under the tree. A non-blank search matches file names (case-insensitive) in every folder;
 * otherwise the root (`''`) shows everything and any other folder shows only the files directly in it.
 */
export function stlFilesInView<F extends Pick<LibraryFile, 'folder' | 'original_filename'>>(
  stlFiles: F[], selectedFolder: string, search: string,
): F[] {
  const q = search.trim().toLowerCase();
  if (q) return stlFiles.filter(f => f.original_filename.toLowerCase().includes(q));
  if (selectedFolder === '') return stlFiles;
  return stlFiles.filter(f => normalizeFolder(f.folder) === selectedFolder);
}
