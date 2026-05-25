import React, { useState, useMemo } from 'react';
import { FILES } from '../data/mock';
import { shade } from '../data/helpers';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import type { FileEntry } from '../data/types';

// -------------------------------------------------------------------------
// Folder tree types and builder
// -------------------------------------------------------------------------

interface FolderNode {
  name: string;
  path: string;
  count: number;
  children: Record<string, FolderNode>;
}

function buildFolderTree(files: FileEntry[]): FolderNode {
  const root: FolderNode = { name: 'All files', path: '', count: 0, children: {} };
  for (const f of files) {
    root.count++;
    const parts = (f.folder || '').split('/').filter(Boolean);
    let node = root;
    let path = '';
    for (const p of parts) {
      path += '/' + p;
      if (!node.children[p]) {
        node.children[p] = { name: p, path, count: 0, children: {} };
      }
      node = node.children[p];
      node.count++;
    }
  }
  return root;
}

// -------------------------------------------------------------------------
// FolderIcon
// -------------------------------------------------------------------------

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

function FolderTreeNode({ node, depth, openSet, toggle, current, setCurrent }: FolderTreeNodeProps) {
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
          background: isActive ? 'var(--bg-3)' : 'transparent',
          border: 'none',
          borderRadius: 6,
          color: isActive ? 'var(--text-1)' : 'var(--text-2)',
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
        <span className="num tiny muted" style={{ flexShrink: 0 }}>{node.count}</span>
      </button>
      {hasChildren && isOpen && (
        <div>
          {childKeys.sort().map(k => (
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
}

function FolderCard({
  expanded, onToggle, currentFolder, setCurrentFolder,
  breadcrumb, tree, openFolders, toggleFolder,
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
          <button className="btn ghost icon sm" title="New folder">{Icons.plus}</button>
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

const TAG_GROUPS = [
  { label: 'Material', tags: ['PLA', 'PETG', 'PA-CF', 'ABS', 'TPU'] },
  { label: 'Purpose',  tags: ['structural', 'fixture', 'cosmetic', 'mechanism', 'damper', 'figurine', 'enclosure', 'display'] },
  { label: 'Stage',    tags: ['prototype', 'production', 'reusable', 'archived', 'multi-color'] },
] as const;

interface FilterCardProps {
  expanded: boolean;
  onToggle: () => void;
  activeTags: string[];
  setActiveTags: (tags: string[]) => void;
  toggleTag: (tag: string) => void;
  tagCounts: Record<string, number>;
}

function FilterCard({ expanded, onToggle, activeTags, setActiveTags, toggleTag, tagCounts }: FilterCardProps) {
  return (
    <div className="card" style={{ padding: expanded ? 14 : '10px 14px' }}>
      <div className="row between" style={{
        marginBottom: expanded ? 8 : 0,
        alignItems: 'center',
        gap: 12,
        flexWrap: 'wrap',
      }}>
        <div className="row gap-3" style={{ alignItems: 'center', flex: 1, minWidth: 0, flexWrap: 'wrap' }}>
          <span className="tag-key" style={{ flexShrink: 0 }}>Filter by tag</span>
          {activeTags.length > 0 ? (
            <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
              {activeTags.map(t => (
                <button key={t} onClick={() => toggleTag(t)}
                        className="row gap-2"
                        style={{
                          padding: '4px 10px',
                          background: 'var(--accent)',
                          color: '#04101f',
                          border: 'none',
                          borderRadius: 999,
                          fontSize: 12,
                          fontWeight: 600,
                          cursor: 'pointer',
                          alignItems: 'center',
                          whiteSpace: 'nowrap',
                          display: 'inline-flex',
                        }}>
                  {t}
                  <span style={{ width: 12, height: 12, display: 'inline-flex' }}>{Icons.x}</span>
                </button>
              ))}
            </div>
          ) : (
            !expanded && <span className="tiny muted">No filters · click to add</span>
          )}
        </div>
        <div className="row gap-2" style={{ flexShrink: 0 }}>
          {activeTags.length > 0 && (
            <button className="btn ghost sm" onClick={() => setActiveTags([])}>
              Clear {activeTags.length}
            </button>
          )}
          <button className="btn ghost icon sm"
                  title={expanded ? 'Hide facets' : 'Show facets'}
                  onClick={onToggle}>
            <span style={{
              display: 'inline-flex',
              transform: expanded ? 'rotate(180deg)' : 'none',
              transition: 'transform 160ms ease',
            }}>{Icons.chevD}</span>
          </button>
        </div>
      </div>

      {expanded && (
        <div className="col gap-3" style={{ marginTop: 4 }}>
          {TAG_GROUPS.map(group => {
            const groupTags = group.tags.filter(t => tagCounts[t] != null);
            if (groupTags.length === 0) return null;
            return (
              <div key={group.label}>
                <div className="tag-key" style={{ marginBottom: 6 }}>{group.label}</div>
                <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
                  {groupTags.map(t => {
                    const on = activeTags.includes(t);
                    const count = tagCounts[t];
                    return (
                      <button key={t} onClick={() => toggleTag(t)}
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 6,
                                padding: '4px 10px',
                                borderRadius: 999,
                                border: `1px solid ${on ? 'rgba(59,130,246,0.4)' : 'var(--border-1)'}`,
                                background: on ? 'rgba(59,130,246,0.12)' : 'var(--bg-1)',
                                color: on ? 'var(--accent-hi)' : 'var(--text-2)',
                                fontSize: 12,
                                fontWeight: 500,
                                cursor: 'pointer',
                                whiteSpace: 'nowrap',
                              }}>
                        <span style={{
                          width: 12, height: 12, borderRadius: 3,
                          display: 'inline-grid', placeItems: 'center',
                          background: on ? 'var(--accent)' : 'transparent',
                          border: `1.5px solid ${on ? 'var(--accent)' : 'var(--border-2)'}`,
                          color: '#04101f', flexShrink: 0,
                        }}>
                          {on && (
                            <svg width="8" height="8" viewBox="0 0 24 24" fill="none"
                                 stroke="currentColor" strokeWidth="4">
                              <path d="M20 6 9 17l-5-5" />
                            </svg>
                          )}
                        </span>
                        {t}
                        <span className="num tiny"
                              style={{ color: on ? 'var(--accent-hi)' : 'var(--text-4)', fontWeight: 500 }}>
                          {count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------
// FilesScreen
// -------------------------------------------------------------------------

export function FilesScreen() {
  const [currentFolder, setCurrentFolder] = useState('');
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [openFolders, setOpenFolders] = useState<Set<string>>(
    new Set(['/Customers', '/Internal', '/Internal/R&D'])
  );
  const [sort, setSort] = useState('updated');
  const [folderExpanded, setFolderExpanded] = useState(true);
  const [filterExpanded, setFilterExpanded] = useState(true);

  const tree = useMemo(() => buildFolderTree(FILES), []);

  const inFolder = FILES.filter(f =>
    currentFolder === '' || f.folder === currentFolder || f.folder.startsWith(currentFolder + '/')
  );

  const tagCounts: Record<string, number> = {};
  for (const f of inFolder) {
    for (const t of f.tags) {
      tagCounts[t] = (tagCounts[t] ?? 0) + 1;
    }
  }

  const filtered = inFolder.filter(f =>
    activeTags.every(t => f.tags.includes(t))
  );

  const sorted = [...filtered].sort((a, b) => {
    if (sort === 'name') return a.name.localeCompare(b.name);
    if (sort === 'size') return parseFloat(b.size) - parseFloat(a.size);
    return 0;
  });

  const toggleFolder = (path: string) => {
    const next = new Set(openFolders);
    if (next.has(path)) next.delete(path); else next.add(path);
    setOpenFolders(next);
  };

  const toggleTag = (tag: string) => {
    setActiveTags(activeTags.includes(tag)
      ? activeTags.filter(t => t !== tag)
      : [...activeTags, tag]);
  };

  const breadcrumb = currentFolder === ''
    ? 'Workshop'
    : currentFolder.split('/').filter(Boolean).join(' / ');

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: folderExpanded ? '240px 1fr' : 'auto 1fr',
      gap: 18,
      alignItems: 'flex-start',
      transition: 'grid-template-columns 200ms ease',
    }}>
      {/* LEFT: folder card */}
      <FolderCard
        expanded={folderExpanded}
        onToggle={() => setFolderExpanded(!folderExpanded)}
        currentFolder={currentFolder}
        setCurrentFolder={setCurrentFolder}
        breadcrumb={breadcrumb}
        tree={tree}
        openFolders={openFolders}
        toggleFolder={toggleFolder}
      />

      {/* RIGHT: filters + grid */}
      <div className="col gap-4" style={{ minWidth: 0 }}>
        {/* Header */}
        <div className="row between">
          <div className="col" style={{ minWidth: 0, flex: 1 }}>
            <span className="tag-key">{currentFolder === '' ? 'Workshop' : 'Folder'}</span>
            <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', marginTop: 2 }}>
              {breadcrumb}
            </div>
            <div className="tiny muted" style={{ marginTop: 2, whiteSpace: 'nowrap' }}>
              {sorted.length} {sorted.length === 1 ? 'file' : 'files'}
              {activeTags.length > 0 && (
                <span> matching {activeTags.length} tag{activeTags.length > 1 ? 's' : ''}</span>
              )}
            </div>
          </div>
          <div className="row gap-2" style={{ flexShrink: 0 }}>
            <select className="select" style={{ width: 'auto', paddingRight: 32 }}
                    value={sort} onChange={e => setSort(e.target.value)}>
              <option value="updated">Recently updated</option>
              <option value="name">Name (A–Z)</option>
              <option value="size">Largest first</option>
            </select>
          </div>
        </div>

        {/* Tag facets card */}
        <FilterCard
          expanded={filterExpanded}
          onToggle={() => setFilterExpanded(!filterExpanded)}
          activeTags={activeTags}
          setActiveTags={setActiveTags}
          toggleTag={toggleTag}
          tagCounts={tagCounts}
        />

        {/* File grid */}
        {sorted.length === 0 ? (
          <Empty title="No files match" sub="Try removing a tag or picking a different folder." icon={Icons.files} />
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
            gap: 12,
          }}>
            {sorted.map(f => (
              <div key={f.id} className="card" style={{ padding: 10, cursor: 'pointer' }}>
                <div style={{
                  width: '100%', aspectRatio: '1/1',
                  background: `linear-gradient(135deg, ${f.thumbColor}, ${shade(f.thumbColor, -25)})`,
                  borderRadius: 6,
                  border: '1px solid var(--border-1)',
                  position: 'relative',
                  overflow: 'hidden',
                }}>
                  <div style={{
                    position: 'absolute', inset: '20%',
                    border: '1px dashed rgba(255,255,255,0.18)',
                    borderRadius: 4,
                  }} />
                  <div style={{
                    position: 'absolute', bottom: 6, right: 6,
                    fontFamily: 'var(--font-mono)', fontSize: 10,
                    color: 'rgba(255,255,255,0.6)',
                    background: 'rgba(0,0,0,0.4)',
                    padding: '1px 5px', borderRadius: 3,
                  }}>{f.parts}p</div>
                </div>
                <div style={{
                  fontSize: 12, marginTop: 8, fontWeight: 500,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  {f.name}
                </div>
                <div className="row between" style={{ marginTop: 3 }}>
                  <span className="tiny muted" style={{ whiteSpace: 'nowrap' }}>{f.size}</span>
                  <span className="tiny muted" style={{ whiteSpace: 'nowrap' }}>{f.updated} ago</span>
                </div>
                <div className="row gap-2" style={{ flexWrap: 'wrap', marginTop: 8 }}>
                  {f.tags.slice(0, 3).map(t => (
                    <span key={t} className="elig" style={{
                      fontSize: 9.5, padding: '1px 5px',
                      background: activeTags.includes(t) ? 'rgba(59,130,246,0.12)' : 'var(--bg-1)',
                      color: activeTags.includes(t) ? 'var(--accent-hi)' : 'var(--text-3)',
                    }}>{t}</span>
                  ))}
                  {f.tags.length > 3 && (
                    <span className="elig" style={{ fontSize: 9.5, padding: '1px 5px' }}>
                      +{f.tags.length - 3}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
