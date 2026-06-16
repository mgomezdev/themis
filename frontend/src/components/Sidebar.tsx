import { NavLink } from 'react-router-dom';
import { Icons } from './icons';

interface QueueCounts { active: number; pending: number; blocked: number; }

interface SidebarProps {
  queueCounts: QueueCounts;
  ordersOpen: number;
  operatorName: string | null;
  printerCount: number;
}

function initials(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() ?? '').join('');
}

function QueueBadges({ counts }: { counts: QueueCounts }) {
  const { active, pending, blocked } = counts;
  if (active === 0 && pending === 0 && blocked === 0) return null;
  return (
    <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
      {active > 0 && (
        <span data-testid="badge-active" className="count num"
              style={{ marginLeft: 0, background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'rgba(34,197,94,0.25)' }}>
          {active}
        </span>
      )}
      {pending > 0 && (
        <span data-testid="badge-pending" className="count num" style={{ marginLeft: 0 }}>
          {pending}
        </span>
      )}
      {blocked > 0 && (
        <span data-testid="badge-blocked" className="count num"
              style={{ marginLeft: 0, background: 'rgba(239,68,68,0.12)', color: 'var(--err)', borderColor: 'rgba(239,68,68,0.3)' }}>
          {blocked}
        </span>
      )}
    </div>
  );
}

export function Sidebar({ queueCounts, ordersOpen, operatorName, printerCount }: SidebarProps) {
  const items = [
    { to: '/queue',     label: 'Job queue',   icon: Icons.queue },
    { to: '/fleet',     label: 'Fleet',       icon: Icons.fleet },
    { to: '/orders',    label: 'Orders',      icon: Icons.orders,   count: ordersOpen },
    { to: '/files',     label: 'Files',       icon: Icons.files },
  ];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" />
        <div className="brand-name">themis<span className="dim">.farm</span></div>
      </div>

      <div className="nav-section">
        <div className="nav-section-label">Workshop</div>
        {items.map(it => (
          <NavLink key={it.to} to={it.to}
                   className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            {it.icon}
            <span className="label">{it.label}</span>
            {it.to === '/queue'
              ? <QueueBadges counts={queueCounts} />
              : it.count != null && it.count > 0 && <span className="count num">{it.count}</span>
            }
          </NavLink>
        ))}
      </div>

      <div className="nav-section">
        <div className="nav-section-label">Account</div>
        <NavLink to="/settings"
                 className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          {Icons.settings}
          <span className="label">Settings</span>
        </NavLink>
      </div>

      <div className="footer">
        {operatorName && (
          <div className="user-chip">
            <div className="avatar">{initials(operatorName)}</div>
            <div className="user-meta">
              <div className="name">{operatorName}</div>
            </div>
          </div>
        )}
        <div className="muted small">{printerCount} {printerCount === 1 ? 'printer' : 'printers'}</div>
      </div>
    </aside>
  );
}
