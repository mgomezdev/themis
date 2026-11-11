// The Project builder's printed parts: one row per STL with quantity, filament ask and (once printing) progress.
import type { Dispatch } from 'react';
import { Icons } from '../icons';
import { FilamentAskEditor } from '../FilamentAskEditor';
import { askOf, type LocalItem } from '../../lib/projectDraft';
import type { BuilderAction } from '../../lib/projectBuilderState';
import type { ProjectItem } from '../../api/projects';
import type { InvMaterial } from '../../api/inventory';

export function ProjectItemsTable({ items, serverItems, materials, catalogEnabled, dispatch }: {
  items: LocalItem[];
  /** The loaded items as the server has them, by id (for print progress). */
  serverItems: Map<number, ProjectItem>;
  materials: InvMaterial[];
  catalogEnabled: boolean;
  dispatch: Dispatch<BuilderAction>;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
          Parts ({items.length})
        </span>
      </div>
      {items.length === 0 ? (
        <div style={{
          border: '1px dashed var(--border)', borderRadius: 6,
          padding: '24px 16px', textAlign: 'center',
          color: 'var(--text-4)', fontSize: 13,
        }}>
          Click an STL file on the left to add parts
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {/* Column headers */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1fr 64px 340px auto',
            gap: 8, padding: '0 4px',
            fontSize: 11, fontWeight: 500, color: 'var(--text-4)',
            textTransform: 'uppercase', letterSpacing: '0.05em',
          }}>
            <span>File</span>
            <span>Qty</span>
            <span>Filament</span>
            <span />
          </div>
          {items.map((it) => (
            <div key={it.localId} style={{
              display: 'grid',
              gridTemplateColumns: '1fr 64px 340px auto',
              gap: 8, alignItems: 'start',
            }}>
              {/* File name + progress */}
              <span style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, paddingTop: 4 }}>
                <span style={{
                  fontSize: 13, color: 'var(--text-1)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }} title={it.file_name}>
                  {it.file_name}
                </span>
                {it.serverId && (() => {
                  const si = serverItems.get(it.serverId!);
                  if (!si || (si.quantity_completed === 0 && si.quantity_failed === 0)) return null;
                  return (
                    <span style={{ fontSize: 11, color: si.quantity_failed > 0 ? 'var(--err)' : 'var(--text-3)', whiteSpace: 'nowrap' }}>
                      {si.quantity_completed}/{si.quantity_completed + si.quantity_failed} ok
                      {si.quantity_failed > 0 && ` · ${si.quantity_failed} failed`}
                    </span>
                  );
                })()}
              </span>

              {/* Quantity */}
              <input
                type="number"
                className="input"
                min={1} max={99}
                value={it.quantity}
                onChange={e => dispatch({ type: 'updateItem', localId: it.localId, patch: { quantity: Math.max(1, parseInt(e.target.value) || 1) } })}
                style={{ textAlign: 'center' }}
              />

              {/* Filament requirement */}
              <FilamentAskEditor
                id={`item-ask-${it.localId}`}
                value={askOf(it)}
                onChange={req => dispatch({ type: 'updateItem', localId: it.localId, patch: askOf(req) })}
                materials={materials}
                catalogEnabled={catalogEnabled}
              />

              {/* Apply to all + Remove */}
              <span style={{ display: 'flex', gap: 4 }}>
                {items.length > 1 && (
                  <button
                    className="btn ghost icon sm"
                    onClick={() => dispatch({ type: 'applyFilamentToAll', ask: askOf(it) })}
                    title="Apply this filament to every part"
                  >
                    {Icons.copy}
                  </button>
                )}
                <button
                  className="btn ghost icon sm"
                  onClick={() => dispatch({ type: 'remove', kind: 'item', localId: it.localId })}
                  title="Remove"
                >
                  {Icons.x}
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
