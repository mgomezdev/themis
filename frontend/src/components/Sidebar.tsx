import { NavLink } from 'react-router-dom';
import { Icons } from './icons';

interface SidebarProps {
  queueCount: number;
  ordersOpen: number;
}

export function Sidebar({ queueCount, ordersOpen }: SidebarProps) {
  const items = [
    { to: '/queue',     label: 'Job queue',   icon: Icons.queue,    count: queueCount },
    { to: '/fleet',     label: 'Fleet',       icon: Icons.fleet },
    { to: '/orders',    label: 'Orders',      icon: Icons.orders,   count: ordersOpen },
    { to: '/files',     label: 'Files',       icon: Icons.files },
    { to: '/filaments', label: 'Filaments',   icon: Icons.spool },
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
            {it.count != null && it.count > 0 && <span className="count num">{it.count}</span>}
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
        <div className="user-chip">
          <div className="avatar">LR</div>
          <div className="user-meta">
            <div className="name">Lev Romero</div>
            <div className="sub">Workshop · 3 printers</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
