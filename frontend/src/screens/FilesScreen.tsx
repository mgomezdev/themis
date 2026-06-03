import { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { shade } from '../data/helpers';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import type { LibraryFile, FolderNode } from '../data/types';
import {
  useFiles, uploadLibraryFile, createFolder, updateFile, deleteFile,
  addFileTag, removeFileTag, rescanLibrary, getFolderDirs,
} from '../api/files';
import { useTags } from '../api/tags';
import type { Tag } from '../api/tags';

// -------------------------------------------------------------------------
// helpers
// -------------------------------------------------------------------------

const fmtSize = (bytes: number) => `${(bytes / 1e6).toFixed(1)} MB`;

// fallback gradient color for files without a thumbnail, derived from the id
const FALLBACK_COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#14b8a6', '#ec4899'];
const fallbackColor = (id: number) => FALLBACK_COLORS[id % FALLBACK_COLORS.length];

// -------------------------------------------------------------------------
// Folder tree builder (client-side from files[].folder)
// -------------------------------------------------------------------------

function buildFolderTree(files: LibraryFile[]): FolderNode {
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
  onNewFolder: () => void;
}

function FolderCard({
  expanded, onToggle, currentFolder, setCurrentFolder,
  breadcrumb, tree, openFolders, toggleFolder, onNewFolder,
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

interface FacetGroup { label: string; tags: string[]; }

interface FilterCardProps {
  expanded: boolean;
  onToggle: () => void;
  activeTags: string[];
  setActiveTags: (tags: string[]) => void;
  toggleTag: (tag: string) => void;
  tagCounts: Record<string, number>;
  facetGroups: FacetGroup[];
}

function FilterCard({
  expanded, onToggle, activeTags, setActiveTags, toggleTag, tagCounts, facetGroups,
}: FilterCardProps) {
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
          {facetGroups.map(group => {
            if (group.tags.length === 0) return null;
            return (
              <div key={group.label}>
                <div className="tag-key" style={{ marginBottom: 6 }}>{group.label}</div>
                <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
                  {group.tags.map(t => {
                    const on = activeTags.includes(t);
                    const count = tagCounts[t] ?? 0;
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
// FilesTabBar
// -------------------------------------------------------------------------

function FilesTabBar({ tab, setTab }: { tab: string; setTab: (t: 'library' | 'manyfold') => void }) {
  return (
    <div className="row gap-2" style={{ marginBottom: 14 }}>
      <button className={`btn sm ${tab === 'library' ? 'primary' : 'ghost'}`}
              onClick={() => setTab('library')}>Library</button>
      <button className={`btn sm ${tab === 'manyfold' ? 'primary' : 'ghost'}`}
              onClick={() => setTab('manyfold')}>Manyfold</button>
    </div>
  );
}

// -------------------------------------------------------------------------
// FileThumb — image when available, gradient fallback otherwise
// -------------------------------------------------------------------------

function FileThumb({ file, large }: { file: LibraryFile; large?: boolean }) {
  const color = fallbackColor(file.id);
  return (
    <div style={{
      width: '100%', aspectRatio: '1/1',
      background: file.thumbnail_url
        ? 'var(--bg-1)'
        : `linear-gradient(135deg, ${color}, ${shade(color, -25)})`,
      borderRadius: 6,
      border: '1px solid var(--border-1)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {file.thumbnail_url ? (
        <img src={file.thumbnail_url} alt={file.original_filename}
             style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      ) : (
        <div style={{
          position: 'absolute', inset: '20%',
          border: '1px dashed rgba(255,255,255,0.18)',
          borderRadius: 4,
        }} />
      )}
      <div style={{
        position: 'absolute', bottom: 6, right: 6,
        fontFamily: 'var(--font-mono)', fontSize: large ? 11 : 10,
        color: 'rgba(255,255,255,0.7)',
        background: 'rgba(0,0,0,0.45)',
        padding: '1px 5px', borderRadius: 3,
      }}>{file.plate_count}p</div>
    </div>
  );
}

// -------------------------------------------------------------------------
// FileDetailPanel — right drawer (chose drawer over modal: simpler, keeps grid visible)
// -------------------------------------------------------------------------

interface FileDetailPanelProps {
  file: LibraryFile;
  tags: Tag[];
  onClose: () => void;
  onRename: (f: LibraryFile) => void;
  onMove: (f: LibraryFile) => void;
  onDelete: (f: LibraryFile) => void;
  onAddTag: (f: LibraryFile, tagId: number) => void;
  onRemoveTag: (f: LibraryFile, tagId: number) => void;
  onUseInJob: (f: LibraryFile) => void;
}

function FileDetailPanel({
  file, tags, onClose, onRename, onMove, onDelete, onAddTag, onRemoveTag, onUseInJob,
}: FileDetailPanelProps) {
  const fileTagIds = new Set(file.tags.map(t => t.id));
  const available = tags.filter(t => !fileTagIds.has(t.id));

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: 360, maxWidth: '90vw',
      background: 'var(--bg-2)', borderLeft: '1px solid var(--border-1)',
      boxShadow: '-12px 0 40px rgba(0,0,0,0.4)', zIndex: 50,
      display: 'flex', flexDirection: 'column', overflowY: 'auto', padding: 16,
    }}>
      <div className="row between" style={{ alignItems: 'center', marginBottom: 12 }}>
        <span className="tag-key">File details</span>
        <button className="btn ghost icon sm" title="Close" onClick={onClose}>{Icons.x}</button>
      </div>

      <div style={{ width: '100%', maxWidth: 240, alignSelf: 'center', marginBottom: 14 }}>
        <FileThumb file={file} large />
      </div>

      <div style={{ fontSize: 16, fontWeight: 600, wordBreak: 'break-word' }}>
        {file.original_filename}
      </div>
      {file.missing && (
        <div className="tiny" style={{ color: 'var(--danger, #ef4444)', marginTop: 4 }}>
          Missing on disk
        </div>
      )}

      <div className="col gap-2" style={{ marginTop: 12 }}>
        <div className="row between"><span className="tiny muted">Folder</span>
          <span className="tiny">{file.folder || '/'}</span></div>
        <div className="row between"><span className="tiny muted">Size</span>
          <span className="tiny">{fmtSize(file.size_bytes)}</span></div>
        <div className="row between"><span className="tiny muted">Plates</span>
          <span className="tiny">{file.plate_count}</span></div>
      </div>

      <div style={{ marginTop: 16 }}>
        <div className="tag-key" style={{ marginBottom: 6 }}>Tags</div>
        <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
          {file.tags.map(t => (
            <button key={t.id} onClick={() => onRemoveTag(file, t.id)}
                    className="row gap-2"
                    title="Remove tag"
                    style={{
                      padding: '3px 9px', borderRadius: 999, border: 'none',
                      background: 'var(--bg-3)', color: 'var(--text-2)',
                      fontSize: 11.5, fontWeight: 500, cursor: 'pointer',
                      alignItems: 'center', display: 'inline-flex',
                    }}>
              {t.name}
              <span style={{ width: 11, height: 11, display: 'inline-flex' }}>{Icons.x}</span>
            </button>
          ))}
          {file.tags.length === 0 && <span className="tiny muted">No tags</span>}
        </div>
        {available.length > 0 && (
          <select className="select" style={{ marginTop: 8, width: '100%' }}
                  value=""
                  onChange={e => {
                    const id = Number(e.target.value);
                    if (id) onAddTag(file, id);
                    e.target.value = '';
                  }}>
            <option value="">Add tag…</option>
            {available.map(t => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        )}
      </div>

      <div className="col gap-2" style={{ marginTop: 18 }}>
        <button className="btn primary" onClick={() => onUseInJob(file)}>
          <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.play}</span>
          Use in new job
        </button>
        <div className="row gap-2">
          <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => onRename(file)}>Rename</button>
          <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => onMove(file)}>Move</button>
        </div>
        <button className="btn ghost sm" onClick={() => onDelete(file)}
                style={{ color: 'var(--danger, #ef4444)' }}>
          <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.trash}</span>
          Delete
        </button>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------
// FolderPicker — modal to choose an existing destination folder (+ New folder)
// -------------------------------------------------------------------------

const NO_PICK = ' '; // sentinel: a path that matches no real folder

function FolderPicker({ count, onPick, onClose }: {
  count: number;
  onPick: (folder: string) => void;
  onClose: () => void;
}) {
  const [dirs, setDirs] = useState<FolderNode | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const reload = () => getFolderDirs().then(setDirs).catch(err => window.alert(String(err)));
  useEffect(() => { reload(); }, []);

  const toggle = (p: string) => setOpen(prev => {
    const n = new Set(prev); if (n.has(p)) n.delete(p); else n.add(p); return n;
  });

  async function newFolder() {
    const parent = picked ?? '';
    const name = window.prompt('New folder name');
    if (!name) return;
    const path = `${parent}/${name}`.replace(/\/+/g, '/');
    try {
      const res = await createFolder(path);
      await reload();
      setPicked(res.path);
      const o = new Set(open);
      let acc = '';
      for (const part of res.path.split('/').filter(Boolean)) { acc += '/' + part; o.add(acc); }
      setOpen(o);
    } catch (err) { window.alert(String(err)); }
  }

  const destLabel = picked == null ? '—' : (picked === '' ? 'All files (root)' : picked);

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
      display: 'grid', placeItems: 'center', zIndex: 50,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{
        width: 'min(460px, 92vw)', maxHeight: '80vh',
        display: 'flex', flexDirection: 'column', padding: 0,
      }}>
        <div className="row between" style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-1)' }}>
          <div style={{ fontWeight: 600 }}>Move {count} file{count === 1 ? '' : 's'} to…</div>
          <button className="btn ghost icon sm" onClick={onClose}>
            <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.x}</span>
          </button>
        </div>
        <div style={{ padding: 8, overflow: 'auto', flex: 1, minHeight: 140 }}>
          {dirs
            ? <FolderTreeNode node={dirs} depth={0} openSet={open} toggle={toggle}
                              current={picked ?? NO_PICK} setCurrent={setPicked} />
            : <div className="muted small" style={{ padding: 12 }}>Loading folders…</div>}
        </div>
        <div className="row between" style={{
          padding: '12px 16px', borderTop: '1px solid var(--border-1)', alignItems: 'center', gap: 8,
        }}>
          <button className="btn ghost sm" onClick={newFolder}>
            <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.plus}</span>
            New folder
          </button>
          <div className="row gap-2" style={{ alignItems: 'center' }}>
            <span className="tiny muted" style={{
              maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>→ {destLabel}</span>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn primary sm" disabled={picked == null}
                    onClick={() => onPick(picked === '' ? '/' : picked as string)}>
              Move here
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------
// FilesScreen
// -------------------------------------------------------------------------

export function FilesScreen() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'library' | 'manyfold'>('library');
  const [currentFolder, setCurrentFolder] = useState('');
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [sort, setSort] = useState('updated');
  const [selected, setSelected] = useState<LibraryFile | null>(null);
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());
  const [folderExpanded, setFolderExpanded] = useState(true);
  const [filterExpanded, setFilterExpanded] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [movePicker, setMovePicker] = useState<{ ids: number[] } | null>(null);

  const filter = useMemo(() => ({
    folder: currentFolder || undefined,
    tags: activeTags.length ? activeTags : undefined,
    sort,
  }), [currentFolder, activeTags, sort]);
  const { files, refetch } = useFiles(filter);
  const { tags } = useTags();

  const tree = useMemo(() => buildFolderTree(files), [files]);

  const tagCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of files) for (const t of f.tags) c[t.name] = (c[t.name] ?? 0) + 1;
    return c;
  }, [files]);

  // facet groups derived from the real tag catalog, grouped by category
  const facetGroups = useMemo<FacetGroup[]>(() => {
    const g: Record<string, string[]> = {};
    for (const t of tags) (g[t.category || 'Other'] ||= []).push(t.name);
    return Object.entries(g).map(([label, names]) => ({ label, tags: names }));
  }, [tags]);

  const sorted = useMemo(() => {
    const arr = [...files];
    if (sort === 'name') arr.sort((a, b) => a.original_filename.localeCompare(b.original_filename));
    else if (sort === 'size') arr.sort((a, b) => b.size_bytes - a.size_bytes);
    return arr;
  }, [files, sort]);

  const toggleTag = (t: string) =>
    setActiveTags(activeTags.includes(t) ? activeTags.filter(x => x !== t) : [...activeTags, t]);

  const toggleFolder = (p: string) => {
    const n = new Set(openFolders);
    if (n.has(p)) n.delete(p); else n.add(p);
    setOpenFolders(n);
  };

  const breadcrumb = currentFolder === ''
    ? 'Workshop'
    : currentFolder.split('/').filter(Boolean).join(' / ');

  // -- operations --------------------------------------------------------

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await uploadLibraryFile(file, currentFolder || '/Job Uploads');
      refetch();
    } catch (err) {
      window.alert(String(err));
    }
    e.target.value = '';
  }

  async function handleNewFolder() {
    const path = window.prompt('New folder path', `${currentFolder || ''}/New folder`);
    if (!path) return;
    try { await createFolder(path); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  async function handleRescan() {
    try { await rescanLibrary(); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  async function handleRename(f: LibraryFile) {
    const name = window.prompt('Rename file', f.original_filename);
    if (!name || name === f.original_filename) return;
    try { await updateFile(f.id, { name }); setSelected(null); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  function handleMove(f: LibraryFile) {
    setMovePicker({ ids: [f.id] });
  }

  async function handleDelete(f: LibraryFile) {
    if (!window.confirm(`Delete ${f.original_filename}?`)) return;
    try { await deleteFile(f.id); setSelected(null); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  // -- bulk selection ----------------------------------------------------

  const toggleSelect = (id: number) => setSelectedIds(prev => {
    const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n;
  });
  const clearSelection = () => setSelectedIds(new Set());
  const selectAll = () => setSelectedIds(new Set(sorted.map(f => f.id)));
  const nameOf = (id: number) => files.find(f => f.id === id)?.original_filename ?? `#${id}`;

  // drop selections for files that vanished after a refetch
  useEffect(() => {
    setSelectedIds(prev => {
      const present = new Set(files.map(f => f.id));
      const next = new Set([...prev].filter(id => present.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [files]);

  function openBulkMove() {
    if (selectedIds.size) setMovePicker({ ids: [...selectedIds] });
  }

  async function applyMove(folder: string) {
    const ids = movePicker?.ids ?? [];
    const failed: string[] = [];
    let ok = 0;
    for (const id of ids) {
      const f = files.find(x => x.id === id);
      if (f && f.folder === folder) continue; // already in the destination
      try { await updateFile(id, { folder }); ok++; }
      catch { failed.push(nameOf(id)); }
    }
    setMovePicker(null);
    clearSelection();
    setSelected(null);
    refetch();
    if (failed.length) window.alert(`Moved ${ok}. Failed ${failed.length}: ${failed.join(', ')}`);
  }

  async function bulkDelete() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    if (!window.confirm(`Delete ${ids.length} file${ids.length > 1 ? 's' : ''}?`)) return;
    const skipped: string[] = [];
    let ok = 0;
    for (const id of ids) {
      try { await deleteFile(id); ok++; }
      catch { skipped.push(nameOf(id)); }
    }
    clearSelection();
    setSelected(null);
    refetch();
    if (skipped.length) {
      window.alert(`Deleted ${ok}. Skipped ${skipped.length} (in use by a job): ${skipped.join(', ')}`);
    }
  }

  async function handleAddTag(f: LibraryFile, tagId: number) {
    try { await addFileTag(f.id, tagId); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  async function handleRemoveTag(f: LibraryFile, tagId: number) {
    try { await removeFileTag(f.id, tagId); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  function handleUseInJob(f: LibraryFile) {
    navigate('/queue/new', { state: { libraryFileId: f.id } });
  }

  // keep the open detail panel in sync with the latest file data after refetch
  const selectedLive = selected ? files.find(f => f.id === selected.id) ?? null : null;

  // -- manyfold tab ------------------------------------------------------

  if (tab === 'manyfold') {
    return (
      <div>
        <FilesTabBar tab={tab} setTab={setTab} />
        <div className="card" style={{ padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>Manyfold integration</div>
          <div className="muted" style={{ marginTop: 8 }}>
            Coming soon — sync this library with a Manyfold server.
          </div>
        </div>
      </div>
    );
  }

  // -- library tab -------------------------------------------------------

  return (
    <div>
      <FilesTabBar tab={tab} setTab={setTab} />
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
          onNewFolder={handleNewFolder}
        />

        {/* RIGHT: header + toolbar + filters + grid */}
        <div className="col gap-4" style={{ minWidth: 0 }}>
          {/* Header + toolbar */}
          <div className="row between" style={{ gap: 12, flexWrap: 'wrap' }}>
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
            <div className="row gap-2" style={{ flexShrink: 0, flexWrap: 'wrap' }}>
              <label className="btn ghost sm" style={{ cursor: 'pointer' }}>
                <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.upload}</span>
                Upload
                <input type="file" style={{ display: 'none' }} onChange={handleUpload} />
              </label>
              <button className="btn ghost sm" onClick={handleNewFolder}>
                <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.plus}</span>
                New folder
              </button>
              <button className="btn ghost sm" onClick={handleRescan}>
                <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.refresh}</span>
                Rescan
              </button>
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
            facetGroups={facetGroups}
          />

          {/* Bulk action bar */}
          {selectedIds.size > 0 && (
            <div className="card row between" style={{
              padding: '8px 12px', alignItems: 'center', gap: 12, flexWrap: 'wrap',
              borderColor: 'var(--border-3)',
            }}>
              <span className="small" style={{ fontWeight: 600 }}>{selectedIds.size} selected</span>
              <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
                <button className="btn ghost sm" onClick={selectAll}>Select all</button>
                <button className="btn ghost sm" onClick={openBulkMove}>
                  <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.files}</span>
                  Move
                </button>
                <button className="btn ghost sm" onClick={bulkDelete}>
                  <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.trash}</span>
                  Delete
                </button>
                <button className="btn ghost sm" onClick={clearSelection}>Clear</button>
              </div>
            </div>
          )}

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
                <div key={f.id} className="card" style={{
                       padding: 10, cursor: 'pointer', position: 'relative',
                       outline: selectedIds.has(f.id) ? '2px solid var(--accent)' : 'none',
                       outlineOffset: -1,
                     }}
                     onClick={() => setSelected(f)}>
                  <label
                    onClick={e => e.stopPropagation()}
                    title="Select"
                    style={{
                      position: 'absolute', top: 8, left: 8, zIndex: 2,
                      display: 'inline-flex', alignItems: 'center', cursor: 'pointer',
                      background: 'rgba(0,0,0,0.45)', borderRadius: 5, padding: '3px 4px',
                    }}>
                    <input
                      type="checkbox"
                      aria-label={`Select ${f.original_filename}`}
                      checked={selectedIds.has(f.id)}
                      onChange={() => toggleSelect(f.id)}
                      style={{ cursor: 'pointer', margin: 0 }}
                    />
                  </label>
                  <FileThumb file={f} />
                  <div style={{
                    fontSize: 12, marginTop: 8, fontWeight: 500,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {f.original_filename}
                  </div>
                  <div className="row between" style={{ marginTop: 3 }}>
                    <span className="tiny muted" style={{ whiteSpace: 'nowrap' }}>{fmtSize(f.size_bytes)}</span>
                    <span className="tiny muted" style={{ whiteSpace: 'nowrap' }}>
                      {f.plate_count} {f.plate_count === 1 ? 'plate' : 'plates'}
                    </span>
                  </div>
                  <div className="row gap-2" style={{ flexWrap: 'wrap', marginTop: 8 }}>
                    {f.tags.slice(0, 3).map(t => (
                      <span key={t.id} className="elig" style={{
                        fontSize: 9.5, padding: '1px 5px',
                        background: activeTags.includes(t.name) ? 'rgba(59,130,246,0.12)' : 'var(--bg-1)',
                        color: activeTags.includes(t.name) ? 'var(--accent-hi)' : 'var(--text-3)',
                      }}>{t.name}</span>
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

      {selectedLive && (
        <FileDetailPanel
          file={selectedLive}
          tags={tags}
          onClose={() => setSelected(null)}
          onRename={handleRename}
          onMove={handleMove}
          onDelete={handleDelete}
          onAddTag={handleAddTag}
          onRemoveTag={handleRemoveTag}
          onUseInJob={handleUseInJob}
        />
      )}

      {movePicker && (
        <FolderPicker
          count={movePicker.ids.length}
          onPick={applyMove}
          onClose={() => setMovePicker(null)}
        />
      )}
    </div>
  );
}
