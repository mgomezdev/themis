// Settings → Print defaults: queue behaviour, slicing cache and the slicer catalog controls.
import { useState, useEffect, useRef } from 'react';
import { getQueueConfig, saveQueueConfig, type QueueConfig } from '../../api/queue';
import { rescanProfiles } from '../../api/printers';
import { getOrcaCatalogStatus, type OrcaCatalogStatus } from '../../api/orca';
import {
  appliedRemapTotal,
  refreshCatalog,
  rescanCatalog,
  type SyncResponse,
  type PendingRemaps,
  type ConfirmResult,
} from '../../api/laminus';
import { RemapModal } from '../../components/RemapModal';
import { Icons } from '../../components/icons';
import { FieldRow, PageHeader, Toggle } from '../../components/settingsUi';

export function PrintDefaultsPage() {
  // Last values the server confirmed, so a refused save can put the field back instead of leaving the
  // screen claiming a value that was never stored.
  const saved = useRef({ check: 5, dead: 30, retry: 2, waive: 2, snapshot: 2, name: '', estimates: false, useLatest: true });
  const [saveError, setSaveError] = useState<string | null>(null);
  async function persist(
    label: string, patch: Partial<QueueConfig>, revert: () => void, onSaved: () => void, setBusy?: (b: boolean) => void,
  ) {
    setSaveError(null);
    setBusy?.(true);
    try { await saveQueueConfig(patch); onSaved(); }
    catch (e) { revert(); setSaveError(`Couldn't save ${label}: ${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy?.(false); }
  }

  // Queue check interval
  const [checkInterval, setCheckInterval] = useState<number>(5);
  const [savingInterval, setSavingInterval] = useState(false);
  useEffect(() => {
    getQueueConfig().then(c => { saved.current.check = c.check_interval_minutes; setCheckInterval(c.check_interval_minutes); }).catch(console.error);
  }, []);
  async function commitInterval(minutes: number) {
    const v = Math.max(1, Math.round(minutes) || 1);
    setCheckInterval(v);
    await persist('the queue check interval', { check_interval_minutes: v },
      () => setCheckInterval(saved.current.check), () => { saved.current.check = v; }, setSavingInterval);
  }

  // How long a printer may be offline before it is considered dead
  const [deadAfter, setDeadAfter] = useState<number>(30);
  const [savingDeadAfter, setSavingDeadAfter] = useState(false);
  useEffect(() => {
    getQueueConfig().then(c => { const v = c.printer_dead_after_minutes ?? 30; saved.current.dead = v; setDeadAfter(v); }).catch(console.error);
  }, []);
  async function commitDeadAfter(minutes: number) {
    const v = Math.min(1440, Math.max(0, Math.round(minutes) || 0));
    setDeadAfter(v);
    await persist('the printer offline limit', { printer_dead_after_minutes: v },
      () => setDeadAfter(saved.current.dead), () => { saved.current.dead = v; }, setSavingDeadAfter);
  }

  // Hold a part after it fails on this many different printers (BIZ-364)
  const [retryLimit, setRetryLimit] = useState<number>(2);
  const [savingRetry, setSavingRetry] = useState(false);
  useEffect(() => {
    getQueueConfig().then(c => { const v = c.part_retry_limit ?? 2; saved.current.retry = v; setRetryLimit(v); }).catch(console.error);
  }, []);
  async function commitRetryLimit(n: number) {
    const v = Math.min(20, Math.max(1, Math.round(n) || 1));
    setRetryLimit(v);
    await persist('the part retry limit', { part_retry_limit: v },
      () => setRetryLimit(saved.current.retry), () => { saved.current.retry = v; }, setSavingRetry);
  }

  // Waive a printer for a part after this many failures of it there (BIZ-365)
  const [waiveAfter, setWaiveAfter] = useState<number>(2);
  const [savingWaive, setSavingWaive] = useState(false);
  useEffect(() => {
    getQueueConfig().then(c => { const v = c.waive_after ?? 2; saved.current.waive = v; setWaiveAfter(v); }).catch(console.error);
  }, []);
  async function commitWaiveAfter(n: number) {
    const v = Math.min(20, Math.max(1, Math.round(n) || 1));
    setWaiveAfter(v);
    await persist('the printer waiver limit', { waive_after: v },
      () => setWaiveAfter(saved.current.waive), () => { saved.current.waive = v; }, setSavingWaive);
  }

  // Snapshot interval
  const [snapshotInterval, setSnapshotInterval] = useState<number>(2);
  const [savingSnapshot, setSavingSnapshot] = useState(false);
  useEffect(() => {
    getQueueConfig().then(c => {
      const v = c.snapshot_interval_seconds ?? 2;
      saved.current.snapshot = v;
      setSnapshotInterval(v);
    }).catch(console.error);
  }, []);
  async function commitSnapshotInterval(seconds: number) {
    const v = Math.max(1, Math.round(seconds) || 1);
    setSnapshotInterval(v);
    await persist('the snapshot interval', { snapshot_interval_seconds: v },
      () => setSnapshotInterval(saved.current.snapshot), () => { saved.current.snapshot = v; }, setSavingSnapshot);
  }

  // Operator display name
  const [operatorName, setOperatorName] = useState<string>('');
  const [savingName, setSavingName] = useState(false);
  const nameTouchedRef = useRef(false);
  useEffect(() => {
    getQueueConfig().then(c => {
      saved.current.name = c.operator_name ?? '';
      if (!nameTouchedRef.current) setOperatorName(c.operator_name ?? '');
    }).catch(console.error);
  }, []);
  async function commitOperatorName(name: string) {
    const v = name.trim();
    await persist('the display name', { operator_name: v },
      () => setOperatorName(saved.current.name), () => { saved.current.name = v; }, setSavingName);
  }

  // Estimate generation toggle
  const [estimatesEnabled, setEstimatesEnabled] = useState<boolean>(false);
  useEffect(() => {
    getQueueConfig().then(c => { saved.current.estimates = c.estimates_enabled ?? false; setEstimatesEnabled(c.estimates_enabled ?? false); }).catch(console.error);
  }, []);
  async function commitEstimatesEnabled(enabled: boolean) {
    setEstimatesEnabled(enabled);
    await persist('estimate generation', { estimates_enabled: enabled },
      () => setEstimatesEnabled(saved.current.estimates), () => { saved.current.estimates = enabled; });
  }

  // Slicing cache: reslice cached gcode whose presets/OrcaSlicer changed, or keep printing it (pinned)
  const [useLatestSettings, setUseLatestSettings] = useState<boolean>(true);
  useEffect(() => {
    getQueueConfig().then(c => {
      const v = c.slice_cache_use_latest_settings ?? true;
      saved.current.useLatest = v;
      setUseLatestSettings(v);
    }).catch(console.error);
  }, []);
  async function commitUseLatestSettings(enabled: boolean) {
    setUseLatestSettings(enabled);
    await persist('the slicer-settings policy', { slice_cache_use_latest_settings: enabled },
      () => setUseLatestSettings(saved.current.useLatest), () => { saved.current.useLatest = enabled; });
  }

  // Printer preset rescan (old legacy rescan endpoint)
  const [rescanning, setRescanning] = useState(false);
  const [rescanMsg, setRescanMsg] = useState<string | null>(null);
  async function doRescan() {
    setRescanning(true);
    setRescanMsg(null);
    try {
      const r = await rescanProfiles();
      setRescanMsg(`Found ${r.machine_presets} printer presets.`);
    } catch {
      setRescanMsg('Rescan failed — is OrcaSlicer config reachable?');
    } finally {
      setRescanning(false);
    }
  }

  // Orca profile library cache controls
  const [catalogStatus, setCatalogStatus] = useState<OrcaCatalogStatus | null>(null);
  const [catalogOp, setCatalogOp] = useState<'idle' | 'refreshing' | 'rescanning'>('idle');
  const [catalogMsg, setCatalogMsg] = useState<string | null>(null);
  const [pendingRemap, setPendingRemap] = useState<PendingRemaps | null>(null);

  useEffect(() => {
    getOrcaCatalogStatus().then(setCatalogStatus).catch(console.error);
  }, []);

  async function doLoadCatalogStatus() {
    const s = await getOrcaCatalogStatus();
    setCatalogStatus(s);
  }

  async function doRefreshCatalog() {
    setCatalogOp('refreshing');
    setCatalogMsg(null);
    try {
      const r: SyncResponse = await refreshCatalog();
      if (r.status === 'pending_remaps') {
        setPendingRemap(r);
      } else {
        setCatalogMsg(`Catalog refreshed — ${(r.bytes / 1024).toFixed(0)} KB cached.`);
        doLoadCatalogStatus();
      }
    } catch (e) {
      setCatalogMsg(`Refresh failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCatalogOp('idle');
    }
  }

  async function doRescanCatalog() {
    setCatalogOp('rescanning');
    setCatalogMsg(null);
    try {
      const r: SyncResponse = await rescanCatalog();
      if (r.status === 'pending_remaps') {
        setPendingRemap(r);
      } else {
        setCatalogMsg(`Rescan complete — ${(r.bytes / 1024).toFixed(0)} KB cached.`);
        doLoadCatalogStatus();
      }
    } catch (e) {
      setCatalogMsg(`Rescan failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCatalogOp('idle');
    }
  }

  const orcaCount = catalogStatus?.catalog_counts;
  const catalogSummary = orcaCount
    ? `${orcaCount.machine} machines · ${orcaCount.process} processes · ${orcaCount.filament} filaments`
    : catalogStatus?.cached
    ? `${(catalogStatus.cached_bytes / 1024).toFixed(0)} KB cached`
    : 'Not cached';

  return (
    <>
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Print defaults"
                  sub="Workshop-wide print behavior. Per-job overrides win when set during new-job intake." />

      {saveError && (
        <div role="alert" className="small" style={{
          padding: '10px 14px', marginBottom: 14, borderRadius: 6,
          background: 'color-mix(in oklch, var(--err) 12%, transparent)', border: '1px solid color-mix(in oklch, var(--err) 30%, transparent)', color: 'var(--err)',
        }}>
          {saveError}
        </div>
      )}

      <FieldRow label="Queue check interval"
                hint="How often the queue engine scans for an available printer and claims the next compatible job. Minutes.">
        <div className="row gap-2">
          <input className="input" type="number" min={1} step={1}
                 value={checkInterval}
                 onChange={e => setCheckInterval(Number(e.target.value))}
                 onBlur={e => commitInterval(Number(e.target.value))}
                 style={{ width: 90 }} />
          <span className="muted small">min{savingInterval ? ' · saving…' : ''}</span>
        </div>
      </FieldRow>

      <FieldRow label="Camera snapshot interval"
                hint="How often printer tiles refresh their camera snapshot. Applies to all printers unless 'No snapshots while idle' is set on a specific printer.">
        <div className="row gap-2">
          <input className="input" type="number" min={1} step={1}
                 value={snapshotInterval}
                 onChange={e => setSnapshotInterval(Number(e.target.value))}
                 onBlur={e => commitSnapshotInterval(Number(e.target.value))}
                 style={{ width: 90 }} />
          <span className="muted small">sec{savingSnapshot ? ' · saving…' : ''}</span>
        </div>
      </FieldRow>

      <FieldRow label="Printer offline limit"
                hint="How long a printer may stay offline before it is considered dead. A shorter drop changes nothing, so a flaky network does not keep reprocessing the queue. When a printer is declared dead, the job it was printing fails and goes back to the top of the queue. 0 = never.">
        <div className="row gap-2">
          <input className="input" type="number" min={0} max={1440} step={1}
                 aria-label="Printer offline limit"
                 value={deadAfter}
                 onChange={e => setDeadAfter(Number(e.target.value))}
                 onBlur={e => commitDeadAfter(Number(e.target.value))}
                 style={{ width: 90 }} />
          <span className="muted small">min{savingDeadAfter ? ' · saving…' : ''}</span>
        </div>
      </FieldRow>

      <FieldRow label="Hold a failed part after"
                hint="When a part fails on this many different printers it is held on its project for you to Reprint or Drop, instead of going back into the queue again.">
        <div className="row gap-2">
          <input className="input" type="number" min={1} max={20} step={1}
                 aria-label="Hold a failed part after"
                 value={retryLimit}
                 onChange={e => setRetryLimit(Number(e.target.value))}
                 onBlur={e => commitRetryLimit(Number(e.target.value))}
                 style={{ width: 90 }} />
          <span className="muted small">printers{savingRetry ? ' · saving…' : ''}</span>
        </div>
      </FieldRow>

      <FieldRow label="Waive a printer for a part after"
                hint="When one printer fails the same part this many times, it stops getting that part, in every project. Clear it on the Printers page, or test-print it there.">
        <div className="row gap-2">
          <input className="input" type="number" min={1} max={20} step={1}
                 aria-label="Waive a printer for a part after"
                 value={waiveAfter}
                 onChange={e => setWaiveAfter(Number(e.target.value))}
                 onBlur={e => commitWaiveAfter(Number(e.target.value))}
                 style={{ width: 90 }} />
          <span className="muted small">failures{savingWaive ? ' · saving…' : ''}</span>
        </div>
      </FieldRow>

      <FieldRow label="Display name" hint="Shown in the sidebar. Leave blank to hide it.">
        <input className="input" value={operatorName}
               onChange={e => { nameTouchedRef.current = true; setOperatorName(e.target.value); }}
               onBlur={e => commitOperatorName(e.target.value)}
               placeholder="e.g. Workshop Lead" style={{ width: '100%' }} />
        {savingName && <span className="muted small">saving…</span>}
      </FieldRow>

      <FieldRow label="Enable estimate generation"
                hint="When enabled, a test slice runs immediately after job creation to estimate print time and filament use. The gcode is discarded — only time and grams are stored.">
        <Toggle checked={estimatesEnabled} onChange={commitEstimatesEnabled} />
      </FieldRow>

      <FieldRow label="Always use latest slicer settings"
                hint="When unchecked, cached gcode sliced with older presets or an older OrcaSlicer version is still printed and marked stale. Use this to keep a proven print unchanged after a profile/slicer update.">
        <label className="row gap-2 small" style={{ alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" aria-label="Always use latest slicer settings" checked={useLatestSettings}
                 onChange={e => commitUseLatestSettings(e.target.checked)} />
          {useLatestSettings ? 'Reslice stale cached gcode' : 'Keep printing stale cached gcode (marked stale)'}
        </label>
      </FieldRow>

      <FieldRow label="OrcaSlicer profiles"
                hint="Themis caches your OrcaSlicer printer/process/filament presets. Rescan after adding or editing presets so new options appear in printer setup and new jobs.">
        <div className="row gap-2">
          <button className="btn sm" disabled={rescanning} onClick={doRescan}>
            {Icons.refresh} {rescanning ? 'Rescanning…' : 'Rescan profiles'}
          </button>
          {rescanMsg && <span className="muted small">{rescanMsg}</span>}
        </div>
      </FieldRow>

      <FieldRow
        label="Profile library"
        hint="Themis caches the full machine/process/filament catalog from Orca. Refresh fetches the latest copy from Orca. Rescan & rebuild tells Orca to re-read all profiles from disk first (needed after installing new OrcaSlicer profiles).">
        <div className="col gap-2">
          <div className="muted small" style={{ marginBottom: 2 }}>
            {catalogStatus?.laminus?.catalog_building
              ? 'Orca is building catalog…'
              : catalogStatus?.cached
              ? catalogSummary
              : 'Not yet cached'}
          </div>
          <div className="row gap-2 wrap">
            <button className="btn sm" disabled={catalogOp !== 'idle'} onClick={doRefreshCatalog}>
              {Icons.refresh} {catalogOp === 'refreshing' ? 'Refreshing…' : 'Refresh catalog'}
            </button>
            <button className="btn sm" disabled={catalogOp !== 'idle'} onClick={doRescanCatalog}>
              {Icons.refresh} {catalogOp === 'rescanning' ? 'Rescanning… (up to 60 s)' : 'Rescan & rebuild'}
            </button>
          </div>
          {catalogMsg && (
            <span className="small" style={{ color: catalogMsg.includes('failed') ? 'var(--err)' : 'var(--ok)' }}>
              {catalogMsg}
            </span>
          )}
        </div>
      </FieldRow>

    </div>
    {pendingRemap && (
      <RemapModal
        payload={pendingRemap}
        onDone={(result: ConfirmResult) => {
          setPendingRemap(null);
          const total = appliedRemapTotal(result);
          setCatalogMsg(`Remapping applied — ${total} reference${total !== 1 ? 's' : ''} updated.`);
          doLoadCatalogStatus();
        }}
        onCancel={() => {
          setPendingRemap(null);
          setCatalogMsg('Remap cancelled — stale references unchanged.');
        }}
      />
    )}
    </>
  );
}

// =========================================================================
// About page
// =========================================================================
