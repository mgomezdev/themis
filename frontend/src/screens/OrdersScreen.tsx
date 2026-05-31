import { useState, useMemo, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { matColor, fmtTime } from '../data/helpers';
import { StatusPill, Progress, MaterialChip, Empty } from '../components/ui';
import { Icons } from '../components/icons';
import { useFilePlates } from '../api/queue';
import {
  useOrders, getOrder, updateOrder, deleteOrder,
  type ApiOrder, type ApiOrderDetail, type OrderJobSummary,
} from '../api/orders';

type Filter = 'open' | 'all' | 'customer' | 'internal';

function PartsTable({ order }: { order: ApiOrder }) {
  if (order.parts.length === 0) {
    return <div className="tiny muted" style={{ padding: '12px 18px' }}>No parts listed.</div>;
  }
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th style={{ paddingLeft: 18 }}>Part</th>
          <th style={{ width: 140 }}>Material</th>
          <th style={{ width: 90 }}>Qty</th>
          <th style={{ width: 120, textAlign: 'right', paddingRight: 18 }}>Est. each</th>
        </tr>
      </thead>
      <tbody>
        {order.parts.map(p => (
          <tr key={p.id}>
            <td style={{ paddingLeft: 18, fontWeight: 500 }}>{p.name || <span className="muted">unnamed</span>}</td>
            <td><MaterialChip material={p.material} color={p.filament_color ?? matColor(p.material)} /></td>
            <td className="num small">{p.qty}</td>
            <td className="num small" style={{ textAlign: 'right', paddingRight: 18 }}>{fmtTime(p.est_minutes)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JobsFilling({ jobs }: { jobs: OrderJobSummary[] }) {
  const fileIds = useMemo(() => [...new Set(jobs.map(j => j.uploaded_file_id))], [jobs]);
  const getPlate = useFilePlates(fileIds);
  if (jobs.length === 0) {
    return <div className="tiny muted" style={{ padding: '12px 18px' }}>No jobs linked yet.</div>;
  }
  return (
    <div className="col gap-2" style={{ padding: '12px 18px' }}>
      {jobs.map(j => {
        const plate = getPlate(j.uploaded_file_id, j.plate_number);
        return (
          <div key={j.id} className="row between" style={{ padding: '10px 12px', background: 'var(--bg-2)', borderRadius: 8, border: '1px solid var(--border-1)' }}>
            <div className="row gap-3" style={{ alignItems: 'center', minWidth: 0 }}>
              <span className="mono tiny muted">#{j.id}</span>
              <div style={{ fontWeight: 500, fontSize: 13 }}>Plate {j.plate_number}</div>
              <StatusPill status={j.status as never} />
            </div>
            <span className="num tiny muted">{plate?.estimated_time ? fmtTime(plate.estimated_time) : '—'}</span>
          </div>
        );
      })}
    </div>
  );
}

function OrderAccordion({ order, expanded, onToggle, onChanged }: {
  order: ApiOrder;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ApiOrderDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const pct = Math.round(order.progress * 100);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    if (!expanded) { setDetail(null); return; }
    let alive = true;
    getOrder(order.id).then(d => { if (alive) setDetail(d); }).catch(console.error);
    return () => { alive = false; };
  }, [expanded, order.id, order.updated_at]);

  async function toggleHold(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    try { await updateOrder(order.id, { on_hold: !order.on_hold }); onChanged(); }
    finally { if (mounted.current) setBusy(false); }
  }

  async function remove(e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm(`Delete order for ${order.customer}? Linked jobs stay in the queue.`)) return;
    setBusy(true);
    try { await deleteOrder(order.id); onChanged(); }
    finally { if (mounted.current) setBusy(false); }
  }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden', borderColor: expanded ? 'var(--border-3)' : 'var(--border-1)' }}>
      <button onClick={onToggle} aria-label={`order-${order.id}`}
              style={{ width: '100%', background: 'transparent', border: 'none', color: 'inherit', textAlign: 'left', padding: '14px 18px', cursor: 'pointer', display: 'block' }}>
        <div aria-hidden="true" className="row gap-4" style={{ alignItems: 'center' }}>
          <div style={{ width: 20, color: 'var(--text-3)', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 160ms ease', display: 'inline-flex' }}>{Icons.chevR}</div>
          <div className="col" style={{ width: 130, flexShrink: 0 }}>
            <span className="mono tiny muted">#{order.id}</span>
            <span className="tiny" style={{
              padding: '1px 6px', borderRadius: 4, marginTop: 3, alignSelf: 'flex-start',
              background: order.order_type === 'internal' ? 'rgba(99,102,241,0.12)' : 'rgba(56,189,248,0.12)',
              color: order.order_type === 'internal' ? '#a5b4fc' : 'var(--info)', fontWeight: 500,
            }}>{order.order_type === 'internal' ? 'INTERNAL' : 'CUSTOMER'}</span>
          </div>
          <div className="col" style={{ flex: '1 1 0', minWidth: 0 }}>
            <div style={{ fontWeight: 500, fontSize: 14, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{order.title}</div>
            <div className="tiny muted" style={{ marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{order.customer}</div>
          </div>
          <div className="col" style={{ width: 90, flexShrink: 0 }}>
            <span className="tag-key">DUE</span>
            <span className="num small" style={{ marginTop: 2 }}>{order.due_date ? order.due_date.slice(5) : '—'}</span>
          </div>
          <div className="col" style={{ width: 140, flexShrink: 0 }}>
            <div className="row between">
              <span className="tag-key">JOBS</span>
              <span className="num tiny" style={{ color: pct === 100 ? 'var(--ok)' : 'var(--text-2)' }}>{pct}%</span>
            </div>
            <div style={{ marginTop: 6 }}><Progress value={pct} /></div>
          </div>
          <div style={{ width: 110, flexShrink: 0, display: 'flex', justifyContent: 'flex-end' }}>
            <StatusPill status={order.status} />
          </div>
        </div>
      </button>

      {expanded && (
        <div style={{ borderTop: '1px solid var(--border-1)', background: 'var(--bg-1)' }}>
          <div className="row between" style={{ padding: '12px 18px', alignItems: 'center' }}>
            <div className="tiny muted">{order.notes || `${order.job_count} job${order.job_count === 1 ? '' : 's'} linked`}</div>
            <div className="row gap-2">
              <button className="btn sm" disabled={busy} onClick={toggleHold}>{order.on_hold ? 'Release hold' : 'Hold'}</button>
              <button className="btn sm" onClick={(e) => { e.stopPropagation(); navigate(`/orders/${order.id}/edit`); }}>{Icons.copy} Edit</button>
              <button className="btn ghost sm" disabled={busy} style={{ color: 'var(--err)' }} onClick={remove}>{Icons.trash} Delete</button>
            </div>
          </div>
          <div style={{ padding: '0 0 8px' }}>
            <div style={{ padding: '0 18px 6px' }}><span className="tag-key">Parts · {order.parts.length}</span></div>
            <PartsTable order={order} />
          </div>
          <div style={{ borderTop: '1px solid var(--border-1)' }}>
            <div style={{ padding: '12px 18px 4px' }}><span className="tag-key">Jobs filling this order</span></div>
            <JobsFilling jobs={detail?.jobs ?? []} />
          </div>
        </div>
      )}
    </div>
  );
}

export function OrdersScreen() {
  const { orders, refetch } = useOrders();
  const [expanded, setExpanded] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>('open');

  const filtered = useMemo(() => orders.filter(o => {
    if (filter === 'open') return o.status !== 'complete';
    if (filter === 'customer') return o.order_type === 'customer';
    if (filter === 'internal') return o.order_type === 'internal';
    return true;
  }), [orders, filter]);

  return (
    <div className="col gap-4" style={{ maxWidth: 1200 }}>
      <div className="row gap-2">
        {([['open', 'Open'], ['all', 'All'], ['customer', 'Customer'], ['internal', 'Internal']] as const).map(([k, l]) => (
          <button key={k} onClick={() => setFilter(k)}
                  className={`btn sm ${filter === k ? 'primary' : ''}`}
                  style={filter === k ? undefined : { background: 'transparent', borderColor: 'var(--border-1)' }}>{l}</button>
        ))}
        <div style={{ flex: 1 }} />
        <span className="tiny muted">{filtered.length} orders</span>
      </div>

      {filtered.length === 0 ? (
        <Empty title="No orders" sub="Create one from the New order button." icon={Icons.orders} />
      ) : (
        <div className="col" style={{ gap: 10 }}>
          {filtered.map(o => (
            <OrderAccordion
              key={o.id}
              order={o}
              expanded={expanded === o.id}
              onToggle={() => setExpanded(expanded === o.id ? null : o.id)}
              onChanged={refetch}
            />
          ))}
        </div>
      )}
    </div>
  );
}
