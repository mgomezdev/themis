import React, { useEffect, useState } from 'react';
import type { OrcaCatalogStatus } from '../api/orca';

const DOT_COLORS: Record<string, string> = {
  online: 'var(--ok, #22c55e)',
  building: '#f59e0b',
  offline: 'var(--err, #ef4444)',
  unconfigured: '#6b7280',
};

function relativeTime(ts: number | null): string {
  if (!ts) return 'never';
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

export function LaminusStatusChip() {
  const [data, setData] = useState<OrcaCatalogStatus | null>(null);
  const [expanded, setExpanded] = useState(false);

  const load = () => {
    fetch('/api/v1/laminus/catalog/status')
      .then(r => r.json())
      .then(setData)
      .catch(() => {});
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, []);

  if (!data || !data.laminus_configured) return null;

  const status = data.status ?? 'offline';
  const dotColor = DOT_COLORS[status] ?? '#6b7280';

  return (
    <div style={{ padding: '4px 8px', fontSize: 13 }}>
      <button
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'inherit', padding: 0, width: '100%',
        }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: dotColor, flexShrink: 0,
          boxShadow: status === 'online' ? `0 0 4px ${dotColor}` : 'none',
        }} />
        <span>Laminus</span>
        <span style={{ color: 'var(--text-muted, #aaa)', marginLeft: 'auto' }}>{status}</span>
      </button>
      {expanded && (
        <div style={{ marginTop: 6, paddingLeft: 14, fontSize: 12, color: 'var(--text-muted, #aaa)' }}>
          {data.catalog_counts ? (
            <>
              <div>{data.catalog_counts.machine} machines</div>
              <div>{data.catalog_counts.process} processes</div>
              <div>{data.catalog_counts.filament} filaments</div>
            </>
          ) : (
            <div>Catalog not loaded</div>
          )}
          <div style={{ marginTop: 4 }}>Fetched: {relativeTime(data.fetched_at)}</div>
        </div>
      )}
    </div>
  );
}
