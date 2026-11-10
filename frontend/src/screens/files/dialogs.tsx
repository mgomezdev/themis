// Files: the delete-with-versions confirmation and the move-to-folder picker.
import { useState, useEffect, useRef } from 'react';
import { Icons } from '../../components/icons';
import type { LibraryFile, FolderNode } from '../../data/types';
import { createFolder, getFolderDirs } from '../../api/files';
import type { CachedVersionRef } from '../../api/files';
import { FolderTreeNode } from './FolderTree';

/** Deleting a model that has cached sliced versions: delete them too, or keep them as standalone gcode (BIZ-195). */
export function DeleteVersionsDialog({ file, versions, onChoose, onCancel }: {
  file: LibraryFile; versions: CachedVersionRef[];
  onChoose: (choice: 'delete' | 'keep') => void; onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();   // the safe choice has focus; Escape backs out
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);
  return (
    <div role="dialog" aria-modal="true" aria-label="Delete model with sliced versions" style={{
      position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 60,
      display: 'grid', placeItems: 'center', padding: 16,
    }}>
      <div className="card" style={{ width: 440, maxWidth: '100%', padding: 18 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Delete {file.original_filename}?</div>
        <div className="small" style={{ marginBottom: 10 }}>
          It has {versions.length} cached sliced version{versions.length === 1 ? '' : 's'}:
        </div>
        <ul className="small" style={{ margin: '0 0 14px 18px', maxHeight: 200, overflowY: 'auto' }}>
          {versions.map(v => <li key={v.id}>{v.name} <span className="muted">({v.folder})</span></li>)}
        </ul>
        <div className="col gap-2">
          <button className="btn primary" style={{ background: 'var(--err)' }} onClick={() => onChoose('delete')}>
            Delete model and versions
          </button>
          <button className="btn ghost" onClick={() => onChoose('keep')}>Delete model, keep versions as gcode</button>
          <button className="btn ghost" ref={cancelRef} onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------
// FolderPicker — modal to choose an existing destination folder (+ New folder)
// -------------------------------------------------------------------------

const NO_PICK = '\u0000'; // sentinel: a path that matches no real folder

export function FolderPicker({ count, onPick, onClose }: {
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
      position: 'fixed', inset: 0, background: 'var(--scrim)',
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
