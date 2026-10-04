import { useCallback, useEffect, useState } from 'react';
import { fmtBytes } from '../data/helpers';
import { isPresliced } from '../lib/fileKind';
import { Icons } from '../components/icons';
import {
  listAllPrinterFiles, listPrinterFiles, printStoredFile, deleteStoredFile, copyStoredFileToLibrary,
  type MergedPrinterFiles, type PrinterFileEntry,
} from '../api/printerFiles';

function fmtDuration(seconds: number): string {
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
}

function metaText(f: PrinterFileEntry): string {
  const m = f.metadata;
  if (!m) return '—';
  const parts: string[] = [];
  if (m.estimated_seconds) parts.push(fmtDuration(m.estimated_seconds));
  if (m.filament_grams) parts.push(`${m.filament_grams.toFixed(1)} g`);
  return parts.join(' · ') || '—';
}

const parentOf = (dir: string) => dir.split('/').filter(Boolean).slice(0, -1).join('/') || '/';
// Models only: a sliced .gcode.3mf ends in .3mf too, but the library refuses it from here (BIZ-196).
const isLibraryType = (name: string) => /\.(3mf|stl)$/i.test(name) && !isPresliced(name);

interface Section extends MergedPrinterFiles { dir: string; busy: boolean }

export function PrinterFilesScreen() {
  const [sections, setSections] = useState<Section[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const all = await listAllPrinterFiles();
      setSections(all.map(p => ({ ...p, dir: '/', busy: false })));
    } catch (e) {
      setNotice({ kind: 'err', text: `Could not load printer files: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const patch = (id: number, p: Partial<Section>) =>
    setSections(prev => prev.map(s => (s.printer_id === id ? { ...s, ...p } : s)));

  async function open(section: Section, dir: string) {
    patch(section.printer_id, { busy: true });
    try {
      const l = await listPrinterFiles(section.printer_id, dir);
      patch(section.printer_id, { dir, files: l.files, error: null, busy: false });
    } catch (e) {
      patch(section.printer_id, { busy: false });
      setNotice({ kind: 'err', text: `${section.printer_name}: ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  async function act(section: Section, label: string, fn: () => Promise<unknown>, refresh = false) {
    setNotice(null);
    patch(section.printer_id, { busy: true });
    try {
      await fn();
      setNotice({ kind: 'ok', text: `${label} — ${section.printer_name}` });
      if (refresh) await open(section, section.dir);
      else patch(section.printer_id, { busy: false });
    } catch (e) {
      patch(section.printer_id, { busy: false });
      setNotice({ kind: 'err', text: `${label} failed on ${section.printer_name}: ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  const needle = filter.trim().toLowerCase();

  return (
    <div className="page" data-testid="printer-files">
      <div className="row gap-3" style={{ alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <input className="input" placeholder="Filter by file name…" aria-label="Filter files" style={{ maxWidth: 280 }}
               value={filter} onChange={e => setFilter(e.target.value)} />
        <button className="btn sm" onClick={() => void load()} disabled={loading}>{Icons.refresh} Refresh</button>
        <span className="small muted">Files stored on the printers themselves. Printing from here bypasses the queue.</span>
      </div>

      {notice && (
        <div role={notice.kind === 'err' ? 'alert' : 'status'} className="small"
             style={{ marginBottom: 12, color: notice.kind === 'err' ? 'var(--err)' : 'var(--ok)' }}>{notice.text}</div>
      )}

      {loading && <div className="muted">Reading printer storage…</div>}
      {!loading && sections.length === 0 && (
        <div className="muted" data-testid="printer-files-empty">
          No printer can list its files right now (they must be connected and support file browsing).
        </div>
      )}

      <div className="col gap-4">
        {sections.map(s => {
          const rows = s.files.filter(f => f.is_dir || !needle || f.name.toLowerCase().includes(needle));
          return (
            <div key={s.printer_id} className="card" style={{ padding: 18 }} data-testid={`printer-files-${s.printer_id}`}>
              <div className="row gap-3" style={{ alignItems: 'center', marginBottom: 10 }}>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{s.printer_name}</div>
                <span className="small muted">{s.dir === '/' ? '/' : `/${s.dir}`}</span>
                {s.dir !== '/' && (
                  <button className="btn ghost sm" disabled={s.busy} onClick={() => void open(s, parentOf(s.dir))}>{Icons.chevL} Up</button>
                )}
                {s.busy && <span className="small muted">working…</span>}
              </div>
              {s.error ? (
                <div className="small" style={{ color: 'var(--warn)' }}>{s.error}</div>
              ) : rows.length === 0 ? (
                <div className="small muted">{s.files.length === 0 ? 'No files.' : 'No files match the filter.'}</div>
              ) : (
                <table className="table" style={{ width: '100%' }}>
                  <thead><tr><th>Name</th><th>Size</th><th>Print</th><th>Modified</th><th /></tr></thead>
                  <tbody>
                    {rows.map(f => (
                      <tr key={f.id}>
                        <td>
                          {f.is_dir
                            ? <button className="btn ghost sm" disabled={s.busy} onClick={() => void open(s, f.id)}>📁 {f.name}</button>
                            : f.name}
                        </td>
                        <td className="num small">{f.is_dir ? '' : fmtBytes(f.size)}</td>
                        <td className="small">{f.is_dir ? '' : metaText(f)}</td>
                        <td className="small muted">{f.modified_at ? f.modified_at.slice(0, 10) : ''}</td>
                        <td>
                          {!f.is_dir && (
                            <span className="row gap-1">
                              {f.printable && (
                                <button className="btn sm" disabled={s.busy} aria-label={`Print ${f.name}`}
                                        onClick={() => {
                                          if (!window.confirm(`Print ${f.name} on ${s.printer_name} now? This bypasses the queue.`)) return;
                                          void act(s, `Started ${f.name}`, () => printStoredFile(s.printer_id, f.id));
                                        }}>{Icons.play} Print</button>
                              )}
                              {s.can_download && isLibraryType(f.name) && (
                                <button className="btn sm" disabled={s.busy} aria-label={`Add ${f.name} to library`}
                                        onClick={() => void act(s, `Added ${f.name} to the library`, () => copyStoredFileToLibrary(s.printer_id, f.id))}>
                                  {Icons.upload} To library
                                </button>
                              )}
                              {s.can_delete && (
                                <button className="btn ghost sm" disabled={s.busy} aria-label={`Delete ${f.name}`}
                                        onClick={() => {
                                          if (!window.confirm(`Delete ${f.name} from ${s.printer_name}? This cannot be undone.`)) return;
                                          void act(s, `Deleted ${f.name}`, () => deleteStoredFile(s.printer_id, f.id), true);
                                        }}>{Icons.trash}</button>
                              )}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
