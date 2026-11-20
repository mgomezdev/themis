import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchCatalog, fetchSources, syncSource, materializeEntry, type Materialization, type CatalogEntry, type LibrarySource } from '../../api/library';

export function SourceCatalogPanel() {
  const [sources, setSources] = useState<LibrarySource[]>([]);
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [prepared, setPrepared] = useState<Record<string, Materialization>>({});
  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([fetchCatalog(offset), fetchSources()]).then(([catalog, providers]) => {
      if (alive) { setEntries(catalog.items); setTotal(catalog.total); setSources(providers.sources); }
    }).catch(e => { if (alive) setError(String(e.message)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [offset, revision]);
  async function sync(id: string) {
    setPrepared({});
    setBusy(id); setError(''); setMessage('');
    try { const result = await syncSource(id); setOffset(0); setRevision(v => v + 1); setMessage(`Synchronized ${result.entries} catalog entries.`); }
    catch (e) { setError(e instanceof Error ? e.message : 'Source synchronization failed'); }
    finally { setBusy(null); }
  }
  async function prepare(id: string) {
    setPrepared(v => { const next = { ...v }; delete next[id]; return next; });
    setBusy(id); setError(''); setMessage('');
    try {
      const result = await materializeEntry(id);
      setPrepared(v => ({ ...v, [id]: result }));
      setRevision(v => v + 1);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not prepare the local file'); }
    finally { setBusy(null); }
  }
  return <div className="col gap-3" data-testid="source-catalog">
    <div><h2>Library sources</h2><p className="muted">Synchronize metadata from connected libraries. Existing local files stay in Library.</p></div>
    {error && <div role="alert">{error}</div>}
    {message && <div role="status">{message}</div>}
    {loading && <p>Loading library sources…</p>}
    {!loading && sources.length === 0 && <p>No library sources are installed. <Link to="/settings/plugins">Open plugin settings</Link></p>}
    {sources.map(source => <div className="card row gap-2 wrap" style={{ padding: 14 }} key={source.plugin_id}>
      <strong>{source.name}</strong>
      <span>{source.active ? 'Enabled' : source.enabled ? 'Needs configuration' : 'Disabled'}</span>
      <Link to={`/plugins/${source.plugin_id}`}>Settings</Link>
      <button className="btn sm" disabled={!source.active || busy !== null} onClick={() => void sync(source.plugin_id)}>
        {busy === source.plugin_id ? 'Synchronizing…' : 'Sync now'}
      </button>
      {source.last_sync_at && <span className="small muted">Last sync: {new Date(source.last_sync_at).toLocaleString()}</span>}
      {source.error && <span className="small text-err">{source.error}</span>}
    </div>)}
    {total > 100 && <div className="row gap-2">
      <button className="btn sm" disabled={loading || offset === 0} onClick={() => setOffset(v => Math.max(0, v - 100))}>Previous</button>
      <span>{offset + 1}–{Math.min(offset + entries.length, total)} of {total}</span>
      <button className="btn sm" disabled={loading || offset + entries.length >= total} onClick={() => setOffset(v => v + 100)}>Next</button>
    </div>}
    {entries.length === 0 ? (!loading && <p>No synchronized files yet.</p>) : <table className="tbl w-full"><thead><tr>
      <th>File</th><th>Source</th><th>Creator</th><th>Source license</th><th>Availability</th><th>Local file</th>
    </tr></thead><tbody>{entries.map(entry => <tr key={entry.id} data-testid={`catalog-${entry.id}`}>
      <td><a href={entry.source_url} target="_blank" rel="noopener noreferrer">{entry.name}</a></td>
      <td>{sources.find(s => s.plugin_id === entry.provider_plugin_id)?.name ?? 'Unavailable source'}</td>
      <td>{entry.metadata.creator?.name || 'Unknown'}</td><td>{entry.metadata.license_code || 'Unknown'}</td>
      <td>{entry.missing ? 'Missing at source' : entry.cached_file_id ? 'Cached locally' : 'Metadata only'}</td>
      <td><button className="btn sm" disabled={busy !== null} onClick={() => void prepare(entry.id)}>
        {busy === entry.id ? 'Preparing…' : 'Prepare locally'}
      </button>
      {prepared[entry.id] && <div>
        {prepared[entry.id].warning && <p role="alert">{prepared[entry.id].warning}</p>}
        <span>{prepared[entry.id].fresh ? 'Local file ready. ' : 'Last-known local file ready. '}</span>
        <Link to={`/files?file=${prepared[entry.id].file_id}`}>Open local details</Link>{' · '}
        <Link to="/queue/new" state={{ libraryFileId: prepared[entry.id].file_id }}>Use in job</Link>
        <p className="small muted">Also available in the local library for projects.</p>
      </div>}</td>
    </tr>)}</tbody></table>}
  </div>;
}
