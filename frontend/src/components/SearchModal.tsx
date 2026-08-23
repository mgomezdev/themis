import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icons } from './icons';
import type { ApiJob } from '../api/queue';
import type { Printer, LibraryFile } from '../data/types';
import { getOrders, type ApiOrder } from '../api/orders';
import { getFiles } from '../api/files';

interface SearchModalProps {
  open: boolean;
  onClose: () => void;
  jobs: ApiJob[];
  printers: Printer[];
}

interface SearchResult {
  key: string;
  group: 'Jobs' | 'Orders' | 'Files' | 'Printers';
  label: string;
  sublabel: string;
  onSelect: () => void;
}

const MAX_PER_GROUP = 8;

export function SearchModal({ open, onClose, jobs, printers }: SearchModalProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [orders, setOrders] = useState<ApiOrder[]>([]);
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    if (loaded) return;
    let alive = true;
    Promise.all([getOrders(), getFiles()])
      .then(([o, f]) => { if (alive) { setOrders(o); setFiles(f); setLoaded(true); } })
      .catch(console.error);
    return () => { alive = false; };
  }, [open, loaded]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  const fileNameById = useMemo(() => {
    const m = new Map<number, string>();
    for (const f of files) m.set(f.id, f.original_filename);
    return m;
  }, [files]);

  const orderById = useMemo(() => {
    const m = new Map<number, ApiOrder>();
    for (const o of orders) m.set(o.id, o);
    return m;
  }, [orders]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [] as SearchResult[];

    const jobResults: SearchResult[] = [];
    for (const j of jobs) {
      const fileName = fileNameById.get(j.uploaded_file_id) ?? '';
      const order = j.order_id != null ? orderById.get(j.order_id) : undefined;
      const haystack = `${fileName} ${j.id} ${order?.title ?? ''}`.toLowerCase();
      if (haystack.includes(q)) {
        jobResults.push({
          key: `job-${j.id}`,
          group: 'Jobs',
          label: fileName || `Job #${j.id}`,
          sublabel: order ? `Order: ${order.title}` : `Job #${j.id} · ${j.status}`,
          onSelect: () => navigate(`/jobs/${j.id}`),
        });
      }
    }

    const orderResults: SearchResult[] = [];
    for (const o of orders) {
      const haystack = `${o.title} ${o.customer} ${o.id}`.toLowerCase();
      if (haystack.includes(q)) {
        orderResults.push({
          key: `order-${o.id}`,
          group: 'Orders',
          label: o.title,
          sublabel: `${o.customer} · Order #${o.id}`,
          onSelect: () => navigate('/orders'),
        });
      }
    }

    const fileResults: SearchResult[] = [];
    for (const f of files) {
      if (f.original_filename.toLowerCase().includes(q)) {
        fileResults.push({
          key: `file-${f.id}`,
          group: 'Files',
          label: f.original_filename,
          sublabel: f.folder || 'Library',
          onSelect: () => navigate('/files'),
        });
      }
    }

    const printerResults: SearchResult[] = [];
    for (const p of printers) {
      const haystack = `${p.name} ${p.nickname}`.toLowerCase();
      if (haystack.includes(q)) {
        printerResults.push({
          key: `printer-${p.id}`,
          group: 'Printers',
          label: p.nickname || p.name,
          sublabel: p.model,
          onSelect: () => navigate('/fleet'),
        });
      }
    }

    return [
      ...jobResults.slice(0, MAX_PER_GROUP),
      ...orderResults.slice(0, MAX_PER_GROUP),
      ...fileResults.slice(0, MAX_PER_GROUP),
      ...printerResults.slice(0, MAX_PER_GROUP),
    ];
  }, [query, jobs, orders, files, printers, fileNameById, orderById, navigate]);

  const grouped = useMemo(() => {
    const groups: { group: string; items: SearchResult[] }[] = [];
    for (const r of results) {
      let g = groups.find(g => g.group === r.group);
      if (!g) { g = { group: r.group, items: [] }; groups.push(g); }
      g.items.push(r);
    }
    return groups;
  }, [results]);

  function select(r: SearchResult) {
    r.onSelect();
    onClose();
  }

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && results.length > 0) {
      e.preventDefault();
      select(results[0]);
    }
  }

  if (!open) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000,
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: '12vh',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="card"
        style={{ width: 560, maxWidth: '90vw', maxHeight: '70vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
      >
        <div className="row gap-2" style={{ alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--border-1)' }}>
          {Icons.search}
          <input
            autoFocus
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder="Search jobs, orders, files, printers…"
            className="input"
            style={{ border: 'none', flex: 1, background: 'transparent' }}
          />
          <span className="muted small">Esc</span>
        </div>
        <div style={{ overflowY: 'auto', padding: query.trim() ? '6px 0' : 0 }}>
          {!loaded && query.trim() && (
            <div className="muted small" style={{ padding: '12px 14px' }}>Loading…</div>
          )}
          {loaded && query.trim() && results.length === 0 && (
            <div className="muted small" style={{ padding: '12px 14px' }}>No results</div>
          )}
          {grouped.map(g => (
            <div key={g.group}>
              <div className="muted small" style={{ padding: '6px 14px' }}>{g.group}</div>
              {g.items.map(r => (
                <button
                  key={r.key}
                  onClick={() => select(r)}
                  className="row gap-2"
                  style={{
                    width: '100%', textAlign: 'left', padding: '8px 14px', border: 'none',
                    background: 'transparent', cursor: 'pointer', alignItems: 'baseline',
                  }}
                >
                  <span>{r.label}</span>
                  <span className="muted small">{r.sublabel}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
