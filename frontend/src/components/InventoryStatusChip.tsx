import { useState } from 'react';
import { CAP, syncTone, useInventory, useSyncStatus, type SyncTone } from '../api/inventory';

const DOT_COLORS: Record<SyncTone, string> = {
  success: 'var(--ok, #22c55e)',
  stale: 'var(--warn, #f59e0b)',
  fail: 'var(--err, #ef4444)',
  disconnected: 'var(--err, #ef4444)',
};

const STATUS_LABEL: Record<SyncTone, string> = {
  success: 'synced',
  stale: 'stale',
  fail: 'sync failing',
  disconnected: 'unreachable',
};

/** Sidebar chip for the active inventory provider. Hidden with no provider; a provider that lives elsewhere (REMOTE) also
 *  shows its sync health and turns red while it is unreachable. */
export function InventoryStatusChip() {
  const inventory = useInventory();
  const { status } = useSyncStatus(!!inventory.plugin);
  const [expanded, setExpanded] = useState(false);

  if (!inventory.plugin || !status) return null;

  const remote = inventory.has(CAP.REMOTE);
  const tone: SyncTone = remote ? syncTone(status) : 'success';
  const dotColor = DOT_COLORS[tone];
  const name = inventory.plugin.ui.nav_label || inventory.plugin.name;

  return (
    <div style={{ padding: '4px 8px', fontSize: 13 }} data-testid="inventory-chip" data-tone={tone}>
      <button
        onClick={() => setExpanded(e => !e)}
        title={!remote ? `${name} inventory` : status.last_sync_at
          ? `Last successful sync: ${new Date(status.last_sync_at).toLocaleString()}`
          : `${name} has never synced successfully`}
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
        <span>{name}</span>
        <span style={{ color: 'var(--text-muted, #aaa)', marginLeft: 'auto' }}>
          {remote ? STATUS_LABEL[tone] : 'ready'}{status.pending_count > 0 ? ` · ${status.pending_count} queued` : ''}
        </span>
      </button>
      {expanded && remote && (
        <div style={{ marginTop: 6, paddingLeft: 14, fontSize: 12, color: 'var(--text-muted, #aaa)' }}>
          <div>Last successful sync: {status.last_sync_at ? new Date(status.last_sync_at).toLocaleString() : 'never'}</div>
          {status.disconnected_since && <div style={{ color: 'var(--err)', marginTop: 4 }}>Unreachable since {new Date(status.disconnected_since).toLocaleString()}</div>}
          {status.pending_count > 0 && <div style={{ marginTop: 4 }}>{status.pending_count} weight update{status.pending_count === 1 ? '' : 's'} waiting for the provider</div>}
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
