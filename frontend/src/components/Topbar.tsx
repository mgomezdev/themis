import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { Crumb } from './topbarOverride';

interface TopbarProps {
  title: string;
  crumbs?: Crumb[];
  actions?: ReactNode;
}

export function Topbar({ title, crumbs = [], actions }: TopbarProps) {
  return (
    <div className="topbar">
      <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
        {crumbs.map((c, i) => typeof c === 'string'
          ? <span key={i} className="crumb">{c}</span>
          : <Link key={i} to={c.to} className="crumb">{c.label}</Link>)}
        <h1 style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</h1>
      </div>
      <div className="spacer" />
      {actions}
    </div>
  );
}
