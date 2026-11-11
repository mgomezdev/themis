// New job: small step, tab and toggle widgets, and the project picker.
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Icons } from '../../components/icons';
import { useProjects } from '../../api/projects';

export function StepNum({ n, done }: { n: number; done: boolean }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 20, height: 20,
      background: done ? 'var(--accent)' : 'var(--bg-2)',
      color: done ? 'white' : 'var(--text-3)',
      border: `1px solid ${done ? 'var(--accent)' : 'var(--border-1)'}`,
      borderRadius: 6,
      fontSize: 11, fontWeight: 600, marginRight: 10,
      fontFamily: 'var(--font-mono)',
      verticalAlign: 'middle',
    }}>
      {done ? React.cloneElement(Icons.check as React.ReactElement<{ size?: number }>, { size: 12 }) : n}
    </span>
  );
}

export function SummaryDot({ done }: { done: boolean }) {
  return (
    <span style={{
      width: 16, height: 16, flexShrink: 0,
      borderRadius: '50%',
      background: done ? 'var(--accent)' : 'var(--bg-2)',
      border: `1px solid ${done ? 'var(--accent)' : 'var(--border-1)'}`,
      display: 'inline-grid', placeItems: 'center', marginTop: 1,
    }}>
      {done && (
        <span style={{ color: 'white', display: 'inline-flex' }}>
          {React.cloneElement(Icons.check as React.ReactElement<{ size?: number; stroke?: number }>, { size: 10, stroke: 3 })}
        </span>
      )}
    </span>
  );
}

export function TabDot({ queued, complete }: { queued: boolean; complete: boolean }) {
  if (!queued) {
    return (
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        border: '1px solid var(--border-2)',
        background: 'transparent',
        flexShrink: 0,
      }} />
    );
  }
  const color = complete ? 'var(--ok)' : 'var(--warn)';
  return (
    <span style={{
      width: 8, height: 8, borderRadius: '50%',
      background: color,
      boxShadow: complete ? `0 0 6px ${color}` : 'none',
      flexShrink: 0,
    }} />
  );
}

// ============================================================
// Dropzone
// ============================================================

export function QueueToggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      role="switch"
      aria-checked={checked}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        background: 'transparent', border: 'none',
        padding: '4px 6px', cursor: 'pointer',
        color: 'var(--text-1)', fontFamily: 'inherit',
        flexShrink: 0,
      }}>
      <div className="col" style={{ alignItems: 'flex-end', lineHeight: 1.2 }}>
        <span style={{ fontSize: 12.5, fontWeight: 500, color: checked ? 'var(--text-1)' : 'var(--text-3)' }}>
          {checked ? 'Send to queue' : 'Skip this plate'}
        </span>
        <span className="tiny muted" style={{ marginTop: 2 }}>
          {checked ? 'Creates one job' : 'No job created'}
        </span>
      </div>
      <div style={{
        width: 36, height: 20, borderRadius: 999,
        background: checked ? 'var(--accent)' : 'var(--bg-2)',
        border: `1px solid ${checked ? 'var(--accent)' : 'var(--border-2)'}`,
        position: 'relative',
        transition: 'background 120ms, border-color 120ms',
        flexShrink: 0,
        boxShadow: checked ? '0 0 0 3px var(--accent-glow)' : 'none',
      }}>
        <div style={{
          width: 14, height: 14, borderRadius: '50%',
          background: 'white',
          position: 'absolute', top: 2,
          left: checked ? 18 : 2,
          transition: 'left 120ms',
          boxShadow: '0 1px 2px rgba(0,0,0,0.25)',
        }} />
      </div>
    </button>
  );
}

// ============================================================
// ProjectPicker — single-select from the projects jobs can be added to
// ============================================================

export function ProjectPicker({ selectedProjectId, onChange }: {
  selectedProjectId: number | null;
  onChange: (id: number | null) => void;
}) {
  const navigate = useNavigate();
  const { projects } = useProjects();
  // A draft project cannot have jobs yet; a finished one stays linkable on purpose (retro-linking costs).
  const open = projects.filter(p => p.stage !== 'draft');

  return (
    <div className="col gap-2">
      <div className="row gap-2 wrap">
        {open.map(p => {
          const selected = selectedProjectId === p.id;
          return (
            <button key={p.id} onClick={() => onChange(selected ? null : p.id)}
              title={p.stage === 'queued' ? undefined : `Jobs start once ${p.name} is moved to Queued`}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 12px',
                background: selected ? 'var(--bg-2)' : 'var(--bg-0)',
                border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-1)'}`,
                boxShadow: selected ? '0 0 0 1px var(--accent)' : 'none',
                borderRadius: 999, cursor: 'pointer', color: 'var(--text-1)', fontFamily: 'inherit', fontSize: 12,
              }}>
              <span className="mono tiny" style={{ color: 'var(--text-3)' }}>#{p.id}</span>
              <span style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
              {p.stage !== 'queued' && <span className="tiny muted">{p.stage}</span>}
            </button>
          );
        })}
        <button onClick={() => navigate('/projects/new')}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                   background: 'transparent', border: '1px dashed var(--border-2)', borderRadius: 999,
                   color: 'var(--text-3)', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12 }}>
          {Icons.plus} New project
        </button>
        {selectedProjectId != null && (
          <button onClick={() => onChange(null)}
            style={{ padding: '6px 10px', background: 'transparent', border: 'none', color: 'var(--text-3)', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12 }}>
            None — standalone job
          </button>
        )}
      </div>
    </div>
  );
}

// ============================================================
// PlateThumbnail — shared image for a plate across all screens
// ============================================================
