import type { ReactNode } from 'react';
import { Icon } from './icons';

interface TopbarProps {
  title: string;
  crumbs?: string[];
  actions?: ReactNode;
  search?: boolean;
}

export function Topbar({ title, crumbs = [], actions, search = true }: TopbarProps) {
  return (
    <div className="topbar">
      <div className="row gap-2" style={{ alignItems: 'center' }}>
        {crumbs.map((c, i) => <span key={i} className="crumb">{c}</span>)}
        <h1>{title}</h1>
      </div>
      <div className="spacer" />
      {search && (
        <div className="search">
          <Icon paths={["M21 21l-4.3-4.3","M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z"]} size={14} />
          <input placeholder="Search jobs, orders, parts…" />
          <span className="kbd">⌘K</span>
        </div>
      )}
      {actions}
      <button className="btn icon ghost" title="Notifications">
        <Icon paths={["M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9","M13.7 21a2 2 0 0 1-3.4 0"]} />
      </button>
    </div>
  );
}
