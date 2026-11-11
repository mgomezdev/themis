// The Project builder's links (reference URLs with optional labels).
import type { Dispatch } from 'react';
import { Icons } from '../icons';
import type { LocalLink } from '../../lib/projectDraft';
import type { BuilderAction } from '../../lib/projectBuilderState';

export function ProjectLinksEditor({ links, dispatch }: { links: LocalLink[]; dispatch: Dispatch<BuilderAction> }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
          Links ({links.length})
        </span>
        <button
          className="btn ghost sm"
          onClick={() => dispatch({ type: 'addLink' })}
        >
          + Add link
        </button>
      </div>
      {links.map(lk => (
        <div key={lk.localId} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input
            className="input"
            placeholder="https://..."
            value={lk.url}
            onChange={e => dispatch({ type: 'updateLink', localId: lk.localId, patch: { url: e.target.value } })}
            style={{ flex: 2 }}
          />
          <input
            className="input"
            placeholder="Label (optional)"
            value={lk.label}
            onChange={e => dispatch({ type: 'updateLink', localId: lk.localId, patch: { label: e.target.value } })}
            style={{ flex: 1 }}
          />
          <button
            className="btn ghost icon sm"
            onClick={() => dispatch({ type: 'remove', kind: 'link', localId: lk.localId })}
            title="Remove link"
          >
            {Icons.x}
          </button>
        </div>
      ))}
    </div>
  );
}
