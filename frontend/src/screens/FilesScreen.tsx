import { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { fmtBytes } from '../data/helpers';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import type { LibraryFile, FolderNode } from '../data/types';
import {
  useFiles,
  uploadLibraryFile,
  createFolder,
  deleteFolder,
  updateFile,
  deleteFile,
  addFileTag,
  removeFileTag,
  rescanLibrary,
  getFolderDirs,
} from '../api/files';
import type { CachedVersionRef, FileKindFilter } from '../api/files';
import { useTags } from '../api/tags';
import { FolderCard } from './files/FolderTree';
import { FacetGroup, FilterCard } from './files/FilterCard';
import { FileDetailPanel, FileThumb } from './files/FileDetailPanel';
import { versionSummary } from './files/palette';
import { DeleteVersionsDialog, FolderPicker } from './files/dialogs';

// -------------------------------------------------------------------------
// helpers
// -------------------------------------------------------------------------

// Empty root for the folder tree before /dirs resolves.
const EMPTY_TREE: FolderNode = { name: 'All files', path: '', count: 0, children: {} };

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

export function FilesScreen() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'library' | 'manyfold'>('library');
  const [currentFolder, setCurrentFolder] = useState('');
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [sort, setSort] = useState('updated');
  const [kind, setKind] = useState<FileKindFilter>('all');
  const [deleteChoice, setDeleteChoice] = useState<{ file: LibraryFile; versions: CachedVersionRef[] } | null>(null);
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
    kind,
  }), [currentFolder, activeTags, sort, kind]);
  const { files, refetch } = useFiles(filter);
  const { tags } = useTags();

  // Folder tree comes from the real on-disk dirs (so empty folders show too);
  // re-fetched whenever `files` changes (every mutation calls refetch()).
  const [dirs, setDirs] = useState<FolderNode>(EMPTY_TREE);
  useEffect(() => { getFolderDirs().then(setDirs).catch(console.error); }, [files]);
  const tree = dirs;

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
      const uploaded = await uploadLibraryFile(file, currentFolder || '/Job Uploads');
      refetch();
      // A pre-sliced file arrives with unknown machine eligibility: open its drawer so the user records which printers it is for
      // right away (BIZ-263). The drawer follows the live row, so it fills in once the refetch lands.
      if (uploaded?.eligibility && !uploaded.eligibility.known) setSelected(uploaded);
    } catch (err) {
      window.alert(String(err));
    }
    e.target.value = '';
  }

  async function handleNewFolder() {
    const parentLabel = currentFolder ? `“${breadcrumb}”` : 'the library root';
    const name = window.prompt(`New folder name (inside ${parentLabel})`);
    if (!name) return;
    const path = `${currentFolder || ''}/${name}`.replace(/\/+/g, '/');
    try { await createFolder(path); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  async function handleDeleteFolder() {
    if (!currentFolder) return;
    if (!window.confirm(`Delete folder "${breadcrumb}"? It must be empty.`)) return;
    try {
      await deleteFolder(currentFolder);
      setCurrentFolder('');
      refetch();
    } catch (err) {
      window.alert(String(err));
    }
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
    // A model with cached versions gets its own dialog (from the server's 409) instead of a second confirm.
    if (!(f.sliced_version_count > 0) && !window.confirm(`Delete ${f.original_filename}?`)) return;
    try {
      const res = await deleteFile(f.id);
      if ('needsChoice' in res) { setDeleteChoice({ file: f, versions: res.versions }); return; }
      setSelected(null); refetch();
    } catch (err) { window.alert(String(err)); }
  }

  async function finishDelete(choice: 'delete' | 'keep') {
    const pending = deleteChoice;
    setDeleteChoice(null);
    if (!pending) return;
    try { await deleteFile(pending.file.id, choice); setSelected(null); refetch(); }
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
    const withVersions: string[] = [];
    let ok = 0;
    for (const id of ids) {
      try {
        const res = await deleteFile(id);
        if ('needsChoice' in res) withVersions.push(nameOf(id)); else ok++;
      }
      catch { skipped.push(nameOf(id)); }
    }
    clearSelection();
    setSelected(null);
    refetch();
    if (skipped.length) {
      window.alert(`Deleted ${ok}. Skipped ${skipped.length} (in use by a job): ${skipped.join(', ')}`);
    }
    if (withVersions.length) {
      window.alert(`Not deleted — these have cached sliced versions; delete them one at a time to choose what happens `
        + `to the versions: ${withVersions.join(', ')}`);
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
          onDeleteFolder={handleDeleteFolder}
        />

        {/* RIGHT: header + toolbar + filters + grid */}
        <div className="col gap-4 min-0">
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
              <select className="select" aria-label="File kind" style={{ width: 'auto', paddingRight: 32 }}
                      value={kind} onChange={e => setKind(e.target.value as FileKindFilter)}>
                <option value="all">All types</option>
                <option value="models">Models</option>
                <option value="sliced">Sliced gcode</option>
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
              <span className="small fw-600">{selectedIds.size} selected</span>
              <div className="row gap-2 wrap">
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
                       borderColor: selectedIds.has(f.id)
                         ? 'var(--accent)'
                         : 'oklch(87% 0.185 95 / 0.22)',
                       boxShadow: selectedIds.has(f.id)
                         ? 'inset 0 0 0 1px var(--accent)'
                         : 'none',
                     }}
                     onClick={() => setSelected(f)}>
                  <label
                    onClick={e => e.stopPropagation()}
                    title="Select"
                    style={{
                      position: 'absolute', top: 8, left: 8, zIndex: 2,
                      display: 'inline-flex', alignItems: 'center', cursor: 'pointer',
                      background: 'rgba(0,0,0,0.45)', borderRadius: 0, padding: '3px 4px',
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
                  {f.sliced_version_count > 0 && (
                    <button className="elig" data-testid="sliced-badge"
                            title="Show its cached sliced versions"
                            onClick={e => { e.stopPropagation(); setSelected(f); }}
                            style={{ marginTop: 4, fontSize: 10, padding: '1px 6px', cursor: 'pointer',
                                     background: 'var(--accent-glow)', color: 'var(--accent-hi)', border: 'none' }}>
                      {f.sliced_version_count} sliced version{f.sliced_version_count === 1 ? '' : 's'}
                    </button>
                  )}
                  {f.sliced_version && (
                    <div className="tiny muted" data-testid="sliced-from-chip" style={{
                      marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    }} title={versionSummary(f.sliced_version)}>
                      {f.sliced_version.source_filename ? `Sliced from ${f.sliced_version.source_filename}` : 'Sliced gcode'}
                      {' · '}{versionSummary(f.sliced_version)}
                    </div>
                  )}
                  <div className="row between" style={{ marginTop: 3 }}>
                    <span className="tiny muted nowrap">{fmtBytes(f.size_bytes)}</span>
                    <span className="tiny muted nowrap">
                      {f.plate_count} {f.plate_count === 1 ? 'plate' : 'plates'}
                    </span>
                  </div>
                  <div className="row gap-2" style={{ flexWrap: 'wrap', marginTop: 8 }}>
                    {f.tags.slice(0, 3).map(t => (
                      <span key={t.id} className="elig" style={{
                        fontSize: 9.5, padding: '1px 5px',
                        background: activeTags.includes(t.name) ? 'var(--accent-glow)' : 'var(--bg-1)',
                        color: activeTags.includes(t.name) ? 'var(--accent-hi)' : 'var(--text-3)',
                        borderColor: activeTags.includes(t.name) ? 'oklch(87% 0.185 95 / 0.30)' : undefined,
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
          onEligibilitySaved={refetch}
        />
      )}

      {movePicker && (
        <FolderPicker
          count={movePicker.ids.length}
          onPick={applyMove}
          onClose={() => setMovePicker(null)}
        />
      )}

      {deleteChoice && (
        <DeleteVersionsDialog
          file={deleteChoice.file}
          versions={deleteChoice.versions}
          onChoose={finishDelete}
          onCancel={() => setDeleteChoice(null)}
        />
      )}
    </div>
  );
}
