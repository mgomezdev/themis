import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchPrinters, type ApiPrinter } from '../api/printers';
import { ANY_KEY, isModelKey, modelGroups, modelKey, modelLabelOf, modelUuidOf, printersOfKey } from './ModelTargets';

/**
 * Which printers may take work (BIZ-357), shared by New Job, Edit Job and the project generate step. Only the chosen entries
 * show, as badges: yellow = a group of printers (Any printer, or any printer of a model, including ones added later), blue =
 * one specific printer. "+ Printer" opens a searchable menu; entries already added, or covered by a chosen group, are greyed.
 * Selection keys are the ones `ModelTargets` defines: `<printer id>`, `model:<uuid>`, `any`.
 */
export function PrinterTargetPicker({ printers, selected, onToggle }: {
  printers: ApiPrinter[];
  selected: string[];
  onToggle: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [open]);

  const groups = useMemo(() => [...modelGroups(printers).entries()], [printers]);
  const anyChosen = selected.includes(ANY_KEY);
  const covered = (p: ApiPrinter) => anyChosen || (!!p.model_uuid && selected.includes(modelKey(p.model_uuid)));
  const q = query.trim().toLowerCase();
  const hit = (text: string) => !q || text.toLowerCase().includes(q);

  const label = (key: string) => {
    if (isModelKey(key)) return key === ANY_KEY ? 'Any printer' : `Any ${modelLabelOf(key, printers)}`;
    return printers.find(p => String(p.id) === key)?.name ?? `Printer ${key}`;
  };
  const pick = (key: string) => { onToggle(key); setQuery(''); setOpen(false); };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <div className="row gap-2" style={{ flexWrap: 'wrap', alignItems: 'center' }} data-testid="printer-targets">
        {selected.map(key => (
          <span key={key} className={`pill ${isModelKey(key) ? 'warn' : 'info'}`} data-testid={`target-badge-${key}`}>
            {label(key)}
            <button type="button" className="btn ghost icon sm" aria-label={`Remove ${label(key)}`}
                    onClick={() => onToggle(key)} style={{ marginLeft: 4, padding: 0, minWidth: 0, height: 'auto' }}>×</button>
          </span>
        ))}
        <button type="button" className="pill idle" onClick={() => setOpen(o => !o)} aria-expanded={open} aria-haspopup="menu"
                style={{ cursor: 'pointer' }}>+ Printer</button>
      </div>

      {selected.length === 0 && (
        <div className="tiny" style={{ color: 'var(--warn)', marginTop: 6 }}>
          No printers chosen: nothing will dispatch until you add one.
        </div>
      )}

      {open && (
        <div className="card" role="menu" style={{ position: 'absolute', zIndex: 20, marginTop: 6, padding: 10, minWidth: 260,
                                                    maxHeight: 320, overflowY: 'auto', background: 'var(--bg-2)' }}>
          <input className="input" autoFocus placeholder="Search printers" value={query}
                 onChange={e => setQuery(e.target.value)} style={{ marginBottom: 8 }} />
          <div className="tag-key" style={{ margin: '4px 0' }}>Group of printers</div>
          {hit('Any printer') && (
            <MenuItem label="Any printer" hint="every printer, including ones added later"
                      disabled={anyChosen} onPick={() => pick(ANY_KEY)} />
          )}
          {groups.filter(([, members]) => hit(`Any ${members[0].model_label ?? ''}`)).map(([uuid, members]) => {
            const key = modelKey(uuid);
            return (
              <MenuItem key={key} label={`Any ${members[0].model_label ?? 'printer of this model'}`}
                        hint={`${members.length} printer${members.length === 1 ? '' : 's'} now`}
                        disabled={anyChosen || selected.includes(key)} onPick={() => pick(key)} />
            );
          })}
          <div className="tag-key" style={{ margin: '8px 0 4px' }}>Specific printer</div>
          {printers.length === 0 && <div className="tiny muted">No printers configured yet.</div>}
          {printers.filter(p => hit(p.name)).map(p => (
            <MenuItem key={p.id} label={p.name} hint={p.model_label ?? p.printer_type}
                      disabled={selected.includes(String(p.id)) || covered(p)} onPick={() => pick(String(p.id))} />
          ))}
        </div>
      )}
    </div>
  );
}

function MenuItem({ label, hint, disabled, onPick }: { label: string; hint?: string; disabled: boolean; onPick: () => void }) {
  return (
    <button type="button" role="menuitem" disabled={disabled} onClick={onPick}
            className="btn ghost sm"
            style={{ display: 'flex', width: '100%', justifyContent: 'space-between', gap: 8, opacity: disabled ? 0.45 : 1,
                     cursor: disabled ? 'default' : 'pointer' }}>
      <span>{label}</span>
      {hint && <span className="tiny muted">{hint}</span>}
    </button>
  );
}

/** Enabled printers, for screens that don't already load the fleet (the project generate step). */
export function useEnabledPrinters(): ApiPrinter[] {
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  useEffect(() => {
    let alive = true;
    fetchPrinters().then(ps => { if (alive) setPrinters(ps.filter(p => p.enabled)); }).catch(console.error);
    return () => { alive = false; };
  }, []);
  return printers;
}

/** Add or remove `key` from a selection. */
export const toggleKey = (keys: string[], key: string): string[] => (keys.includes(key) ? keys.filter(k => k !== key) : [...keys, key]);

/** A selection as the project generate request's fields. */
export function splitTargets(keys: string[]): { printerIds: number[]; modelUuids: string[]; anyPrinter: boolean } {
  return {
    printerIds: keys.filter(k => !isModelKey(k)).map(Number),
    modelUuids: keys.filter(k => isModelKey(k) && k !== ANY_KEY).map(k => modelUuidOf(k) as string),
    anyPrinter: keys.includes(ANY_KEY),
  };
}

/** Ids of the printers a selection covers right now (e.g. to offer only presets they all support). */
export const coveredPrinterIds = (keys: string[], printers: ApiPrinter[]): number[] =>
  [...new Set(keys.flatMap(k => printersOfKey(k, printers).map(p => p.id)))];

/** The bed a project is packed for: the smallest among the printers the selection covers (projects pack for every one of them). */
export function PackBedNote({ keys, printers }: { keys: string[]; printers: ApiPrinter[] }) {
  const ids = new Set(coveredPrinterIds(keys, printers));
  const covered = printers.filter(p => ids.has(p.id));
  if (covered.length === 0) return null;
  const x = Math.min(...covered.map(p => p.bed_x_mm)), y = Math.min(...covered.map(p => p.bed_y_mm));
  return <div className="tiny" style={{ color: 'var(--text-3)', marginTop: 6 }}>Pack bed: {x}×{y} mm (smallest selected)</div>;
}
