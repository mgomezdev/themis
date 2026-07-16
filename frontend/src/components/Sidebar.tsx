import { NavLink, useLocation } from 'react-router-dom';
import { Icons } from './icons';
import { LaminusStatusChip } from './LaminusStatusChip';
import { useSpoolmanConfig } from '../api/spoolman';

interface QueueCounts { active: number; pending: number; blocked: number; }

interface SidebarProps {
  queueCounts: QueueCounts;
  operatorName: string | null;
  printerCount: number;
  collapsed?: boolean;
  onToggle?: () => void;
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

export function Sidebar({ queueCounts, operatorName, printerCount, collapsed = false, onToggle = () => {} }: SidebarProps) {
  const items = [
    { to: '/queue',     label: 'Job queue',   icon: Icons.queue },
    { to: '/fleet',     label: 'Fleet',       icon: Icons.fleet },
    { to: '/projects',  label: 'Projects',    icon: Icons.layers },
    { to: '/files',     label: 'Files',       icon: Icons.files },
    { to: '/history',   label: 'History',     icon: Icons.clock },
  ];

  const location = useLocation();
  const isSettingsRoute = location.pathname.startsWith('/settings');
  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanEnabled = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);

  const settingsSubItems = [
    { to: '/settings/tags',             label: 'Tags' },
    { to: '/settings/print',            label: 'Print defaults' },
    { to: '/settings/spoolman',         label: 'Spoolman' },
    ...(spoolmanEnabled ? [{ to: '/settings/spoolman-mappings', label: 'Filament Mappings' }] : []),
    { to: '/settings/webhook',          label: 'Webhooks' },
    { to: '/settings/fleet-backup',     label: 'Fleet backup' },
    { to: '/settings/about',            label: 'About' },
  ];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" />
        <div className="brand-name">themis<span className="dim">.farm</span></div>
      </div>

      <div className="sidebar-user">
        {operatorName ? (
          <div className="user-chip">
            <div className="avatar">{initials(operatorName)}</div>
            <div className="user-meta">
              <div className="name">{operatorName}</div>
              <div className="sub">{printerCount} {printerCount === 1 ? 'printer' : 'printers'}</div>
            </div>
          </div>
        ) : (
          <div className="muted small" style={{ padding: '6px 8px' }}>
            {printerCount} {printerCount === 1 ? 'printer' : 'printers'}
          </div>
        )}
      </div>

      <div className="nav-section">
        <div className="nav-section-label">Workshop</div>
        {items.map(it => (
          <NavLink key={it.to} to={it.to}
                   className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            {it.icon}
            <span className="label">{it.label}</span>
            {it.to === '/queue' && <QueueBadges counts={queueCounts} />}
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
        {!collapsed && isSettingsRoute && (
          <div style={{ paddingLeft: 12 }}>
            {settingsSubItems.map(item => (
              <NavLink key={item.to} to={item.to}
                       className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                       style={{ fontSize: 12, paddingLeft: 8 }}>
                <span className="label">{item.label}</span>
              </NavLink>
            ))}
          </div>
        )}
        <LaminusStatusChip />
      </div>

      <div className="sidebar-toggle">
        <button className="btn ghost icon sm" onClick={onToggle}
                title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? Icons.chevR : Icons.chevL}
        </button>
      </div>
    </aside>
  );
}
