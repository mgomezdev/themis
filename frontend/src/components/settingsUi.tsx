import React from 'react';

// Layout helpers shared by the settings pages (core ones and plugin pages).

export function PageHeader({ title, sub, actions }: { title: string; sub?: string; actions?: React.ReactNode }) {
  return (
    <div className="row between settings-page-header" style={{ marginBottom: 18, alignItems: 'flex-start' }}>
      <div className="settings-page-title min-0">
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>{title}</h2>
        {sub && <div className="muted small" style={{ marginTop: 4 }}>{sub}</div>}
      </div>
      {actions && <div className="row gap-2 settings-page-actions">{actions}</div>}
    </div>
  );
}

export function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="settings-field-row">
      <div style={{ paddingTop: 4 }}>
        <div style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-1)' }}>{label}</div>
        {hint && <div className="tiny muted" style={{ marginTop: 4, lineHeight: 1.5, maxWidth: 480 }}>{hint}</div>}
      </div>
      <div className="min-0">{children}</div>
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`toggle-hit ${checked ? 'checked' : ''}`}>
      <span className="toggle-track"><span className="toggle-thumb" /></span>
    </button>
  );
}
