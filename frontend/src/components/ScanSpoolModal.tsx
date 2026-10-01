import { useEffect, useMemo, useState } from 'react';
import { Icons } from './icons';
import { qrScanSupported, useQrScanner } from './useQrScanner';
import {
  fetchFilaments, fetchSpools, parseSpoolCode, slotPatchForSpool, spoolDisplayName,
  type ApiFilament, type ApiSpool,
} from '../api/spoolman';
import { fetchPrinters, updatePrinter, type ApiPrinter, type LoadedFilament } from '../api/printers';

const NEW_SLOT = 'new';

/**
 * Load a spool into a printer slot from its label: scan the Spoolman QR (or type/paste the code), pick the
 * printer and slot, done. Sets the slot's filament from the spool and links the Spoolman spool id.
 */
export function ScanSpoolModal({ onClose, onAssigned }: { onClose: () => void; onAssigned?: () => void }) {
  const [spools, setSpools] = useState<ApiSpool[] | null>(null);
  const [filaments, setFilaments] = useState<ApiFilament[]>([]);
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  const [code, setCode] = useState('');
  const [camera, setCamera] = useState(false);
  const [printerId, setPrinterId] = useState<string>('');
  const [slot, setSlot] = useState<string>(NEW_SLOT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  useEffect(() => {
    let alive = true;
    Promise.all([fetchSpools(), fetchFilaments(), fetchPrinters()])
      .then(([s, f, p]) => { if (!alive) return; setSpools(s); setFilaments(f); setPrinters(p); })
      .catch(e => { if (alive) { setSpools([]); setError(e instanceof Error ? e.message : String(e)); } });
    return () => { alive = false; };
  }, []);

  const { videoRef, error: cameraError } = useQrScanner(camera, text => { setCode(text); setCamera(false); });

  const spoolId = parseSpoolCode(code);
  const spool = useMemo(() => (spoolId != null ? spools?.find(s => s.id === spoolId) ?? null : null), [spools, spoolId]);
  const printer = printers.find(p => String(p.id) === printerId) ?? null;

  const slotLabel = (s: LoadedFilament) => `T${s.slot} — ${s.name || s.type || 'empty'}`;

  async function assign() {
    if (!spool || !printer) return;
    setBusy(true); setError('');
    try {
      const slots = [...(printer.loaded_filaments ?? [])];
      const idx = slot === NEW_SLOT ? -1 : slots.findIndex(s => String(s.slot) === slot);
      const current = idx >= 0 ? slots[idx] : undefined;
      const patch = slotPatchForSpool(spool, filaments, printer.current_orca_printer_profile, current);
      if (idx >= 0) {
        slots[idx] = { ...current!, ...patch };
      } else {
        const next = slots.reduce((m, s) => Math.max(m, s.slot + 1), 0);
        slots.push({ slot: next, filament_id: null, name: '', type: '', color: '', ...patch });
      }
      await updatePrinter(printer.id, { loaded_filaments: slots });
      setDone(`Loaded ${spoolDisplayName(spool)} into ${printer.name}.`);
      onAssigned?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 200,
                                     display: 'grid', placeItems: 'center', padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" role="dialog" aria-label="Scan spool"
           style={{ width: 'min(440px, 100%)', maxHeight: '90vh', overflowY: 'auto', padding: 20 }}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>Load a spool</div>
          <button className="btn ghost icon sm" aria-label="Close" onClick={onClose}>{Icons.x}</button>
        </div>

        {done ? (
          <div className="col gap-3">
            <div role="status" style={{ color: 'var(--ok)', fontSize: 13 }}>{done}</div>
            <button className="btn primary" onClick={onClose}>Done</button>
          </div>
        ) : (
          <div className="col gap-3">
            {qrScanSupported() && (
              <button className="btn" onClick={() => setCamera(c => !c)}>
                {Icons.camera} {camera ? 'Stop camera' : 'Scan QR with camera'}
              </button>
            )}
            {camera && <video ref={videoRef} muted playsInline aria-label="Camera preview"
                              style={{ width: '100%', borderRadius: 8, background: '#000', aspectRatio: '4 / 3', objectFit: 'cover' }} />}
            {cameraError && <div role="alert" style={{ color: 'var(--warn)', fontSize: 12 }}>{cameraError}</div>}

            <label className="col" style={{ gap: 4 }}>
              <span className="label">Spool code</span>
              <input className="input" placeholder="Scan, or type the spool # (e.g. 12)" value={code}
                     onChange={e => setCode(e.target.value)} aria-label="Spool code" />
            </label>

            {spools === null ? <div className="muted small">Loading spools…</div>
              : code.trim() && spoolId == null ? <div style={{ color: 'var(--warn)', fontSize: 12 }}>That doesn’t look like a Spoolman spool code.</div>
              : spoolId != null && !spool ? <div style={{ color: 'var(--warn)', fontSize: 12 }}>Spool #{spoolId} isn’t in Spoolman.</div>
              : spool && (
                <div data-testid="scanned-spool" className="row gap-3"
                     style={{ padding: 10, border: '1px solid var(--border-1)', background: 'var(--bg-1)' }}>
                  <span style={{ width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                                 background: spool.filament.color_hex ? `#${spool.filament.color_hex}` : '#94a3b8' }} />
                  <div className="col" style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>#{spool.id} {spoolDisplayName(spool)} · {spool.filament.material}</div>
                    <div className="small muted">
                      {Math.round(spool.remaining_weight)}g left{spool.location?.trim() ? ` · Stored at ${spool.location.trim()}` : ''}
                    </div>
                  </div>
                </div>
              )}

            {spool && (
              <>
                <label className="col" style={{ gap: 4 }}>
                  <span className="label">Printer</span>
                  <select className="select" value={printerId} aria-label="Printer"
                          onChange={e => { setPrinterId(e.target.value); setSlot(NEW_SLOT); }}>
                    <option value="">Choose a printer…</option>
                    {printers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </label>
                {printer && (
                  <label className="col" style={{ gap: 4 }}>
                    <span className="label">Slot</span>
                    <select className="select" value={slot} aria-label="Slot" onChange={e => setSlot(e.target.value)}>
                      {(printer.loaded_filaments ?? []).map(s => <option key={s.slot} value={String(s.slot)}>{slotLabel(s)}</option>)}
                      <option value={NEW_SLOT}>New slot</option>
                    </select>
                  </label>
                )}
                <button className="btn primary" disabled={!printer || busy} onClick={assign}>
                  {busy ? 'Loading…' : 'Load into printer'}
                </button>
              </>
            )}
            {error && <div role="alert" style={{ color: 'var(--err)', fontSize: 12 }}>{error}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
