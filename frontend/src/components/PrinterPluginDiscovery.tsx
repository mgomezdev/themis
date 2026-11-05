import { useState } from 'react';
import { fetchPrinterTypes, type DiscoveredPrinter, type PrinterType } from '../api/printers';
import { PrinterAddForm } from '../screens/PrintersScreen';
import { DiscoverPrinters } from './DiscoverPrinters';

/** Keep discovery and its selected address in memory, within this plug-in's settings. */
export function PrinterPluginDiscovery({ pluginId }: { pluginId: string }) {
  const [picked, setPicked] = useState<DiscoveredPrinter | null>(null);
  const [types, setTypes] = useState<PrinterType[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [created, setCreated] = useState(false);

  async function pick(printer: DiscoveredPrinter) {
    if (loading) return;
    setLoading(true); setError(''); setCreated(false);
    try {
      const available = (await fetchPrinterTypes()).filter(t => t.plugin_id === pluginId);
      if (!available.length) throw new Error('No enabled printer models are available for this plug-in.');
      setTypes(available); setPicked(printer);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }

  return <section aria-label="Printer discovery" style={{ marginTop: 24 }}>
    <h2 style={{ fontSize: 16 }}>Find printers</h2>
    {picked && <PrinterAddForm types={types} discovered={picked} backLabel="Scan results"
      onCancel={() => setPicked(null)} onCreated={() => { setPicked(null); setCreated(true); }} />}
    <div hidden={!!picked}><DiscoverPrinters pluginId={pluginId} onPick={p => void pick(p)} /></div>
    {loading && <div role="status" className="small muted">Loading printer models…</div>}
    {error && <div role="alert" className="small" style={{ color: 'var(--err)' }}>{error}</div>}
    {created && <div role="status" className="small">Printer added. It is now available in Printers.</div>}
  </section>;
}
