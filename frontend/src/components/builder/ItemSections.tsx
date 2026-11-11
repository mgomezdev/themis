import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { ProjectGroup } from '../../api/projects';
import { RULES, type ItemKind, type PackRule } from '../ItemSourcePicker';
import { DRAG_ITEMS, groupColour, type BannerSelection } from './GroupBanner';

/** What the item list needs of a Builder row. */
export interface SectionItem {
  localId: string;
  file_id: number;
  file_name: string;
  kind: ItemKind;
  quantity: number;
  group_id: number | null;
}

export type BulkAction =
  | { kind: 'group'; groupId: number }
  | { kind: 'rule'; rule: PackRule }
  | { kind: 'qty'; quantity: number }
  | { kind: 'ask' }                       // the caller opens its filament editor for the selection
  | { kind: 'remove' };

const RULE_LABEL: Record<PackRule, string> = { split: 'Split', together: 'Together', solo: 'Solo', locked: 'Locked' };

/** A section header checkbox: checked when all of its rows are, "mixed" (indeterminate) when some are. */
function TriBox({ ids, selected, onChange, label }: {
  ids: string[]; selected: Set<string>; onChange: (ids: string[], on: boolean) => void; label: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const n = ids.filter(id => selected.has(id)).length;
  useEffect(() => { if (ref.current) ref.current.indeterminate = n > 0 && n < ids.length; }, [n, ids.length]);
  return <input ref={ref} type="checkbox" aria-label={label} checked={ids.length > 0 && n === ids.length}
                onChange={e => onChange(ids, e.target.checked)} onClick={e => e.stopPropagation()} />;
}

/**
 * The Builder's item list (BIZ-363): one collapsible section per source file (loose STLs together), or per group with "By group";
 * rows coloured by group and filtered by the banner selection. Rows and section headers have checkboxes; the bulk bar acts on the
 * checked rows the current filter shows. Rows drag (every checked row when the dragged one is checked) onto a banner badge.
 */
export function ItemSections({ items, groups, filter, renderRow, onBulk, emptyHint }: {
  items: SectionItem[];
  groups: ProjectGroup[];
  filter: BannerSelection;
  renderRow: (item: SectionItem) => ReactNode;
  onBulk: (ids: string[], action: BulkAction) => void;
  emptyHint: ReactNode;
}) {
  const [view, setView] = useState<'file' | 'group'>('file');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [qty, setQty] = useState('');

  const defaultId = groups.find(g => g.is_default)?.id ?? null;
  const groupOf = (it: SectionItem) => it.group_id ?? defaultId;
  const visible = useMemo(() => items.filter(it => filter === 'all' || groupOf(it) === filter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [items, filter, defaultId]);
  // Selection never outlives its rows (removed, or filtered away).
  useEffect(() => {
    const ids = new Set(visible.map(i => i.localId));
    setSelected(prev => (Array.from(prev).every(id => ids.has(id)) ? prev : new Set(Array.from(prev).filter(id => ids.has(id)))));
  }, [visible]);

  const sections = useMemo(() => {
    const out = new Map<string, { title: string; rows: SectionItem[]; groupId?: number | null }>();
    for (const it of visible) {
      const key = view === 'group' ? `g:${groupOf(it)}` : it.kind === 'stl' ? 'stl' : `f:${it.file_id}`;
      const title = view === 'group' ? (groups.find(g => g.id === groupOf(it))?.name ?? 'Default Group')
        : it.kind === 'stl' ? 'Loose STL files' : it.file_name;
      if (!out.has(key)) out.set(key, { title, rows: [], groupId: view === 'group' ? groupOf(it) : undefined });
      out.get(key)!.rows.push(it);
    }
    return Array.from(out.entries());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, view, groups]);

  const chosen = visible.filter(i => selected.has(i.localId));
  const ids = chosen.map(i => i.localId);
  const toggle = (rows: string[], on: boolean) =>
    setSelected(prev => { const next = new Set(prev); rows.forEach(id => (on ? next.add(id) : next.delete(id))); return next; });
  const rules = (Object.keys(RULE_LABEL) as PackRule[]).filter(r => chosen.some(i => RULES[i.kind].includes(r)));
  const allCollapsed = sections.length > 0 && sections.every(([k]) => collapsed.has(k));

  if (items.length === 0) return <>{emptyHint}</>;
  return (
    <div className="col gap-2" data-testid="item-sections">
      <div className="row between" style={{ flexWrap: 'wrap', gap: 8 }}>
        <label className="row gap-2 small" style={{ alignItems: 'center' }}>
          <TriBox ids={visible.map(i => i.localId)} selected={selected} onChange={toggle} label="Select every shown item" />
          {visible.length} shown
        </label>
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          <span className="tiny muted">Show</span>
          <button className={view === 'file' ? 'btn sm primary' : 'btn sm'} onClick={() => setView('file')}>By file</button>
          <button className={view === 'group' ? 'btn sm primary' : 'btn sm'} onClick={() => setView('group')}>By group</button>
          <button className="btn ghost sm" onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(sections.map(([k]) => k)))}>
            {allCollapsed ? 'Expand all' : 'Collapse all'}
          </button>
        </div>
      </div>

      {chosen.length > 0 && (
        <div className="row gap-2" role="toolbar" aria-label="Bulk edit" data-testid="bulk-bar"
             style={{ flexWrap: 'wrap', alignItems: 'center', padding: '8px 12px', borderRadius: 6,
                      border: '1px solid var(--accent)', background: 'color-mix(in oklch, var(--accent) 8%, transparent)' }}>
          <b className="small">{chosen.length} selected</b>
          <select className="select" aria-label="Move selected to group" value=""
                  onChange={e => { if (e.target.value) onBulk(ids, { kind: 'group', groupId: Number(e.target.value) }); }}>
            <option value="">Move to group…</option>
            {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
          <select className="select" aria-label="Set packing rule" value=""
                  onChange={e => { if (e.target.value) onBulk(ids, { kind: 'rule', rule: e.target.value as PackRule }); }}>
            <option value="">Packing…</option>
            {rules.map(r => <option key={r} value={r}>{RULE_LABEL[r]}</option>)}
          </select>
          <button className="btn sm" onClick={() => onBulk(ids, { kind: 'ask' })}>Set filament…</button>
          <input className="input" aria-label="Quantity for selected" style={{ width: 64 }} placeholder="qty" value={qty}
                 onChange={e => setQty(e.target.value)} />
          <button className="btn sm" disabled={!(Math.floor(Number(qty)) >= 1)}
                  onClick={() => { onBulk(ids, { kind: 'qty', quantity: Math.floor(Number(qty)) }); setQty(''); }}>Set qty</button>
          <button className="btn ghost sm" onClick={() => onBulk(ids, { kind: 'remove' })}>Remove</button>
          <button className="btn ghost sm" style={{ marginLeft: 'auto' }} onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
      )}

      {sections.map(([key, sec]) => {
        const open = !collapsed.has(key);
        const rowIds = sec.rows.map(r => r.localId);
        const parts = sec.rows.reduce((n, r) => n + r.quantity, 0);
        return (
          <div key={key} className="card" style={{ padding: 0, overflow: 'hidden' }} data-testid={`section-${key}`}>
            <div className="row gap-2" style={{ padding: '8px 12px', alignItems: 'center', cursor: 'pointer', background: 'var(--bg-1)' }}
                 onClick={() => setCollapsed(prev => { const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key); return n; })}>
              <TriBox ids={rowIds} selected={selected} onChange={toggle} label={`Select all in ${sec.title}`} />
              <span aria-hidden>{open ? '▾' : '▸'}</span>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{sec.title}</span>
              <span className="tiny muted" style={{ flex: 1 }}>
                {sec.rows.length} item{sec.rows.length === 1 ? '' : 's'} · {parts} part{parts === 1 ? '' : 's'}
              </span>
              {!open && (
                <span className="row" style={{ gap: 3 }} aria-label="Groups of its items">
                  {sec.rows.map(r => <i key={r.localId} style={{ width: 8, height: 8, borderRadius: '50%',
                                                                 background: groupColour(groups, groupOf(r)) }} />)}
                </span>
              )}
            </div>
            {open && (
              <div className="col gap-1" style={{ padding: 8 }}>
                {sec.rows.map(r => {
                  const g = groups.find(x => x.id === groupOf(r));
                  return (
                    <div key={r.localId} className="row gap-2" draggable data-testid={`row-${r.localId}`}
                         style={{ alignItems: 'start', borderLeft: `3px solid ${groupColour(groups, groupOf(r))}`, paddingLeft: 6 }}
                         onDragStart={e => e.dataTransfer.setData(DRAG_ITEMS,
                           JSON.stringify(selected.has(r.localId) ? ids : [r.localId]))}>
                      <input type="checkbox" aria-label={`Select ${r.file_name}`} checked={selected.has(r.localId)}
                             onChange={e => toggle([r.localId], e.target.checked)} style={{ marginTop: 8 }} />
                      <span aria-hidden title="Drag onto a group" style={{ cursor: 'grab', color: 'var(--text-4)', paddingTop: 6 }}>⋮⋮</span>
                      <div style={{ flex: 1, minWidth: 0 }}>{renderRow(r)}</div>
                      {r.kind !== 'gcode' && g && (
                        <span className="tiny" style={{ paddingTop: 8, whiteSpace: 'nowrap', color: groupColour(groups, g.id) }}>{g.name}</span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
