import React from 'react';
import { Icons } from './icons';

interface TopbarProps {
  title: string;
  crumbs?: string[];
  actions?: React.ReactNode;
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
          {React.cloneElement(Icons.search as React.ReactElement, { size: 14 })}
          <input placeholder="Search jobs, orders, parts…" />
          <span className="kbd">⌘K</span>
        </div>
      )}
      {actions}
      <button className="btn icon ghost" title="Notifications">{Icons.bell}</button>
    </div>
  );
}
