import type { ReactNode } from 'react';

interface TopbarProps {
  title: string;
  crumbs?: string[];
  actions?: ReactNode;
}

export function Topbar({ title, crumbs = [], actions }: TopbarProps) {
  return (
    <div className="topbar">
      <div className="row gap-2" style={{ alignItems: 'center' }}>
        {crumbs.map((c, i) => <span key={i} className="crumb">{c}</span>)}
        <h1>{title}</h1>
      </div>
      <div className="spacer" />
      {actions}
    </div>
  );
}
