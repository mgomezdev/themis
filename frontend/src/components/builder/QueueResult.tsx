// The Builder's Queue project outcome (Review tab): the jobs added (with any group warnings), or the error + Retry.
import { Icons } from '../icons';

export function QueueSuccess({ jobCount, warnings, onOpenQueue, onOpenProject }: {
  jobCount: number;
  /** One per group that queued with a warning (BIZ-360). */
  warnings: string[];
  onOpenQueue: () => void;
  onOpenProject: () => void;
}) {
  return (
    <div style={{
      border: '1px solid var(--ok)', borderRadius: 8,
      padding: '12px 16px', background: 'oklch(55% 0.15 142 / 0.06)',
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
    }}>
      <span style={{ color: 'var(--ok)', flexShrink: 0 }}>{Icons.check}</span>
      <span style={{ fontSize: 14, flex: 1 }}>
        {jobCount} job{jobCount !== 1 ? 's' : ''} added to queue
        {warnings.map(w => <span key={w} className="tiny" style={{ display: 'block', color: 'var(--warn)' }}>{w}</span>)}
      </span>
      <button className="btn sm" onClick={onOpenQueue}>Queue</button>
      <button className="btn sm" onClick={onOpenProject}>Details</button>
    </div>
  );
}

export function QueueError({ message, busy, onRetry }: { message: string; busy: boolean; onRetry: () => void }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      background: 'color-mix(in oklch, var(--err) 8%, transparent)', border: '1px solid color-mix(in oklch, var(--err) 25%, transparent)',
      borderRadius: 6, padding: '10px 14px',
    }}>
      <span style={{ color: 'var(--err)', flexShrink: 0 }}>{Icons.alert}</span>
      <span style={{ fontSize: 13, color: 'var(--err)', flex: 1 }}>{message}</span>
      <button className="btn sm" onClick={onRetry} disabled={busy}>Retry</button>
    </div>
  );
}
