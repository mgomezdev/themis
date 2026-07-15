import { useMemo, useState } from 'react';
import type { PendingRemaps, Resolutions, ConfirmResult } from '../api/laminus';
import { confirmRemap } from '../api/laminus';

interface Props {
  payload: PendingRemaps;
  onDone: (result: ConfirmResult) => void;
  onCancel: () => void;
}

type SelectionMap = Record<string, string>;

// Sorted longest-first so longer keywords (PETG, Nylon) win over shorter ones (PA, PC).
const MATERIALS = [
  'Nylon', 'PETG', 'PEKK', 'PEEK', 'PA12', 'HIPS', 'BVOH', 'FLEX',
  'ASA', 'ABS', 'TPU', 'TPE', 'PVA', 'PPS', 'PEI', 'PHA', 'PA6',
  'PLA', 'PA', 'PC', 'PP',
];

function extractMaterialAndBrand(name: string): { material: string; brand: string } {
  const base = name.replace(/@.*$/, '').trim();
  for (const m of MATERIALS) {
    const regex = new RegExp(`(^|\\s|-)${m}(\\s|-|$)`, 'i');
    const match = regex.exec(base);
    if (match) {
      const matStart = match.index + match[1].length;
      const brand = base.slice(0, matStart).trim();
      return { material: m.toUpperCase(), brand: brand || 'Generic' };
    }
  }
  return { material: 'Other', brand: 'Generic' };
}

export function RemapModal({ payload, onDone, onCancel }: Props) {
  const [printerSelections, setPrinterSelections] = useState<SelectionMap>({});
  const [jobSelections, setJobSelections] = useState<SelectionMap>({});
  const [spoolMaterial, setSpoolMaterial] = useState<SelectionMap>({});
  const [spoolBrand, setSpoolBrand] = useState<SelectionMap>({});
  const [spoolSearch, setSpoolSearch] = useState<SelectionMap>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { pending, options, spoolman_error, sync_id } = payload;

  // Parse all filament names into { material → { brand → [names] } } once.
  const parsedFilaments = useMemo(() => {
    const map: Record<string, Record<string, string[]>> = {};
    for (const name of options.filament) {
      const { material, brand } = extractMaterialAndBrand(name);
      if (!map[material]) map[material] = {};
      if (!map[material][brand]) map[material][brand] = [];
      map[material][brand].push(name);
    }
    return map;
  }, [options.filament]);

  const materialList = useMemo(() => Object.keys(parsedFilaments).sort(), [parsedFilaments]);

  const requiredPrintersMet = pending.printers.every(entry => {
    const key = `${entry.field}|${entry.stale_value}`;
    const val = printerSelections[key] ?? '';
    const validSet = entry.options_kind === 'machine' ? options.machine : options.filament;
    return !entry.required || validSet.includes(val);
  });
  const canConfirm = requiredPrintersMet && !submitting;

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);

    const resolutions: Resolutions = {
      printers: pending.printers.map(entry => {
        const val = printerSelections[`${entry.field}|${entry.stale_value}`] ?? '';
        return { field: entry.field, stale_value: entry.stale_value, new_value: val || null };
      }),
      jobs: pending.jobs.map(entry => {
        const val = jobSelections[`${entry.field}|${entry.stale_value}`] ?? '';
        return { field: entry.field, stale_value: entry.stale_value, new_value: val || null };
      }),
      spoolman_filaments: pending.spoolman_filaments.map(entry => {
        const key = `${entry.printer_preset}|${entry.stale_name}`;
        const name = spoolSearch[key] ?? '';
        return {
          printer_preset: entry.printer_preset,
          stale_name: entry.stale_name,
          new_name: name || null,
          affected_filament_ids: entry.affected_filament_ids,
        };
      }),
    };

    try {
      const result = await confirmRemap(sync_id, resolutions);
      onDone(result);
    } catch (err: any) {
      if (err?.status === 409) {
        setError('Sync superseded — run the catalog sync again');
      } else {
        setError(err?.message ?? 'Unknown error');
      }
      setSubmitting(false);
    }
  };

  const countBadge = (names: string[], noun: string) =>
    names.length === 1 ? names[0] : `affects ${names.length} ${noun}`;

  const safeId = (s: string) => s.replace(/[^a-z0-9]/gi, '_');

  const filterOpts = (opts: string[], query: string): string[] => {
    if (!query) return opts;
    const q = query.toLowerCase();
    return opts.filter(o => o.toLowerCase().includes(q));
  };

  const filterSpoolOpts = (opts: string[], query: string): string[] => {
    const cap = 50;
    if (!query) return opts.slice(0, cap);
    const q = query.toLowerCase();
    const result: string[] = [];
    for (const o of opts) {
      if (o.toLowerCase().includes(q)) {
        result.push(o);
        if (result.length >= cap) break;
      }
    }
    return result;
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ background: 'var(--surface, #1e1e2e)', borderRadius: 8, minWidth: 480, maxWidth: 640, maxHeight: '80vh', display: 'flex', flexDirection: 'column' }}>

        <div style={{ padding: '24px 24px 12px', overflowY: 'auto', flex: 1 }}>
          <h2 style={{ marginTop: 0 }}>Profile References Need Remapping</h2>
          <p style={{ color: 'var(--text-muted, #aaa)', fontSize: 14 }}>
            The incoming catalog removed profiles still referenced below. Printers need a replacement; jobs and Spoolman filaments can be cleared.
          </p>

          {spoolman_error && (
            <div style={{ background: '#7c2d12', padding: '8px 12px', borderRadius: 4, marginBottom: 12, fontSize: 13 }}>
              Spoolman references could not be fully checked: {spoolman_error}
            </div>
          )}

          {pending.printers.length > 0 && (
            <section>
              <h3>Printers</h3>
              {pending.printers.map(entry => {
                const key = `${entry.field}|${entry.stale_value}`;
                const listId = `p-${safeId(key)}`;
                const optList = entry.options_kind === 'machine' ? options.machine : options.filament;
                const printerQuery = printerSelections[key] ?? '';
                const isValid = optList.includes(printerQuery);
                return (
                  <div key={key} style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                      <s>{entry.stale_value}</s>{' → '}
                      <span style={{ fontSize: 12 }}>{countBadge(entry.affected_printer_names, 'printers')}</span>
                    </div>
                    <input
                      type="text"
                      list={listId}
                      value={printerQuery}
                      onChange={e => setPrinterSelections(s => ({ ...s, [key]: e.target.value }))}
                      placeholder="Search or select a replacement…"
                      style={{ width: '100%', marginTop: 4, padding: '4px 8px', boxSizing: 'border-box' }}
                    />
                    <datalist id={listId}>
                      {filterOpts(optList, printerQuery).map(o => <option key={o} value={o} />)}
                    </datalist>
                    {entry.required && !isValid && (
                      <div style={{ color: 'var(--err, #f87171)', fontSize: 12, marginTop: 2 }}>Required — choose from the list</div>
                    )}
                  </div>
                );
              })}
            </section>
          )}

          {pending.jobs.length > 0 && (
            <section>
              <h3>Queued Jobs</h3>
              {pending.jobs.map(entry => {
                const key = `${entry.field}|${entry.stale_value}`;
                const listId = `j-${safeId(key)}`;
                const optList = entry.options_kind === 'process' ? options.process : options.filament;
                const jobQuery = jobSelections[key] ?? '';
                return (
                  <div key={key} style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                      <s>{entry.stale_value}</s>{' → '}
                      <span style={{ fontSize: 12 }}>{countBadge(entry.affected_file_names, 'jobs')}</span>
                    </div>
                    <input
                      type="text"
                      list={listId}
                      value={jobQuery}
                      onChange={e => setJobSelections(s => ({ ...s, [key]: e.target.value }))}
                      placeholder="Search or leave blank to clear…"
                      style={{ width: '100%', marginTop: 4, padding: '4px 8px', boxSizing: 'border-box' }}
                    />
                    <datalist id={listId}>
                      {filterOpts(optList, jobQuery).map(o => <option key={o} value={o} />)}
                    </datalist>
                  </div>
                );
              })}
            </section>
          )}

          {pending.spoolman_filaments.length > 0 && (
            <section>
              <h3>Spoolman Filaments</h3>
              {pending.spoolman_filaments.map(entry => {
                const key = `${entry.printer_preset}|${entry.stale_name}`;
                const listId = `s-${safeId(key)}`;
                const mat = spoolMaterial[key] ?? '';
                const brand = spoolBrand[key] ?? '';
                const search = spoolSearch[key] ?? '';
                const brandList = mat ? Object.keys(parsedFilaments[mat] || {}).sort() : [];
                const candidates: string[] = !mat
                  ? []
                  : brand
                  ? parsedFilaments[mat]?.[brand] ?? []
                  : Object.values(parsedFilaments[mat] || {}).flat();
                const filtered = filterSpoolOpts(candidates, search);
                return (
                  <div key={key} style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)', marginBottom: 6 }}>
                      <s>{entry.stale_name}</s>
                      {' on '}<em>{entry.printer_preset}</em>{' → '}
                      <span style={{ fontSize: 12 }}>{countBadge(entry.affected_filament_names, 'filaments')}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                      <select
                        value={mat}
                        onChange={e => {
                          const v = e.target.value;
                          setSpoolMaterial(s => ({ ...s, [key]: v }));
                          setSpoolBrand(s => ({ ...s, [key]: '' }));
                          setSpoolSearch(s => ({ ...s, [key]: '' }));
                        }}
                        style={{ flex: '0 0 auto', padding: '4px 6px' }}
                      >
                        <option value="">— material —</option>
                        {materialList.map(m => <option key={m} value={m}>{m}</option>)}
                      </select>
                      {mat && (
                        <select
                          value={brand}
                          onChange={e => {
                            setSpoolBrand(s => ({ ...s, [key]: e.target.value }));
                            setSpoolSearch(s => ({ ...s, [key]: '' }));
                          }}
                          style={{ flex: '0 0 auto', padding: '4px 6px' }}
                        >
                          <option value="">Any Brand</option>
                          {brandList.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                      )}
                      {mat && (
                        <input
                          type="text"
                          list={listId}
                          value={search}
                          onChange={e => setSpoolSearch(s => ({ ...s, [key]: e.target.value }))}
                          placeholder={brand ? `Search ${mat} by ${brand}…` : `Search ${mat} (any brand)…`}
                          style={{ flex: 1, minWidth: 180, padding: '4px 8px' }}
                        />
                      )}
                    </div>
                    {mat && (
                      <datalist id={listId}>
                        {filtered.map(o => <option key={o} value={o} />)}
                      </datalist>
                    )}
                    <div style={{ fontSize: 11, color: 'var(--text-muted, #888)' }}>
                      Leave blank to remove this profile reference from the filament
                    </div>
                  </div>
                );
              })}
            </section>
          )}

          {error && (
            <div style={{ color: 'var(--err, #f87171)', fontSize: 13, marginBottom: 8 }}>{error}</div>
          )}
        </div>

        {/* Footer outside scroll area — always visible */}
        <div style={{ padding: '12px 24px 20px', borderTop: '1px solid var(--border, #333)' }}>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button onClick={onCancel} disabled={submitting} style={{ padding: '6px 16px' }}>
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={!canConfirm}
              style={{ padding: '6px 16px', background: canConfirm ? 'var(--accent, #7c3aed)' : '#555', color: '#fff', border: 'none', borderRadius: 4, cursor: canConfirm ? 'pointer' : 'default' }}
            >
              {submitting ? 'Applying…' : 'Confirm'}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
