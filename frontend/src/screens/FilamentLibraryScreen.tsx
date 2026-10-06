import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CAP, archiveMaterial, archiveSpool, createMaterial, createSpool, fetchMaterialList, fetchSpoolList, materialDisplayName,
  setSpoolWeight, spoolColor, spoolDisplayName, updateMaterial, updateSpool, useInventory,
  type InvMaterial, type InvSpool,
} from '../api/inventory';

/** The filament library: materials and spools of the active provider, for providers that own their library. Works only
 *  through the neutral inventory API and its capabilities (MANAGE_MATERIALS / MANAGE_SPOOLS / WRITE_WEIGHT). */

type Tab = 'spools' | 'materials';
const num = (s: string): number | null => (s.trim() === '' || Number.isNaN(Number(s)) ? null : Number(s));

function MaterialForm({ initial, onSave, onCancel }: { initial?: InvMaterial; onSave: (v: Record<string, unknown>) => Promise<void>; onCancel: () => void }) {
  const [v, setV] = useState({
    name: initial?.name ?? '', material: initial?.material ?? '', vendor: initial?.vendor ?? '', color_hex: initial?.color_hex ?? '',
    density: initial?.density != null ? String(initial.density) : '', diameter: initial?.diameter != null ? String(initial.diameter) : '',
  });
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV(s => ({ ...s, [k]: e.target.value }));
  return (
    <div className="card col gap-2" style={{ padding: 14 }} data-testid="material-form">
      <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
        <input className="input" aria-label="Material name" placeholder="Name *" value={v.name} onChange={set('name')} />
        <input className="input" aria-label="Material type" placeholder="Type (PLA…)" value={v.material} onChange={set('material')} style={{ width: 120 }} />
        <input className="input" aria-label="Vendor" placeholder="Vendor" value={v.vendor} onChange={set('vendor')} />
        <input className="input" aria-label="Colour" placeholder="#RRGGBB" value={v.color_hex} onChange={set('color_hex')} style={{ width: 110 }} />
        <input className="input" aria-label="Density" placeholder="Density" type="number" step="0.01" value={v.density} onChange={set('density')} style={{ width: 90 }} />
        <input className="input" aria-label="Diameter" placeholder="Diameter" type="number" step="0.01" value={v.diameter} onChange={set('diameter')} style={{ width: 90 }} />
      </div>
      <div className="row gap-2">
        <button className="btn primary sm" disabled={!v.name.trim()}
                onClick={() => onSave({ name: v.name, material: v.material || null, vendor: v.vendor || null, color_hex: v.color_hex || null, density: num(v.density), diameter: num(v.diameter) })}>
          {initial ? 'Save material' : 'Add material'}
        </button>
        <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function SpoolForm({ initial, materials, canWeigh, onSave, onCancel }: {
  initial?: InvSpool; materials: InvMaterial[]; canWeigh: boolean; onSave: (v: Record<string, unknown>) => Promise<void>; onCancel: () => void;
}) {
  const [v, setV] = useState({
    material_ref: initial?.material_ref ?? '', label: initial?.label ?? '', location: initial?.location ?? '',
    initial_g: '', remaining_g: '',
  });
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV(s => ({ ...s, [k]: e.target.value }));
  return (
    <div className="card col gap-2" style={{ padding: 14 }} data-testid="spool-form">
      <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
        {!initial && (
          <select className="select" aria-label="Spool material" value={v.material_ref} onChange={set('material_ref')}>
            <option value="">Material *</option>
            {materials.filter(m => !m.archived).map(m => <option key={m.ref} value={m.ref}>{materialDisplayName(m)}</option>)}
          </select>
        )}
        <input className="input" aria-label="Spool label" placeholder="Label" value={v.label} onChange={set('label')} />
        <input className="input" aria-label="Storage location" placeholder="Location" value={v.location} onChange={set('location')} />
        {!initial && <input className="input" aria-label="Initial weight" placeholder="Initial g" type="number" min="0" value={v.initial_g} onChange={set('initial_g')} style={{ width: 110 }} />}
        {!initial && canWeigh && <input className="input" aria-label="Remaining weight" placeholder="Remaining g" type="number" min="0" value={v.remaining_g} onChange={set('remaining_g')} style={{ width: 120 }} />}
      </div>
      <div className="row gap-2">
        <button className="btn primary sm" disabled={!initial && !v.material_ref}
                onClick={() => onSave(initial
                  ? { label: v.label || null, location: v.location || null }
                  : { material_ref: v.material_ref, label: v.label || null, location: v.location || null, initial_g: num(v.initial_g), remaining_g: num(v.remaining_g) })}>
          {initial ? 'Save spool' : 'Add spool'}
        </button>
        <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export function FilamentLibraryScreen() {
  const inventory = useInventory();
  const canMaterials = inventory.has(CAP.MANAGE_MATERIALS);
  const canSpools = inventory.has(CAP.MANAGE_SPOOLS);
  const canWeigh = inventory.has(CAP.WRITE_WEIGHT);
  const [chosen, setTab] = useState<Tab>('spools');
  const tab: Tab = chosen === 'spools' && !canSpools && canMaterials ? 'materials' : chosen;   // a materials-only provider opens on its only section
  const [showArchived, setShowArchived] = useState(false);
  const [materials, setMaterials] = useState<InvMaterial[]>([]);
  const [spools, setSpools] = useState<InvSpool[]>([]);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [weighing, setWeighing] = useState<{ ref: string; value: string } | null>(null);

  const reload = useCallback(() => {
    Promise.all([fetchMaterialList(true), fetchSpoolList(true)])
      .then(([m, s]) => { setMaterials(m.items); setSpools(s.items); setError(''); })
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => { if (canMaterials || canSpools) reload(); }, [inventory.id, canMaterials, canSpools, reload]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(fn: () => Promise<unknown>) {
    setError('');
    try { await fn(); setAdding(false); setEditing(null); setWeighing(null); reload(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  const visibleMaterials = useMemo(() => materials.filter(m => showArchived || !m.archived), [materials, showArchived]);
  const visibleSpools = useMemo(() => spools.filter(s => showArchived || !s.archived), [spools, showArchived]);
  const spoolCount = (ref: string) => spools.filter(s => s.material_ref === ref && !s.archived).length;

  if (!inventory.plugin || (!canMaterials && !canSpools)) {
    return <div className="card" style={{ padding: 24 }} data-testid="library-unavailable">
      <div className="small muted">The active inventory keeps its library elsewhere (or none is selected), so there is nothing to manage here.</div>
    </div>;
  }

  return (
    <div className="col gap-3" data-testid="library">
      <div className="row between" style={{ alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <nav className="settings-tabs" style={{ display: 'flex', gap: 4 }} aria-label="Library sections">
          {canSpools && <button className={`settings-tab ${tab === 'spools' ? 'active' : ''}`} onClick={() => { setTab('spools'); setAdding(false); setEditing(null); }}>Spools</button>}
          {canMaterials && <button className={`settings-tab ${tab === 'materials' ? 'active' : ''}`} onClick={() => { setTab('materials'); setAdding(false); setEditing(null); }}>Materials</button>}
        </nav>
        <div className="row gap-3" style={{ alignItems: 'center' }}>
          <label className="row gap-2 small" style={{ alignItems: 'center' }}>
            <input type="checkbox" checked={showArchived} onChange={e => setShowArchived(e.target.checked)} /> Show archived
          </label>
          <button className="btn primary sm" onClick={() => { setAdding(true); setEditing(null); }}>{tab === 'spools' ? 'Add spool' : 'Add material'}</button>
        </div>
      </div>
      {error && <div role="alert" className="small" style={{ color: 'var(--err)' }}>{error}</div>}

      {tab === 'materials' && canMaterials && (
        <>
          {adding && <MaterialForm onSave={v => act(() => createMaterial(v as never))} onCancel={() => setAdding(false)} />}
          <table className="table" style={{ width: '100%' }} data-testid="materials-table">
            <thead><tr><th /><th style={{ textAlign: 'left' }}>Material</th><th style={{ textAlign: 'left' }}>Type</th><th>Spools</th><th /></tr></thead>
            <tbody>
              {visibleMaterials.length === 0 && <tr><td colSpan={5} className="muted small">No materials yet.</td></tr>}
              {visibleMaterials.map(m => (
                <tr key={m.ref} data-testid={`material-${m.ref}`} style={{ opacity: m.archived ? 0.5 : 1 }}>
                  <td><span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: '50%', background: m.color_hex || '#94a3b8' }} /></td>
                  <td>{materialDisplayName(m)}{m.archived && <span className="tiny muted"> · archived</span>}</td>
                  <td>{m.material ?? '—'}</td>
                  <td style={{ textAlign: 'center' }}>{spoolCount(m.ref)}</td>
                  <td className="row gap-2" style={{ justifyContent: 'flex-end' }}>
                    <button className="btn ghost sm" onClick={() => { setEditing(`m:${m.ref}`); setAdding(false); }}>Edit</button>
                    <button className="btn ghost sm" onClick={() => act(() => archiveMaterial(m.ref, !m.archived))}>{m.archived ? 'Restore' : 'Archive'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {materials.filter(m => editing === `m:${m.ref}`).map(m => (
            <MaterialForm key={m.ref} initial={m} onCancel={() => setEditing(null)}
                          onSave={v => act(() => updateMaterial(m.ref, v as never))} />
          ))}
        </>
      )}

      {tab === 'spools' && canSpools && (
        <>
          {adding && <SpoolForm materials={materials} canWeigh={canWeigh} onSave={v => act(() => createSpool(v as never))} onCancel={() => setAdding(false)} />}
          <table className="table" style={{ width: '100%' }} data-testid="spools-table">
            <thead><tr><th /><th style={{ textAlign: 'left' }}>Spool</th><th style={{ textAlign: 'left' }}>Location</th><th style={{ textAlign: 'right' }}>Remaining</th><th /></tr></thead>
            <tbody>
              {visibleSpools.length === 0 && <tr><td colSpan={5} className="muted small">No spools yet.</td></tr>}
              {visibleSpools.map(s => (
                <tr key={s.ref} data-testid={`spool-${s.ref}`} style={{ opacity: s.archived ? 0.5 : 1 }}>
                  <td><span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: '50%', background: spoolColor(s) }} /></td>
                  <td>#{s.ref} {spoolDisplayName(s)}{s.archived && <span className="tiny muted"> · archived</span>}</td>
                  <td>{s.location?.trim() || '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    {weighing?.ref === s.ref ? (
                      <span className="row gap-1" style={{ justifyContent: 'flex-end' }}>
                        <input className="input" type="number" min="0" aria-label={`New weight for spool ${s.ref}`} style={{ width: 90 }}
                               value={weighing.value} onChange={e => setWeighing({ ref: s.ref, value: e.target.value })} />
                        <button className="btn sm" disabled={num(weighing.value) == null}
                                onClick={() => act(() => setSpoolWeight(s.ref, num(weighing.value) as number))}>Set</button>
                      </span>
                    ) : (
                      <span className="num">{s.remaining_g != null ? `${Math.round(s.remaining_g)} g` : '—'}{s.initial_g ? ` / ${Math.round(s.initial_g)} g` : ''}</span>
                    )}
                  </td>
                  <td className="row gap-2" style={{ justifyContent: 'flex-end' }}>
                    {canWeigh && <button className="btn ghost sm" onClick={() => setWeighing({ ref: s.ref, value: s.remaining_g != null ? String(Math.round(s.remaining_g)) : '' })}>Set weight</button>}
                    <button className="btn ghost sm" onClick={() => { setEditing(`s:${s.ref}`); setAdding(false); }}>Edit</button>
                    <button className="btn ghost sm" onClick={() => act(() => archiveSpool(s.ref, !s.archived))}>{s.archived ? 'Restore' : 'Archive'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {spools.filter(s => editing === `s:${s.ref}`).map(s => (
            <SpoolForm key={s.ref} initial={s} materials={materials} canWeigh={canWeigh} onCancel={() => setEditing(null)}
                       onSave={v => act(() => updateSpool(s.ref, v as never))} />
          ))}
        </>
      )}
    </div>
  );
}
