// The Project builder's STL folder tree: one row per folder, the root open by default, a folder with
// subfolders toggling open/closed when clicked. Selecting is the caller's (`onSelect(path)`).
import { useState } from 'react';
import { Icons } from './icons';
import type { FolderNode } from '../data/types';

export function StlFolderTree({
  node, depth, selected, onSelect,
}: {
  node: FolderNode;
  depth: number;
  selected: string;
  onSelect: (path: string) => void;
}) {
  const [open, setOpen] = useState(depth === 0);
  const childKeys = Object.keys(node.children).sort();
  const isActive = selected === node.path;
  return (
    <div>
      <button
        onClick={() => { onSelect(node.path); if (childKeys.length) setOpen(o => !o); }}
        style={{
          width: '100%',
          padding: `5px 8px 5px ${8 + depth * 14}px`,
          background: isActive ? 'oklch(87% 0.185 95 / 0.10)' : 'transparent',
          border: 'none',
          color: isActive ? 'var(--accent)' : 'var(--text-2)',
          cursor: 'pointer',
          textAlign: 'left',
          fontSize: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          borderRadius: 4,
        }}
      >
        <span style={{ color: 'var(--text-4)', width: 12 }}>
          {childKeys.length ? (open ? Icons.chevD : Icons.chevR) : null}
        </span>
        <span style={{ color: isActive ? 'var(--accent-hi)' : 'var(--text-3)' }}>{Icons.files}</span>
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </span>
      </button>
      {open && childKeys.map(k => (
        <StlFolderTree key={k} node={node.children[k]} depth={depth + 1}
                   selected={selected} onSelect={onSelect} />
      ))}
    </div>
  );
}
