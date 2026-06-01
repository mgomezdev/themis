import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { matColor, fmtTime } from '../data/helpers';
import { Icons } from '../components/icons';
import { SectionHeader } from '../components/ui';
import { createOrder, updateOrder, getOrder, type OrderType, type OrderPartInput } from '../api/orders';
import { useSpoolmanConfig, useFilaments, filamentDisplayName, type ApiFilament } from '../api/spoolman';

interface PartRow {
  id?: string;
  name: string;
  material: string;
  qty: number;
  est_minutes: number;
  filament_id: number | null;
  filament_color: string | null;
}

function emptyRow(): PartRow {
  return { name: '', material: 'PLA', qty: 1, est_minutes: 30, filament_id: null, filament_color: null };
}

// ---- FilamentCell ----
// Mirrors the PerPrinterConfig filament picker from NewJobScreen:
// Spoolman catalog dropdown when available, manual type+color otherwise.
function FilamentCell({ part, spoolmanActive, filaments, onChange }: {
  part: PartRow;
  spoolmanActive: boolean;
  filaments: ApiFilament[];
  onChange: (patch: Partial<PartRow>) => void;
}) {
  // Start in manual mode if the part was previously entered manually
  // (has a color but no Spoolman ID, with Spoolman now active).
  const [manualMode, setManualMode] = useState(
    () => spoolmanActive && part.filament_id === null && part.filament_color !== null,
  );

  // Default to neutral grey when manual inputs first appear — color picker can't be empty.
  useEffect(() => {
    if ((!spoolmanActive || manualMode) && part.filament_color === null) {
      onChange({ filament_color: '#888888' });
    }
  }, [spoolmanActive, manualMode]); // eslint-disable-line react-hooks/exhaustive-deps

  const catalogValue = part.filament_id != null
    ? (filaments.find(f => f.id === part.filament_id) != null
        ? filamentDisplayName(filaments.find(f => f.id === part.filament_id)!)
        : '')
    : (part.material && !manualMode ? part.material : '');

  if (spoolmanActive && !manualMode) {
    return (
      <div className="col gap-1">
        <select
          className="select"
          value={catalogValue}
          onChange={e => {
            const v = e.target.value;
            if (v === '__manual__') {
              setManualMode(true);
              onChange({ filament_id: null, filament_color: null });
              return;
            }
            const f = filaments.find(f => filamentDisplayName(f) === v) ?? null;
            onChange({
              material: f?.material ?? v,
              filament_id: f?.id ?? null,
              filament_color: f?.color_hex ? `#${f.color_hex}` : null,
            });
          }}>
          <option value="">— select filament —</option>
          {filaments.map(f => (
            <option key={f.id} value={filamentDisplayName(f)}>
              {filamentDisplayName(f)} · {f.material}
            </option>
          ))}
          <option value="__manual__">Enter manually…</option>
        </select>
        {filaments.length === 0 && (
          <div className="tiny muted">No filaments in Spoolman</div>
        )}
      </div>
    );
  }

  return (
    <div className="col gap-1">
      <div className="row gap-2">
        <input
          className="input"
          list="order-filament-types"
          placeholder="Type (PLA, PETG…)"
          value={part.material}
          onChange={e => onChange({ material: e.target.value, filament_id: null })}
          style={{ flex: 1 }}
        />
        {spoolmanActive && (
          <button className="btn ghost sm" onClick={() => {
            setManualMode(false);
            onChange({ filament_id: null, filament_color: null });
          }}>↩ Catalog</button>
        )}
      </div>
      <datalist id="order-filament-types">
        {['PLA', 'PLA+', 'PETG', 'PA-CF', 'ABS', 'ASA', 'TPU', 'Nylon', 'PC'].map(t => (
          <option key={t} value={t} />
        ))}
      </datalist>
      <div className="row gap-2" style={{ alignItems: 'center' }}>
        <input
          type="color"
          value={part.filament_color ?? '#888888'}
          onChange={e => onChange({ filament_color: e.target.value })}
          style={{ width: 36, height: 28, padding: 2, borderRadius: 6, border: '1px solid var(--border-2)', background: 'var(--bg-1)', cursor: 'pointer', flexShrink: 0 }}
        />
        <span className="tiny muted">{part.filament_color ?? '#888888'}</span>
      </div>
    </div>
  );
}

export function NewOrderScreen() {
  const navigate = useNavigate();
  const { id } = useParams();
  const editingId = id ? Number(id) : null;

  const [orderType, setOrderType] = useState<OrderType>('customer');
  const [customer, setCustomer] = useState('');
  const [due, setDue] = useState('');
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [parts, setParts] = useState<PartRow[]>([emptyRow()]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanActive = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);
  const filaments = useFilaments(spoolmanActive);

  useEffect(() => {
    if (editingId == null) return;
    let alive = true;
    getOrder(editingId).then(o => {
      if (!alive) return;
      setOrderType(o.order_type);
      setCustomer(o.customer);
      setDue(o.due_date ?? '');
      setTitle(o.title);
      setNotes(o.notes ?? '');
      setParts(o.parts.length ? o.parts.map(p => ({
        id: p.id, name: p.name, material: p.material, qty: p.qty,
        est_minutes: p.est_minutes,
        filament_id: p.filament_id ?? null,
        filament_color: p.filament_color ?? null,
      })) : [emptyRow()]);
    }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [editingId]);

  function addPart() { setParts(prev => [...prev, emptyRow()]); }
  function updPart(i: number, patch: Partial<PartRow>) {
    setParts(prev => prev.map((p, idx) => idx === i ? { ...p, ...patch } : p));
  }
  function delPart(i: number) { setParts(prev => prev.filter((_, idx) => idx !== i)); }

  const totalQty = parts.reduce((a, b) => a + (Number(b.qty) || 0), 0);
  const totalTime = parts.reduce((a, b) => a + (Number(b.qty) || 0) * (Number(b.est_minutes) || 0), 0);

  async function handleSubmit() {
    setSaving(true);
    setError(null);
    const payloadParts: OrderPartInput[] = parts
      .filter(p => p.name.trim())
      .map(p => ({
        id: p.id, name: p.name, material: p.material,
        qty: Number(p.qty) || 1, est_minutes: Number(p.est_minutes) || 0,
        filament_id: p.filament_id,
        filament_color: p.filament_color,
      }));
    const body = {
      order_type: orderType,
      customer,
      title,
      due_date: due || null,
      notes: notes || null,
      parts: payloadParts,
    };
    try {
      if (editingId == null) await createOrder(body);
      else await updateOrder(editingId, body);
      navigate('/orders');
    } catch (e) {
      setError(`Failed to save order: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={() => navigate('/orders')}>{Icons.chevL} Orders</button>
        <span className="muted small">/</span>
        <span className="small">{editingId == null ? 'New order' : 'Edit order'}</span>
      </div>

      {error && (
        <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--err)', fontSize: 13 }}>{error}</div>
      )}

      <div className="layout-main-sidebar" style={{ gridTemplateColumns: 'minmax(0, 1fr) 300px' }}>
        <div className="col gap-4">
          <div className="card" style={{ padding: 20 }}>
            <SectionHeader title="Order info" />
            <div className="row gap-3" style={{ marginBottom: 14, flexWrap: 'wrap' }}>
              {([
                { id: 'customer' as OrderType, label: 'Customer order', sub: 'Goes to a paying customer' },
                { id: 'internal' as OrderType, label: 'Internal project', sub: 'R&D, marketing, spares' },
              ]).map(opt => (
                <button key={opt.id} onClick={() => setOrderType(opt.id)} className="card"
                        style={{ flex: 1, textAlign: 'left', padding: 14, cursor: 'pointer',
                                 background: orderType === opt.id ? 'var(--bg-3)' : 'var(--bg-1)',
                                 borderColor: orderType === opt.id ? 'var(--accent)' : 'var(--border-1)' }}>
                  <div style={{ fontWeight: 500 }}>{opt.label}</div>
                  <div className="tiny muted" style={{ marginTop: 2 }}>{opt.sub}</div>
                </button>
              ))}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
              <div>
                <label className="label">{orderType === 'internal' ? 'Project name' : 'Customer'}</label>
                <input className="input" value={customer} onChange={e => setCustomer(e.target.value)}
                       placeholder={orderType === 'internal' ? 'e.g. R&D — reflow oven' : 'e.g. Vela Robotics'} />
              </div>
              <div>
                <label className="label">Due</label>
                <input type="date" className="input" value={due} onChange={e => setDue(e.target.value)} />
              </div>
            </div>

            <div style={{ marginTop: 12 }}>
              <label className="label">Title</label>
              <input className="input" value={title} onChange={e => setTitle(e.target.value)}
                     placeholder="e.g. Mk3 chassis brackets — batch 5" />
            </div>

            <div style={{ marginTop: 12 }}>
              <label className="label">Notes (optional)</label>
              <textarea className="textarea" value={notes} onChange={e => setNotes(e.target.value)}
                        placeholder="Material preferences, finishing, anything special…" />
            </div>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border-1)' }}>
              <SectionHeader title="Parts to print" sub={`${parts.length} parts · ${totalQty} units total`} />
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}></th>
                    <th>Part name</th>
                    <th style={{ width: 200 }}>Filament</th>
                    <th style={{ width: 80 }}>Qty</th>
                    <th style={{ width: 100 }}>Est. each</th>
                    <th style={{ width: 40 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {parts.map((p, i) => (
                    <tr key={i} style={{ cursor: 'default' }}>
                      <td>
                        <div style={{
                          width: 32, height: 32, borderRadius: 4,
                          background: p.filament_color ?? matColor(p.material),
                          border: '1px solid var(--border-1)', opacity: 0.85,
                        }} />
                      </td>
                      <td>
                        <input className="input" placeholder="Part name" value={p.name}
                               onChange={e => updPart(i, { name: e.target.value })} />
                      </td>
                      <td style={{ verticalAlign: 'top', paddingTop: 10 }}>
                        <FilamentCell
                          part={p}
                          spoolmanActive={spoolmanActive}
                          filaments={filaments}
                          onChange={patch => updPart(i, patch)}
                        />
                      </td>
                      <td>
                        <input className="input num" type="number" min="1" value={p.qty}
                               onChange={e => updPart(i, { qty: Number(e.target.value) })} />
                      </td>
                      <td>
                        <input className="input num" type="number" min="0" step="5" value={p.est_minutes}
                               onChange={e => updPart(i, { est_minutes: Number(e.target.value) })} />
                      </td>
                      <td>
                        <button className="btn ghost icon sm" aria-label="Remove part"
                                onClick={() => delPart(i)}>{Icons.x}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ padding: 14, borderTop: '1px solid var(--border-1)' }}>
              <button className="btn sm" onClick={addPart} aria-label="Add part">{Icons.plus} Add part</button>
            </div>
          </div>
        </div>

        <div className="col gap-4">
          <div className="card" style={{ padding: 18 }}>
            <div className="tag-key">Summary</div>
            <div className="row between" style={{ marginTop: 12 }}>
              <span className="tag-key">Parts</span><span className="num small">{parts.filter(p => p.name.trim()).length}</span>
            </div>
            <div className="row between" style={{ marginTop: 6 }}>
              <span className="tag-key">Units</span><span className="num small">{totalQty}</span>
            </div>
            <div className="divider" />
            <div className="row between">
              <span className="tag-key">Total time</span><span className="num small">{totalTime > 0 ? fmtTime(totalTime) : '—'}</span>
            </div>
          </div>

          <div className="card" style={{ padding: 18 }}>
            <button className="btn primary" style={{ width: '100%' }}
                    disabled={saving || !customer.trim() || !title.trim()} onClick={handleSubmit}>
              {Icons.check} {editingId == null ? 'Create order' : 'Save changes'}
            </button>
            <button className="btn ghost sm" style={{ width: '100%', marginTop: 8 }}
                    disabled={saving} onClick={() => navigate('/orders')}>Cancel</button>
            <div className="tiny muted" style={{ marginTop: 10, textAlign: 'center', lineHeight: 1.5 }}>
              Parts are a checklist. Link jobs to this order from New job.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
