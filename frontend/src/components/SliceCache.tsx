import { useState } from 'react';
import { Link } from 'react-router-dom';
import { setJobSaveSlice, type ApiJob, type SliceCacheInfo } from '../api/queue';

// Slicing cache (BIZ-194): the per-job pieces of UI — status markers, the "save sliced gcode" control, and the
// debug block that shows the latest cache decision.

const TERMINAL = new Set(['complete', 'failed', 'cancelled']);
const SLICED = new Set(['sliced', 'uploading', 'printing', 'paused']);

const STALE_LABELS: Record<string, string> = {
  presets_changed: 'Stale: presets edited',
  slicer_version_changed: 'Stale: OrcaSlicer updated',
};

export function staleLabels(reasons: readonly string[] | undefined): string[] {
  return (reasons ?? []).map(r => STALE_LABELS[r] ?? `Stale: ${r}`);
}

type MarkerJob = Pick<ApiJob, 'status' | 'save_slice' | 'sliced_version_id' | 'slice_cache_info'>;

/** Short chips: saving / saved / save failed / used cached / stale. */
export function SliceCacheMarkers({ job }: { job: MarkerJob }) {
  const info = job.slice_cache_info;
  const save = info?.save;
  const chips: { key: string; label: string; tone: string; title?: string }[] = [];
  if (job.sliced_version_id != null) chips.push({ key: 'cached', label: 'Used cached gcode', tone: 'var(--accent-hi)' });
  if (save?.outcome === 'saved' || save?.outcome === 'duplicate') {
    chips.push({ key: 'saved', label: 'Gcode saved to library', tone: 'var(--ok, #22c55e)' });
  } else if (save?.outcome === 'failed') {
    chips.push({ key: 'save-failed', label: 'Gcode save failed', tone: 'var(--err)', title: save.error ?? undefined });
  } else if (job.save_slice && job.sliced_version_id == null && !TERMINAL.has(job.status)) {
    // only while a fresh slice can still come: a job printing cached gcode, or one that ended unsliced, never saves
    chips.push({ key: 'saving', label: 'Saving gcode', tone: 'var(--text-2)' });
  }
  if (info?.stale) {
    for (const label of staleLabels(info.stale_reasons)) chips.push({ key: label, label, tone: 'var(--warn)' });
  }
  if (chips.length === 0) return null;
  return (
    <span className="row gap-1" style={{ flexWrap: 'wrap' }} data-testid="slice-cache-markers">
      {chips.map(c => (
        <span key={c.key} className="pill" title={c.title}
              style={{ color: c.tone, border: '1px solid currentColor', background: 'transparent', fontSize: 10.5 }}>
          {c.label}
        </span>
      ))}
    </span>
  );
}

/** "Save sliced gcode to library" for an unfinished job; saves right away if it's already sliced. Hidden for jobs that
 * print a pre-sliced file or a cached version (nothing new to save) and for finished ones (their slice is gone). */
export function SaveSliceControl({ job, presliced, onChange }: {
  job: Pick<ApiJob, 'id' | 'status' | 'save_slice' | 'save_slice_name' | 'sliced_version_id' | 'slice_cache_info'>;
  presliced: boolean;
  onChange: (updated: ApiJob) => void;
}) {
  const [name, setName] = useState(job.save_slice_name ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (TERMINAL.has(job.status) || presliced || job.sliced_version_id != null) return null;
  const sliced = SLICED.has(job.status);
  const save = job.slice_cache_info?.save;
  const saved = save?.outcome === 'saved' || save?.outcome === 'duplicate';

  async function apply(on: boolean) {
    setBusy(true);
    setError(null);
    try { onChange(await setJobSaveSlice(job.id, on, on ? name.trim() || null : null)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  }

  return (
    <div className="col gap-2" data-testid="save-slice-control">
      <label className="row gap-2 small" style={{ alignItems: 'center', cursor: 'pointer' }}>
        <input type="checkbox" checked={job.save_slice} disabled={busy}
               onChange={e => apply(e.target.checked)} />
        {sliced && !job.save_slice ? 'Save sliced gcode now' : 'Save sliced gcode to library'}
      </label>
      {!job.save_slice && (
        <input className="input" placeholder="Name (optional)" aria-label="Saved gcode name" value={name}
               onChange={e => setName(e.target.value)} style={{ maxWidth: 320 }} />
      )}
      {job.save_slice && !saved && save?.outcome !== 'failed' && (
        <span className="tiny muted">{sliced ? 'Saving…' : 'Will be saved next to the model once it is sliced.'}</span>
      )}
      {saved && (
        <span className="tiny">Saved next to the model — <Link to="/files">open the library</Link></span>
      )}
      {save?.outcome === 'failed' && (
        <span className="tiny" style={{ color: 'var(--err)' }}>
          Save failed: {save.error}{' '}
          <button className="btn ghost sm" disabled={busy} onClick={() => apply(true)}>Retry</button>
        </span>
      )}
      {error && <span className="tiny" style={{ color: 'var(--err)' }}>{error}</span>}
    </div>
  );
}

const ROWS: [keyof SliceCacheInfo, string][] = [
  ['decision', 'Decision'], ['reason', 'Reason'], ['at', 'At'], ['policy', 'Policy'],
  ['sliced_version_id', 'Version'], ['cached_file_id', 'Cached file'], ['cached_file_hash', 'Cached file hash'],
  ['source_content_hash', 'Model hash'],
  ['preset_content_hash_stored', 'Preset hash (sliced)'], ['preset_content_hash_current', 'Preset hash (now)'],
  ['slicer_version_stored', 'OrcaSlicer (sliced)'], ['slicer_version_current', 'OrcaSlicer (now)'],
];

/** Collapsible debug view of the job's latest slicing-cache decision and save outcome. */
export function SliceCacheDebug({ info }: { info: SliceCacheInfo | null }) {
  const [copied, setCopied] = useState(false);
  if (!info) return null;
  const save = info.save;
  async function copyKey() {
    try { await navigator.clipboard.writeText(info?.cache_key ?? ''); setCopied(true); }
    catch { /* clipboard blocked: the key is still selectable */ }
  }
  return (
    <details className="card" style={{ padding: 12 }} data-testid="slice-cache-debug">
      <summary className="small" style={{ cursor: 'pointer', fontWeight: 600 }}>Slice cache</summary>
      <div className="col gap-1 tiny" style={{ marginTop: 8, fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
        {info.cache_key && (
          <div className="row gap-2" style={{ alignItems: 'center' }}>
            <span className="muted">Cache key</span><span>{info.cache_key}</span>
            <button className="btn ghost sm" onClick={copyKey}>{copied ? 'Copied' : 'Copy'}</button>
          </div>
        )}
        {ROWS.filter(([k]) => info[k] != null && info[k] !== '').map(([k, label]) => (
          <div key={k} className="row gap-2"><span className="muted">{label}</span><span>{String(info[k])}</span></div>
        ))}
        {info.stale && (
          <div className="row gap-2"><span className="muted">Stale</span><span>{staleLabels(info.stale_reasons).join(', ')}</span></div>
        )}
        {save && (
          <div className="row gap-2">
            <span className="muted">Save</span>
            <span>{save.outcome}{save.sliced_version_id != null ? ` · version ${save.sliced_version_id}` : ''}
              {save.file_id != null ? ` · file ${save.file_id}` : ''}{save.error ? ` · ${save.error}` : ''}</span>
          </div>
        )}
      </div>
    </details>
  );
}
