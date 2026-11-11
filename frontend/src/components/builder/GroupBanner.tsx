import { useState } from 'react';
import type { ProjectGroup } from '../../api/projects';

/** Group colours (BIZ-363): the Default Group is neutral, the others cycle a fixed palette by queue position. */
const PALETTE = ['#e0a526', '#4f9de0', '#c45bd6', '#3fb98a', '#e0663f', '#8a7be8', '#d6c03f', '#5bc1d6'];
export const DEFAULT_COLOUR = '#8b8f98';

export function groupColour(groups: ProjectGroup[], groupId: number | null): string {
  const g = groups.find(x => x.id === groupId);
  if (!g || g.is_default) return DEFAULT_COLOUR;
  const others = groups.filter(x => !x.is_default);
  return PALETTE[others.findIndex(x => x.id === g.id) % PALETTE.length];
}

/** `localIds` dragged from the item list travel as text/plain JSON under this type prefix. */
export const DRAG_ITEMS = 'application/x-themis-items';
const DRAG_GROUP = 'application/x-themis-group';

export type BannerSelection = 'all' | number;

/**
 * The group banner (BIZ-363) under the Builder tabs: All (optional), the Default Group, one badge per group with its item and
 * part counts, and "+ New group". Selecting a badge filters the item list. Rows dropped on a badge move there; dropped on
 * "+ New group" they go to a new group. With `reorderable`, badges drag among themselves to set queue order.
 */
export function GroupBanner({ groups, counts, selected, onSelect, showAll, onDropItems, onNewGroup, onReorder, newGroupDisabled }: {
  groups: ProjectGroup[];
  counts: Map<number | 'all', { items: number; parts: number }>;
  selected: BannerSelection;
  onSelect: (s: BannerSelection) => void;
  showAll: boolean;
  onDropItems?: (localIds: string[], groupId: number | 'new') => void;
  onNewGroup: () => void;
  onReorder?: (groupIds: number[]) => void;
  newGroupDisabled?: string;        // why "+ New group" is unavailable (e.g. a project not saved yet)
}) {
  const [over, setOver] = useState<number | 'new' | 'all' | null>(null);

  const itemsOf = (e: React.DragEvent): string[] | null => {
    const raw = e.dataTransfer.getData(DRAG_ITEMS);
    try { return raw ? (JSON.parse(raw) as string[]) : null; } catch { return null; }
  };
  const dropTarget = (target: number | 'new') => ({
    onDragOver: (e: React.DragEvent) => {
      const types = Array.from(e.dataTransfer.types ?? []);
      if (types.includes(DRAG_ITEMS) || (typeof target === 'number' && types.includes(DRAG_GROUP))) {
        e.preventDefault();
        setOver(target);
      }
    },
    onDragLeave: () => setOver(null),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      setOver(null);
      const ids = itemsOf(e);
      if (ids && ids.length && onDropItems) { onDropItems(ids, target); return; }
      const moved = Number(e.dataTransfer.getData(DRAG_GROUP));
      if (onReorder && moved && typeof target === 'number' && moved !== target) {
        const ids = groups.map(g => g.id);
        const forward = ids.indexOf(moved) < ids.indexOf(target);
        const order = ids.filter(id => id !== moved);
        order.splice(order.indexOf(target) + (forward ? 1 : 0), 0, moved);    // dropped on a later badge: after it
        onReorder(order);
      }
    },
  });
  const count = (key: number | 'all') => {
    const c = counts.get(key) ?? { items: 0, parts: 0 };
    return `${c.items} item${c.items === 1 ? '' : 's'}${c.parts && key !== 'all' ? ` · ${c.parts} part${c.parts === 1 ? '' : 's'}` : ''}`;
  };
  const badge = (sel: boolean, colour: string, hot: boolean) => ({
    padding: '6px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 13, display: 'inline-flex', alignItems: 'center', gap: 6,
    border: `1px ${hot ? 'dashed' : 'solid'} ${sel || hot ? colour : 'var(--border-1)'}`,
    background: sel ? `${colour}22` : 'var(--bg-0)', color: 'var(--text-1)',
  });

  return (
    <div className="card" style={{ padding: '10px 12px' }} data-testid="group-banner">
      <div className="row between" style={{ marginBottom: 8 }}>
        <span className="label" style={{ margin: 0 }}>{onDropItems ? 'Groups · drag items onto a badge' : 'Groups'}</span>
        <span className="tiny muted">{onReorder ? 'Drag badges to set queue order' : 'Select a badge to show its items'}</span>
      </div>
      <div className="row gap-2 wrap" role="tablist" aria-label="Print groups">
        {showAll && (
          <button type="button" role="tab" aria-selected={selected === 'all'} style={badge(selected === 'all', 'var(--accent)', false)}
                  onClick={() => onSelect('all')}>
            All <span className="tiny muted">{count('all')}</span>
          </button>
        )}
        {groups.map(g => {
          const colour = groupColour(groups, g.id);
          return (
            <button key={g.id} type="button" role="tab" aria-selected={selected === g.id} data-testid={`badge-${g.id}`}
                    style={badge(selected === g.id, colour, over === g.id)} onClick={() => onSelect(g.id)}
                    draggable={!!onReorder}
                    onDragStart={onReorder ? e => e.dataTransfer.setData(DRAG_GROUP, String(g.id)) : undefined}
                    {...dropTarget(g.id)}>
              <span style={{ width: 10, height: 10, borderRadius: 3, background: colour }} />
              {g.name} <span className="tiny muted">{count(g.id)}</span>
            </button>
          );
        })}
        <button type="button" className="btn ghost sm" data-testid="badge-new" disabled={!!newGroupDisabled} title={newGroupDisabled}
                style={over === 'new' ? { outline: '1px dashed var(--accent)' } : undefined}
                onClick={onNewGroup} {...(newGroupDisabled ? {} : dropTarget('new'))}>
          + New group
        </button>
      </div>
    </div>
  );
}
