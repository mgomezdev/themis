import { useCallback, useEffect, useState } from 'react';
import {
  createProjectGroup, deleteProjectGroup, listProjectGroups, orderProjectGroups, patchProjectGroup, updateProjectItem,
  type ProjectGroup, type ProjectGroupPatch, type ProjectItem,
} from '../api/projects';
import { getPrinterProfiles } from '../api/queue';
import type { ApiPrinter } from '../api/printers';
import { isModelKey, modelLabelOf, printersOfKey, ANY_KEY } from './ModelTargets';
import { PrinterTargetPicker, toggleKey, useEnabledPrinters } from './PrinterTargetPicker';

/**
 * A project's print groups (BIZ-360), in queue order: each group's eligible printers (one badge per target) with a process
 * profile per target ("Printer default" = each printer's own, BIZ-358), the bed envelope it packs for, and its items.
 * Every change is saved straight away; `onChanged` lets the page refresh what depends on it.
 */
export function PrintGroupsCard({ projectId, items, onChanged, groupId, refreshKey }: {
  projectId: number;
  items: ProjectItem[];
  onChanged?: () => void;
  /** BIZ-363 Builder Groups tab: edit only this group (its items and the group list are managed by the banner). */
  groupId?: number;
  refreshKey?: unknown;
}) {
  const printers = useEnabledPrinters();
  const [groups, setGroups] = useState<ProjectGroup[]>([]);
  const [newName, setNewName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listProjectGroups(projectId).then(gs => setGroups(Array.isArray(gs) ? gs : [])).catch(e => setError(String(e)));
  }, [projectId]);
  useEffect(load, [load, refreshKey]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); load(); onChanged?.(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); load(); }   // re-sync after a refusal
  }
  const patch = (g: ProjectGroup, body: ProjectGroupPatch) => run(() => patchProjectGroup(projectId, g.id, body));
  const move = (index: number, by: number) => {
    const ids = groups.map(g => g.id);
    const [id] = ids.splice(index, 1);
    ids.splice(index + by, 0, id);
    return run(() => orderProjectGroups(projectId, ids));
  };

  if (groupId !== undefined) {
    const i = groups.findIndex(g => g.id === groupId);
    if (i < 0) return null;
    const g = groups[i];
    return (
      <div data-testid="print-groups">
        {error && <div style={{ color: 'var(--err)', fontSize: 13, marginBottom: 8 }}>{error}</div>}
        <GroupRow group={g} index={i} count={groups.length} groups={groups} printers={printers} items={[]} scoped
                  onPatch={body => patch(g, body)} onMove={by => move(i, by)}
                  onDelete={() => run(() => deleteProjectGroup(projectId, g.id))}
                  onMoveItem={() => {}} />
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 20 }} data-testid="print-groups">
      <div className="row between" style={{ marginBottom: 12, alignItems: 'baseline' }}>
        <div style={{ fontSize: 15, fontWeight: 600 }}>Print groups</div>
        <div className="tiny muted">Jobs queue in group order.</div>
      </div>
      {error && <div style={{ color: 'var(--err)', fontSize: 13, marginBottom: 8 }}>{error}</div>}
      <div className="col gap-3">
        {groups.map((g, i) => (
          <GroupRow key={g.id} group={g} index={i} count={groups.length} groups={groups} printers={printers}
                    items={items.filter(it => it.group_id === g.id
                                              || (g.is_default && !groups.some(x => x.id === it.group_id)))}   // ungrouped → Default
                    onPatch={body => patch(g, body)}
                    onMove={by => move(i, by)}
                    onDelete={() => run(() => deleteProjectGroup(projectId, g.id))}
                    onMoveItem={(itemId, groupId) => run(() => updateProjectItem(projectId, itemId, { group_id: groupId }))} />
        ))}
      </div>
      <div className="row gap-2" style={{ marginTop: 12 }}>
        <input className="input" placeholder="New group name" value={newName} onChange={e => setNewName(e.target.value)}
               style={{ maxWidth: 240 }} />
        <button className="btn sm" disabled={!newName.trim()}
                onClick={() => run(async () => { await createProjectGroup(projectId, newName.trim()); setNewName(''); })}>
          + New group
        </button>
      </div>
    </div>
  );
}

function GroupRow({ group, index, count, groups, printers, items, onPatch, onMove, onDelete, onMoveItem, scoped }: {
  group: ProjectGroup; index: number; count: number; groups: ProjectGroup[]; printers: ApiPrinter[]; items: ProjectItem[];
  scoped?: boolean;     // the Builder's Groups tab: the banner shows the items and sets the order
  onPatch: (body: ProjectGroupPatch) => void; onMove: (by: number) => void; onDelete: () => void;
  onMoveItem: (itemId: number, groupId: number) => void;
}) {
  const [name, setName] = useState(group.name);
  const [env, setEnv] = useState({ x: group.envelope_x?.toString() ?? '', y: group.envelope_y?.toString() ?? '' });
  useEffect(() => { setName(group.name); setEnv({ x: group.envelope_x?.toString() ?? '', y: group.envelope_y?.toString() ?? '' }); },
            [group.name, group.envelope_x, group.envelope_y]);
  // BIZ-362: a pool packs each claimed plate for the claiming printer's own bed, so it has no envelope.
  const pool = group.strategy === 'pool';
  const [maxParts, setMaxParts] = useState(group.max_parts?.toString() ?? '');
  useEffect(() => { setMaxParts(group.max_parts?.toString() ?? ''); }, [group.max_parts]);
  const saveMaxParts = () => {
    const n = maxParts.trim() === '' ? null : Math.floor(Number(maxParts));
    if (n !== null && !(n >= 1)) { setMaxParts(group.max_parts?.toString() ?? ''); return; }
    if (n !== group.max_parts) onPatch({ max_parts: n });
  };
  const keys = group.targets.map(t => t.target_key);
  const auto = group.envelope_x == null;
  // What "auto" packs for now: the smallest bed among the printers the targets cover (the custom envelope starts from it).
  const covered = keys.flatMap(k => printersOfKey(k, printers));
  const autoBed = covered.length ? { x: Math.min(...covered.map(p => p.bed_x_mm)), y: Math.min(...covered.map(p => p.bed_y_mm)) }
                                 : { x: 256, y: 256 };

  const label = (key: string) => (key === ANY_KEY ? 'Any printer' : isModelKey(key) ? `Any ${modelLabelOf(key, printers)}`
    : printers.find(p => String(p.id) === key)?.name ?? `Printer ${key}`);
  const setTargets = (next: string[]) => onPatch({
    targets: next.map(k => ({ target_key: k, process_profile: group.targets.find(t => t.target_key === k)?.process_profile ?? '' })),
  });
  const saveEnvelope = () => {
    const x = Number(env.x), y = Number(env.y);
    if (x > 0 && y > 0 && (x !== group.envelope_x || y !== group.envelope_y)) onPatch({ envelope_x: x, envelope_y: y });
  };

  return (
    <div className="card" style={{ padding: 12, background: 'var(--bg-0)' }} data-testid={`group-${group.id}`}>
      <div className="row gap-2" style={{ alignItems: 'center', marginBottom: 10 }}>
        <input className="input" aria-label="Group name" value={name} onChange={e => setName(e.target.value)}
               onBlur={() => { if (name.trim() && name !== group.name) onPatch({ name: name.trim() }); }}
               style={{ maxWidth: 260, fontWeight: 600 }} />
        {group.is_default && <span className="pill idle">Default</span>}
        <span className="tiny muted grow">{scoped ? '' : `${items.length} item${items.length === 1 ? '' : 's'}`}</span>
        {!scoped && <>
          <button className="btn ghost sm" aria-label={`Move ${group.name} up`} disabled={index === 0} onClick={() => onMove(-1)}>↑</button>
          <button className="btn ghost sm" aria-label={`Move ${group.name} down`} disabled={index === count - 1} onClick={() => onMove(1)}>↓</button>
        </>}
        {!group.is_default && <button className="btn ghost sm" onClick={onDelete}>Delete</button>}
      </div>

      <div className="label" style={{ marginBottom: 6 }}>Eligible printers</div>
      <PrinterTargetPicker printers={printers} selected={keys} onToggle={k => setTargets(toggleKey(keys, k))} />
      {group.targets.length > 0 && (
        <div className="col gap-2" style={{ marginTop: 10 }}>
          {group.targets.map(t => (
            <TargetProcess key={t.target_key} label={label(t.target_key)} value={t.process_profile}
                           printerIds={printersOfKey(t.target_key, printers).map(p => p.id)}
                           onChange={v => onPatch({ targets: group.targets.map(x => (x.target_key === t.target_key
                             ? { ...x, process_profile: v } : x)) })} />
          ))}
        </div>
      )}

      <div className="row gap-2" style={{ alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
        <span className="label" style={{ margin: 0 }}>Pack</span>
        <select className="select" aria-label={`Packing for ${group.name}`} value={pool ? 'pool' : 'fixed'}
                onChange={e => onPatch({ strategy: e.target.value as 'fixed' | 'pool' })} style={{ maxWidth: 260 }}>
          <option value="fixed">Now, for one bed size</option>
          <option value="pool">When a printer picks it up</option>
        </select>
        {pool && (
          <>
            <span className="small">at most</span>
            <input className="input" aria-label="Most parts per plate" style={{ width: 70 }} placeholder="any" value={maxParts}
                   onChange={e => setMaxParts(e.target.value)} onBlur={saveMaxParts} />
            <span className="tiny muted">parts per plate (blank = as many as fit)</span>
          </>
        )}
      </div>
      {pool && group.pool && (
        <div className="tiny muted" style={{ marginTop: 6 }} data-testid={`pool-${group.id}`}>
          {group.pool.done} done · {group.pool.printing} printing · {group.pool.left} left
        </div>
      )}
      {pool && group.pool_warning && (
        <div className="tiny" role="alert" style={{ color: 'var(--warn)', marginTop: 4 }}>{group.pool_warning}</div>
      )}

      {!pool && (
      <div className="row gap-2" style={{ alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
        <span className="label" style={{ margin: 0 }}>Pack for</span>
        <label className="row gap-1 small">
          <input type="checkbox" checked={auto}
                 onChange={e => onPatch(e.target.checked ? { envelope_x: null, envelope_y: null }
                                                         : { envelope_x: autoBed.x, envelope_y: autoBed.y })} />
          the smallest selected bed{auto && covered.length > 0 && ` (${autoBed.x} × ${autoBed.y} mm)`}
        </label>
        {!auto && (
          <>
            <input className="input" aria-label="Envelope X (mm)" style={{ width: 80 }} value={env.x}
                   onChange={e => setEnv({ ...env, x: e.target.value })} onBlur={saveEnvelope} />
            <span>×</span>
            <input className="input" aria-label="Envelope Y (mm)" style={{ width: 80 }} value={env.y}
                   onChange={e => setEnv({ ...env, y: e.target.value })} onBlur={saveEnvelope} />
            <span className="tiny muted">mm (printers with a smaller bed are left out)</span>
          </>
        )}
      </div>
      )}

      {items.length > 0 && (
        <div className="col gap-1" style={{ marginTop: 10 }}>
          {items.map(it => (
            <div key={it.id} className="row gap-2 small">
              <span className="grow">{it.file_name} × {it.quantity}</span>
              {groups.length > 1 && (
                <select className="select" aria-label={`Group for ${it.file_name}`} value={group.id}
                        onChange={e => onMoveItem(it.id, Number(e.target.value))} style={{ maxWidth: 200 }}>
                  {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
                </select>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** A target's process profile: "Printer default", or a preset every printer the target covers supports. */
function TargetProcess({ label, value, printerIds, onChange }: {
  label: string; value: string; printerIds: number[]; onChange: (v: string) => void;
}) {
  const [profiles, setProfiles] = useState<string[]>([]);
  const key = [...printerIds].sort((a, b) => a - b).join(',');
  useEffect(() => {
    let alive = true;
    Promise.all(printerIds.map(id => getPrinterProfiles(id).catch(() => ({ print_profiles: [], filament_profiles: [] }))))
      .then(rs => {
        if (!alive) return;
        const sets = rs.map(r => new Set(r.print_profiles));
        setProfiles(sets.length ? [...sets[0]].filter(n => sets.every(s => s.has(n))).sort() : []);
      });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return (
    <label className="row gap-2 small">
      <span style={{ minWidth: 160 }}>{label}</span>
      <select className="select" aria-label={`Process profile for ${label}`} value={value} onChange={e => onChange(e.target.value)}>
        <option value="">Printer default</option>
        {value && !profiles.includes(value) && <option value={value}>{value}</option>}
        {profiles.map(p => <option key={p} value={p}>{p}</option>)}
      </select>
    </label>
  );
}
