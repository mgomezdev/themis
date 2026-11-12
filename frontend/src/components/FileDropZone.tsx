import { useEffect, useRef, useState } from 'react';
import { uploadLibraryFile } from '../api/files';
import type { LibraryFile } from '../data/types';

const SUPPORTED = ['.3mf', '.stl', '.gcode'];          // .gcode.3mf ends in .3mf

/** An API error (`"<status> <body>"`) as the server's `detail` when it sent one, else the message as is. */
function reason(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  const m = /^\d{3} (\{.*\})$/s.exec(msg);
  if (m) {
    try {
      const detail = (JSON.parse(m[1]) as { detail?: unknown }).detail;
      if (typeof detail === 'string' && detail) return detail;
    } catch { /* not JSON: keep the message */ }
  }
  return msg;
}

const hasFiles = (e: { dataTransfer: DataTransfer | null }) => Array.from(e.dataTransfer?.types ?? []).includes('Files');

/** Wraps a page so files dragged from the desktop onto it are uploaded to the file library, one at a time, and each
 *  uploaded file is handed to `onUploaded` (e.g. to add it to a project). Drags that carry no files (the page's own
 *  item drags) pass through untouched. While mounted, a file dropped anywhere else in the window is swallowed so the
 *  browser doesn't navigate away to it. */
export function FileDropZone({ onUploaded, label, style, children }: {
  onUploaded: (f: LibraryFile) => Promise<void> | void;
  label: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const depth = useRef(0);                              // dragenter/leave fire for every child crossed
  const uploading = useRef(false);                      // a ref: a second drop in the same render must see the first

  useEffect(() => {
    const swallow = (e: DragEvent) => { if (hasFiles(e)) e.preventDefault(); };
    window.addEventListener('dragover', swallow);
    window.addEventListener('drop', swallow);
    return () => { window.removeEventListener('dragover', swallow); window.removeEventListener('drop', swallow); };
  }, []);

  async function drop(e: React.DragEvent) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth.current = 0;
    setOver(false);
    if (uploading.current) {
      setError('Wait for the current upload to finish, then drop again');
      return;
    }
    uploading.current = true;
    const files = Array.from(e.dataTransfer.files);
    const skipped = files.filter(f => !SUPPORTED.some(ext => f.name.toLowerCase().endsWith(ext)));
    const errors = skipped.length ? [`Not a .3mf, .stl or .gcode file: ${skipped.map(f => f.name).join(', ')}`] : [];
    setError('');
    try {
      for (const f of files.filter(f => !skipped.includes(f))) {
        setBusy(f.name);
        try {
          await onUploaded(await uploadLibraryFile(f));
        } catch (err) {
          errors.push(`${f.name}: ${reason(err)}`);
        }
      }
    } finally {
      uploading.current = false;
      setBusy(null);
      setError(errors.join(' · '));
    }
  }

  return (
    <div data-testid="file-drop-zone" style={{ position: 'relative', ...style }}
         onDragEnter={e => { if (hasFiles(e)) { e.preventDefault(); depth.current += 1; setOver(true); } }}
         onDragOver={e => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } }}
         onDragLeave={e => { if (hasFiles(e) && --depth.current <= 0) { depth.current = 0; setOver(false); } }}
         onDrop={drop}>
      {/* Live regions stay mounted so screen readers announce what is put in them. */}
      <div role="status" className="small muted" style={{ marginBottom: busy ? 12 : 0 }}>{busy ? `Uploading ${busy}…` : ''}</div>
      <div role="alert" className="small" style={{ color: 'var(--err)', marginBottom: error ? 12 : 0 }}>{error}</div>
      {children}
      {over && (
        <div className="tone-accent tint-bg" aria-hidden style={{ position: 'absolute', inset: 0, zIndex: 20, pointerEvents: 'none', border: '2px dashed var(--accent)' }}>
          <div style={{ position: 'sticky', top: '40vh', display: 'flex', justifyContent: 'center' }}>
            <div className="card" style={{ padding: '10px 16px', fontWeight: 600 }}>{label}</div>
          </div>
        </div>
      )}
    </div>
  );
}
