import { useState } from 'react';
import { discoverPrinters, type DiscoveredPrinter, type DiscoveryResult } from '../api/printers';
import { Icons } from './icons';

const STORAGE_KEY = 'themis.discovery.ranges';

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

/** "Scan network" for the add-printer wizard. The range box matters when Themis and the printers are on different
 *  networks/VLANs (e.g. Themis on 192.168.3.x, printers on 192.168.7.x) or Themis runs in Docker. */
export function DiscoverPrinters({ onPick, pluginId }: { onPick: (p: DiscoveredPrinter) => void; pluginId?: string }) {
  const [open, setOpen] = useState(!!pluginId);
  const [ranges, setRanges] = useState(() => pluginId ? '192.168.x.x' : savedRanges());
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, DiscoveredPrinter>>({});
  const [editing, setEditing] = useState<{ printer: DiscoveredPrinter; model: string; serial: string } | null>(null);
  const keyOf = (p: DiscoveredPrinter) => `${p.printer_type}:${p.ip}`;

  function resetDetails(printer: DiscoveredPrinter) {
    setOverrides(previous => {
      const next = { ...previous };
      delete next[keyOf(printer)];
      return next;
    });
    setEditing(null);
  }

  function applyDetails() {
    if (!editing) return;
    const original = editing.printer;
    const model = editing.model.trim() || null;
    const serial = editing.serial.trim() || null;
    const serialChanged = serial !== (original.serial ?? original.connection_config.serial_number ?? null);
    setOverrides(previous => ({ ...previous, [keyOf(original)]: {
      ...original, model, serial,
      // An announced UUID must not outrank the user's edited model in the add wizard.
      model_uuid: model === original.model ? original.model_uuid : null,
      connection_config: { ...original.connection_config, ...(serialChanged ? { serial_number: serial ?? '' } : {}) },
    } }));
    setEditing(null);
  }

  async function scan() {
    setScanning(true);
    setError(null);
    setResult(null);
    setOverrides({});
    setEditing(null);
    if (!pluginId) saveRanges(ranges);
    try {
      setResult(await discoverPrinters(ranges.split(/[\s,]+/).filter(Boolean), pluginId));
    } catch (e) {
      setError(readable(e));
    } finally {
      setScanning(false);
    }
  }

  if (!open) {
    return (
      <div style={{ marginBottom: 16 }}>
        <button className="btn sm" onClick={() => setOpen(true)}>{Icons.search} Scan network for printers</button>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16 }} data-testid="discover-printers">
      <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="input" aria-label="Network ranges" style={{ maxWidth: 320 }}
               placeholder="192.168.x.x or 192.168.7.0/24"
               disabled={scanning}
               value={ranges} onChange={e => setRanges(e.target.value)} />
        <button className="btn primary sm" onClick={() => void scan()} disabled={scanning}>
          {scanning ? 'Scanning…' : 'Scan'}
        </button>
        {!pluginId && <button className="btn ghost sm" onClick={() => setOpen(false)}>Close</button>}
      </div>
      <div className="tiny muted" style={{ marginTop: 6 }}>
        Use trailing x or * octets, CIDR, or an IP address. A /16 can take up to 15 minutes; narrower ranges take up to 45 seconds.
        The Themis server must be able to reach this network. Access codes and API keys are entered when adding a printer.
      </div>

      {error && <div role="alert" className="small" style={{ color: 'var(--err)', marginTop: 10 }}>{error}</div>}
      {scanning && <div role="status" className="small muted" style={{ marginTop: 10 }}>Scanning… Broad ranges can take up to 15 minutes.</div>}

      {result && (
        <div style={{ marginTop: 12 }}>
          <div className="small muted" style={{ marginBottom: 6 }}>
            Probed {result.scanned} address{result.scanned === 1 ? '' : 'es'} in {result.ranges.join(', ')}
            {result.truncated ? ' — stopped early; narrow the range for a complete scan' : ''}.
          </div>
          {result.found.length === 0 ? (
            <div className="small" data-testid="discover-none">No printers answered.</div>
          ) : (
            <table className="tbl" style={{ width: '100%' }}>
              <tbody>
                {result.found.map(discovered => {
                  const p = overrides[keyOf(discovered)] ?? discovered;
                  return (
                  <tr key={`${p.printer_type}-${p.ip}`}>
                    <td>
                      {p.display_name}{p.model ? ` · ${p.model}` : ''}{p.name ? ` (${p.name})` : ''}
                      {p.serial && <div className="tiny muted">Serial: {p.serial}</div>}
                      {overrides[keyOf(p)] && <div className="tiny muted">Manual details</div>}
                    </td>
                    <td className="num small">{p.ip}</td>
                    <td className="small">
                      {p.already_added && <span className="muted">already added</span>}
                      {p.note && <span style={{ color: 'var(--warn)' }}>{p.note}</span>}
                    </td>
                    <td>
                      <button className="btn sm" aria-label={`Use ${p.ip}`} disabled={!!editing} onClick={() => onPick(p)}>
                        {p.already_added ? 'Add again' : 'Use'}
                      </button>
                      {' '}
                      <button className="btn ghost sm" aria-label={`Edit details for ${p.ip}`}
                        onClick={() => setEditing({ printer: discovered, model: p.model ?? '',
                          serial: p.serial ?? String(p.connection_config.serial_number ?? '') })}>Edit details</button>
                    </td>
                  </tr>
                ); })}
              </tbody>
            </table>
          )}
          {editing && <form aria-label={`Edit details for ${editing.printer.ip}`} className="col gap-2"
            style={{ marginTop: 12 }} onSubmit={event => { event.preventDefault(); applyDetails(); }}>
            <div className="small">Details for {editing.printer.ip}</div>
            <label className="col gap-2 small">Model
              <input className="input" value={editing.model} maxLength={200}
                onChange={event => setEditing({ ...editing, model: event.target.value })} />
            </label>
            <label className="col gap-2 small">Serial number
              <input className="input" value={editing.serial} maxLength={200} autoComplete="off"
                onChange={event => setEditing({ ...editing, serial: event.target.value })} />
            </label>
            <div className="tiny muted">Use the model name from this plug-in's model list to preselect it when adding.
              Unrecognized names require a model choice. Details stay on this page until you add the printer or scan again.</div>
            <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
              <button type="submit" className="btn primary sm">Apply details</button>
              <button type="button" className="btn ghost sm" onClick={() => setEditing(null)}>Cancel</button>
              <button type="button" className="btn ghost sm" onClick={() => resetDetails(editing.printer)}>Reset to discovered</button>
            </div>
          </form>}
        </div>
      )}
    </div>
  );
}
