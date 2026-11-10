// Fleet: the edit-printer dialog (connection, profiles, loaded filament slots, delete).
import { useState, useEffect } from 'react';
import { Icons } from '../../components/icons';
import type { Printer } from '../../data/types';
import {
  fetchPrinter,
  updatePrinter,
  deletePrinter,
  testConnection,
  type PrinterType,
  type ApiPrinter,
  type NozzleVariant,
} from '../../api/printers';
import { fetchPlugins, type PluginSummary } from '../../api/plugins';
import { NozzleVariantPicker } from '../../components/NozzleVariantPicker';

export function EditPrinterModal({ printer: p, printerTypes, onSaved, onDeleted, onClose }: {
  printer: Printer;
  printerTypes: PrinterType[];
  onSaved: () => void;
  onDeleted: () => void;
  onClose: () => void;
}) {
  const [draftName, setDraftName] = useState(p.name);
  const [draftConn, setDraftConn] = useState<Record<string, string>>({});
  const [nozzle, setNozzle] = useState<NozzleVariant | null>(null);   // null = the model's default nozzle
  const [apiPrinter, setApiPrinter] = useState<ApiPrinter | null>(null);
  const [noSnapshotsWhileIdle, setNoSnapshotsWhileIdle] = useState(false);
  const [quietStart, setQuietStart] = useState('');   // '' = no quiet hours
  const [quietEnd, setQuietEnd] = useState('');
  const [machineRate, setMachineRate] = useState('');   // '' = use the shop rate
  const [slicer, setSlicer] = useState('');   // '' = the default slicing provider
  const [slicerTouched, setSlicerTouched] = useState(false);   // only a changed choice is sent, so an unloaded list can never wipe it
  const [slicers, setSlicers] = useState<PluginSummary[]>([]);   // enabled plugins that provide gcode.produce
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);   // Save stays off until the printer's real values are in the form
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const printerType = printerTypes.find(t => t.plugin_id === p.pluginId);

  useEffect(() => {
    fetchPrinter(Number(p.id))
      .then(api => {
        const conn: Record<string, string> = {};
        for (const [k, v] of Object.entries(api.connection_config)) {
          conn[k] = String(v ?? '');
        }
        setDraftConn(conn);
        setApiPrinter(api);
        setNozzle(api.nozzle_variant ?? null);
        setNoSnapshotsWhileIdle(api.no_snapshots_while_idle ?? false);
        setQuietStart(api.quiet_start ?? '');
        setQuietEnd(api.quiet_end ?? '');
        setMachineRate(api.machine_rate_per_hour != null ? String(api.machine_rate_per_hour) : '');
        setSlicer(api.preferred_slicer_plugin_id ?? '');
        setLoaded(true);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
    fetchPlugins()
      .then(list => setSlicers((list?.plugins ?? []).filter(pl => pl.enabled && pl.provides.some(x => x.capability === 'gcode.produce'))))
      .catch(console.error);
  }, [p.id]);

  const runTestConnection = async () => {
    if (!printerType) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testConnection({ printer_type: p.pluginId ?? p.model, connection_config: draftConn });
      setTestResult(result);
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof Error ? e.message : 'Test failed' });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await updatePrinter(Number(p.id), {
        name: draftName,
        connection_config: draftConn,
        nozzle_variant: nozzle,
        no_snapshots_while_idle: noSnapshotsWhileIdle,
        machine_rate_per_hour: machineRate.trim() === '' ? null : Number(machineRate),
        ...(slicerTouched ? { preferred_slicer_plugin_id: slicer || null } : {}),
        // Both or neither: a half-filled window is treated as none.
        quiet_start: quietStart && quietEnd ? quietStart : null,
        quiet_end: quietStart && quietEnd ? quietEnd : null,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) { setConfirmDelete(true); return; }
    try {
      await deletePrinter(Number(p.id));
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
      setConfirmDelete(false);
    }
  };

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0,
      background: 'var(--scrim)', backdropFilter: 'blur(4px)',
      zIndex: 100, display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{
        width: 'min(640px, 100%)', maxHeight: '90vh', overflowY: 'auto',
        padding: 0, borderColor: 'var(--border-3)',
        boxShadow: '0 20px 60px -20px rgba(0,0,0,0.7), 0 0 0 1px var(--accent-glow)',
      }}>
        <div className="row between" style={{
          padding: '16px 20px', borderBottom: '1px solid var(--border-1)',
          alignItems: 'center', background: 'var(--bg-3)',
        }}>
          <div className="row gap-3" style={{ alignItems: 'center' }}>
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
              border: '1px solid var(--border-1)', display: 'grid', placeItems: 'center',
            }}>
              <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
            </div>
            <div className="col">
              <div className="tag-key">Edit printer</div>
              <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}>{p.name}</div>
            </div>
          </div>
          <button className="btn ghost icon sm" onClick={onClose}>{Icons.x}</button>
        </div>

        <div className="col gap-5" style={{ padding: 20 }}>
          <div className="col gap-2">
            <div className="tag-key">Identity</div>
            <label className="label">Name</label>
            <input className="input" value={draftName} onChange={e => setDraftName(e.target.value)} />
            <div className="row gap-4" style={{ marginTop: 4 }}>
              {[
                { label: 'Type', value: printerType?.display_name ?? p.model },
                { label: 'Printer ID', value: p.id },
              ].map(kv => (
                <div key={kv.label} className="col">
                  <span className="tag-key" style={{ fontSize: 9.5 }}>{kv.label}</span>
                  <span className="small mono" style={{ marginTop: 2 }}>{kv.value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="col gap-3">
            <div className="tag-key">Connection</div>
            {loading ? (
              <div className="tiny muted">Loading connection settings…</div>
            ) : printerType ? (
              <>
                {printerType.connection_fields.map(field => (
                  <div key={field.name} className="col gap-1">
                    <label className="label">{field.label}{field.required ? ' *' : ''}</label>
                    <input
                      className="input mono"
                      type={field.field_type === 'password' ? 'password' : 'text'}
                      value={draftConn[field.name] ?? ''}
                      onChange={e => {
                        setDraftConn(prev => ({ ...prev, [field.name]: e.target.value }));
                        setTestResult(null);
                      }}
                      placeholder={field.placeholder || String(field.default ?? '')}
                    />
                    {field.help_text && <div className="tiny muted">{field.help_text}</div>}
                  </div>
                ))}
                <div className="row gap-3" style={{ alignItems: 'center', paddingTop: 4 }}>
                  <button
                    className="btn sm"
                    disabled={testing || saving}
                    onClick={runTestConnection}
                    style={{ width: 'fit-content' }}
                  >
                    {testing ? 'Testing…' : <>{Icons.link} Test connection</>}
                  </button>
                  {testResult && (
                    <span className="small" style={{ color: testResult.ok ? 'var(--ok)' : 'var(--err)' }}>
                      {testResult.ok ? 'Connected' : (testResult.error ?? 'Could not connect')}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <div className="tiny muted">Unknown printer type — connection fields unavailable.</div>
            )}
          </div>

          {loaded && apiPrinter && (
            <NozzleVariantPicker modelUuid={apiPrinter.model_uuid} value={nozzle} onChange={setNozzle}
                                 resolvedProfile={apiPrinter.machine_profile} reason={apiPrinter.machine_profile_reason} />
          )}

          <div className="col gap-2">
            <div className="tag-key">Machine cost</div>
            <label className="label" htmlFor="machine-rate">Machine rate ($ per hour of print time)</label>
            <input id="machine-rate" className="input" type="number" min="0" step="0.01" style={{ maxWidth: 160 }}
                   placeholder="Shop rate" value={machineRate} onChange={e => setMachineRate(e.target.value)} />
            <div className="tiny muted">
              Leave blank to use the shop rate (Settings → Costs). Prices the completed jobs this printer ran in each project&apos;s expenses.
            </div>
          </div>

          {(slicers.length > 1 || slicer) && (
            <div className="col gap-2">
              <div className="tag-key">Slicer</div>
              <label className="label" htmlFor="preferred-slicer">Slice jobs for this printer with</label>
              <select id="preferred-slicer" className="input" style={{ maxWidth: 260 }} value={slicer}
                      onChange={e => { setSlicer(e.target.value); setSlicerTouched(true); }}>
                <option value="">Default slicer</option>
                {slicer && !slicers.some(s => s.id === slicer) && <option value={slicer}>{slicer} (disabled or removed)</option>}
                {slicers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              <div className="tiny muted">
                A chosen slicer is never swapped for another: if it is off or down, this printer&apos;s jobs wait (Settings → Plugins).
              </div>
            </div>
          )}

          <div className="col gap-2">
            <div className="tag-key">Quiet hours</div>
            <div className="row gap-2" style={{ alignItems: 'center' }}>
              <input type="time" className="input" aria-label="Quiet hours start" style={{ maxWidth: 130 }}
                     value={quietStart} onChange={e => setQuietStart(e.target.value)} />
              <span className="small muted">to</span>
              <input type="time" className="input" aria-label="Quiet hours end" style={{ maxWidth: 130 }}
                     value={quietEnd} onChange={e => setQuietEnd(e.target.value)} />
              {(quietStart || quietEnd) && (
                <button type="button" className="btn ghost sm" onClick={() => { setQuietStart(''); setQuietEnd(''); }}>Clear</button>
              )}
            </div>
            <span className="tiny muted">This printer won&apos;t start new jobs in this window (server clock, UTC in Docker by default; may wrap midnight). A running print is never interrupted.</span>
          </div>

          <div className="col gap-2">
            <div className="tag-key">Camera</div>
            <div className="row gap-3" style={{ alignItems: 'center' }}>
              <button
                role="switch"
                aria-checked={noSnapshotsWhileIdle}
                onClick={() => setNoSnapshotsWhileIdle(v => !v)}
                style={{
                  width: 38, height: 22, borderRadius: 999,
                  background: noSnapshotsWhileIdle ? 'var(--accent)' : 'var(--bg-3)',
                  border: `1px solid ${noSnapshotsWhileIdle ? 'var(--accent)' : 'var(--border-2)'}`,
                  position: 'relative', cursor: 'pointer', flexShrink: 0,
                  boxShadow: noSnapshotsWhileIdle ? '0 0 0 3px var(--accent-glow)' : 'none',
                  transition: 'background 120ms, border-color 120ms', padding: 0,
                }}>
                <div style={{
                  position: 'absolute', top: 2, left: noSnapshotsWhileIdle ? 18 : 2,
                  width: 16, height: 16, borderRadius: '50%',
                  background: 'white', boxShadow: '0 1px 2px rgba(0,0,0,0.3)',
                  transition: 'left 120ms',
                }}/>
              </button>
              <div className="col">
                <span className="small" style={{ fontWeight: 500 }}>No snapshots while idle</span>
                <span className="tiny muted">Pause camera polling when this printer is not printing. Saves resources on printers that are often idle.</span>
              </div>
            </div>
          </div>

          {error && <div className="tiny" style={{ color: 'var(--err)' }}>{error}</div>}
        </div>

        <div className="row between" style={{
          padding: '14px 20px', borderTop: '1px solid var(--border-1)',
          background: 'var(--bg-3)', alignItems: 'center',
        }}>
          {confirmDelete ? (
            <div className="row gap-2" style={{ alignItems: 'center' }}>
              <span className="small" style={{ color: 'var(--err)' }}>Remove printer?</span>
              <button className="btn ghost sm" style={{ color: 'var(--err)' }} onClick={handleDelete}>
                {Icons.trash} Confirm
              </button>
              <button className="btn ghost sm" onClick={() => setConfirmDelete(false)}>Cancel</button>
            </div>
          ) : (
            <button className="btn ghost sm" style={{ color: 'var(--err)' }} onClick={handleDelete}>
              {Icons.trash} Remove printer
            </button>
          )}
          <div className="row gap-2">
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" onClick={save} disabled={saving || !loaded}>
              {Icons.check} {saving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Filament picker ──────────────────────────────────────────────────────────
