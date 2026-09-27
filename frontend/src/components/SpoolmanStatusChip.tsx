import { useState } from 'react';
import { useSpoolmanSyncStatus, spoolmanSyncTone } from '../api/spoolman';

const DOT_COLORS: Record<string, string> = {
  success: 'var(--ok, #22c55e)',
  stale: 'var(--warn, #f59e0b)',
  fail: 'var(--err, #ef4444)',
};

const STATUS_LABEL: Record<string, string> = {
  success: 'synced',
  stale: 'stale',
  fail: 'sync failing',
};

export function SpoolmanStatusChip() {
  const { status } = useSpoolmanSyncStatus();
  const [expanded, setExpanded] = useState(false);

  if (!status || !status.enabled) return null;

  const tone = spoolmanSyncTone(status);
  const dotColor = DOT_COLORS[tone];

  return (
    <div style={{ padding: '4px 8px', fontSize: 13 }}>
      <button
        onClick={() => setExpanded(e => !e)}
        title={status.last_sync_at
          ? `Last successful sync: ${new Date(status.last_sync_at).toLocaleString()}`
          : 'Spoolman has never synced successfully'}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'inherit', padding: 0, width: '100%',
        }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: dotColor, flexShrink: 0,
          boxShadow: tone === 'success' ? `0 0 4px ${dotColor}` : 'none',
        }} />
        <span>Spoolman</span>
        <span style={{ color: 'var(--text-muted, #aaa)', marginLeft: 'auto' }}>{STATUS_LABEL[tone]}</span>
      </button>
      {expanded && (
        <div style={{ marginTop: 6, paddingLeft: 14, fontSize: 12, color: 'var(--text-muted, #aaa)' }}>
          <div>
            Last successful sync: {status.last_sync_at ? new Date(status.last_sync_at).toLocaleString() : 'never'}
          </div>
          {status.last_error && (
            <div style={{ color: 'var(--err)', marginTop: 4 }}>
              {status.last_error_code ? `[${status.last_error_code}] ` : ''}{status.last_error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
