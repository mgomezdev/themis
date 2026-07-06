import React, { useState, useEffect } from 'react';
import { JOBS } from '../data/mock';
import { useFleetData } from '../api/fleet';
import { fmtTime } from '../data/helpers';
import { StatusPill, Progress, VideoTile, Swatch, Kv } from '../components/ui';
import { Icons } from '../components/icons';
import type { Printer, Job } from '../data/types';
import { pausePrinter, resumePrinter, stopPrinter, fetchPrinterTypes, fetchPrinter, updatePrinter, deletePrinter, fetchMachineCatalog, markPlateCleared, testConnection, reconnectPrinter, type PrinterType, type MachinePreset, type LoadedFilament } from '../api/printers';
import { useSpoolmanConfig, useSpools, useFilaments } from '../api/spoolman';
import { getPrinterProfiles, getQueueConfig } from '../api/queue';
import { PrinterAddForm } from './PrintersScreen';
import { MachinePicker } from '../components/MachinePicker';
import { SlotSpoolPicker } from '../components/SlotSpoolPicker';

type Layout = 'cards' | 'rows';

function partsFromJob(job: Job) {
  return job.parts.map(p => ({ name: p.partId, orderId: p.orderId, material: job.material, qty: p.qty }));
}

// ── Telem row ────────────────────────────────────────────────────────────────
function Telem({ label, value, target, tone }: { label: string; value: string; target: string; tone?: string | null }) {
  return (
    <div className="row between">
      <div className="small muted">{label}</div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span className="num" style={{ fontSize: 14, fontWeight: 600, color: tone === 'warn' ? 'var(--warn)' : 'var(--text-1)' }}>
          {value}
        </span>
        <span className="num tiny muted">/ {target}</span>
      </div>
    </div>
  );
}

// ── Fan icon (spins proportional to duty cycle) ───────────────────────────────
function FanIcon({ pct }: { pct: number }) {
  const running = pct > 0;
  const period = running ? Math.max(0.16, 1.8 - (pct / 100) * 1.6) : 0;
  return (
    <svg width="16" height="16" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round"
         style={{
           color: running ? 'var(--accent-hi)' : 'var(--text-4)',
           animation: running ? `spin ${period.toFixed(2)}s linear infinite` : 'none',
           transformOrigin: 'center',
           flexShrink: 0,
         }}>
      <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
      <path d="M12 10.5 C 12 6.5, 14.5 4, 18.5 4 C 18.5 8, 16 10.5, 12 10.5 Z" />
      <path d="M13.5 12 C 17.5 12, 20 14.5, 20 18.5 C 16 18.5, 13.5 16, 13.5 12 Z" />
      <path d="M12 13.5 C 12 17.5, 9.5 20, 5.5 20 C 5.5 16, 8 13.5, 12 13.5 Z" />
      <path d="M10.5 12 C 6.5 12, 4 9.5, 4 5.5 C 8 5.5, 10.5 8, 10.5 12 Z" />
    </svg>
  );
}

function FanTelem({ label, pct, maxRpm = 7000 }: { label: string; pct: number; maxRpm?: number }) {
  const running = pct > 0;
  const rpm = Math.round((pct / 100) * maxRpm);
  return (
    <div className="row between" style={{ alignItems: 'center' }}>
      <div className="row gap-2" style={{ alignItems: 'center' }}>
        <FanIcon pct={pct} />
        <span className="small muted">{label}</span>
      </div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span className="num" style={{ fontSize: 14, fontWeight: 600, color: running ? 'var(--text-1)' : 'var(--text-3)' }}>
          {pct}%
        </span>
        <span className="num tiny muted">{rpm} rpm</span>
      </div>
    </div>
  );
}

// ── Edit printer modal ───────────────────────────────────────────────────────

function EditPrinterModal({ printer: p, printerTypes, onSaved, onDeleted, onClose }: {
  printer: Printer;
  printerTypes: PrinterType[];
  onSaved: () => void;
  onDeleted: () => void;
  onClose: () => void;
}) {
  const [draftName, setDraftName] = useState(p.name);
  const [draftConn, setDraftConn] = useState<Record<string, string>>({});
  const [machinePreset, setMachinePreset] = useState<string>('');
  const [noSnapshotsWhileIdle, setNoSnapshotsWhileIdle] = useState(false);
  const [catalog, setCatalog] = useState<MachinePreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const printerType = printerTypes.find(t => t.printer_type === p.model);

  useEffect(() => {
    fetchPrinter(Number(p.id))
      .then(api => {
        const conn: Record<string, string> = {};
        for (const [k, v] of Object.entries(api.connection_config)) {
          conn[k] = String(v ?? '');
        }
        setDraftConn(conn);
        setMachinePreset(api.current_orca_printer_profile ?? '');
        setNoSnapshotsWhileIdle(api.no_snapshots_while_idle ?? false);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
    fetchMachineCatalog().then(setCatalog).catch(console.error);
  }, [p.id]);

  const runTestConnection = async () => {
    if (!printerType) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testConnection({ printer_type: p.model, connection_config: draftConn });
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
        current_orca_printer_profile: machinePreset || null,
        no_snapshots_while_idle: noSnapshotsWhileIdle,
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
      background: 'rgba(2,6,16,0.65)', backdropFilter: 'blur(4px)',
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

          <div className="col gap-2">
            <div className="tag-key">Slicer profile</div>
            <MachinePicker catalog={catalog} value={machinePreset} onChange={setMachinePreset} />
            <div className="tiny muted">
              Sets which OrcaSlicer process &amp; filament profiles are offered when queuing jobs, and the machine config used for slicing.
              {machinePreset && <> Preset: <span className="mono">{machinePreset}</span>.</>}
            </div>
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
            <button className="btn primary" onClick={save} disabled={saving}>
              {Icons.check} {saving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Filament picker ──────────────────────────────────────────────────────────

// Multi-slot filament editor. A printer can have N manually-defined slots
// (e.g. the Snapmaker U1 has 4 tools); each maps to a tool index Tn and carries
// its own type/color/name + OrcaSlicer filament profile + optional Spoolman spool.
function FilamentPicker({ printerId, onClose, onSaved }: {
  printerId: number;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { config } = useSpoolmanConfig();
  const spoolmanActive = !!(config?.enabled && config?.url);
  const spools = useSpools(spoolmanActive);
  const filaments = useFilaments(spoolmanActive);
  const [machinePreset, setMachinePreset] = useState<string | null>(null);
  const [filamentProfiles, setFilamentProfiles] = useState<string[]>([]);
  const [slots, setSlots] = useState<LoadedFilament[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getPrinterProfiles(printerId)
      .then(p => { if (alive) setFilamentProfiles(p.filament_profiles); })
      .catch(() => {});
    fetchPrinter(printerId)
      .then(p => {
        if (alive) {
          setSlots(p.loaded_filaments ?? []);
          setMachinePreset(p.current_orca_printer_profile ?? null);
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [printerId]);

  const addSlot = () =>
    setSlots(s => [...s, { slot: s.length, filament_id: null, name: '', type: 'PLA', color: '#888888' }]);
  const removeSlot = (i: number) =>
    setSlots(s => s.filter((_, idx) => idx !== i).map((x, idx) => ({ ...x, slot: idx })));
  const updateSlot = (i: number, patch: Partial<LoadedFilament>) =>
    setSlots(s => s.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await updatePrinter(printerId, {
        loaded_filaments: slots.map((s, idx) => ({ ...s, slot: idx })),
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  }

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-1)' }}>
      <div className="col gap-3">
        {slots.map((s, i) => (
          <div key={i} className="card" style={{ padding: 10, background: 'var(--bg-2)' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <span className="tiny muted mono" style={{ width: 28, flexShrink: 0 }}>T{i}</span>
              <input
                className="input"
                style={{ flex: '1 1 130px' }}
                placeholder="Filament name"
                value={s.name}
                onChange={e => updateSlot(i, { name: e.target.value })}
              />
              <button className="btn ghost icon sm" onClick={() => removeSlot(i)} title="Remove slot">
                {Icons.x}
              </button>
            </div>
            <SlotSpoolPicker
              slot={s}
              printerPreset={machinePreset}
              spools={spools}
              filaments={filaments}
              filamentProfiles={filamentProfiles}
              onChange={patch => updateSlot(i, patch)}
            />
          </div>
        ))}
        <button className="btn ghost sm" onClick={addSlot} style={{ alignSelf: 'flex-start' }}>
          {Icons.plus} Add slot
        </button>
      </div>
      {error && <div className="tiny" style={{ color: 'var(--err)', marginTop: 8 }}>{error}</div>}
      <div className="row between" style={{ marginTop: 12, alignItems: 'center' }}>
        <span className="tiny muted">
          {slots.length === 0
            ? 'No filament — saving leaves this printer unloaded.'
            : `${slots.length} slot${slots.length > 1 ? 's' : ''}`}
        </span>
        <div className="row gap-2">
          <button className="btn ghost sm" onClick={onClose}>Cancel</button>
          <button className="btn primary sm" disabled={saving} onClick={save}>
            {saving ? 'Saving…' : 'Save filaments'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── PrinterExpandedCard ───────────────────────────────────────────────────────
function PrinterExpandedCard({ printer: p, printerTypes, refetchFleet, onCollapse, snapshotIntervalMs }: {
  printer: Printer;
  printerTypes: PrinterType[];
  refetchFleet: () => void;
  onCollapse: () => void;
  snapshotIntervalMs?: number;
}) {
  const isPrinting = p.status === 'printing';
  const isPaused = p.status === 'paused';
  const isOffline = p.status === 'offline';
  const [nickname, setNickname] = useState(p.nickname);
  const [editingName, setEditingName] = useState(false);
  const [editingPrinter, setEditingPrinter] = useState(false);
  const [pickingFilament, setPickingFilament] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => { setNickname(p.nickname); }, [p.nickname]);

  const job: Job | undefined = p.currentJobId ? JOBS.find(j => j.id === p.currentJobId) : undefined;
  const mats = p.materials ?? [p.material];

  return (
    <>
      <div className="card" style={{
        padding: 0, overflow: 'hidden',
        borderColor: 'var(--border-3)',
        boxShadow: '0 0 0 1px var(--accent-glow), 0 18px 40px -20px rgba(0,0,0,0.6)',
        ...cardCueStyle(p),
      }}>
        {/* Header */}
        <div className="row between" style={{
          padding: '14px 18px', background: 'var(--bg-3)',
          borderBottom: '1px solid var(--border-1)', gap: 16, alignItems: 'center',
        }}>
          <div className="col" style={{ minWidth: 0, flex: 1 }}>
            <div className="row gap-2" style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}>
              {editingName ? (
                <input autoFocus className="input" value={nickname}
                       onChange={e => setNickname(e.target.value)}
                       onBlur={() => setEditingName(false)}
                       onKeyDown={e => { if (e.key === 'Enter' || e.key === 'Escape') setEditingName(false); }}
                       style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', padding: '2px 8px', width: 220, background: 'var(--bg-1)' }} />
              ) : (
                <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em' }}>{nickname}</div>
              )}
              <button className="btn ghost icon sm" title="Rename" onClick={() => setEditingName(v => !v)}>
                <svg className="ico" width="14" height="14" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9"/>
                  <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>
                </svg>
              </button>
              <div className="muted small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{p.name}</div>
              <span className="mono tiny muted" style={{ flexShrink: 0 }}>· {p.id}</span>
            </div>
            <div className="tiny muted" style={{ marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              <span className="num">{p.buildVolume}</span> mm · {p.chamber ? 'enclosed' : 'open frame'} · capable: {p.capabilities.join(' · ')}
            </div>
          </div>
          <div className="row gap-2" style={{ flexShrink: 0, alignItems: 'center' }}>
            <StatusPill status={p.status} />
            {isOffline && (
              <button
                className="btn sm"
                disabled={reconnecting}
                title="Attempt to reconnect this printer"
                onClick={async () => {
                  setReconnecting(true);
                  try { await reconnectPrinter(p.id); refetchFleet(); }
                  catch { /* connection attempt launched — fleet will update via WS */ }
                  finally { setReconnecting(false); }
                }}>
                {reconnecting ? 'Connecting…' : <>{Icons.refresh} Reconnect</>}
              </button>
            )}
            {p.awaitingPlateClear && (
              <ReadyForWorkButton printerId={p.id} refetchFleet={refetchFleet} />
            )}
            <button
              className={`btn sm${p.queueOn ? '' : ' ghost'}`}
              title={p.queueOn ? 'Disable queue pulling' : 'Enable queue pulling'}
              style={p.queueOn ? {} : { color: 'var(--text-3)' }}
              onClick={async () => {
                await updatePrinter(Number(p.id), { queue_on: !p.queueOn });
                refetchFleet();
              }}>
              {p.queueOn ? <>{Icons.queue} Queue on</> : <>{Icons.queue} Queue off</>}
            </button>
            <button className="btn sm">{Icons.camera} Snapshot</button>
            <button className="btn icon sm" title="Edit printer" onClick={() => setEditingPrinter(true)}>
              {Icons.wrench}
            </button>
            <button className="btn ghost icon sm" title="Collapse" onClick={onCollapse}>{Icons.x}</button>
          </div>
        </div>

        {/* Body */}
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.6fr) minmax(300px, 1fr)', gap: 18, padding: 18 }}>
          {/* LEFT */}
          <div className="col gap-4" style={{ minWidth: 0 }}>
            <VideoTile
              live={p.status !== 'offline'}
              status={p.status}
              time={isPrinting ? p.timeElapsed : undefined}
              printerId={p.id}
              intervalMs={snapshotIntervalMs}
              noSnapshotsWhileIdle={p.noSnapshotsWhileIdle}
            />

            <div className="row gap-6" style={{ flexWrap: 'wrap' }}>
              <Kv k="Progress" v={<span className="num" style={{ fontSize: 22, fontWeight: 600 }}>{p.progress}%</span>} />
              <Kv k="Time left" v={
                <span className="num" style={{ fontSize: 22, fontWeight: 600, color: isPrinting ? 'var(--accent-hi)' : 'var(--text-3)' }}>
                  {isPrinting ? fmtTime(p.timeRemaining) : '—'}
                </span>
              } />
              <Kv k="Elapsed" v={<span className="num" style={{ fontSize: 22 }}>{isPrinting ? fmtTime(p.timeElapsed) : '—'}</span>} />
              {p.layer && (
                <Kv k="Layer" v={
                  <span className="num" style={{ fontSize: 22 }}>
                    {p.layer.now}<span className="muted" style={{ fontSize: 14 }}> / {p.layer.total}</span>
                  </span>
                } />
              )}
            </div>
            {isPrinting && <Progress value={p.progress} large />}

            <div className="row gap-2" style={{ marginTop: 2 }}>
              {isPrinting && (
                <>
                  <button className="btn" onClick={() => pausePrinter(p.id).catch(console.error)}>{Icons.pause} Pause</button>
                  <button className="btn" onClick={() => stopPrinter(p.id).catch(console.error)}>{Icons.stop} Stop</button>
                </>
              )}
              {isPaused && (
                <>
                  <button className="btn" onClick={() => resumePrinter(p.id).catch(console.error)}>{Icons.play} Resume</button>
                  <button className="btn" onClick={() => stopPrinter(p.id).catch(console.error)}>{Icons.stop} Stop</button>
                </>
              )}
              {!isPrinting && !isPaused && (
                <button className="btn primary">{Icons.play} Claim next from queue</button>
              )}
            </div>

            {job && (
              <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
                <div className="row between" style={{ marginBottom: 10, alignItems: 'center' }}>
                  <div className="col">
                    <span className="tag-key">Current job</span>
                    <div className="row gap-2" style={{ marginTop: 2 }}>
                      <span className="mono tiny muted">{job.id}</span>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{job.plateName}</span>
                    </div>
                  </div>
                  <button className="btn ghost sm">{Icons.arrowR} Open job</button>
                </div>
                <div className="col gap-2">
                  {partsFromJob(job).map((part, i) => (
                    <div key={i} className="row between" style={{
                      padding: '8px 12px', background: 'var(--bg-2)',
                      borderRadius: 8, border: '1px solid var(--border-1)',
                    }}>
                      <div className="col" style={{ minWidth: 0 }}>
                        <div className="small" style={{ fontWeight: 500 }}>{part.name}</div>
                        <div className="tiny muted">{part.orderId} · {part.material}</div>
                      </div>
                      <div className="num small" style={{ flexShrink: 0 }}>×{part.qty}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* RIGHT */}
          <div className="col gap-4">
            <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
              <div className="row between" style={{ alignItems: 'center' }}>
                <span className="tag-key">Loaded filament{mats.length > 1 ? 's' : ''}</span>
                <button className="btn ghost sm" onClick={() => setPickingFilament(v => !v)}>
                  {pickingFilament ? 'Cancel' : 'Change'}
                </button>
              </div>
              {mats.length === 0 ? (
                <div className="tiny muted" style={{ marginTop: 10 }}>— no filament loaded</div>
              ) : (
                <div className="col gap-2" style={{ marginTop: 10 }}>
                  {mats.map((m, i) => (
                    <div key={i} className="row gap-3" style={{ alignItems: 'center' }}>
                      <Swatch color={m.color} large={mats.length === 1} />
                      <div className="col" style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {m.name}
                        </div>
                        <div className="tiny muted">{m.type}</div>
                      </div>
                      {mats.length > 1 && <span className="tiny muted mono" style={{ flexShrink: 0 }}>T{i}</span>}
                    </div>
                  ))}
                </div>
              )}
              {pickingFilament && (
                <FilamentPicker
                  printerId={Number(p.id)}
                  onClose={() => setPickingFilament(false)}
                  onSaved={() => { setPickingFilament(false); refetchFleet(); }}
                />
              )}
            </div>

            <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
              <div className="tag-key" style={{ marginBottom: 10 }}>Temperatures</div>
              <div className="col gap-3">
                <Telem label="Nozzle" value={`${p.nozzleTemp}°C`} target={isPrinting ? '220°C' : '—'} tone={isPrinting ? 'warn' : null} />
                <Telem label="Bed" value={`${p.bedTemp}°C`} target={isPrinting ? '60°C' : '—'} />
                {p.chamberTemp != null && (
                  <Telem label="Chamber" value={`${p.chamberTemp}°C`} target={p.chamber ? '60°C' : '—'} />
                )}
              </div>

              <div className="tag-key" style={{ marginTop: 16, marginBottom: 10 }}>Fans</div>
              <div className="col gap-3">
                <FanTelem label="Model" pct={p.fanModel} maxRpm={7200} />
                <FanTelem label="Auxiliary" pct={p.fanAux} maxRpm={5400} />
                <FanTelem label="Box" pct={p.fanBox} maxRpm={5400} />
              </div>
            </div>
          </div>
        </div>
      </div>

      {editingPrinter && (
        <EditPrinterModal
          printer={p} printerTypes={printerTypes}
          onSaved={() => { setEditingPrinter(false); refetchFleet(); }}
          onDeleted={() => { setEditingPrinter(false); refetchFleet(); onCollapse(); }}
          onClose={() => setEditingPrinter(false)}
        />
      )}
    </>
  );
}

// ── Shared printer-card affordances ───────────────────────────────────────────
const QUEUE_OFF_COLOR = '#f59e0b';

// Border/shadow that flags a card as needing attention (awaiting clear) or
// manually held out of the queue.
function cardCueStyle(p: Printer): React.CSSProperties {
  if (p.awaitingPlateClear) {
    return { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' };
  }
  if (!p.queueOn) {
    return { borderColor: QUEUE_OFF_COLOR, boxShadow: `0 0 0 1px rgba(245,158,11,0.35)` };
  }
  return {};
}

function QueueOffBadge() {
  return (
    <span className="tiny" style={{
      padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap',
      background: 'rgba(245,158,11,0.15)', color: QUEUE_OFF_COLOR, fontWeight: 600,
    }}>QUEUE OFF</span>
  );
}

function ReadyForWorkButton({ printerId, refetchFleet, block }: {
  printerId: string; refetchFleet: () => void; block?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className="btn primary sm"
      style={block ? { width: '100%' } : undefined}
      disabled={busy}
      onClick={async (e) => {
        e.stopPropagation();
        setBusy(true);
        try { await markPlateCleared(printerId); refetchFleet(); }
        finally { setBusy(false); }
      }}>
      {Icons.check} Ready for new work
    </button>
  );
}

// ── PrinterTile ───────────────────────────────────────────────────────────────
function PrinterTile({ printer: p, onClick, refetchFleet, snapshotIntervalMs }: { printer: Printer; onClick: () => void; refetchFleet: () => void; snapshotIntervalMs?: number }) {
  const isPrinting = p.status === 'printing';
  return (
    <div className="card" onClick={onClick} style={{ cursor: 'pointer', padding: 0, overflow: 'hidden', transition: 'border-color 120ms ease', ...cardCueStyle(p) }}>
      <div className="row between" style={{ padding: '12px 14px 8px' }}>
        <div className="row gap-2" style={{ alignItems: 'baseline' }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{p.nickname}</span>
          <span className="tiny muted">{p.badge}</span>
        </div>
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          {!p.queueOn && <QueueOffBadge />}
          <StatusPill status={p.status} />
        </div>
      </div>
      <div style={{ padding: '0 14px' }}>
        <VideoTile
          live={p.status !== 'offline'}
          status={p.status}
          printerId={p.id}
          intervalMs={snapshotIntervalMs}
          noSnapshotsWhileIdle={p.noSnapshotsWhileIdle}
        />
      </div>
      <div className="row between" style={{ padding: '12px 14px 10px' }}>
        <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
          <Swatch color={p.material.color} />
          <div className="col" style={{ minWidth: 0 }}>
            <div className="small" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.material.name}</div>
            <div className="tiny muted">{p.material.type}</div>
          </div>
        </div>
        {isPrinting ? (
          <div className="col" style={{ alignItems: 'flex-end', whiteSpace: 'nowrap' }}>
            <div className="num" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text-1)' }}>
              {fmtTime(p.timeRemaining)}
            </div>
            <div className="tiny muted">remaining</div>
          </div>
        ) : (
          <div className="tiny muted" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{p.note ?? '—'}</div>
        )}
      </div>
      {isPrinting && (
        <div style={{ padding: '0 14px 14px' }}>
          <Progress value={p.progress} />
          <div className="row between" style={{ marginTop: 6 }}>
            <span className="tiny muted">{p.currentJobId}</span>
            <span className="num tiny muted">{p.progress}%</span>
          </div>
        </div>
      )}
      {p.awaitingPlateClear && (
        <div style={{ padding: '0 14px 14px' }}>
          <ReadyForWorkButton printerId={p.id} refetchFleet={refetchFleet} block />
          <div className="tiny muted" style={{ textAlign: 'center', marginTop: 4 }}>
            Clear the plate, then mark ready
          </div>
        </div>
      )}
    </div>
  );
}

// ── PrinterRow (rows layout — no video feed) ──────────────────────────────────
function PrinterRow({ printer: p, expanded, onClick, refetchFleet }: { printer: Printer; expanded: boolean; onClick: () => void; refetchFleet: () => void }) {
  const isPrinting = p.status === 'printing';
  return (
    <div className="card" onClick={onClick} style={{
      cursor: 'pointer', padding: '12px 14px',
      display: 'grid',
      gridTemplateColumns: 'auto auto 1.4fr auto 1.6fr 1fr auto',
      alignItems: 'center', gap: 16,
      background: expanded ? 'var(--bg-3)' : undefined,
      ...(expanded ? { borderColor: 'var(--accent)' } : cardCueStyle(p)),
    }}>
      <span style={{ display: 'inline-flex', color: 'var(--text-3)', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 120ms ease' }}>
        {Icons.chevR}
      </span>
      <div style={{
        width: 36, height: 36, borderRadius: 6,
        background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
        border: '1px solid var(--border-1)', display: 'grid', placeItems: 'center',
      }}>
        <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
      </div>
      <div className="col" style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{p.nickname}</div>
        <div className="tiny muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
      </div>
      <div className="col gap-1" style={{ alignItems: 'flex-start' }}>
        <StatusPill status={p.status} />
        {!p.queueOn && <QueueOffBadge />}
      </div>
      <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
        <Swatch color={p.material.color} />
        <div className="col" style={{ minWidth: 0 }}>
          <div className="small" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.material.name}</div>
          <div className="tiny muted">{p.material.type}</div>
        </div>
      </div>
      <div className="col" style={{ minWidth: 0 }}>
        {isPrinting ? (
          <>
            <Progress value={p.progress} />
            <div className="row between" style={{ marginTop: 4 }}>
              <span className="mono tiny muted">{p.currentJobId}</span>
              <span className="num tiny muted">{p.progress}%</span>
            </div>
          </>
        ) : (
          <span className="tiny muted">{p.note ?? '—'}</span>
        )}
      </div>
      {p.awaitingPlateClear ? (
        <div style={{ justifySelf: 'end' }} onClick={e => e.stopPropagation()}>
          <ReadyForWorkButton printerId={p.id} refetchFleet={refetchFleet} />
        </div>
      ) : isPrinting ? (
        <div className="col" style={{ alignItems: 'flex-end', whiteSpace: 'nowrap', minWidth: 80 }}>
          <div className="num" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text-1)' }}>
            {fmtTime(p.timeRemaining)}
          </div>
          <div className="tiny muted">remaining</div>
        </div>
      ) : (
        <div className="tiny muted" style={{ minWidth: 80, textAlign: 'right' }}>—</div>
      )}
    </div>
  );
}

// ── AddPrinterCard ────────────────────────────────────────────────────────────
function AddPrinterCard({ onClick, variant }: { onClick: () => void; variant: 'card' | 'row' }) {
  const hoverOn = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.borderColor = 'var(--accent)';
    e.currentTarget.style.color = 'var(--accent-hi)';
    e.currentTarget.style.background = 'var(--accent-glow)';
  };
  const hoverOff = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.borderColor = 'var(--border-2)';
    e.currentTarget.style.color = 'var(--text-3)';
    e.currentTarget.style.background = 'transparent';
  };

  if (variant === 'row') {
    return (
      <button onClick={onClick} className="row gap-3"
              style={{ padding: '14px 18px', borderRadius: 10, width: '100%',
                       border: '1.5px dashed var(--border-2)', background: 'transparent',
                       color: 'var(--text-3)', cursor: 'pointer', alignItems: 'center',
                       justifyContent: 'center', transition: 'all 140ms ease' }}
              onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
        {Icons.plus}
        <span className="small" style={{ fontWeight: 500 }}>Add printer</span>
      </button>
    );
  }
  return (
    <button onClick={onClick} className="col"
            style={{ alignItems: 'center', justifyContent: 'center', gap: 8,
                     minHeight: 280, borderRadius: 10, padding: 14,
                     border: '1.5px dashed var(--border-2)', background: 'transparent',
                     color: 'var(--text-3)', cursor: 'pointer', transition: 'all 140ms ease' }}
            onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
      <div style={{ width: 44, height: 44, borderRadius: 12, display: 'grid', placeItems: 'center',
                    background: 'var(--bg-3)', border: '1px solid var(--border-1)' }}>
        {Icons.plus}
      </div>
      <div className="small" style={{ fontWeight: 500 }}>Add printer</div>
      <div className="tiny muted" style={{ textAlign: 'center', maxWidth: 220 }}>
        Connect a Bambu, Elegoo, Snapmaker, Prusa or OctoPrint host.
      </div>
    </button>
  );
}

// ── Layout toggle (Cards / Rows) ──────────────────────────────────────────────
function LayoutToggle({ value, onChange }: { value: Layout; onChange: (v: Layout) => void }) {
  const opts: { id: Layout; label: string; icon: React.ReactElement }[] = [
    { id: 'cards', label: 'Cards', icon: Icons.fleet },
    { id: 'rows',  label: 'Rows',  icon: Icons.layers },
  ];
  return (
    <div className="row" style={{ gap: 0, padding: 2, borderRadius: 7, background: 'var(--bg-2)', border: '1px solid var(--border-1)' }}>
      {opts.map(o => {
        const on = value === o.id;
        return (
          <button key={o.id} onClick={() => onChange(o.id)} className="row gap-2" title={o.label}
                  style={{
                    padding: '4px 10px', borderRadius: 5, border: 'none',
                    background: on ? 'var(--bg-4)' : 'transparent',
                    color: on ? 'var(--text-1)' : 'var(--text-3)',
                    fontSize: 12, fontWeight: 500, cursor: 'pointer', alignItems: 'center',
                    boxShadow: on ? '0 1px 0 rgba(0,0,0,0.25), 0 0 0 1px var(--border-2)' : 'none',
                  }}>
            {React.cloneElement(o.icon, { size: 13 } as React.SVGProps<SVGSVGElement>)}
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── FleetGrid (cards layout) ──────────────────────────────────────────────────
function FleetGrid({ printers, expandedId, onToggle, onAdd, printerTypes, refetchFleet, snapshotIntervalMs }: {
  printers: Printer[]; expandedId: string | null; onToggle: (id: string) => void; onAdd: () => void;
  printerTypes: PrinterType[]; refetchFleet: () => void; snapshotIntervalMs?: number;
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
      {printers.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div key={p.id} style={{ gridColumn: expanded ? '1 / -1' : 'auto' }}>
            {expanded
              ? <PrinterExpandedCard printer={p} printerTypes={printerTypes} refetchFleet={refetchFleet} onCollapse={() => onToggle(p.id)} snapshotIntervalMs={snapshotIntervalMs} />
              : <PrinterTile printer={p} onClick={() => onToggle(p.id)} refetchFleet={refetchFleet} snapshotIntervalMs={snapshotIntervalMs} />}
          </div>
        );
      })}
      <AddPrinterCard onClick={onAdd} variant="card" />
    </div>
  );
}

// ── FleetRows (rows layout) ───────────────────────────────────────────────────
function FleetRows({ printers, expandedId, onToggle, onAdd, printerTypes, refetchFleet, snapshotIntervalMs }: {
  printers: Printer[]; expandedId: string | null; onToggle: (id: string) => void; onAdd: () => void;
  printerTypes: PrinterType[]; refetchFleet: () => void; snapshotIntervalMs?: number;
}) {
  return (
    <div className="col gap-2">
      {printers.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div key={p.id}>
            <PrinterRow printer={p} expanded={expanded} onClick={() => onToggle(p.id)} refetchFleet={refetchFleet} />
            {expanded && (
              <div style={{ marginTop: 8 }}>
                <PrinterExpandedCard printer={p} printerTypes={printerTypes} refetchFleet={refetchFleet} onCollapse={() => onToggle(p.id)} snapshotIntervalMs={snapshotIntervalMs} />
              </div>
            )}
          </div>
        );
      })}
      <AddPrinterCard onClick={onAdd} variant="row" />
    </div>
  );
}

// ── FleetScreen ───────────────────────────────────────────────────────────────
export function FleetScreen() {
  const [printers, refetchFleet] = useFleetData();
  const [layout, setLayout] = useState<Layout>('cards');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [printerTypes, setPrinterTypes] = useState<PrinterType[]>([]);
  const [snapshotIntervalMs, setSnapshotIntervalMs] = useState<number>(2000);

  useEffect(() => {
    fetchPrinterTypes().then(setPrinterTypes).catch(console.error);
    getQueueConfig().then(c => setSnapshotIntervalMs((c.snapshot_interval_seconds ?? 2) * 1000)).catch(console.error);
  }, []);

  const toggle = (id: string) => setExpandedId(expandedId === id ? null : id);
  const statusCounts = {
    printing: printers.filter(p => p.status === 'printing').length,
    claiming: printers.filter(p => p.status === 'claiming').length,
    idle:     printers.filter(p => p.status === 'idle').length,
    paused:   printers.filter(p => p.status === 'paused').length,
    error:    printers.filter(p => p.status === 'error').length,
    offline:  printers.filter(p => p.status === 'offline').length,
  };
  const fleetSummary = [
    statusCounts.error    && `${statusCounts.error} error`,
    statusCounts.paused   && `${statusCounts.paused} paused`,
    statusCounts.printing && `${statusCounts.printing} printing`,
    statusCounts.claiming && `${statusCounts.claiming} claiming`,
    statusCounts.idle     && `${statusCounts.idle} idle`,
    statusCounts.offline  && `${statusCounts.offline} offline`,
  ].filter(Boolean).join(' · ');

  if (adding) {
    return (
      <PrinterAddForm
        types={printerTypes}
        backLabel="Fleet"
        onCancel={() => setAdding(false)}
        onCreated={() => { setAdding(false); refetchFleet(); }}
      />
    );
  }

  return (
    <div className="col gap-5">
      <div className="row between">
        <div>
          <div className="tag-key" style={{ marginBottom: 2 }}>Workshop</div>
          <div className="row gap-3" style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}>
            <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
              {printers.length} printers
            </div>
            <div className="muted small">{fleetSummary}</div>
          </div>
        </div>
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          <LayoutToggle value={layout} onChange={setLayout} />
          <button className="btn sm" style={{ whiteSpace: 'nowrap' }}>{Icons.refresh} Sync now</button>
          <button className="btn primary sm" style={{ whiteSpace: 'nowrap' }} onClick={() => setAdding(true)}>
            {Icons.plus} Add printer
          </button>
        </div>
      </div>

      {layout === 'cards' && (
        <FleetGrid printers={printers} expandedId={expandedId} onToggle={toggle} onAdd={() => setAdding(true)} printerTypes={printerTypes} refetchFleet={refetchFleet} snapshotIntervalMs={snapshotIntervalMs} />
      )}
      {layout === 'rows' && (
        <FleetRows printers={printers} expandedId={expandedId} onToggle={toggle} onAdd={() => setAdding(true)} printerTypes={printerTypes} refetchFleet={refetchFleet} snapshotIntervalMs={snapshotIntervalMs} />
      )}
    </div>
  );
}
