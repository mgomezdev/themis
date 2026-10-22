import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CAP, INVENTORY_CAPABILITY, getInventorySettings, saveInventorySettings, useInventory, useMaterials } from '../api/inventory';
import { setCapabilityProvider, updatePlugin, usePlugins } from '../api/plugins';
import { LowStockSettings } from '../components/LowStockSettings';
import { FieldRow, PageHeader, Toggle } from '../components/settingsUi';

/** Settings → Filament inventory (core-owned): which provider supplies spools, whether completed prints deduct from them,
 *  and the low-stock thresholds. Everything below the picker only appears for a provider that can do it. */
export function FilamentInventoryPage() {
  const { plugins } = usePlugins();
  const inventory = useInventory();
  const providers = plugins.filter(p => p.provides.some(x => x.capability === INVENTORY_CAPABILITY));
  const materials = useMaterials(inventory.has(CAP.TRACKS_WEIGHT));
  const [deduct, setDeduct] = useState<boolean | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    getInventorySettings().then(s => { if (alive) setDeduct(s.deduct_on_complete); }).catch(() => { if (alive) setDeduct(null); });
    return () => { alive = false; };
  }, [inventory.id]);

  async function choose(id: string) {
    setMsg(null);
    try {
      if (id) {
        const p = providers.find(x => x.id === id);
        if (p && !p.enabled) await updatePlugin(id, { enabled: true });
      }
      await setCapabilityProvider(INVENTORY_CAPABILITY, id || null);
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
  }

  async function toggleDeduct(v: boolean) {
    setMsg(null);
    const before = deduct;
    setDeduct(v);
    try { setDeduct((await saveInventorySettings({ deduct_on_complete: v })).deduct_on_complete); }
    catch (e) { setDeduct(before); setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
  }

  return (
    <div>
      <PageHeader title="Filament inventory" sub="Where Themis looks up spools, and how it keeps their weights up to date." />
      {msg && <div role="alert" className="small" style={{ color: 'var(--err)', marginBottom: 8 }}>{msg.text}</div>}

      <FieldRow label="Inventory provider" hint="The one source of spools and materials. Only the selected provider is used; switching never deletes the other's data.">
        <div className="col gap-2">
          <select className="select" aria-label="Inventory provider" value={inventory.id ?? ''} onChange={e => choose(e.target.value)}>
            <option value="">None</option>
            {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {inventory.plugin && <Link className="small" to={`/plugins/${inventory.plugin.id}`}>Open {inventory.plugin.name} settings →</Link>}
        </div>
      </FieldRow>

      {inventory.has(CAP.TRACKS_WEIGHT) && inventory.has(CAP.WRITE_WEIGHT) && (
        <FieldRow label="Deduct filament when a job completes" hint="Subtracts the grams a finished print used from its spool. If off, Themis reads weights but never changes them.">
          {deduct !== null && <Toggle checked={deduct} onChange={toggleDeduct} />}
        </FieldRow>
      )}

      {inventory.has(CAP.TRACKS_WEIGHT) && <LowStockSettings materials={materials} />}

      {!inventory.plugin && <div className="muted small" style={{ paddingTop: 16 }}>No provider is selected, so spool pickers, scanning and low-stock alerts are hidden.</div>}
    </div>
  );
}
