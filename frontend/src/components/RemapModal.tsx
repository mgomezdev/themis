import { useState } from 'react';
import type { PendingRemaps, Resolutions, ConfirmResult } from '../api/laminus';
import { confirmRemap } from '../api/laminus';

interface Props {
  payload: PendingRemaps;
  onDone: (result: ConfirmResult) => void;
  onCancel: () => void;
}

type SelectionMap = Record<string, string>;

export function RemapModal({ payload, onDone, onCancel }: Props) {
  const [printerSelections, setPrinterSelections] = useState<SelectionMap>({});
  const [jobSelections, setJobSelections] = useState<SelectionMap>({});
  // Stores filament display name; resolved to UUID on submit
  const [spoolmanSelections, setSpoolmanSelections] = useState<SelectionMap>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { pending, options, spoolman_error, sync_id } = payload;
  const filamentUuids = options.filament_uuids ?? [];

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

    const nameToUuid = Object.fromEntries(filamentUuids.map(o => [o.name, o.uuid]));

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
        const name = spoolmanSelections[entry.stale_uuid] ?? '';
        return { stale_uuid: entry.stale_uuid, new_uuid: name ? (nameToUuid[name] ?? null) : null };
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

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      {/* flex column: scrollable body + fixed footer */}
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
                const listId = `s-${safeId(entry.stale_uuid)}`;
                const spoolQuery = spoolmanSelections[entry.stale_uuid] ?? '';
                const spoolOpts = filterOpts(filamentUuids.map(o => o.name), spoolQuery);
                return (
                  <div key={entry.stale_uuid} style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                      <s>{entry.stale_name}</s>{' → '}
                      <span style={{ fontSize: 12 }}>{countBadge(entry.affected_filament_names, 'filaments')}</span>
                    </div>
                    <input
                      type="text"
                      list={listId}
                      value={spoolQuery}
                      onChange={e => setSpoolmanSelections(s => ({ ...s, [entry.stale_uuid]: e.target.value }))}
                      placeholder="Search or leave blank to clear…"
                      style={{ width: '100%', marginTop: 4, padding: '4px 8px', boxSizing: 'border-box' }}
                    />
                    <datalist id={listId}>
                      {spoolOpts.map(o => <option key={o} value={o} />)}
                    </datalist>
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
