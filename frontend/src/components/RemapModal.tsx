import React, { useState } from 'react';
import type { PendingRemaps, Resolutions, ConfirmResult } from '../api/laminus';
import { confirmRemap } from '../api/laminus';

interface Props {
  payload: PendingRemaps;
  onDone: (result: ConfirmResult) => void;
  onCancel: () => void;
}

type SelectionMap = Record<string, string | null>;

export function RemapModal({ payload, onDone, onCancel }: Props) {
  const [printerSelections, setPrinterSelections] = useState<SelectionMap>({});
  const [jobSelections, setJobSelections] = useState<SelectionMap>({});
  const [spoolmanSelections, setSpoolmanSelections] = useState<SelectionMap>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { pending, options, spoolman_error, sync_id } = payload;

  const requiredPrintersMet = pending.printers.every(entry => {
    const sel = printerSelections[`${entry.field}|${entry.stale_value}`];
    return !entry.required || (sel !== undefined && sel !== null && sel !== '');
  });
  const canConfirm = requiredPrintersMet && !submitting;

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);
    const resolutions: Resolutions = {
      printers: pending.printers.map(entry => ({
        field: entry.field,
        stale_value: entry.stale_value,
        new_value: printerSelections[`${entry.field}|${entry.stale_value}`] ?? null,
      })),
      jobs: pending.jobs.map(entry => ({
        field: entry.field,
        stale_value: entry.stale_value,
        new_value: jobSelections[`${entry.field}|${entry.stale_value}`] ?? null,
      })),
      spoolman_filaments: pending.spoolman_filaments.map(entry => ({
        stale_uuid: entry.stale_uuid,
        new_uuid: spoolmanSelections[entry.stale_uuid] ?? null,
      })),
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

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ background: 'var(--surface, #1e1e2e)', borderRadius: 8, padding: 24, minWidth: 480, maxWidth: 640, maxHeight: '80vh', overflowY: 'auto' }}>
        <h2 style={{ marginTop: 0 }}>Profile References Need Remapping</h2>
        <p style={{ color: 'var(--text-muted, #aaa)', fontSize: 14 }}>
          The incoming catalog removed profiles that are still referenced. Printers require a replacement; jobs and Spoolman filaments can be cleared.
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
              const optList = entry.options_kind === 'machine' ? options.machine : options.filament;
              return (
                <div key={key} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                    <s>{entry.stale_value}</s>
                    {' → '}
                    <span style={{ fontSize: 12 }}>{countBadge(entry.affected_printer_names, 'printers')}</span>
                  </div>
                  <select
                    value={printerSelections[key] ?? ''}
                    onChange={e => setPrinterSelections(s => ({ ...s, [key]: e.target.value || null }))}
                    style={{ width: '100%', marginTop: 4, padding: '4px 8px' }}
                  >
                    <option value="">— select a replacement —</option>
                    {optList.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                  {entry.required && !printerSelections[key] && (
                    <div style={{ color: 'var(--err, #f87171)', fontSize: 12, marginTop: 2 }}>Required</div>
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
              const optList = entry.options_kind === 'process' ? options.process : options.filament;
              return (
                <div key={key} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                    <s>{entry.stale_value}</s>
                    {' → '}
                    <span style={{ fontSize: 12 }}>{countBadge(entry.affected_file_names, 'jobs')}</span>
                  </div>
                  <select
                    value={jobSelections[key] ?? ''}
                    onChange={e => setJobSelections(s => ({ ...s, [key]: e.target.value || null }))}
                    style={{ width: '100%', marginTop: 4, padding: '4px 8px' }}
                  >
                    <option value="">— clear —</option>
                    {optList.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              );
            })}
          </section>
        )}

        {pending.spoolman_filaments.length > 0 && (
          <section>
            <h3>Spoolman Filaments</h3>
            {pending.spoolman_filaments.map(entry => (
              <div key={entry.stale_uuid} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                  <s>{entry.stale_name}</s>
                  {' → '}
                  <span style={{ fontSize: 12 }}>{countBadge(entry.affected_filament_names, 'filaments')}</span>
                </div>
                <select
                  value={spoolmanSelections[entry.stale_uuid] ?? ''}
                  onChange={e => setSpoolmanSelections(s => ({ ...s, [entry.stale_uuid]: e.target.value || null }))}
                  style={{ width: '100%', marginTop: 4, padding: '4px 8px' }}
                >
                  <option value="">— clear —</option>
                  {options.filament_uuids.map(o => (
                    <option key={o.uuid} value={o.uuid}>{o.name}</option>
                  ))}
                </select>
              </div>
            ))}
          </section>
        )}

        {error && (
          <div style={{ color: 'var(--err, #f87171)', fontSize: 13, marginBottom: 8 }}>{error}</div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
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
  );
}
