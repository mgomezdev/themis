// The Builder's Queue project outcome (Review tab): the jobs added (with any group warnings), or the error + Retry.
import { Icons } from '../icons';
import { generateHeadline } from '../../api/projects';

export function QueueSuccess({ jobCount, pools = 0, poolParts, warnings, onOpenProject }: {
  jobCount: number;
  /** Pool groups queued for printers to pack (they add no jobs yet). */
  pools?: number;
  poolParts?: number;
  /** One per group that queued with a warning (BIZ-360). */
  warnings: string[];
  onOpenProject: () => void;
}) {
  return (
    <div role="status" style={{
      border: '1px solid var(--ok)', borderRadius: 8,
      padding: '12px 16px', background: 'oklch(55% 0.15 142 / 0.06)',
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
    }}>
      <span style={{ color: 'var(--ok)', flexShrink: 0 }}>{Icons.check}</span>
      <span style={{ fontSize: 14, flex: 1 }}>
        {generateHeadline(jobCount, poolParts == null ? pools : 0, 'added to queue')}
        {poolParts != null && <span className="small" style={{ display: 'block' }}>
          {poolParts} {poolParts === 1 ? 'part' : 'parts'} waiting in pools
        </span>}
        {warnings.map(w => <span key={w} className="tiny" style={{ display: 'block', color: 'var(--warn)' }}>{w}</span>)}
      </span>
      <button className="btn sm" onClick={onOpenProject}>Details</button>
    </div>
  );
}

export function QueueError({ message, busy, onRetry }: { message: string; busy: boolean; onRetry: () => void }) {
  return (
    <div className="tone-err tint" style={{ display: 'flex', alignItems: 'center', gap: 8, borderRadius: 6, padding: '10px 14px' }}>
      <span style={{ color: 'var(--err)', flexShrink: 0 }}>{Icons.alert}</span>
      <span style={{ fontSize: 13, color: 'var(--err)', flex: 1 }}>{message}</span>
      <button className="btn sm" onClick={onRetry} disabled={busy}>Retry</button>
    </div>
  );
}
