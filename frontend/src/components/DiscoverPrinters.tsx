import { useState } from 'react';
import {
  createPrinter, discoverPrinters, fetchPrinter, fetchPrinterTypes, testConnection, testSavedPrinter, updatePrinter,
  type ApiPrinter, type DiscoveredPrinter, type DiscoveryResult, type PrinterType,
} from '../api/printers';
import { Icons } from './icons';
import {
  POLL, defaultNickname, missingQuickAdd, quickAddFields, runPool, statusLabel, statusTone, stripBlank, type RowStatus,
} from './quickAdd';

const STORAGE_KEY = 'themis.discovery.ranges';
const BATCH_LIMIT = 4;                       // concurrent creates; the server tests them in parallel too

function savedRanges(): string {
  try { return window.localStorage.getItem(STORAGE_KEY) ?? ''; } catch { return ''; }
}
function saveRanges(v: string) {
  try { window.localStorage.setItem(STORAGE_KEY, v); } catch { /* storage unavailable: just don't remember */ }
}

/** The API client throws `"<status> <body>"`; show the server's `detail` when the body is its JSON error. */
function readable(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  const m = msg.match(/^\d{3}\s+(\{.*\})$/s);
  if (m) {
    try { const d = JSON.parse(m[1])?.detail; if (typeof d === 'string') return d; } catch { /* fall through */ }
  }
  return msg;
}
const statusOf = (e: unknown) => Number((e instanceof Error ? e.message : '').slice(0, 3));

const keyOf = (p: DiscoveredPrinter) => `${p.plugin_id}:${p.printer_type}:${p.ip}`;
const modelKey = (t: PrinterType) => `${t.manufacturer_id}/${t.model_id}`;
const modelLabel = (t: PrinterType) => `${t.manufacturer_name} ${t.display_name}`;

interface Row {
  checked: boolean;
  nickname: string;
  nicknameEdited?: boolean;          // typed by the user: a model choice no longer renames it
  model: string;                       // modelKey of the chosen type; '' = none chosen
  modelText: string;                   // what the type-ahead box shows
  values: Record<string, string>;      // quick-add inputs
  fromScan: string[];                  // quick-add values the scan supplied
  status: RowStatus | null;            // null = derived (ready / missing)
  detail?: string;
  error?: string;                      // a create/save that failed for another reason
  notice?: string;                     // what an IP-conflict save did to the other printer
  printerId?: number;                  // the saved printer (added here, or already in the fleet / using the IP)
  editing?: boolean;                   // a saved printer's fields reopened
}

function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }

/** Network scan with in-place quick add (BIZ-369): every found printer is a row that can be added on the spot — singly or in a
 *  batch — with only the fields its plugin marks quick-add. Results stay until the next scan. The range box matters when Themis
 *  and the printers are on different networks/VLANs or Themis runs in Docker. */
export function DiscoverPrinters({ pluginId, types: givenTypes, onMoreSettings, onAdded }: {
  pluginId?: string;
  types?: PrinterType[];
  onMoreSettings?: (p: DiscoveredPrinter) => void;
  onAdded?: () => void;
}) {
  const [open, setOpen] = useState(!!pluginId);
  const [ranges, setRanges] = useState(() => pluginId ? '192.168.x.x' : savedRanges());
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [types, setTypes] = useState<PrinterType[]>(givenTypes ?? []);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [busy, setBusy] = useState(false);

  const patch = (key: string, p: Partial<Row>) => setRows(prev => ({ ...prev, [key]: { ...prev[key], ...p } }));
  const optionsFor = (p: DiscoveredPrinter) =>
    types.filter(t => t.plugin_id === p.plugin_id && t.plugin_enabled && t.model_enabled);
  const typeOf = (p: DiscoveredPrinter, row: Row | undefined) => optionsFor(p).find(t => modelKey(t) === row?.model) ?? null;

  function initialRow(p: DiscoveredPrinter, all: PrinterType[]): Row {
    const opts = all.filter(t => t.plugin_id === p.plugin_id && t.plugin_enabled && t.model_enabled);
    const matched = opts.find(t => p.model_uuid && t.model_uuid === p.model_uuid) ?? null;
    const fields = matched ? quickAddFields(matched) : (opts[0] ? quickAddFields(opts[0]) : []);
    const values: Record<string, string> = {};
    const fromScan: string[] = [];
    for (const f of fields) {
      const v = p.connection_config[f.name];
      if (v !== undefined && String(v).trim()) { values[f.name] = String(v); fromScan.push(f.name); } else values[f.name] = '';
    }
    return {
      checked: !p.already_added && !p.ip_in_use_by, nickname: defaultNickname(p, matched),
      model: matched ? modelKey(matched) : '', modelText: matched ? modelLabel(matched) : '', values, fromScan, status: null,
      printerId: p.added_printer?.printer_id,
    };
  }

  async function scan() {
    setScanning(true);
    setError(null);
    setResult(null);
    setRows({});
    if (!pluginId) saveRanges(ranges);
    try {
      const [res, all] = await Promise.all([
        discoverPrinters(ranges.split(/[\s,]+/).filter(Boolean), pluginId),
        givenTypes ? Promise.resolve(givenTypes) : fetchPrinterTypes(),
      ]);
      setTypes(all);
      setRows(Object.fromEntries(res.found.map(p => [keyOf(p), initialRow(p, all)])));
      setResult(res);
    } catch (e) {
      setError(readable(e));
    } finally {
      setScanning(false);
    }
  }

  function derived(p: DiscoveredPrinter, row: Row): { status: RowStatus; detail?: string } {
    if (row.status) return { status: row.status, detail: row.detail };
    const t = typeOf(p, row);
    if (!t) return { status: 'missing', detail: 'model' };
    const missing = missingQuickAdd(t, row.values);
    return missing.length ? { status: 'missing', detail: missing.join(', ') } : { status: 'ready' };
  }

  const configFor = (p: DiscoveredPrinter, row: Row) =>
    stripBlank({ ...p.connection_config, ...row.values, ip_address: p.ip });

  /** Follow the background connection test of a saved printer until it decides (or POLL runs out). */
  async function follow(key: string, printerId: number) {
    for (let i = 0; i < POLL.tries; i++) {
      let p: ApiPrinter;
      try { p = await fetchPrinter(printerId); } catch { break; }
      if (p.configured) { patch(key, { status: 'added_configured', detail: undefined }); return; }
      if (p.config_error) { patch(key, { status: 'added_unconfigured', detail: p.config_error }); return; }
      if (i < POLL.tries - 1) await sleep(POLL.intervalMs);
    }
    patch(key, { status: 'added_unconfigured', detail: 'still testing — check Fleet' });
  }

  async function add(p: DiscoveredPrinter) {
    const key = keyOf(p);
    const row = rows[key];
    const t = typeOf(p, row);
    if (!t) return;
    patch(key, { status: 'adding', error: undefined, checked: false });
    try {
      const created = await createPrinter({
        ...(row.nickname.trim() ? { name: row.nickname.trim() } : {}),
        plugin_id: t.plugin_id, manufacturer_id: t.manufacturer_id, model_id: t.model_id,
        connection_config: configFor(p, row),
      });
      patch(key, { printerId: created.id });
      onAdded?.();
      void follow(key, created.id);      // not awaited: a batch's pool slots go to creates, not to ~20 s of polling
    } catch (e) {
      if (statusOf(e) === 409) patch(key, { status: 'exists', detail: readable(e) });
      else patch(key, { status: null, error: readable(e), checked: true });
    }
  }

  async function addSelected(keys: string[]) {
    setBusy(true);
    const found = (result?.found ?? []).filter(p => keys.includes(keyOf(p)));
    await runPool(found, BATCH_LIMIT, add);
    setBusy(false);
  }

  async function test(p: DiscoveredPrinter) {
    const key = keyOf(p);
    const row = rows[key];
    patch(key, { status: 'testing', error: undefined });
    try {
      if (row.printerId !== undefined) {
        const out = await testSavedPrinter(row.printerId);
        patch(key, out.configured ? { status: 'added_configured', detail: undefined }
          : { status: 'added_unconfigured', detail: out.config_error ?? out.error ?? undefined });
        return;
      }
      const out = await testConnection({ printer_type: p.plugin_id ?? p.printer_type, connection_config: configFor(p, row) });
      patch(key, out.ok ? { status: 'test_ok', detail: undefined } : { status: 'test_failed', detail: out.error });
    } catch (e) {
      patch(key, { status: 'test_failed', detail: readable(e) });
    }
  }

  /** Save the row's fields onto a saved printer (merged over what is stored, blanks keep the stored value) and follow the re-test
   *  the change triggers; with nothing changed, test it as it is (e.g. it was asleep). `own` = the printer is this row's (added
   *  here or already in the fleet); an IP-conflict save moves *another* printer, so the row only notes it. */
  async function save(p: DiscoveredPrinter, printerId: number, changes: Record<string, unknown>, own = true) {
    const key = keyOf(p);
    patch(key, { status: own ? 'adding' : null, error: undefined, notice: undefined });
    try {
      const stored = await fetchPrinter(printerId);
      const merged = { ...stored.connection_config, ...stripBlank(changes) };
      const same = JSON.stringify(stripBlank(stored.connection_config)) === JSON.stringify(stripBlank(merged));
      if (same) await testSavedPrinter(printerId);
      else await updatePrinter(printerId, { connection_config: merged });
      patch(key, { editing: false });
      onAdded?.();
      if (own) await follow(key, printerId);
      else patch(key, { notice: `Saved — ${p.ip_in_use_by?.name ?? 'that printer'} is being re-tested at its new address.` });
    } catch (e) {
      patch(key, { status: null, error: readable(e) });
    }
  }

  if (!open) {
    return (
      <div style={{ marginBottom: 16 }}>
        <button className="btn sm" onClick={() => setOpen(true)}>{Icons.search} Scan network for printers</button>
      </div>
    );
  }

  const found = result?.found ?? [];
  const fresh = found.filter(p => !p.already_added);
  const fleet = found.filter(p => p.already_added);
  const pending = (p: DiscoveredPrinter) => rows[keyOf(p)]?.printerId === undefined && rows[keyOf(p)]?.status !== 'exists';
  const selected = fresh.filter(p => rows[keyOf(p)]?.checked && pending(p));
  const addableSel = selected.filter(p => typeOf(p, rows[keyOf(p)]));
  const incomplete = addableSel.filter(p => derived(p, rows[keyOf(p)]).status === 'missing');
  const noModel = selected.filter(p => !typeOf(p, rows[keyOf(p)]));
  const select = (pred: (p: DiscoveredPrinter) => boolean) =>
    setRows(prev => Object.fromEntries(Object.entries(prev).map(([k, r]) => {
      const p = found.find(f => keyOf(f) === k);
      return [k, { ...r, checked: !!p && !p.already_added && pending(p) && pred(p) }];
    })));
  const groups = [...new Set(fresh.map(p => p.plugin_id ?? p.printer_type))];

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16 }} data-testid="discover-printers">
      <div className="row gap-2 wrap">
        <input className="input" aria-label="Network ranges" style={{ maxWidth: 320 }}
               placeholder="192.168.x.x or 192.168.7.0/24"
               disabled={scanning}
               value={ranges} onChange={e => setRanges(e.target.value)} />
        <button className="btn primary sm" onClick={() => void scan()} disabled={scanning || busy}>
          {scanning ? 'Scanning…' : 'Scan'}
        </button>
        {!pluginId && <button className="btn ghost sm" onClick={() => setOpen(false)}>Close</button>}
      </div>
      <div className="tiny muted" style={{ marginTop: 6 }}>
        Use trailing x or * octets, CIDR, or an IP address. A /16 can take up to 15 minutes; narrower ranges take up to 45 seconds.
        The Themis server must be able to reach this network. Access codes and API keys are never discovered — type them on the row.
      </div>

      {error && <div role="alert" className="small" style={{ color: 'var(--err)', marginTop: 10 }}>{error}</div>}
      {scanning && <div role="status" className="small muted" style={{ marginTop: 10 }}>Scanning… Broad ranges can take up to 15 minutes.</div>}

      {result && (
        <div style={{ marginTop: 12 }}>
          <div className="small muted" style={{ marginBottom: 6 }}>
            Probed {result.scanned} address{result.scanned === 1 ? '' : 'es'} in {result.ranges.join(', ')}
            {result.truncated ? ' — stopped early; narrow the range for a complete scan' : ''}.
          </div>
          {found.length === 0 && <div className="small" data-testid="discover-none">No printers answered.</div>}

          {groups.map(g => (
            <section key={g} aria-label={`Found ${fresh.find(p => (p.plugin_id ?? p.printer_type) === g)!.display_name} printers`}
                     style={{ marginTop: 12 }}>
              <div className="small" style={{ fontWeight: 600, marginBottom: 4 }}>
                {fresh.find(p => (p.plugin_id ?? p.printer_type) === g)!.display_name}
              </div>
              <table className="tbl w-full">
                <tbody>
                  {fresh.filter(p => (p.plugin_id ?? p.printer_type) === g).map(p => {
                    const key = keyOf(p);
                    const row = rows[key];
                    if (!row) return null;
                    return <NewRow key={key} p={p} row={row} options={optionsFor(p)} type={typeOf(p, row)} state={derived(p, row)}
                      busy={busy} patch={r => patch(key, r)}
                      onAdd={() => void add(p)} onTest={() => void test(p)}
                      onSave={() => row.printerId !== undefined && void save(p, row.printerId, row.values)}
                      onEditConflict={() => patch(key, { editing: true })}
                      onSaveConflict={ip => p.ip_in_use_by && void save(p, p.ip_in_use_by.printer_id, { ip_address: ip }, false)}
                      onMoreSettings={onMoreSettings ? () => onMoreSettings({ ...p, name: row.nickname || p.name,
                        connection_config: { ...p.connection_config, ...stripBlank(row.values) } as Record<string, string | number> }) : undefined} />;
                  })}
                </tbody>
              </table>
            </section>
          ))}

          {fleet.length > 0 && (
            <section aria-label="Already in your fleet" style={{ marginTop: 16 }}>
              <div className="small" style={{ fontWeight: 600, marginBottom: 4 }}>Already in your fleet</div>
              <table className="tbl w-full">
                <tbody>
                  {fleet.map(p => {
                    const key = keyOf(p);
                    const row = rows[key];
                    if (!row || !p.added_printer) return null;
                    const fields = (typeOf(p, row) ?? optionsFor(p)[0]) ? quickAddFields(typeOf(p, row) ?? optionsFor(p)[0]) : [];
                    const status = row.status ?? (p.added_printer.configured ? 'added_configured' : 'added_unconfigured');
                    const detail = row.status ? row.detail : p.added_printer.config_error ?? undefined;
                    return (
                      <tr key={key}>
                        <td>{p.added_printer.name}<div className="tiny muted">{p.display_name}{p.model ? ` · ${p.model}` : ''}</div></td>
                        <td className="num small">{p.ip}</td>
                        <td>
                          {row.editing ? <QuickInputs ip={p.ip} fields={fields} row={row} patch={r => patch(key, r)} /> : null}
                        </td>
                        <td><Chip status={status} detail={detail} /></td>
                        <td className="row gap-2">
                          {row.editing ? <>
                            <button className="btn primary sm" aria-label={`Save ${p.ip}`}
                                    onClick={() => void save(p, p.added_printer!.printer_id, row.values)}>Save</button>
                            <button className="btn ghost sm" onClick={() => patch(key, { editing: false })}>Cancel</button>
                          </> : <>
                            <button className="btn sm" aria-label={`Edit ${p.added_printer.name}`} onClick={() => patch(key, { editing: true })}>Edit</button>
                            <button className="btn ghost sm" aria-label={`Test ${p.ip}`} onClick={() => void test(p)}>Test</button>
                          </>}
                          {row.error && <span role="alert" className="tiny text-err">{row.error}</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}

          {fresh.length > 0 && (
            <div className="row gap-2" style={{ marginTop: 12, alignItems: 'center', flexWrap: 'wrap' }} data-testid="discover-footer">
              <span className="small">{selected.length} selected</span>
              <button className="btn ghost sm" onClick={() => select(p => !p.ip_in_use_by)}>All new</button>
              <button className="btn ghost sm" onClick={() => select(p => !p.ip_in_use_by && derived(p, rows[keyOf(p)]).status === 'ready')}>Complete only</button>
              <button className="btn ghost sm" onClick={() => select(() => false)}>None</button>
              {incomplete.length > 0 && <span className="tiny text-warn">
                {incomplete.length} selected {incomplete.length === 1 ? 'printer is' : 'printers are'} missing details — {incomplete.length === 1 ? 'it' : 'they'} will be added as not configured and kept out of the queue until finished.
              </span>}
              {noModel.length > 0 && <span className="tiny text-warn">
                Choose a model for {noModel.map(p => p.ip).join(', ')} to add {noModel.length === 1 ? 'it' : 'them'}.
              </span>}
              <button className="btn primary sm" style={{ marginLeft: 'auto' }} disabled={busy || addableSel.length === 0}
                      onClick={() => void addSelected(addableSel.map(keyOf))}>
                Add {addableSel.length} printer{addableSel.length === 1 ? '' : 's'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Chip({ status, detail }: { status: RowStatus; detail?: string }) {
  const tone = statusTone(status);
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'warn' ? 'var(--warn)' : tone === 'err' ? 'var(--err)' : 'var(--text-3)';
  return <span className="tiny" data-testid="row-status" style={{ color, border: `1px solid ${color}`, borderRadius: 10, padding: '1px 8px' }}>
    {statusLabel(status, detail)}
  </span>;
}

function QuickInputs({ ip, fields, row, patch }: {
  ip: string; fields: PrinterType['connection_fields']; row: Row; patch: (r: Partial<Row>) => void;
}) {
  if (fields.length === 0) return <span className="tiny muted">Nothing to enter</span>;
  return <div className="row gap-2 wrap">
    {fields.map(f => (
      <label key={f.name} className="col tiny" style={{ gap: 2 }}>
        <span>{f.label}{row.fromScan.includes(f.name) && <span className="muted"> · from scan</span>}</span>
        <input className="input" style={{ width: 150 }} aria-label={`${f.label} for ${ip}`} autoComplete="off"
               type={f.field_type === 'password' ? 'password' : 'text'} placeholder={f.placeholder}
               value={row.values[f.name] ?? ''}
               onChange={e => patch({ values: { ...row.values, [f.name]: e.target.value }, fromScan: row.fromScan.filter(n => n !== f.name),
                 ...(row.status === 'test_ok' || row.status === 'test_failed' ? { status: null, detail: undefined } : {}) })} />
      </label>
    ))}
  </div>;
}

function NewRow({ p, row, options, type, state, busy, patch, onAdd, onTest, onSave, onEditConflict, onSaveConflict, onMoreSettings }: {
  p: DiscoveredPrinter; row: Row; options: PrinterType[]; type: PrinterType | null; state: { status: RowStatus; detail?: string };
  busy: boolean; patch: (r: Partial<Row>) => void; onAdd: () => void; onTest: () => void; onSave: () => void;
  onEditConflict: () => void; onSaveConflict: (ip: string) => void; onMoreSettings?: () => void;
}) {
  const [conflictIp, setConflictIp] = useState(p.ip);
  const fields = type ? quickAddFields(type) : [];
  const saved = row.printerId !== undefined;
  const working = state.status === 'adding' || state.status === 'testing';
  const filled = fields.every(f => (row.values[f.name] ?? '').trim());
  const listId = `models-${p.plugin_id}-${p.ip}`.replace(/[^\w-]/g, '_');
  return (
    <tr>
      <td>
        <input type="checkbox" aria-label={`Select ${p.ip}`} checked={row.checked} disabled={saved || busy}
               onChange={e => patch({ checked: e.target.checked })} />
      </td>
      <td>
        <input className="input" aria-label={`Nickname for ${p.ip}`} value={row.nickname} disabled={saved} style={{ width: 160 }}
               onChange={e => patch({ nickname: e.target.value, nicknameEdited: true })} />
        <div style={{ marginTop: 4 }}>
          <input className="input" list={listId} aria-label={`Model for ${p.ip}`} value={row.modelText} disabled={saved}
                 placeholder={p.model ? `Found: ${p.model}` : 'Choose a model'} style={{ width: 160 }}
                 onChange={e => {
                   const t = options.find(o => modelLabel(o) === e.target.value);
                   patch({ modelText: e.target.value, model: t ? modelKey(t) : '', status: null, detail: undefined,
                     ...(t && !row.nicknameEdited ? { nickname: defaultNickname(p, t) } : {}) });
                 }} />
          <datalist id={listId}>{options.map(o => <option key={modelKey(o)} value={modelLabel(o)} />)}</datalist>
        </div>
        {p.note && <div className="tiny text-warn">{p.note}</div>}
        {p.ip_in_use_by && (
          <div className="tiny" role="note" style={{ color: 'var(--warn)', marginTop: 4 }}>
            {p.ip} is already used by {p.ip_in_use_by.name}{p.ip_in_use_by.model ? ` (${p.ip_in_use_by.model})` : ''} — the address may have changed.{' '}
            {row.editing ? (
              <span className="row gap-2" style={{ display: 'inline-flex', alignItems: 'center' }}>
                <input className="input" aria-label={`New IP for ${p.ip_in_use_by.name}`} value={conflictIp} style={{ width: 130 }}
                       onChange={e => setConflictIp(e.target.value)} />
                <button className="btn sm" onClick={() => onSaveConflict(conflictIp)}>Save {p.ip_in_use_by.name}</button>
                <button className="btn ghost sm" onClick={() => patch({ editing: false })}>Cancel</button>
              </span>
            ) : <button className="btn ghost sm" onClick={onEditConflict}>Edit {p.ip_in_use_by.name}</button>}
          </div>
        )}
        {row.notice && <div className="tiny" role="status" style={{ marginTop: 4 }}>{row.notice}</div>}
      </td>
      <td className="num small">{p.ip}</td>
      <td><QuickInputs ip={p.ip} fields={fields} row={row} patch={patch} /></td>
      <td><Chip status={state.status} detail={state.detail} />
        {row.error && <div role="alert" className="tiny" style={{ color: 'var(--err)', marginTop: 2 }}>{row.error}</div>}</td>
      <td className="row gap-2 wrap">
        {fields.length > 0 && <button className="btn ghost sm" aria-label={`Test ${p.ip}`} disabled={working || !filled} onClick={onTest}>Test</button>}
        {saved
          ? state.status === 'added_unconfigured' && <button className="btn sm" aria-label={`Save ${p.ip}`} onClick={onSave}>Save</button>
          : <button className="btn primary sm" aria-label={`Add ${p.ip}`} disabled={working || busy || !type} onClick={onAdd}>Add</button>}
        {onMoreSettings && !saved && <button className="btn ghost sm" aria-label={`More settings for ${p.ip}`} onClick={onMoreSettings}>More settings…</button>}
      </td>
    </tr>
  );
}
