import { useState } from 'react';
import type { Printer } from '../data/types';
import {
  fetchPrinter, fetchPrinterTypes, testSavedPrinter, updatePrinter, type ApiPrinter, type ConnectionField,
} from '../api/printers';
import { quickAddFields, stripBlank } from './quickAdd';

/** Why a printer is out of the queue, system cause first (BIZ-369): "Setup incomplete" is the blocking problem (no connection has
 *  confirmed its setup yet); "Queue off" is the operator's own choice. Both can hold at once. */
export function QueueStateBadges({ printer: p }: { printer: Printer }) {
  return <>
    {p.configured === false && (
      <span className="tiny" data-testid="setup-badge" title={p.configError ?? 'Waiting for a connection test'} style={{
        padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap', fontWeight: 600,
        color: 'var(--warn)', border: '1px solid var(--warn)',
      }}>SETUP INCOMPLETE</span>
    )}
    {!p.queueOn && (
      <span className="tiny" data-testid="queue-off-badge" style={{
        padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap', fontWeight: 600,
        background: 'var(--idle-bg)', color: 'var(--text-3)',
      }}>QUEUE OFF</span>
    )}
  </>;
}

/** The card section of an unconfigured printer: the reason, its quick-add fields to finish setup, and Test / Save & connect. */
export function SetupIncompleteCard({ printer: p, refetchFleet }: { printer: Printer; refetchFleet: () => void }) {
  const [form, setForm] = useState<{ printer: ApiPrinter; fields: ConnectionField[] } | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (p.configured !== false) return null;
  const id = Number(p.id);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try { await fn(); refetchFleet(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function open() {
    await run(async () => {
      const [printer, types] = await Promise.all([fetchPrinter(id), fetchPrinterTypes()]);
      const t = types.find(x => x.plugin_id === printer.plugin_id && x.manufacturer_id === printer.manufacturer_id
                                && x.model_id === printer.model_id);
      const fields = [...(t?.connection_fields.filter(f => f.name === 'ip_address') ?? []), ...(t ? quickAddFields(t) : [])];
      setValues(Object.fromEntries(fields.map(f => [f.name, f.field_type === 'password' ? '' : String(printer.connection_config[f.name] ?? '')])));
      setForm({ printer, fields });
    });
  }

  return (
    <div data-testid="setup-card" onClick={e => e.stopPropagation()}
         style={{ padding: '8px 14px 12px', borderTop: '1px solid var(--border-1)' }}>
      <div className="tiny text-warn">
        Setup incomplete — {p.configError ?? 'waiting for a connection test'}. It takes no queue work until a connection succeeds.
      </div>
      {form && (
        <div className="row gap-2" style={{ flexWrap: 'wrap', marginTop: 6 }}>
          {form.fields.map(f => (
            <label key={f.name} className="col tiny" style={{ gap: 2 }}>
              {f.label}
              <input className="input" style={{ width: 150 }} aria-label={`${f.label} for ${p.nickname}`} autoComplete="off"
                     type={f.field_type === 'password' ? 'password' : 'text'}
                     placeholder={f.field_type === 'password' && form.printer.connection_config[f.name] ? 'unchanged' : f.placeholder}
                     value={values[f.name] ?? ''} onChange={e => setValues(v => ({ ...v, [f.name]: e.target.value }))} />
            </label>
          ))}
        </div>
      )}
      <div className="row gap-2" style={{ marginTop: 6 }}>
        {form ? (
          <button className="btn primary sm" disabled={busy} onClick={() => void run(async () => {
            const stored = form.printer.connection_config;
            const merged = { ...stored, ...stripBlank(values) };
            if (JSON.stringify(stripBlank(stored)) === JSON.stringify(stripBlank(merged))) {
              const out = await testSavedPrinter(id);          // nothing changed (e.g. it was asleep): just try again
              if (!out.ok && out.error) setError(out.error);
            } else {
              await updatePrinter(id, { connection_config: merged });   // a real change re-tests on the server
            }
            setForm(null);
          })}>Save &amp; connect</button>
        ) : <button className="btn sm" disabled={busy} onClick={() => void open()}>Finish setup</button>}
        <button className="btn ghost sm" disabled={busy} onClick={() => void run(async () => {
          const out = await testSavedPrinter(id);
          if (!out.ok && out.error) setError(out.error);
        })}>Test</button>
      </div>
      {error && <div role="alert" className="tiny" style={{ color: 'var(--err)', marginTop: 4 }}>{error}</div>}
    </div>
  );
}
