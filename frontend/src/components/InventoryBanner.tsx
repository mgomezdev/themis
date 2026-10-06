import { Link } from 'react-router-dom';
import { CAP, syncTone, useInventory, useSyncStatus } from '../api/inventory';

/** Shown across screens while a remote inventory provider is unreachable: the spool data on screen is the last-known copy,
 *  and finished prints are queued (and applied when it is back). */
export function InventoryBanner() {
  const inventory = useInventory();
  const remote = inventory.has(CAP.REMOTE);
  const { status } = useSyncStatus(remote);
  if (!remote || !status || !inventory.plugin) return null;
  const tone = syncTone(status);
  if (tone !== 'disconnected' && tone !== 'fail') return null;

  const name = inventory.plugin.name;
  return (
    <div role="alert" data-testid="inventory-banner"
         style={{ padding: '8px 14px', background: 'rgba(239,68,68,0.10)', borderBottom: '1px solid rgba(239,68,68,0.3)', fontSize: 13, color: 'var(--text-1)' }}>
      <strong>{name} is unreachable.</strong>{' '}
      Spool data is the last-known copy{status.cache_as_of ? ` (as of ${new Date(status.cache_as_of).toLocaleString()})` : ''}
      {status.pending_count > 0 ? `; ${status.pending_count} weight update${status.pending_count === 1 ? ' is' : 's are'} queued and will apply when it is back` : ''}.{' '}
      <Link to={`/plugins/${inventory.plugin.id}`}>Details</Link>
    </div>
  );
}
