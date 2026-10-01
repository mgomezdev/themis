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

/** "Scan network" for the add-printer wizard. The range box matters when Themis and the printers are on different
 *  networks/VLANs (e.g. Themis on 192.168.3.x, printers on 192.168.7.x) or Themis runs in Docker. */
export function DiscoverPrinters({ onPick }: { onPick: (p: DiscoveredPrinter) => void }) {
  const [open, setOpen] = useState(false);
  const [ranges, setRanges] = useState(savedRanges);
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function scan() {
    setScanning(true);
    setError(null);
    setResult(null);
    saveRanges(ranges);
    try {
      setResult(await discoverPrinters(ranges.split(/[\s,]+/).filter(Boolean)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
               placeholder="Range(s), e.g. 192.168.7.0/24 — blank = this server's network"
               value={ranges} onChange={e => setRanges(e.target.value)} />
        <button className="btn primary sm" onClick={() => void scan()} disabled={scanning}>
          {scanning ? 'Scanning…' : 'Scan'}
        </button>
        <button className="btn ghost sm" onClick={() => setOpen(false)}>Close</button>
      </div>
      <div className="tiny muted" style={{ marginTop: 6 }}>
        Printers on another VLAN need their range typed here. In Docker, use the printers&apos; range too (the container&apos;s own
        network is not your LAN). Access codes and API keys are never discovered — you enter those next.
      </div>

      {error && <div role="alert" className="small" style={{ color: 'var(--err)', marginTop: 10 }}>{error}</div>}
      {scanning && <div className="small muted" style={{ marginTop: 10 }}>Scanning — large ranges can take up to 45 s…</div>}

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
                {result.found.map(p => (
                  <tr key={`${p.printer_type}-${p.ip}`}>
                    <td>{p.display_name}{p.model ? ` · ${p.model}` : ''}{p.name ? ` (${p.name})` : ''}</td>
                    <td className="num small">{p.ip}</td>
                    <td className="small">
                      {p.already_added && <span className="muted">already added</span>}
                      {p.note && <span style={{ color: 'var(--warn)' }}>{p.note}</span>}
                    </td>
                    <td>
                      <button className="btn sm" aria-label={`Use ${p.ip}`} onClick={() => onPick(p)}>
                        {p.already_added ? 'Add again' : 'Use'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
