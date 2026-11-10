// Files: the folder tree and the folder card around it.
import { Icons } from '../../components/icons';
import type { FolderNode } from '../../data/types';
import { JOB_UPLOADS } from './model';

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg className="ico" width="14" height="14" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {open
        ? <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H3V7zM3 10h19l-2 8a2 2 0 0 1-2 1H5a2 2 0 0 1-2-2v-7z" />
        : <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
      }
    </svg>
  );
}

// -------------------------------------------------------------------------
// FolderTreeNode
// -------------------------------------------------------------------------

interface FolderTreeNodeProps {
  node: FolderNode;
  depth: number;
  openSet: Set<string>;
  toggle: (path: string) => void;
  current: string;
  setCurrent: (path: string) => void;
}

export function FolderTreeNode({ node, depth, openSet, toggle, current, setCurrent }: FolderTreeNodeProps) {
  const childKeys = Object.keys(node.children);
  const hasChildren = childKeys.length > 0;
  const isOpen = openSet.has(node.path) || depth === 0;
  const isActive = current === node.path;

  return (
    <div>
      <button
        onClick={() => { setCurrent(node.path); if (hasChildren) toggle(node.path); }}
        style={{
          width: '100%',
          padding: `6px 8px 6px ${8 + depth * 14}px`,
          background: isActive ? 'oklch(87% 0.185 95 / 0.10)' : 'transparent',
          border: 'none',
          borderRadius: 0,
          color: isActive ? 'var(--accent)' : 'var(--text-2)',
          boxShadow: isActive ? 'inset 0 0 0 1px oklch(87% 0.185 95 / 0.25)' : 'none',
          cursor: 'pointer',
          textAlign: 'left',
          fontSize: 13,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}>
        <span style={{
          width: 14, height: 14, display: 'inline-flex',
          color: 'var(--text-4)',
          transform: hasChildren && isOpen ? 'rotate(90deg)' : 'none',
          transition: 'transform 120ms ease',
          flexShrink: 0,
        }}>
          {hasChildren ? Icons.chevR : null}
        </span>
        <span style={{
          width: 14, height: 14, display: 'inline-flex',
          color: isActive ? 'var(--accent-hi)' : 'var(--text-3)',
          flexShrink: 0,
        }}>
          {depth === 0 ? Icons.files : (hasChildren ? <FolderIcon open={isOpen} /> : Icons.files)}
        </span>
        <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {node.name}
        </span>
        <span className="num tiny muted shrink-0">{node.count}</span>
      </button>
      {hasChildren && isOpen && (
        <div>
          {childKeys.sort((a, b) => a === JOB_UPLOADS ? -1 : b === JOB_UPLOADS ? 1 : a.localeCompare(b)).map(k => (
            <FolderTreeNode key={k} node={node.children[k]} depth={depth + 1}
                            openSet={openSet} toggle={toggle}
                            current={current} setCurrent={setCurrent} />
          ))}
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------
// FolderCard
// -------------------------------------------------------------------------

interface FolderCardProps {
  expanded: boolean;
  onToggle: () => void;
  currentFolder: string;
  setCurrentFolder: (path: string) => void;
  breadcrumb: string;
  tree: FolderNode;
  openFolders: Set<string>;
  toggleFolder: (path: string) => void;
  onNewFolder: () => void;
  onDeleteFolder: () => void;
}

export function FolderCard({
  expanded, onToggle, currentFolder, setCurrentFolder,
  breadcrumb, tree, openFolders, toggleFolder, onNewFolder, onDeleteFolder,
}: FolderCardProps) {
  if (!expanded) {
    return (
      <div className="card" style={{
        padding: 6,
        position: 'sticky',
        top: 0,
        width: 44,
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 8,
      }}
      onClick={onToggle}
      title={`Folders — current: ${breadcrumb}`}>
        <button className="btn ghost icon sm"
                onClick={(e) => { e.stopPropagation(); onToggle(); }}>
          {Icons.panel}
        </button>
        <div style={{
          writingMode: 'vertical-rl',
          transform: 'rotate(180deg)',
          padding: '8px 4px',
          color: 'var(--text-2)',
          fontSize: 11,
          fontWeight: 500,
          letterSpacing: '0.05em',
          whiteSpace: 'nowrap',
        }}>
          {breadcrumb}
        </div>
        <div className="tag-key" style={{
          writingMode: 'vertical-rl',
          transform: 'rotate(180deg)',
          color: 'var(--text-4)',
          padding: '4px 0',
        }}>Folders</div>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 10, position: 'sticky', top: 0 }}>
      <div className="row between" style={{ padding: '4px 6px 8px', alignItems: 'center' }}>
        <span className="tag-key">Folders</span>
        <div className="row gap-2">
          <button className="btn ghost icon sm" title="New folder"
                  onClick={onNewFolder}>{Icons.plus}</button>
          <button className="btn ghost icon sm"
                  aria-label="Delete folder"
                  title={
                    !currentFolder ? 'Select a folder to delete'
                    : currentFolder === `/${JOB_UPLOADS}` ? 'The Job Uploads folder can’t be deleted'
                    : `Delete folder “${breadcrumb}” (must be empty)`
                  }
                  disabled={!currentFolder || currentFolder === `/${JOB_UPLOADS}`}
                  onClick={onDeleteFolder}>{Icons.trash}</button>
          <button className="btn ghost icon sm" title="Collapse" onClick={onToggle}>{Icons.chevL}</button>
        </div>
      </div>
      <FolderTreeNode node={tree} depth={0}
                      openSet={openFolders} toggle={toggleFolder}
                      current={currentFolder} setCurrent={setCurrentFolder} />
    </div>
  );
}

// -------------------------------------------------------------------------
// FilterCard
// -------------------------------------------------------------------------
