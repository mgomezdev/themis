// The Project builder's left column: the library's pickable STL files, by folder or by name search.
// Clicking a file adds it to the project (`onAdd`).
import { useState } from 'react';
import { Icons } from '../icons';
import { StlFolderTree } from '../StlFolderTree';
import { buildStlTree, isPickableStl, stlFilesInView } from '../../lib/stlTree';
import type { LibraryFile } from '../../data/types';

export function StlPicker({ files, onAdd }: { files: LibraryFile[]; onAdd: (f: LibraryFile) => void }) {
  const stlFiles = files.filter(isPickableStl);
  const [selectedFolder, setSelectedFolder] = useState('');
  const [search, setSearch] = useState('');
  const searchLower = search.trim().toLowerCase();
  const folderTree = buildStlTree(stlFiles);
  const folderFiles = stlFilesInView(stlFiles, selectedFolder, search);
  return (
    <div className="card" style={{ padding: 10, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 0 }}>
      <div style={{ padding: '4px 6px 8px', fontSize: 11, fontWeight: 600,
                    color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        STL Files
      </div>
      <input
        className="input"
        placeholder="Search files…"
        value={search}
        onChange={e => setSearch(e.target.value)}
        style={{ marginBottom: 6, fontSize: 12 }}
      />
      {!searchLower && (
        <StlFolderTree node={folderTree} depth={0} selected={selectedFolder} onSelect={setSelectedFolder} />
      )}
      <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 6 }}>
        {folderFiles.length === 0 && (
          <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--text-4)' }}>
            {searchLower ? 'No matching STL files' : 'No STL files here'}
          </div>
        )}
        {folderFiles.map(f => (
          <button
            key={f.id}
            onClick={() => onAdd(f)}
            title={`Add ${f.original_filename}`}
            style={{
              width: '100%', padding: '5px 10px',
              background: 'transparent', border: 'none',
              color: 'var(--text-2)', cursor: 'pointer',
              textAlign: 'left', fontSize: 12,
              display: 'flex', alignItems: 'center', gap: 6, borderRadius: 4,
            }}
          >
            <span style={{ color: 'var(--text-4)', flexShrink: 0 }}>{Icons.files}</span>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {f.original_filename}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
