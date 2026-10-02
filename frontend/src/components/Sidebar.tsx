import { NavLink, useLocation } from 'react-router-dom';
import { Icons } from './icons';
import { LaminusStatusChip } from './LaminusStatusChip';
import { SpoolmanStatusChip } from './SpoolmanStatusChip';
import { useSpoolmanConfig } from '../api/spoolman';
import { useBuildInfo, shortSha } from '../api/version';

interface QueueCounts { active: number; pending: number; blocked: number; }

interface SidebarProps {
  queueCounts: QueueCounts;
  operatorName: string | null;
  printerCount: number;
  alarmCount?: number;
  alarmWorst?: 'info' | 'warning' | 'error' | 'fatal' | null;
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

export function Sidebar({ queueCounts, operatorName, printerCount, alarmCount = 0, alarmWorst = null, collapsed = false, onToggle = () => {} }: SidebarProps) {
  const items = [
    { to: '/queue',     label: 'Job queue',   icon: Icons.queue },
    { to: '/fleet',     label: 'Fleet',       icon: Icons.fleet },
    { to: '/wall',      label: 'Camera wall', icon: Icons.camera },
    { to: '/projects',  label: 'Projects',    icon: Icons.layers },
    { to: '/customers', label: 'Customers',   icon: Icons.user },
    { to: '/files',     label: 'Files',       icon: Icons.files },
    { to: '/printer-files', label: 'Printer files', icon: Icons.printer },
    { to: '/alarms',    label: 'Alarms',      icon: Icons.alert },
    { to: '/history',   label: 'History',     icon: Icons.clock },
    { to: '/analytics', label: 'Analytics',   icon: Icons.chart },
  ];

  const location = useLocation();
  const isSettingsRoute = location.pathname.startsWith('/settings');
  const { config: spoolmanCfg } = useSpoolmanConfig();
  const build = useBuildInfo();
  const spoolmanEnabled = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);

  const settingsSubItems = [
    { to: '/settings/tags',             label: 'Tags' },
    { to: '/settings/print',            label: 'Print defaults' },
    { to: '/settings/costs',            label: 'Costs' },
    { to: '/settings/maintenance',       label: 'Maintenance' },
    { to: '/settings/spoolman',         label: 'Spoolman' },
    ...(spoolmanEnabled ? [{ to: '/settings/spoolman-mappings', label: 'Filament Mappings' }] : []),
    { to: '/settings/webhook',          label: 'Webhooks' },
    { to: '/settings/notifications',    label: 'Notifications' },
    { to: '/settings/fleet-backup',     label: 'Fleet backup' },
    { to: '/settings/api-keys',         label: 'API Keys' },
    { to: '/settings/admin-account',    label: 'Admin account' },
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
            {it.to === '/alarms' && alarmCount > 0 && (
              <span data-testid="badge-alarms" className="count num" aria-label={`${alarmCount} unacknowledged alarm${alarmCount === 1 ? '' : 's'}`}
                    style={{ marginLeft: 'auto', background: 'rgba(239,68,68,0.12)', borderColor: 'rgba(239,68,68,0.3)',
                             color: alarmWorst === 'info' || alarmWorst === 'warning' ? 'var(--warn)' : 'var(--err)' }}>
                {alarmCount}
              </span>
            )}
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
        <SpoolmanStatusChip />
      </div>

      {!collapsed && build && (
        <div data-testid="build-info" className="muted small num" title={`Build ${build.git_sha}`}
             style={{ padding: '0 14px 6px', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          v{build.version} · {shortSha(build.git_sha)}
        </div>
      )}

      <div className="sidebar-toggle">
        <button className="btn ghost icon sm" onClick={onToggle}
                title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? Icons.chevR : Icons.chevL}
        </button>
      </div>
    </aside>
  );
}
