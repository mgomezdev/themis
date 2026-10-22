import React from 'react';

// Layout helpers shared by the settings pages (core ones and plugin pages).

export function PageHeader({ title, sub, actions }: { title: string; sub?: string; actions?: React.ReactNode }) {
  return (
    <div className="row between" style={{ marginBottom: 18, alignItems: 'flex-start' }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>{title}</h2>
        {sub && <div className="muted small" style={{ marginTop: 4 }}>{sub}</div>}
      </div>
      {actions && <div className="row gap-2">{actions}</div>}
    </div>
  );
}

export function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr 360px', gap: 24,
      padding: '16px 0',
      borderBottom: '1px solid var(--border-1)',
      alignItems: 'flex-start',
    }}>
      <div style={{ paddingTop: 4 }}>
        <div style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-1)' }}>{label}</div>
        {hint && <div className="tiny muted" style={{ marginTop: 4, lineHeight: 1.5, maxWidth: 480 }}>{hint}</div>}
      </div>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

export function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        width: 38, height: 22, borderRadius: 999,
        background: checked ? 'var(--accent)' : 'var(--bg-3)',
        border: `1px solid ${checked ? 'var(--accent)' : 'var(--border-2)'}`,
        position: 'relative',
        cursor: 'pointer',
        boxShadow: checked ? '0 0 0 3px var(--accent-glow)' : 'none',
        transition: 'background 120ms, border-color 120ms',
        padding: 0,
        flexShrink: 0,
      }}>
      <div style={{
        position: 'absolute', top: 2, left: checked ? 18 : 2,
        width: 16, height: 16, borderRadius: '50%',
        background: 'white',
        boxShadow: '0 1px 2px rgba(0,0,0,0.3)',
        transition: 'left 120ms',
      }}/>
    </button>
  );
}
