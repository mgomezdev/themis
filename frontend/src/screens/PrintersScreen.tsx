import { useState, useEffect, Fragment } from 'react';
import { Icons } from '../components/icons';
import { StatusPill, SectionHeader } from '../components/ui';
import {
  fetchPrinters,
  fetchPrinterTypes,
  createPrinter,
  updatePrinter,
  deletePrinter,
  testConnection,
  type ApiPrinter,
  type PrinterType,
  type ConnectionField,
  type LoadedFilament,
} from '../api/printers';

// ---------------------------------------------------------------------------
// Constants + small components
// ---------------------------------------------------------------------------

const MAT_TYPES = ['PLA', 'PETG', 'ABS', 'ASA', 'PA-CF', 'PC', 'TPU', 'Other'] as const;

function SlotSwatch({ color, name, type }: { color: string; name: string; type: string }) {
  return (
    <div
      title={`${name} (${type})`}
      style={{
        width: 16,
        height: 16,
        borderRadius: '50%',
        background: color,
        border: '1px solid var(--border-2)',
        flexShrink: 0,
        display: 'inline-block',
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ConnStatus = 'idle' | 'testing' | 'success' | 'error';

interface WizardData {
  printerType: PrinterType | null;
  nickname: string;
  connectionConfig: Record<string, string>;
}

// ---------------------------------------------------------------------------
// EditForm — inline edit for an existing printer
// ---------------------------------------------------------------------------

function EditForm({
  printer,
  types,
  onSave,
  onCancel,
}: {
  printer: ApiPrinter;
  types: PrinterType[];
  onSave: () => void;
  onCancel: () => void;
}) {
  const ptype = types.find(t => t.printer_type === printer.printer_type);
  const [name, setName] = useState(printer.name);
  const [config, setConfig] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(printer.connection_config).map(([k, v]) => [k, String(v)])
    )
  );
  const [slots, setSlots] = useState<LoadedFilament[]>(printer.loaded_filaments ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addSlot() {
    setSlots(s => [...s, { slot: s.length, filament_id: null, name: '', type: 'PLA', color: '#888888' }]);
  }

  function removeSlot(i: number) {
    setSlots(s => s.filter((_, idx) => idx !== i).map((x, idx) => ({ ...x, slot: idx })));
  }

  function updateSlot(i: number, patch: Partial<LoadedFilament>) {
    setSlots(s => s.map((x, idx) => idx === i ? { ...x, ...patch } : x));
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updatePrinter(printer.id, { name, connection_config: config, loaded_filaments: slots });
      onSave();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card" style={{ padding: 16, marginTop: 8 }}>
      <div className="col gap-3">
        <div>
          <label className="label">Nickname</label>
          <input className="input" value={name} onChange={e => setName(e.target.value)} />
        </div>
        {ptype?.connection_fields.map((f: ConnectionField) => (
          <div key={f.name}>
            <label className="label">{f.label}</label>
            <input
              className="input"
              type={f.field_type === 'password' ? 'password' : 'text'}
              placeholder={f.placeholder}
              value={config[f.name] ?? ''}
              onChange={e => setConfig({ ...config, [f.name]: e.target.value })}
            />
          </div>
        ))}
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Loaded filaments</div>
          <div className="col gap-2">
            {slots.map((s, i) => (
              <div key={i} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                <span className="tiny muted" style={{ width: 44, flexShrink: 0 }}>Slot {i + 1}</span>
                <input
                  type="color"
                  value={s.color}
                  onChange={e => updateSlot(i, { color: e.target.value })}
                  style={{
                    width: 32, height: 32, padding: 2,
                    border: '1px solid var(--border-1)', borderRadius: 6,
                    cursor: 'pointer', background: 'var(--bg-2)', flexShrink: 0,
                  }}
                />
                <select
                  className="input"
                  style={{ width: 90, flexShrink: 0 }}
                  value={s.type}
                  onChange={e => updateSlot(i, { type: e.target.value })}>
                  {MAT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
                <input
                  className="input"
                  style={{ flex: '1 1 140px' }}
                  placeholder="Filament name"
                  value={s.name}
                  onChange={e => updateSlot(i, { name: e.target.value })}
                />
                <button
                  className="btn ghost icon sm"
                  onClick={() => removeSlot(i)}
                  title="Remove slot">
                  {Icons.x}
                </button>
              </div>
            ))}
            <button
              className="btn ghost sm"
              onClick={addSlot}
              style={{ alignSelf: 'flex-start' }}>
              {Icons.plus} Add slot
            </button>
          </div>
        </div>
        {error && <div style={{ color: 'var(--err)', fontSize: 13 }}>{error}</div>}
        <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
          <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
          <button className="btn primary sm" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PrinterAddForm — 3-step wizard
// ---------------------------------------------------------------------------

export function PrinterAddForm({
  types,
  onCancel,
  onCreated,
  backLabel = 'Printers',
}: {
  types: PrinterType[];
  onCancel: () => void;
  onCreated: () => void;
  backLabel?: string;
}) {
  const [step, setStep] = useState(1);
  const [data, setData] = useState<WizardData>({
    printerType: types[0] ?? null,
    nickname: '',
    connectionConfig: {},
  });
  const [connStatus, setConnStatus] = useState<ConnStatus>('idle');
  const [connError, setConnError] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);

  async function handleTestConnection() {
    if (!data.printerType) return;
    setConnStatus('testing');
    setConnError(null);
    try {
      const result = await testConnection({
        printer_type: data.printerType.printer_type,
        connection_config: data.connectionConfig,
      });
      if (result.ok) {
        setConnStatus('success');
      } else {
        setConnStatus('error');
        setConnError(result.error ?? 'Could not connect');
      }
    } catch (e) {
      setConnStatus('error');
      setConnError(e instanceof Error ? e.message : 'Connection test failed');
    }
  }

  async function handleFinish() {
    if (!data.printerType) return;
    setFinishing(true);
    setFinishError(null);
    try {
      await createPrinter({
        name: data.nickname || data.printerType.display_name,
        printer_type: data.printerType.printer_type,
        connection_config: data.connectionConfig,
      });
      onCreated();
    } catch (e) {
      setFinishError(e instanceof Error ? e.message : 'Failed to add printer');
      setFinishing(false);
    }
  }

  const steps = [
    { n: 1, label: 'Type' },
    { n: 2, label: 'Connect' },
    { n: 3, label: 'Review' },
  ] as const;

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={onCancel}>
          {Icons.chevL} {backLabel}
        </button>
        <span className="muted small">/</span>
        <span className="small">Add printer</span>
      </div>

      <div style={{ maxWidth: 760 }}>
        {/* Step indicators */}
        <div className="row gap-3" style={{ marginBottom: 24 }}>
          {steps.map(s => (
            <div key={s.n} className="row gap-2" style={{ alignItems: 'center' }}>
              <div style={{
                width: 24, height: 24, borderRadius: 12,
                display: 'grid', placeItems: 'center',
                fontSize: 11, fontWeight: 600,
                background: step >= s.n ? 'var(--accent)' : 'var(--bg-3)',
                color: step >= s.n ? '#04101f' : 'var(--text-3)',
                border: step === s.n ? '2px solid var(--accent-hi)' : '1px solid var(--border-1)',
              }}>
                {step > s.n ? '✓' : s.n}
              </div>
              <span className="small" style={{ color: step >= s.n ? 'var(--text-1)' : 'var(--text-3)' }}>
                {s.label}
              </span>
              {s.n < 3 && (
                <div style={{ width: 40, height: 1, background: 'var(--border-1)', marginLeft: 8 }} />
              )}
            </div>
          ))}
        </div>

        {/* Step 1: Printer type */}
        {step === 1 && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Select printer type" sub="Choose the vendor for this printer." />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 20 }}>
              {types.map(t => {
                const active = data.printerType?.printer_type === t.printer_type;
                return (
                  <button key={t.printer_type}
                    onClick={() => setData({ ...data, printerType: t, connectionConfig: {} })}
                    className="card"
                    style={{
                      textAlign: 'left', padding: 14, cursor: 'pointer',
                      background: active ? 'var(--bg-3)' : 'var(--bg-1)',
                      borderColor: active ? 'var(--accent)' : 'var(--border-1)',
                    }}>
                    <div className="row between">
                      <div style={{ fontWeight: 500 }}>{t.display_name}</div>
                      {active && <div style={{ color: 'var(--accent-hi)' }}>{Icons.check}</div>}
                    </div>
                  </button>
                );
              })}
            </div>
            <div>
              <label className="label">Nickname</label>
              <input className="input" value={data.nickname}
                placeholder="e.g. Atlas, Forge, Iris"
                onChange={e => setData({ ...data, nickname: e.target.value })} />
              <div className="tiny muted" style={{ marginTop: 6 }}>
                Shown in queue and on tiles.
              </div>
            </div>
            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn ghost" onClick={onCancel}>Cancel</button>
              <button className="btn primary" disabled={!data.printerType} onClick={() => setStep(2)}>
                Next {Icons.chevR}
              </button>
            </div>
          </div>
        )}

        {/* Step 2: Connection fields */}
        {step === 2 && data.printerType && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title={`Connect to ${data.printerType.display_name}`} />
            <div className="col gap-3">
              {data.printerType.connection_fields.map((f: ConnectionField) => (
                <div key={f.name}>
                  <label className="label">{f.label}{f.required ? '' : ' (optional)'}</label>
                  <input
                    className="input"
                    type={f.field_type === 'password' ? 'password' : 'text'}
                    placeholder={f.placeholder}
                    value={data.connectionConfig[f.name] ?? (f.default != null ? String(f.default) : '')}
                    onChange={e => setData({
                      ...data,
                      connectionConfig: { ...data.connectionConfig, [f.name]: e.target.value },
                    })}
                  />
                  {f.help_text && <div className="tiny muted" style={{ marginTop: 4 }}>{f.help_text}</div>}
                </div>
              ))}
            </div>

            {connStatus === 'success' && (
              <div className="row gap-2" style={{ marginTop: 12, color: 'var(--ok)', fontSize: 13 }}>
                {Icons.check} Connection successful
              </div>
            )}
            {connStatus === 'error' && (
              <div className="row gap-2" style={{ marginTop: 12, color: 'var(--err)', fontSize: 13 }}>
                {Icons.alert} {connError ?? 'Could not connect'}
              </div>
            )}

            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setStep(1)}>{Icons.chevL} Back</button>
              <button className="btn"
                onClick={handleTestConnection}
                disabled={connStatus === 'testing'}>
                {connStatus === 'testing' ? 'Testing…' : 'Test connection'}
              </button>
              <button className="btn primary" onClick={() => setStep(3)}>
                Next {Icons.chevR}
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Review + finish */}
        {step === 3 && data.printerType && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Review" sub="Confirm the details before adding." />
            <div className="col gap-2" style={{ marginBottom: 20 }}>
              <div className="row gap-3">
                <span className="muted small" style={{ width: 120 }}>Type</span>
                <span className="small">{data.printerType.display_name}</span>
              </div>
              <div className="row gap-3">
                <span className="muted small" style={{ width: 120 }}>Nickname</span>
                <span className="small">{data.nickname || data.printerType.display_name}</span>
              </div>
              {Object.entries(data.connectionConfig).map(([k, v]) => {
                const field = data.printerType!.connection_fields.find(f => f.name === k);
                const isPassword = field?.field_type === 'password';
                return (
                  <div key={k} className="row gap-3">
                    <span className="muted small" style={{ width: 120 }}>{field?.label ?? k}</span>
                    <span className="small mono">{isPassword ? '••••••••' : v}</span>
                  </div>
                );
              })}
            </div>

            {finishError && (
              <div style={{ color: 'var(--err)', fontSize: 13, marginBottom: 12 }}>{finishError}</div>
            )}

            <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setStep(2)}>{Icons.chevL} Back</button>
              <button className="btn primary" onClick={handleFinish} disabled={finishing}>
                {finishing ? 'Adding…' : <>{Icons.check} Finish</>}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PrintersScreen — main export
// ---------------------------------------------------------------------------

export function PrintersScreen() {
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  const [types, setTypes] = useState<PrinterType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [ps, ts] = await Promise.all([fetchPrinters(), fetchPrinterTypes()]);
      setPrinters(ps);
      setTypes(ts);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load printers');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleDelete(id: number) {
    if (!confirm('Delete this printer?')) return;
    try {
      await deletePrinter(id);
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  if (adding) {
    return (
      <PrinterAddForm
        types={types}
        onCancel={() => setAdding(false)}
        onCreated={() => { setAdding(false); load(); }}
      />
    );
  }

  const onlineCount = printers.filter(p => p.connected).length;
  const offlineCount = printers.filter(p => !p.connected).length;

  const displayName = (p: ApiPrinter) =>
    types.find(t => t.printer_type === p.printer_type)?.display_name ?? p.printer_type;

  const connectionSummary = (p: ApiPrinter) => {
    const cfg = p.connection_config;
    if ('ip_address' in cfg) return String(cfg.ip_address);
    if ('host' in cfg) return String(cfg.host);
    const first = Object.values(cfg)[0];
    return first != null ? String(first) : '—';
  };

  return (
    <div className="col gap-4">
      <SectionHeader
        title="Printers"
        sub={`${onlineCount} connected · ${offlineCount} offline`}
        actions={
          <button className="btn primary sm" onClick={() => setAdding(true)}>
            {Icons.plus} Add printer
          </button>
        }
      />

      {loading && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <span className="muted small">Loading…</span>
        </div>
      )}

      {error && (
        <div className="card" style={{ padding: 16, color: 'var(--err)' }}>
          {error}
          <button className="btn ghost sm" style={{ marginLeft: 12 }} onClick={load}>Retry</button>
        </div>
      )}

      {!loading && !error && printers.length === 0 && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <span className="muted small">No printers yet — add one to get started.</span>
        </div>
      )}

      {!loading && !error && printers.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table className="tbl" style={{ minWidth: 540 }}>
            <thead>
              <tr>
                <th>Printer</th>
                <th>Type</th>
                <th>Connection</th>
                <th>Filament</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {printers.map(p => (
                <Fragment key={p.id}>
                  <tr style={{ opacity: p.enabled ? 1 : 0.5 }}>
                    <td>
                      <div className="col">
                        <div style={{ fontWeight: 500 }}>{p.name}</div>
                        <div className="mono tiny muted">#{p.id}</div>
                      </div>
                    </td>
                    <td><div className="small">{displayName(p)}</div></td>
                    <td className="mono small">{connectionSummary(p)}</td>
                    <td>
                      {p.loaded_filaments.length === 0 ? (
                        <span className="tiny muted">— no filament</span>
                      ) : (
                        <div className="row gap-1" style={{ alignItems: 'center' }}>
                          {p.loaded_filaments.map(s => (
                            <SlotSwatch key={s.slot} color={s.color} name={s.name} type={s.type} />
                          ))}
                        </div>
                      )}
                    </td>
                    <td>
                      <StatusPill status={p.connected ? 'idle' : 'offline'} />
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
                        <button
                          className="btn ghost sm"
                          onClick={() => setEditingId(editingId === p.id ? null : p.id)}>
                          Edit
                        </button>
                        <button
                          className="btn icon ghost sm"
                          onClick={() => handleDelete(p.id)}>
                          {Icons.trash}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {editingId === p.id && (
                    <tr>
                      <td colSpan={6} style={{ padding: '0 16px 16px' }}>
                        <EditForm
                          printer={p}
                          types={types}
                          onSave={() => { setEditingId(null); load(); }}
                          onCancel={() => setEditingId(null)}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
