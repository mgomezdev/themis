// One item row of the Builder (Items tab): file + source + print progress, quantity, filament ask, actions.
import type { Dispatch } from 'react';
import { Icons } from '../icons';
import { FilamentAskEditor } from '../FilamentAskEditor';
import { ItemSourcePicker } from '../ItemSourcePicker';
import { askOf, type LocalItem } from '../../lib/projectDraft';
import type { BuilderAction } from '../../lib/projectBuilderState';
import type { ProjectItem } from '../../api/projects';
import type { InvMaterial } from '../../api/inventory';
import type { LibraryFile } from '../../data/types';

export function ItemRow({ item: it, file, serverItem, materials, catalogEnabled, canApplyToAll, dispatch }: {
  item: LocalItem;
  /** The item's library file, when loaded (its kind and plate count drive the source picker). */
  file: LibraryFile | undefined;
  /** The saved item as the server has it (print progress); undefined for a new row. */
  serverItem: ProjectItem | undefined;
  materials: InvMaterial[];
  catalogEnabled: boolean;
  /** Offer "Apply this filament to every part" (there is more than one item). */
  canApplyToAll: boolean;
  dispatch: Dispatch<BuilderAction>;
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 64px 340px auto', gap: 8, alignItems: 'start' }}>
      <span style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, paddingTop: 4 }}>
        <span style={{ fontSize: 13, color: 'var(--text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={it.file_name}>
          {it.file_name}
        </span>
        <ItemSourcePicker fileId={it.file_id} fileKind={file?.kind ?? (it.kind === 'stl' ? 'stl' : '3mf')}
                          plateCount={file?.plate_count ?? 1}
                          kind={it.kind} sourceRef={it.source_ref} packRule={it.pack_rule} fixedSource={!!it.serverId}
                          onChange={v => dispatch({ type: 'updateItem', localId: it.localId, patch: v })} />
        {it.serverId && (() => {
          const si = serverItem;
          if (!si || (si.quantity_completed === 0 && si.quantity_failed === 0)) return null;
          return (
            <span style={{ fontSize: 11, color: si.quantity_failed > 0 ? 'var(--err)' : 'var(--text-3)', whiteSpace: 'nowrap' }}>
              {si.quantity_completed}/{si.quantity_completed + si.quantity_failed} ok
              {si.quantity_failed > 0 && ` · ${si.quantity_failed} failed`}
            </span>
          );
        })()}
      </span>
      <input
        type="number"
        className="input"
        aria-label={`Quantity of ${it.file_name}`}
        min={1} max={99}
        value={it.quantity}
        onChange={e => dispatch({ type: 'updateItem', localId: it.localId, patch: { quantity: Math.max(1, parseInt(e.target.value) || 1) } })}
        style={{ textAlign: 'center' }}
      />
      <FilamentAskEditor
        id={`item-ask-${it.localId}`}
        value={askOf(it)}
        onChange={req => dispatch({ type: 'updateItem', localId: it.localId, patch: askOf(req) })}
        materials={materials}
        catalogEnabled={catalogEnabled}
      />
      <span style={{ display: 'flex', gap: 4 }}>
        {canApplyToAll && (
          <button className="btn ghost icon sm" onClick={() => dispatch({ type: 'applyFilamentToAll', ask: askOf(it) })}
                  title="Apply this filament to every part">
            {Icons.copy}
          </button>
        )}
        <button className="btn ghost icon sm" onClick={() => dispatch({ type: 'removeItems', localIds: [it.localId] })} title="Remove">
          {Icons.x}
        </button>
      </span>
    </div>
  );
}
