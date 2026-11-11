// The Project builder's non-printed parts (hardware, e.g. magnets/screws): name, quantity, unit cost, allocated.
import type { Dispatch } from 'react';
import { Icons } from '../icons';
import type { LocalPart } from '../../lib/projectDraft';
import type { BuilderAction } from '../../lib/projectBuilderState';

export function ProjectPartsEditor({ parts, dispatch }: { parts: LocalPart[]; dispatch: Dispatch<BuilderAction> }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
          Non-printed parts ({parts.length})
        </span>
        <button
          className="btn ghost sm"
          onClick={() => dispatch({ type: 'addPart' })}
        >
          + Add part
        </button>
      </div>
      {parts.map(pt => (
        <div key={pt.localId} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input
            className="input"
            placeholder="e.g. 3mm magnet"
            value={pt.name}
            onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { name: e.target.value } })}
            style={{ flex: 2 }}
          />
          <input
            type="number"
            className="input"
            min={1}
            value={pt.quantity}
            onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { quantity: Math.max(1, parseInt(e.target.value) || 1) } })}
            style={{ width: 64, textAlign: 'center' }}
          />
          <input
            type="number"
            className="input"
            min={0}
            step="0.01"
            placeholder="$ each"
            aria-label="Unit cost"
            value={pt.unitCost}
            onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { unitCost: e.target.value } })}
            style={{ width: 84 }}
          />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                          fontSize: 12, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={pt.allocated}
              onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { allocated: e.target.checked } })}
            />
            Allocated
          </label>
          <button
            className="btn ghost icon sm"
            onClick={() => dispatch({ type: 'remove', kind: 'part', localId: pt.localId })}
            title="Remove part"
          >
            {Icons.x}
          </button>
        </div>
      ))}
    </div>
  );
}
