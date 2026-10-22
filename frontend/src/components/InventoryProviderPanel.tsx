import { useCallback, useEffect, useState } from 'react';
import {
  CAP, INVENTORY_CAPABILITY, discardPendingWrite, listPendingWrites, listSuspended, resolvePendingWrite, resolveWeightConflict, resumeTracking,
  syncNow, syncTone, useSyncStatus, type PendingWrite, type SuspendedSpool,
} from '../api/inventory';
import { featuresOf, type PluginSummary } from '../api/plugins';

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : 'never');

/** Sync health, queued weight updates and suspended spools of the active inventory provider. The sync half only exists
 *  for REMOTE providers; the suspended-tracking list applies to any provider that deducts. */
export function InventoryProviderPanel({ plugin }: { plugin: Pick<PluginSummary, 'provides'> }) {
  const features = featuresOf(plugin, INVENTORY_CAPABILITY);
  const remote = features.includes(CAP.REMOTE);
  const tracksWeight = features.includes(CAP.TRACKS_WEIGHT);
  const { status, refetch } = useSyncStatus(remote);
  const [writes, setWrites] = useState<PendingWrite[]>([]);
  const [suspended, setSuspended] = useState<SuspendedSpool[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [grams, setGrams] = useState<Record<string, string>>({});

  const reload = useCallback(() => {
    if (remote || tracksWeight) listPendingWrites().then(r => setWrites(Array.isArray(r.items) ? r.items : [])).catch(() => setWrites([]));
    if (tracksWeight) listSuspended().then(r => setSuspended(Array.isArray(r.items) ? r.items : [])).catch(() => setSuspended([]));
  }, [remote, tracksWeight]);
  useEffect(reload, [reload]);

  async function act(fn: () => Promise<unknown>, done: string) {
    setMsg(null);
    try { await fn(); setMsg({ ok: true, text: done }); } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
    reload(); refetch();
  }

  const conflicts = writes.filter(w => w.status === 'conflict');
  const queued = writes.filter(w => w.status === 'pending');
  const tone = status ? syncTone(status) : null;
  const toneColor = tone === 'success' ? 'var(--ok)' : tone === 'stale' ? 'var(--warn)' : 'var(--err)';

  return (
    <div data-testid="inventory-panel" style={{ borderTop: '1px solid var(--border-1)', paddingTop: 16 }}>
      {msg && <div role={msg.ok ? 'status' : 'alert'} className="small" style={{ color: msg.ok ? 'var(--ok)' : 'var(--err)', marginBottom: 8 }}>{msg.text}</div>}

      {remote && status && (
        <div className="col gap-2" style={{ marginBottom: 16 }}>
          <div className="row between" style={{ alignItems: 'center' }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Sync status</div>
            <span style={{ color: toneColor, fontSize: 12, fontWeight: 500 }} data-testid="sync-tone">
              {tone === 'disconnected' ? 'Unreachable' : tone === 'fail' ? 'Sync failing' : tone === 'stale' ? 'Stale' : 'Synced'}
            </span>
          </div>
          <div className="small muted">Last successful sync: {fmt(status.last_sync_at)}</div>
          {status.cache_as_of && <div className="small muted">Last-known data cached {fmt(status.cache_as_of)}</div>}
          {status.disconnected_since && (
            <div className="small" style={{ color: 'var(--err)' }} data-testid="outage">
              Unreachable since {fmt(status.disconnected_since)}
              {status.max_disconnect_minutes ? ` (alert after ${status.max_disconnect_minutes} min${status.disconnect_alerted ? ' — sent' : ''})` : ''}
            </div>
          )}
          {status.last_error && <div className="small" style={{ color: 'var(--err)' }}>{status.last_error_code ? `[${status.last_error_code}] ` : ''}{status.last_error}</div>}
          <div><button className="btn sm" onClick={() => act(syncNow, 'Synced')}>Sync now</button></div>
        </div>
      )}

      {conflicts.length > 0 && (
        <div className="col gap-2" style={{ marginBottom: 16 }} data-testid="weight-conflicts">
          <div style={{ fontSize: 13, fontWeight: 600 }}>Spool weight changed during a print</div>
          <div className="small muted">The weight in the inventory is not the one the print started from, so the deduction is on hold. Choose which weight to keep.</div>
          {conflicts.map(w => {
            const spent = Math.max(0, (w.pre_weight_g ?? 0) - w.target_g);
            const found = w.conflict_current_g ?? 0;
            return (
              <div key={w.id} className="col gap-2" data-testid={`conflict-${w.id}`}>
                <span style={{ fontSize: 13 }}>
                  Spool #{w.spool_ref}: {w.pre_weight_g !== null ? `${Math.round(w.pre_weight_g)} g` : '?'} at print start, <span className="num">{Math.round(found)} g</span> now
                  {w.job_id ? ` · job #${w.job_id} used ${Math.round(spent)} g` : ''}
                </span>
                <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
                  <button className="btn sm" aria-label={`Set spool ${w.spool_ref} to ${Math.round(w.target_g)} g`}
                          onClick={() => act(() => resolveWeightConflict(w.id, 'themis'), 'Weight updated')}>Use {Math.round(w.target_g)} g</button>
                  <button className="btn sm" aria-label={`Keep spool ${w.spool_ref} at ${Math.round(found)} g`}
                          onClick={() => act(() => resolveWeightConflict(w.id, 'provider'), 'Kept the inventory weight')}>Keep {Math.round(found)} g</button>
                  <button className="btn sm" aria-label={`Subtract the job's usage from spool ${w.spool_ref}`}
                          onClick={() => act(() => resolveWeightConflict(w.id, 'subtract'), 'Weight updated')}>Subtract job usage → {Math.max(0, Math.round(found - spent))} g</button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {remote && (
        <div className="col gap-2" style={{ marginBottom: 16 }} data-testid="pending-writes">
          <div style={{ fontSize: 13, fontWeight: 600 }}>Queued weight updates</div>
          {queued.length === 0 && <div className="small muted">None — every deduction has reached the provider.</div>}
          {queued.map(w => (
            <div key={w.id} className="row gap-2" data-testid={`pending-${w.id}`} style={{ alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13 }}>Spool #{w.spool_ref} → <span className="num">{Math.round(w.target_g)} g</span></span>
              <span className="tiny muted">{w.source === 'manual_complete' ? 'manual completion' : 'job'}{w.job_id ? ` #${w.job_id}` : ''} · {w.attempts} attempt{w.attempts === 1 ? '' : 's'}</span>
              {w.last_error && <span className="tiny" style={{ color: 'var(--err)' }}>{w.last_error}</span>}
              <button className="btn sm" onClick={() => act(() => resolvePendingWrite(w.id), 'Applied')}>Apply now</button>
              <button className="btn ghost sm" aria-label={`Discard update for spool ${w.spool_ref}`}
                      onClick={() => act(() => discardPendingWrite(w.id), 'Discarded')}>Discard</button>
            </div>
          ))}
        </div>
      )}

      {tracksWeight && suspended.length > 0 && (
        <div className="col gap-2" data-testid="suspended">
          <div style={{ fontSize: 13, fontWeight: 600 }}>Spools with tracking suspended</div>
          <div className="small muted">Usage is not recorded for these until their weight is corrected.</div>
          {suspended.map(s => (
            <div key={s.spool_ref} className="row gap-2" data-testid={`suspended-${s.spool_ref}`} style={{ alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13 }}>Spool #{s.spool_ref}</span>
              <span className="tiny muted">{s.reason} · since {fmt(s.since)}</span>
              <input className="input" type="number" min="0" step="1" placeholder="Weight (g)" aria-label={`Corrected weight for spool ${s.spool_ref}`}
                     style={{ width: 110 }} value={grams[s.spool_ref] ?? ''} onChange={e => setGrams(g => ({ ...g, [s.spool_ref]: e.target.value }))} />
              <button className="btn sm"
                      onClick={() => act(() => resumeTracking(s.spool_ref, grams[s.spool_ref] ? Number(grams[s.spool_ref]) : undefined), 'Tracking resumed')}>
                {grams[s.spool_ref] ? 'Set weight & resume' : 'Weight is correct — resume'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
