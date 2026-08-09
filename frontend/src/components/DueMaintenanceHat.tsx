// frontend/src/components/DueMaintenanceHat.tsx
import { useState } from 'react';

export function DueMaintenanceHat({ dueItemNames }: { dueItemNames: string[] }) {
  const [open, setOpen] = useState(false);
  if (dueItemNames.length === 0) return null;

  const label = dueItemNames.join(', ');

  return (
    <span
      style={{ position: 'relative', display: 'inline-flex', cursor: 'pointer', lineHeight: 1 }}
      title={label}
      onClick={e => { e.stopPropagation(); setOpen(v => !v); }}
    >
      👷
      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 20,
          background: 'var(--bg-3)', border: '1px solid var(--border-2)', color: 'var(--text-1)',
          padding: '6px 10px', borderRadius: 0, fontSize: 12, whiteSpace: 'nowrap',
          boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
        }}>
          {label}
        </div>
      )}
    </span>
  );
}
