import { useState, useEffect } from 'react';
import { shade, fmtBytes } from '../../data/helpers';
import { Icons } from '../../components/icons';
import type { LibraryFile } from '../../data/types';
import { getSlicedVersions } from '../../api/files';
import type { SlicedVersion } from '../../api/files';
import { FileEligibilityEditor } from '../../components/FileEligibilityEditor';
import type { Tag } from '../../api/tags';
import { fallbackColor, versionSummary } from './palette';

// Files: the selected file's detail drawer (thumbnail, plates, eligibility, sliced versions).

export function FileThumb({ file, large }: { file: LibraryFile; large?: boolean }) {
  const color = fallbackColor(file.id);
  return (
    <div style={{
      width: '100%', aspectRatio: '1/1',
      background: file.thumbnail_url
        ? 'var(--bg-1)'
        : `linear-gradient(135deg, ${color}, ${shade(color, -25)})`,
      borderRadius: 0,
      border: '1px solid var(--border-2)',
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
          borderRadius: 0,
        }} />
      )}
      <div style={{
        position: 'absolute', bottom: 6, right: 6,
        fontFamily: 'var(--font-mono)', fontSize: large ? 11 : 10,
        color: 'rgba(255,255,255,0.7)',
        background: 'rgba(0,0,0,0.45)',
        padding: '1px 5px', borderRadius: 0,
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
  onEligibilitySaved?: () => void;
}

export function FileDetailPanel({
  file, tags, onClose, onRename, onMove, onDelete, onAddTag, onRemoveTag, onUseInJob, onEligibilitySaved,
}: FileDetailPanelProps) {
  const fileTagIds = new Set(file.tags.map(t => t.id));
  const available = tags.filter(t => !fileTagIds.has(t.id));
  const thumbs = file.plate_thumbnails ?? [];
  const [activePlate, setActivePlate] = useState<number>(
    thumbs.length > 0 ? thumbs[0].plate_number : 1
  );
  const activeThumb = thumbs.find(t => t.plate_number === activePlate);

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

      {thumbs.length > 1 ? (
        <div style={{ marginBottom: 14 }}>
          {/* Large view of the selected plate */}
          <div style={{
            width: '100%', aspectRatio: '1/1', background: 'var(--bg-1)',
            border: '1px solid var(--border-2)', marginBottom: 8, position: 'relative',
          }}>
            {activeThumb && (
              <img src={activeThumb.thumbnail_url} alt={`Plate ${activePlate}`}
                   style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            )}
            <div style={{
              position: 'absolute', bottom: 6, left: 8,
              fontFamily: 'var(--font-mono)', fontSize: 11,
              color: 'rgba(255,255,255,0.8)', background: 'rgba(0,0,0,0.5)',
              padding: '1px 6px',
            }}>
              Plate {activePlate} / {file.plate_count}
            </div>
          </div>
          {/* Thumbnail strip */}
          <div style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 4 }}>
            {thumbs.map(t => (
              <button
                key={t.plate_number}
                onClick={() => setActivePlate(t.plate_number)}
                title={`Plate ${t.plate_number}`}
                style={{
                  flexShrink: 0, width: 64, padding: 0, border: 'none', cursor: 'pointer',
                  outline: t.plate_number === activePlate
                    ? '2px solid var(--accent)' : '1px solid var(--border-2)',
                  background: 'var(--bg-1)',
                }}
              >
                <img src={t.thumbnail_url} alt={`Plate ${t.plate_number}`}
                     style={{ width: '100%', aspectRatio: '1/1', objectFit: 'contain', display: 'block' }} />
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 9, textAlign: 'center',
                  color: t.plate_number === activePlate ? 'var(--accent)' : 'var(--text-3)',
                  padding: '2px 0',
                }}>
                  P{t.plate_number}
                </div>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ width: '100%', maxWidth: 240, alignSelf: 'center', marginBottom: 14 }}>
          <FileThumb file={file} large />
        </div>
      )}

      <div style={{ fontSize: 16, fontWeight: 600, wordBreak: 'break-word' }}>
        {file.original_filename}
      </div>
      {file.missing && (
        <div className="tiny" style={{ color: 'var(--err)', marginTop: 4 }}>
          Missing on disk
        </div>
      )}

      {file.sliced_version && (
        <div className="tiny" data-testid="sliced-from" style={{ marginTop: 6, color: 'var(--text-2)' }}>
          Sliced {file.sliced_version.source_filename ? <>from <b>{file.sliced_version.source_filename}</b></> : '(model removed)'}
          {' · '}{versionSummary(file.sliced_version)}
        </div>
      )}
      {file.sliced_version_count > 0 && <SlicedVersionsList fileId={file.id} />}
      {file.eligibility && <FileEligibilityEditor key={file.id} fileId={file.id} onSaved={onEligibilitySaved} />}

      <div className="col gap-2" style={{ marginTop: 12 }}>
        <div className="row between"><span className="tiny muted">Folder</span>
          <span className="tiny">{file.folder || '/'}</span></div>
        <div className="row between"><span className="tiny muted">Size</span>
          <span className="tiny">{fmtBytes(file.size_bytes)}</span></div>
        <div className="row between"><span className="tiny muted">Plates</span>
          <span className="tiny">{file.plate_count}</span></div>
      </div>

      <div style={{ marginTop: 16 }}>
        <div className="tag-key" style={{ marginBottom: 6 }}>Tags</div>
        <div className="row gap-2 wrap">
          {file.tags.map(t => (
            <button key={t.id} onClick={() => onRemoveTag(file, t.id)}
                    className="row gap-2"
                    title="Remove tag"
                    style={{
                      padding: '3px 9px', borderRadius: 0, border: '1px solid var(--border-1)',
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
          <button className="btn ghost sm grow" onClick={() => onRename(file)}>Rename</button>
          <button className="btn ghost sm grow" onClick={() => onMove(file)}>Move</button>
        </div>
        <button className="btn ghost sm" onClick={() => onDelete(file)}
                style={{ color: 'var(--err)' }}>
          <span style={{ width: 14, height: 14, display: 'inline-flex' }}>{Icons.trash}</span>
          Delete
        </button>
      </div>
    </div>
  );
}

/** A model's cached sliced versions, in its detail panel (BIZ-196). */
function SlicedVersionsList({ fileId }: { fileId: number }) {
  const [versions, setVersions] = useState<SlicedVersion[] | null>(null);
  useEffect(() => {
    let alive = true;
    getSlicedVersions(fileId).then(v => { if (alive) setVersions(v); }).catch(() => { if (alive) setVersions([]); });
    return () => { alive = false; };
  }, [fileId]);
  if (!versions) return null;
  return (
    <div style={{ marginTop: 12 }} data-testid="sliced-versions">
      <div className="tag-key" style={{ marginBottom: 6 }}>Sliced versions</div>
      {versions.length === 0 && <span className="tiny muted">None on disk</span>}
      <div className="col gap-2">
        {versions.map(v => (
          <div key={v.id} className="tiny" style={{ padding: '4px 6px', background: 'var(--bg-1)' }}>
            <div style={{ fontWeight: 600, wordBreak: 'break-word' }}>{v.name}</div>
            <div className="muted">Plate {v.plate_number} · {v.process_preset} · {versionSummary(v)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
