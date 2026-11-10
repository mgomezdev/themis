import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getQueuePools, type QueuePool } from '../api/queue';

/**
 * The queue's print pools (BIZ-362): each pooled group of a queued project is one queue entry; idle printers pack their next
 * plate from it (claimed plates are queue rows labelled with their pool). `refreshKey` re-reads the pools when the queue changes.
 */
export function QueuePools({ refreshKey, onPools }: { refreshKey: unknown; onPools?: (pools: QueuePool[]) => void }) {
  const [pools, setPools] = useState<QueuePool[]>([]);
  useEffect(() => {
    let alive = true;
    getQueuePools()
      .then(ps => { if (!alive) return; const list = Array.isArray(ps) ? ps : []; setPools(list); onPools?.(list); })
      .catch(() => { /* the queue still works without its pool rows */ });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);
  if (pools.length === 0) return null;
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }} data-testid="queue-pools">
      <div className="label" style={{ marginBottom: 8 }}>Print pools</div>
      <div className="col gap-2">
        {pools.map(p => (
          <div key={p.group_id} className="row gap-3 small" style={{ alignItems: 'baseline', flexWrap: 'wrap' }}
               data-testid={`queue-pool-${p.group_id}`}>
            <span className="fw-600">{p.name}</span>
            <Link className="tiny" to={`/projects/${p.project_id}`}>{p.project_name ?? `Project #${p.project_id}`}</Link>
            <span className="tiny muted grow">{p.done} done · {p.printing} printing · {p.left} left</span>
            {p.warning && <span className="tiny" role="alert" style={{ color: 'var(--warn)' }}>{p.warning}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
