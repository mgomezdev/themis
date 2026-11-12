// Fleet: add a maintenance item for a printer without leaving the page.
import { useState, useEffect, useRef } from 'react';
import { Icons } from '../../components/icons';
import type { Printer } from '../../data/types';
import { fetchPrinter, fetchMachineCatalog } from '../../api/printers';
import { MaintenanceItemForm, emptyDraft, type ItemDraft } from '../../components/MaintenanceItemForm';
import { resolveVendorModelForProfile, createMaintenanceItem, type FleetVendorModel } from '../../api/maintenance';

// ── Quick-add maintenance modal ───────────────────────────────────────────────
export function QuickAddMaintenanceModal({ printer, fleetModels, onClose, onSaved }: {
  printer: Printer;
  fleetModels: FleetVendorModel[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<ItemDraft>(emptyDraft());
  const [error, setError] = useState<string | null>(null);
  // Tracks whether the user has already touched the form before the printer's
  // vendor/model resolves, so the async pre-fill below never clobbers an
  // in-flight manual edit (e.g. typing a name or picking "General" before the
  // fetch/catalog round-trip completes).
  const userEditedRef = useRef(false);

  useEffect(() => {
    let alive = true;
    Promise.all([fetchPrinter(Number(printer.id)), fetchMachineCatalog()])
      .then(([apiPrinter, catalog]) => {
        if (!alive || userEditedRef.current) return;
        const resolved = resolveVendorModelForProfile(apiPrinter.machine_profile, catalog);
        if (resolved) {
          setDraft(d => ({ ...d, scope: 'model', machine_vendor: resolved.vendor, machine_model: resolved.printer_model }));
        }
      })
      .catch(console.error);
    return () => { alive = false; };
  }, [printer.id]);

  function handleDraftChange(next: ItemDraft) {
    userEditedRef.current = true;
    setDraft(next);
  }

  async function handleSave() {
    setError(null);
    try {
      await createMaintenanceItem({
        name: draft.name, scope: draft.scope,
        machine_vendor: draft.scope === 'model' ? draft.machine_vendor : null,
        machine_model: draft.scope === 'model' ? draft.machine_model : null,
        triggers: draft.triggers,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0,
      background: 'var(--scrim)', backdropFilter: 'blur(4px)',
      zIndex: 100, display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{
        width: 'min(480px, 100%)', maxHeight: '90vh', overflowY: 'auto',
        padding: 0, borderColor: 'var(--border-3)',
        boxShadow: '0 20px 60px -20px rgba(0,0,0,0.7), 0 0 0 1px var(--accent-glow)',
      }}>
        <div className="row between" style={{
          padding: '16px 20px', borderBottom: '1px solid var(--border-1)',
          alignItems: 'center', background: 'var(--bg-2)',
        }}>
          <div className="col">
            <div className="tag-key">Add maintenance item</div>
            <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}>{printer.nickname}</div>
          </div>
          <button className="btn ghost icon sm" onClick={onClose}>{Icons.x}</button>
        </div>
        <div style={{ padding: 20 }}>
          {error && (
            <div style={{ marginBottom: 12, padding: '10px 14px', background: 'var(--bg-0)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
              {error}
            </div>
          )}
          <MaintenanceItemForm draft={draft} catalog={fleetModels} onChange={handleDraftChange} onSave={handleSave} onCancel={onClose} />
        </div>
      </div>
    </div>
  );
}
