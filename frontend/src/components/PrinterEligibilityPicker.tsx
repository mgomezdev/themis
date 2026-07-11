import { useEffect, useState } from 'react';
import { fetchPrinters, type ApiPrinter } from '../api/printers';

interface Props {
  selected: number[];
  onChange: (ids: number[]) => void;
}

export function PrinterEligibilityPicker({ selected, onChange }: Props) {
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);

  useEffect(() => {
    fetchPrinters().then(setPrinters).catch(console.error);
  }, []);

  const eligible = printers.filter(p => p.enabled);

  function toggle(id: number) {
    onChange(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id]);
  }

  const selectedPrinters = eligible.filter(p => selected.includes(p.id));
  const minX = selectedPrinters.length ? Math.min(...selectedPrinters.map(p => p.bed_x_mm)) : null;
  const minY = selectedPrinters.length ? Math.min(...selectedPrinters.map(p => p.bed_y_mm)) : null;

  if (eligible.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--text-4)', fontStyle: 'italic' }}>
        No enabled printers configured.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {eligible.map(p => {
          const checked = selected.includes(p.id);
          return (
            <label key={p.id} style={{
              display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
              padding: '5px 8px', borderRadius: 6, fontSize: 13,
              background: checked ? 'oklch(87% 0.185 95 / 0.08)' : 'transparent',
              border: `1px solid ${checked ? 'var(--accent)' : 'var(--border)'}`,
              color: checked ? 'var(--text-1)' : 'var(--text-2)',
              userSelect: 'none',
            }}>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(p.id)}
                style={{ flexShrink: 0 }}
              />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {p.name}
              </span>
              <span style={{ fontSize: 11, color: 'var(--text-4)', flexShrink: 0 }}>
                {p.bed_x_mm}×{p.bed_y_mm} mm
              </span>
            </label>
          );
        })}
      </div>
      {minX !== null && minY !== null && (
        <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
          Pack bed: {minX}×{minY} mm (smallest selected)
        </div>
      )}
      {selectedPrinters.length === 0 && (
        <div style={{ fontSize: 11, color: 'var(--warn)' }}>
          No printers selected — jobs will be created but not dispatched.
        </div>
      )}
    </div>
  );
}
