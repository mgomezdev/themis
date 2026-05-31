import React, { useState, useMemo, useEffect } from 'react';
import { getSpoolmanConfig, saveSpoolmanConfig, testSpoolmanConnection, useSpools } from '../api/spoolman';
import { getQueueConfig, saveQueueConfig } from '../api/queue';
import { rescanProfiles } from '../api/printers';
import { TAGS } from '../data/mock';
import { Icons, Icon } from '../components/icons';
import type { Tag } from '../data/types';

// =========================================================================
// Local icons not in the main Icons set
// =========================================================================

const SettingsIcons = {
  tag:      <Icon paths={["M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 12.9V3h9.9l7.7 7.7a2 2 0 0 1 0 2.7z","M7 7h.01"]} />,
  backup:   <Icon paths={["M21 12v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6","M21 12a9 9 0 0 0-15.36-6.36L3 8","M3 4v4h4","M12 8v8","M9 13l3 3 3-3"]} />,
  info:     <Icon paths={["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z","M12 16v-4","M12 8h.01"]} />,
  spoolman: <Icon paths={["M5 5h14","M5 19h14","M5 5v14","M19 5v14","M9 8h6","M9 16h6","M9 8v8","M15 8v8"]} />,
};

// =========================================================================
// Shared layout helpers
// =========================================================================

function PageHeader({ title, sub, actions }: { title: string; sub?: string; actions?: React.ReactNode }) {
  return (
    <div className="row between" style={{ marginBottom: 18, alignItems: 'flex-start' }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>{title}</h2>
        {sub && <div className="muted small" style={{ marginTop: 4 }}>{sub}</div>}
      </div>
      {actions && <div className="row gap-2">{actions}</div>}
    </div>
  );
}

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr 360px', gap: 24,
      padding: '16px 0',
      borderBottom: '1px solid var(--border-1)',
      alignItems: 'flex-start',
    }}>
      <div style={{ paddingTop: 4 }}>
        <div style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-1)' }}>{label}</div>
        {hint && <div className="tiny muted" style={{ marginTop: 4, lineHeight: 1.5, maxWidth: 480 }}>{hint}</div>}
      </div>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        width: 38, height: 22, borderRadius: 999,
        background: checked ? 'var(--accent)' : 'var(--bg-3)',
        border: `1px solid ${checked ? 'var(--accent)' : 'var(--border-2)'}`,
        position: 'relative',
        cursor: 'pointer',
        boxShadow: checked ? '0 0 0 3px var(--accent-glow)' : 'none',
        transition: 'background 120ms, border-color 120ms',
        padding: 0,
        flexShrink: 0,
      }}>
      <div style={{
        position: 'absolute', top: 2, left: checked ? 18 : 2,
        width: 16, height: 16, borderRadius: '50%',
        background: 'white',
        boxShadow: '0 1px 2px rgba(0,0,0,0.3)',
        transition: 'left 120ms',
      }}/>
    </button>
  );
}

function Segmented({ value, onChange, options }: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div style={{
      display: 'inline-flex',
      background: 'var(--bg-1)',
      border: '1px solid var(--border-1)',
      borderRadius: 8,
      padding: 3,
      gap: 2,
      flexWrap: 'wrap',
    }}>
      {options.map(o => (
        <button key={o.value}
                onClick={() => onChange(o.value)}
                style={{
                  padding: '6px 12px',
                  background: value === o.value ? 'var(--bg-3)' : 'transparent',
                  border: '1px solid',
                  borderColor: value === o.value ? 'var(--border-2)' : 'transparent',
                  borderRadius: 6,
                  color: value === o.value ? 'var(--text-1)' : 'var(--text-3)',
                  fontFamily: 'inherit',
                  fontSize: 12.5,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  fontWeight: value === o.value ? 500 : 400,
                }}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// =========================================================================
// General page
// =========================================================================

interface GeneralSettings {
  workshopName: string;
  units: string;
  dateFormat: string;
  weekStart: string;
  defaultPriority: string;
}

function GeneralPage() {
  const [s, set] = useState<GeneralSettings>({
    workshopName: 'My Print Farm',
    units: 'metric',
    dateFormat: 'iso',
    weekStart: 'monday',
    defaultPriority: 'normal',
  });
  const update = (patch: Partial<GeneralSettings>) => set(prev => ({ ...prev, ...patch }));

  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="General" sub="Workshop-wide defaults that the rest of the app inherits." />

      <FieldRow label="Workshop name"
                hint="Shown on print labels, exported PDFs, and at the top of dashboard reports.">
        <input className="input" value={s.workshopName}
               onChange={e => update({ workshopName: e.target.value })}
               style={{ width: '100%' }} />
      </FieldRow>

      <FieldRow label="Units"
                hint="Affects build volumes, layer heights, and material lengths shown across the app.">
        <Segmented value={s.units} onChange={v => update({ units: v })}
                   options={[{value:'metric', label:'Metric · mm / g'},
                             {value:'imperial', label:'Imperial · in / oz'}]} />
      </FieldRow>

      <FieldRow label="Date format"
                hint="Used for order due dates and job timestamps.">
        <Segmented value={s.dateFormat} onChange={v => update({ dateFormat: v })}
                   options={[{value:'iso',label:'2026-05-25'},
                             {value:'us', label:'05/25/2026'},
                             {value:'eu', label:'25.05.2026'}]} />
      </FieldRow>

      <FieldRow label="Week starts on"
                hint="Affects the weekly schedule view.">
        <Segmented value={s.weekStart} onChange={v => update({ weekStart: v })}
                   options={[{value:'monday',label:'Monday'},{value:'sunday',label:'Sunday'}]} />
      </FieldRow>

      <FieldRow label="Default job priority"
                hint="Applied when creating a new job from the queue or from an order.">
        <select className="select" value={s.defaultPriority}
                onChange={e => update({ defaultPriority: e.target.value })}>
          <option value="rush">Rush — top of queue</option>
          <option value="high">High</option>
          <option value="normal">Normal</option>
          <option value="low">Low / fill</option>
        </select>
      </FieldRow>

      <div className="row gap-2" style={{ marginTop: 20, justifyContent: 'flex-end' }}>
        <button className="btn sm">Reset defaults</button>
        <button className="btn primary sm">{Icons.check} Save changes</button>
      </div>
    </div>
  );
}

// =========================================================================
// Tags page
// =========================================================================

const TAG_COLOR_PALETTE = [
  '#60a5fa', '#67e8f9', '#94a3b8', '#a78bfa', '#f87171', '#f472b6',
  '#fbbf24', '#fb7185', '#a3e635', '#34d399', '#fb923c', '#e879f9',
  '#22d3ee', '#f0abfc', '#22c55e', '#38bdf8', '#a8a29e', '#475472',
];

function hexToRgba(hex: string, a: number) {
  if (!hex || !hex.startsWith('#')) return `rgba(148,163,184,${a})`;
  const v = parseInt(hex.replace('#',''), 16);
  const r = (v >> 16) & 0xff, g = (v >> 8) & 0xff, b = v & 0xff;
  return `rgba(${r},${g},${b},${a})`;
}

function TagChip({ tag, large }: { tag: Tag; large?: boolean }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: large ? '6px 14px' : '4px 10px',
      background: hexToRgba(tag.color, 0.12),
      border: `1px solid ${hexToRgba(tag.color, 0.30)}`,
      borderRadius: 999,
      color: tag.color,
      fontSize: large ? 13 : 12,
      fontWeight: 500,
      whiteSpace: 'nowrap',
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: tag.color, boxShadow: `0 0 6px ${tag.color}`,
      }}/>
      {tag.name}
    </span>
  );
}

function TagRow({ tag, onEdit, onDelete }: { tag: Tag; onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="row gap-3" style={{
      padding: '10px 16px',
      borderBottom: '1px solid var(--border-1)',
      alignItems: 'center',
      transition: 'background 80ms',
    }}>
      <span style={{ width: 4, height: 28, borderRadius: 2, flexShrink: 0, background: tag.color }}/>
      <TagChip tag={tag} />
      <div className="col" style={{ flex: 1, minWidth: 0 }} />
      <div style={{ width: 140, flexShrink: 0 }}>
        <span className="tiny muted">{tag.category || '—'}</span>
      </div>
      <div style={{ width: 110, flexShrink: 0, display: 'flex', justifyContent: 'flex-end', gap: 4 }}>
        <button className="btn ghost icon sm" title="Edit" onClick={onEdit}>
          {React.cloneElement(Icons.settings, { size: 14 } as React.SVGProps<SVGSVGElement>)}
        </button>
        <button className="btn ghost icon sm" title="Delete tag" onClick={onDelete}
                style={{ color: 'var(--err)' }}>
          {Icons.trash}
        </button>
      </div>
    </div>
  );
}

function TagEditorRow({ initial, isNew, onSave, onCancel }: {
  initial: Partial<Tag>;
  isNew?: boolean;
  onSave: (draft: Partial<Tag>) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial.name || '');
  const [color, setColor] = useState(initial.color || TAG_COLOR_PALETTE[0]);
  const [category, setCategory] = useState(initial.category || 'Custom');

  const previewTag: Tag = { id: 'preview', name: name || 'new tag', color, category };

  function submit() {
    if (!name.trim()) return;
    onSave({ name: name.trim(), color, category: category.trim() || 'Custom' });
  }

  return (
    <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-1)', background: 'var(--bg-2)' }}>
      <div className="row gap-3" style={{ alignItems: 'flex-start' }}>
        <span style={{ width: 4, height: 28, borderRadius: 2, flexShrink: 0, marginTop: 8, background: color }}/>
        <div className="col" style={{ flex: 1, minWidth: 0, gap: 10 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 10 }}>
            <div>
              <label className="label">Name</label>
              <input className="input" autoFocus value={name}
                     onChange={e => setName(e.target.value)}
                     onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onCancel(); }}
                     placeholder="tag-name" />
            </div>
            <div>
              <label className="label">Category</label>
              <input className="input" value={category}
                     onChange={e => setCategory(e.target.value)}
                     placeholder="Material, Use…" />
            </div>
            <div>
              <label className="label">Preview</label>
              <div style={{ padding: '8px 12px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 8, display: 'flex', alignItems: 'center', minHeight: 36 }}>
                <TagChip tag={previewTag} />
              </div>
            </div>
          </div>
          <div>
            <label className="label">Color</label>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {TAG_COLOR_PALETTE.map(c => (
                <button key={c} onClick={() => setColor(c)}
                        style={{
                          width: 24, height: 24, borderRadius: 6,
                          background: c,
                          border: `2px solid ${c === color ? 'var(--text-1)' : 'transparent'}`,
                          boxShadow: c === color ? `0 0 0 1px ${c}, 0 0 8px ${c}66` : 'none',
                          cursor: 'pointer', padding: 0,
                        }}
                        title={c}/>
              ))}
            </div>
          </div>
        </div>
        <div className="col gap-2" style={{ flexShrink: 0, paddingTop: 22 }}>
          <button className="btn primary sm" onClick={submit} disabled={!name.trim()}>
            {Icons.check} {isNew ? 'Create' : 'Save'}
          </button>
          <button className="btn sm" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function TagStat({ label, value, sub, tone }: { label: string; value: number; sub?: string; tone?: string }) {
  const color = tone === 'warn' ? 'var(--warn)' : tone === 'idle' ? 'var(--text-3)' : 'var(--text-1)';
  return (
    <div style={{ flex: '1 1 0', minWidth: 140, padding: '14px 16px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10 }}>
      <div className="tag-key">{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 600, color, marginTop: 6, letterSpacing: '-0.02em' }}>{value}</div>
      {sub && <div className="tiny muted" style={{ marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function TagsPage() {
  const [tags, setTags] = useState<Tag[]>([...TAGS]);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const categories = useMemo(() => {
    const set = new Set(tags.map(t => t.category).filter(Boolean));
    return ['all', ...Array.from(set)] as string[];
  }, [tags]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tags.filter(t => {
      if (filter !== 'all' && t.category !== filter) return false;
      if (!q) return true;
      return t.name.toLowerCase().includes(q) || (t.category || '').toLowerCase().includes(q);
    });
  }, [tags, filter, query]);

  const totals = {
    total: tags.length,
    categories: new Set(tags.map(t => t.category).filter(Boolean)).size,
    inUse: 0,
    orphan: tags.length,
  };

  function deleteTag(id: string) {
    setTags(prev => prev.filter(t => t.id !== id));
    if (editingId === id) setEditingId(null);
  }

  function saveTag(id: string, patch: Partial<Tag>) {
    setTags(prev => prev.map(t => t.id === id ? { ...t, ...patch } : t));
    setEditingId(null);
  }

  function createTag(draft: Partial<Tag>) {
    if (!draft.name?.trim()) return;
    const dup = tags.find(t => t.name.toLowerCase() === draft.name!.trim().toLowerCase());
    if (dup) return;
    const newTag: Tag = {
      id: `tag-${draft.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${Math.random().toString(36).slice(2,5)}`,
      name: draft.name.trim(),
      color: draft.color || TAG_COLOR_PALETTE[tags.length % TAG_COLOR_PALETTE.length],
      category: draft.category?.trim() || 'Custom',
    };
    setTags(prev => [...prev, newTag]);
    setCreating(false);
  }

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <PageHeader
          title="Tags"
          sub="Labels you can attach to files, jobs, and orders. Tags are shared across the app — renaming one updates everywhere it's used."
        />
        <div className="row gap-3" style={{ marginBottom: 18, flexWrap: 'wrap' }}>
          <TagStat label="Total tags" value={totals.total} />
          <TagStat label="Categories" value={totals.categories} />
          <TagStat label="In use" value={totals.inUse} sub={`${totals.total > 0 ? Math.round(totals.inUse/totals.total*100) : 0}% of all tags`} />
          <TagStat label="Unused" value={totals.orphan} tone={totals.orphan > 0 ? 'warn' : 'idle'} sub={totals.orphan === 0 ? '' : 'Candidates to delete'} />
        </div>

        <div className="row gap-2" style={{ marginBottom: 14, flexWrap: 'wrap' }}>
          <div className="row gap-2" style={{ flex: 1, minWidth: 280 }}>
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', background:'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 8 }}>
              {React.cloneElement(Icons.search, { size: 14 } as React.SVGProps<SVGSVGElement>)}
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search tags…"
                     style={{ flex: 1, background: 'transparent', border: 'none', color: 'var(--text-1)', outline: 'none', fontSize: 13, fontFamily: 'var(--font-sans)' }}/>
              {query && (
                <button className="btn ghost icon sm" onClick={() => setQuery('')}>{Icons.x}</button>
              )}
            </div>
          </div>
          <button className="btn primary sm" onClick={() => setCreating(true)}>{Icons.plus} New tag</button>
        </div>

        <div className="row gap-2" style={{ marginBottom: 14, flexWrap: 'wrap' }}>
          {categories.map(c => (
            <button key={c} onClick={() => setFilter(c)}
                    className={`btn sm ${filter === c ? 'primary' : ''}`}
                    style={filter === c ? undefined : { background:'transparent', borderColor:'var(--border-1)' }}>
              {c === 'all' ? 'All' : c}
              <span className="num muted" style={{ marginLeft: 6, fontSize: 11 }}>
                {c === 'all' ? tags.length : tags.filter(t => t.category === c).length}
              </span>
            </button>
          ))}
        </div>

        <div style={{ border: '1px solid var(--border-1)', borderRadius: 8, overflow: 'hidden', background: 'var(--bg-1)' }}>
          <div className="row gap-3" style={{ padding: '10px 16px', borderBottom: '1px solid var(--border-1)', color: 'var(--text-4)', fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500, background: 'var(--bg-2)' }}>
            <div style={{ width: 4, flexShrink: 0 }}/>
            <div style={{ flex: 1 }}>Tag</div>
            <div style={{ width: 140, flexShrink: 0 }}>Category</div>
            <div style={{ width: 110, flexShrink: 0, textAlign: 'right' }}>Actions</div>
          </div>
          {creating && (
            <TagEditorRow isNew initial={{ name: '', color: TAG_COLOR_PALETTE[0], category: filter === 'all' ? 'Custom' : filter }}
                          onSave={createTag} onCancel={() => setCreating(false)} />
          )}
          {filtered.length === 0 && !creating && (
            <div className="col" style={{ alignItems: 'center', padding: '40px 20px', color: 'var(--text-3)' }}>
              <div className="small">No tags match.</div>
            </div>
          )}
          {filtered.map(tag => (
            editingId === tag.id ? (
              <TagEditorRow key={tag.id} initial={tag}
                            onSave={(patch) => saveTag(tag.id, patch)}
                            onCancel={() => setEditingId(null)} />
            ) : (
              <TagRow key={tag.id} tag={tag} onEdit={() => setEditingId(tag.id)} onDelete={() => deleteTag(tag.id)} />
            )
          ))}
        </div>
      </div>
    </div>
  );
}

// =========================================================================
// Notifications page
// =========================================================================

interface NotifSettings {
  onJobComplete: boolean;
  onJobFailed: boolean;
  onClaimWaiting: boolean;
  onPrinterIdle: boolean;
  onLowSpool: boolean;
  desktopNotifications: boolean;
  soundOnAlerts: boolean;
}

function NotificationsPage() {
  const [s, set] = useState<NotifSettings>({
    onJobComplete: true,
    onJobFailed: true,
    onClaimWaiting: false,
    onPrinterIdle: false,
    onLowSpool: true,
    desktopNotifications: false,
    soundOnAlerts: false,
  });
  const update = (patch: Partial<NotifSettings>) => set(prev => ({ ...prev, ...patch }));

  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Notifications"
                  sub="Choose which events trigger an alert. All notifications stay local — nothing is sent to anyone else." />

      <div style={{ marginBottom: 4, fontSize: 11, color: 'var(--text-4)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500 }}>
        Events
      </div>

      <FieldRow label="Job completed"
                hint="Notify when any printer finishes a print successfully.">
        <Toggle checked={s.onJobComplete} onChange={v => update({ onJobComplete: v })} />
      </FieldRow>
      <FieldRow label="Job failed or paused"
                hint="Fires on detected layer-shift, filament runout, thermal runaway, or user-paused jobs that idle for more than 10 minutes.">
        <Toggle checked={s.onJobFailed} onChange={v => update({ onJobFailed: v })} />
      </FieldRow>
      <FieldRow label="Claim waiting"
                hint="When a queued job is waiting for a free printer that just became idle.">
        <Toggle checked={s.onClaimWaiting} onChange={v => update({ onClaimWaiting: v })} />
      </FieldRow>
      <FieldRow label="Printer goes idle"
                hint="Notify whenever any printer transitions to idle — useful for back-to-back batches.">
        <Toggle checked={s.onPrinterIdle} onChange={v => update({ onPrinterIdle: v })} />
      </FieldRow>
      <FieldRow label="Low spool"
                hint="Warn when an assigned filament drops below the threshold set in Print defaults.">
        <Toggle checked={s.onLowSpool} onChange={v => update({ onLowSpool: v })} />
      </FieldRow>

      <div style={{ marginTop: 24, marginBottom: 4, fontSize: 11, color: 'var(--text-4)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500 }}>
        Delivery
      </div>
      <FieldRow label="Desktop notifications"
                hint="Show system notifications in the corner of the screen even when Themis is in a background tab.">
        <Toggle checked={s.desktopNotifications} onChange={v => update({ desktopNotifications: v })} />
      </FieldRow>
      <FieldRow label="Sound on alerts"
                hint="Plays a chime for failure-level events only.">
        <Toggle checked={s.soundOnAlerts} onChange={v => update({ soundOnAlerts: v })} />
      </FieldRow>
    </div>
  );
}

// =========================================================================
// Print defaults page
// =========================================================================

interface PrintSettings {
  sliceOnClaim: boolean;
  autoStartAfterSlice: boolean;
  chamberPreheat: boolean;
  requireDryBefore: string[];
  lowSpoolPercent: number;
  cooldownWaitMinutes: number;
}

function PrintDefaultsPage() {
  const [s, set] = useState<PrintSettings>({
    sliceOnClaim: true,
    autoStartAfterSlice: false,
    chamberPreheat: true,
    requireDryBefore: ['PA-CF', 'PC', 'ABS'],
    lowSpoolPercent: 20,
    cooldownWaitMinutes: 5,
  });
  const update = (patch: Partial<PrintSettings>) => set(prev => ({ ...prev, ...patch }));

  const allDryMaterials = ['PLA','PETG','PA-CF','ABS','ASA','PC','TPU'];
  function toggleDry(m: string) {
    const has = s.requireDryBefore.includes(m);
    update({ requireDryBefore: has ? s.requireDryBefore.filter(x => x !== m) : [...s.requireDryBefore, m] });
  }

  // Queue check interval — wired to the backend (how often the engine scans
  // printer availability and claims the next compatible queued job).
  const [checkInterval, setCheckInterval] = useState<number>(5);
  const [savingInterval, setSavingInterval] = useState(false);
  useEffect(() => {
    getQueueConfig().then(c => setCheckInterval(c.check_interval_minutes)).catch(console.error);
  }, []);
  async function commitInterval(minutes: number) {
    const v = Math.max(1, Math.round(minutes) || 1);
    setCheckInterval(v);
    setSavingInterval(true);
    try { await saveQueueConfig({ check_interval_minutes: v }); }
    finally { setSavingInterval(false); }
  }

  // Rescan OrcaSlicer presets (pick up models/profiles added since startup).
  const [rescanning, setRescanning] = useState(false);
  const [rescanMsg, setRescanMsg] = useState<string | null>(null);
  async function doRescan() {
    setRescanning(true);
    setRescanMsg(null);
    try {
      const r = await rescanProfiles();
      setRescanMsg(`Found ${r.machine_presets} printer presets.`);
    } catch {
      setRescanMsg('Rescan failed — is OrcaSlicer config reachable?');
    } finally {
      setRescanning(false);
    }
  }

  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Print defaults"
                  sub="Workshop-wide print behavior. Per-job overrides win when set during new-job intake." />

      <FieldRow label="Queue check interval"
                hint="How often the queue engine scans for an available printer and claims the next compatible job. Minutes.">
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          <input className="input" type="number" min={1} step={1}
                 value={checkInterval}
                 onChange={e => setCheckInterval(Number(e.target.value))}
                 onBlur={e => commitInterval(Number(e.target.value))}
                 style={{ width: 90 }} />
          <span className="muted small">min{savingInterval ? ' · saving…' : ''}</span>
        </div>
      </FieldRow>

      <FieldRow label="OrcaSlicer profiles"
                hint="Themis caches your OrcaSlicer printer/process/filament presets. Rescan after adding or editing presets so new options appear in printer setup and new jobs.">
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          <button className="btn sm" disabled={rescanning} onClick={doRescan}>
            {Icons.refresh} {rescanning ? 'Rescanning…' : 'Rescan profiles'}
          </button>
          {rescanMsg && <span className="muted small">{rescanMsg}</span>}
        </div>
      </FieldRow>

      <FieldRow label="Slice on claim"
                hint="When a printer claims a queued job, slice immediately using its configured profile rather than waiting for a manual slice.">
        <Toggle checked={s.sliceOnClaim} onChange={v => update({ sliceOnClaim: v })} />
      </FieldRow>
      <FieldRow label="Auto-start after slice"
                hint="Once slicing finishes, start the print without waiting for confirmation.">
        <Toggle checked={s.autoStartAfterSlice} onChange={v => update({ autoStartAfterSlice: v })} />
      </FieldRow>
      <FieldRow label="Chamber pre-heat"
                hint="On chamber-heated printers, pre-heat the chamber as part of the slice-on-claim step.">
        <Toggle checked={s.chamberPreheat} onChange={v => update({ chamberPreheat: v })} />
      </FieldRow>

      <FieldRow label="Require dry-before-print"
                hint="Block printing until the assigned spool has been through the dryer recently.">
        <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
          {allDryMaterials.map(m => {
            const on = s.requireDryBefore.includes(m);
            return (
              <button key={m} onClick={() => toggleDry(m)}
                      className={`btn sm ${on ? 'primary' : ''}`}
                      style={on ? undefined : { background:'transparent', borderColor:'var(--border-1)' }}>
                {m}
              </button>
            );
          })}
        </div>
      </FieldRow>

      <FieldRow label="Low-spool threshold"
                hint="Warn when a spool drops below this percentage of its starting weight.">
        <div className="row gap-3" style={{ alignItems: 'center' }}>
          <input type="range" min="5" max="50" step="5"
                 value={s.lowSpoolPercent}
                 onChange={e => update({ lowSpoolPercent: Number(e.target.value) })}
                 style={{ flex: 1 }} />
          <span className="num" style={{ minWidth: 48, color: 'var(--text-1)', fontSize: 14, textAlign: 'right' }}>
            {s.lowSpoolPercent}%
          </span>
        </div>
      </FieldRow>

      <FieldRow label="Cooldown wait after job"
                hint="Pause this many minutes between back-to-back claims for the same printer.">
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          <input className="input num" type="number" min="0" max="60"
                 value={s.cooldownWaitMinutes}
                 onChange={e => update({ cooldownWaitMinutes: Number(e.target.value) })}
                 style={{ width: 80 }}/>
          <span className="small muted">minutes</span>
        </div>
      </FieldRow>
    </div>
  );
}

// =========================================================================
// Spoolman page
// =========================================================================

type ConnectionStatus = 'connected' | 'connecting' | 'error' | 'disconnected';

interface SpoolmanSettings {
  enabled: boolean;
  url: string;
  apiKey: string;
  connectionStatus: ConnectionStatus;
  lastSyncedAt: string | null;
  syncInterval: number;
  syncOnEvents: boolean;
  deductFromSpoolman: boolean;
  pullVendorMaterials: boolean;
  autoCreateSpools: boolean;
  syncLocation: string;
  syncLot: boolean;
}

function ConnectionPill({ status }: { status: ConnectionStatus }) {
  const map: Record<ConnectionStatus, { label: string; tone: string }> = {
    connected:    { label: 'Connected',          tone: 'ok'   },
    connecting:   { label: 'Connecting…',        tone: 'info' },
    error:        { label: "Can't reach server", tone: 'err'  },
    disconnected: { label: 'Not connected',      tone: 'idle' },
  };
  const { label, tone } = map[status];
  return <span className={`pill ${tone}`}><span className="dot" />{label}</span>;
}

function SpoolmanMark() {
  return (
    <div style={{
      width: 44, height: 44, borderRadius: 10,
      background: 'linear-gradient(135deg, #f59e0b, #b45309)',
      border: '1px solid var(--border-2)',
      boxShadow: '0 0 16px rgba(245,158,11,0.25)',
      display: 'grid', placeItems: 'center',
      color: 'white', flexShrink: 0,
    }}>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="2" fill="currentColor" />
      </svg>
    </div>
  );
}

function SpoolStat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  const color = tone === 'warn' ? 'var(--warn)' : tone === 'idle' ? 'var(--text-3)' : 'var(--text-1)';
  return (
    <div style={{ flex: '1 1 0', minWidth: 110, padding: '12px 14px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10 }}>
      <div className="tag-key">{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 600, color, marginTop: 4, letterSpacing: '-0.02em' }}>{value}</div>
    </div>
  );
}

function SpoolmanPage() {
  const [s, set] = useState<SpoolmanSettings>({
    enabled: false,
    url: '',
    apiKey: '',
    connectionStatus: 'disconnected',
    lastSyncedAt: null,
    syncInterval: 15,
    syncOnEvents: true,
    deductFromSpoolman: true,
    pullVendorMaterials: true,
    autoCreateSpools: false,
    syncLocation: 'Workshop',
    syncLot: false,
  });
  const update = (patch: Partial<SpoolmanSettings>) => set(prev => ({ ...prev, ...patch }));

  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getSpoolmanConfig()
      .then(cfg => update({ enabled: cfg.enabled, url: cfg.url ?? '', apiKey: cfg.api_key ?? '' }))
      .catch(console.error);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const spools = useSpools(s.connectionStatus === 'connected');

  const stats = useMemo(() => ({
    spools: spools.length,
    materials: new Set(spools.map(sp => sp.filament.material)).size,
    vendors: new Set(spools.map(sp => sp.filament.vendor?.name ?? '').filter(Boolean)).size,
    lowSpools: spools.filter(sp => sp.remaining_weight < 100).length,
  }), [spools]);

  async function saveConfig() {
    setSaving(true);
    try {
      await saveSpoolmanConfig({ enabled: s.enabled, url: s.url, api_key: s.apiKey || null });
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    if (!s.url.trim()) return;
    setTesting(true);
    update({ connectionStatus: 'connecting' });
    try {
      await saveSpoolmanConfig({ enabled: s.enabled, url: s.url, api_key: s.apiKey || null });
      const result = await testSpoolmanConnection(s.url, s.apiKey || null);
      update({
        connectionStatus: result.ok ? 'connected' : 'error',
        lastSyncedAt: result.ok ? new Date().toISOString() : s.lastSyncedAt,
      });
    } catch {
      update({ connectionStatus: 'error' });
    } finally {
      setTesting(false);
    }
  }

  async function disconnect() {
    update({ enabled: false, connectionStatus: 'disconnected', lastSyncedAt: null });
    await saveSpoolmanConfig({ enabled: false }).catch(console.error);
  }

  function syncNow() {
    update({ connectionStatus: 'connecting' });
    testSpoolmanConnection(s.url, s.apiKey || null)
      .then(r => update({ connectionStatus: r.ok ? 'connected' : 'error', lastSyncedAt: r.ok ? new Date().toISOString() : s.lastSyncedAt }))
      .catch(() => update({ connectionStatus: 'error' }));
  }

  const isConnected = s.connectionStatus === 'connected';

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <div className="row between" style={{ marginBottom: 18, alignItems: 'flex-start' }}>
          <div className="row gap-3" style={{ alignItems: 'center' }}>
            <SpoolmanMark />
            <div>
              <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>Spoolman</h2>
              <div className="muted small" style={{ marginTop: 4, maxWidth: 540, lineHeight: 1.5 }}>
                Open-source filament & spool tracker. Themis can pull your spool inventory and push usage automatically as jobs complete.
              </div>
            </div>
          </div>
          <ConnectionPill status={s.connectionStatus} />
        </div>

        <FieldRow label="Enable Spoolman sync"
                  hint="When off, Themis ignores Spoolman entirely. Local filament library still works.">
          <Toggle checked={s.enabled} onChange={v => update({ enabled: v })} />
        </FieldRow>

        <div style={{ opacity: s.enabled ? 1 : 0.5, pointerEvents: s.enabled ? 'auto' : 'none', transition: 'opacity 120ms' }}>
          <FieldRow label="Server URL"
                    hint="The address your Spoolman instance listens on. Common default: http://spoolman.local:7912.">
            <div className="row gap-2">
              <input className="input" value={s.url}
                     onChange={e => update({ url: e.target.value, connectionStatus: 'disconnected' })}
                     placeholder="http://spoolman.local:7912"
                     style={{ flex: 1 }} />
            </div>
          </FieldRow>

          <FieldRow label="API key"
                    hint="Optional. Required only if you've enabled Spoolman API authentication.">
            <div className="row gap-2" style={{ flex: 1 }}>
              <input className="input" type="password" value={s.apiKey}
                     onChange={e => update({ apiKey: e.target.value })}
                     placeholder="Leave blank if auth is off"
                     style={{ flex: 1 }} />
            </div>
          </FieldRow>

          <div style={{ padding: '16px 0', borderBottom: '1px solid var(--border-1)', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn primary sm" disabled={!s.url.trim() || testing || saving} onClick={testConnection}>
              {testing ? 'Connecting…' : <>{Icons.link} Test connection</>}
            </button>
            <button className="btn sm" disabled={saving || testing} onClick={saveConfig}>
              {saving ? 'Saving…' : <>{Icons.check} Save</>}
            </button>
            {isConnected && (
              <>
                <button className="btn sm" onClick={syncNow} disabled={testing}>{Icons.refresh} Sync now</button>
                <button className="btn ghost sm" onClick={disconnect} style={{ color: 'var(--err)' }}>Disconnect</button>
              </>
            )}
          </div>

          {isConnected && (
            <div style={{ padding: '20px 0', borderBottom: '1px solid var(--border-1)' }}>
              <div className="row gap-3" style={{ flexWrap: 'wrap' }}>
                <SpoolStat label="Spools"     value={stats.spools} />
                <SpoolStat label="Materials"  value={stats.materials} />
                <SpoolStat label="Vendors"    value={stats.vendors} />
                <SpoolStat label="Low spools" value={stats.lowSpools} tone={stats.lowSpools > 0 ? 'warn' : 'idle'} />
              </div>
            </div>
          )}

          <div style={{ marginTop: 24, marginBottom: 4, fontSize: 11, color: 'var(--text-4)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500 }}>
            Sync behavior
          </div>

          <FieldRow label="Sync interval" hint="How often Themis pulls fresh spool data from Spoolman.">
            <div className="row gap-3" style={{ alignItems: 'center' }}>
              <input type="range" min="1" max="60" step="1"
                     value={s.syncInterval}
                     onChange={e => update({ syncInterval: Number(e.target.value) })}
                     style={{ flex: 1 }} />
              <span className="num" style={{ minWidth: 84, color: 'var(--text-1)', fontSize: 14, textAlign: 'right' }}>
                every {s.syncInterval}m
              </span>
            </div>
          </FieldRow>

          <FieldRow label="Push usage on job events"
                    hint="When a job completes, immediately tell Spoolman how many grams to deduct.">
            <Toggle checked={s.syncOnEvents} onChange={v => update({ syncOnEvents: v })} />
          </FieldRow>
          <FieldRow label="Deduct grams from spools"
                    hint="If off, Themis reads from Spoolman but never writes weight changes back.">
            <Toggle checked={s.deductFromSpoolman} onChange={v => update({ deductFromSpoolman: v })} />
          </FieldRow>
          <FieldRow label="Mirror vendor & material catalog"
                    hint="Keep Themis's manufacturer + material-type fields in sync with Spoolman's catalog.">
            <Toggle checked={s.pullVendorMaterials} onChange={v => update({ pullVendorMaterials: v })} />
          </FieldRow>
          <FieldRow label="Auto-create spools"
                    hint="If you add a new filament in Themis and Spoolman doesn't know about it, push a new spool record automatically.">
            <Toggle checked={s.autoCreateSpools} onChange={v => update({ autoCreateSpools: v })} />
          </FieldRow>

          <FieldRow label="Location label"
                    hint="Tag every spool record this Themis instance writes with a location.">
            <input className="input" value={s.syncLocation}
                   onChange={e => update({ syncLocation: e.target.value })}
                   placeholder="Workshop" style={{ width: '100%' }} />
          </FieldRow>

          <FieldRow label="Send lot numbers"
                    hint="Include the manufacturer's lot/batch number in synced records when known.">
            <Toggle checked={s.syncLot} onChange={v => update({ syncLot: v })} />
          </FieldRow>
        </div>

        <div style={{ marginTop: 28, padding: '14px 16px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          <div style={{ color: 'var(--accent-hi)', paddingTop: 2 }}>
            {React.cloneElement(SettingsIcons.info, { size: 16 } as React.SVGProps<SVGSVGElement>)}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="small" style={{ fontWeight: 500, color: 'var(--text-1)' }}>First time setting up?</div>
            <div className="tiny muted" style={{ marginTop: 4, lineHeight: 1.5, maxWidth: 580 }}>
              Spoolman runs as a Docker container or a Python service. Once it's up, add the URL above, hit <strong>Test connection</strong>, then choose what you want Themis to sync.
            </div>
            <div className="row gap-2" style={{ marginTop: 10 }}>
              <a className="btn ghost sm" href="https://github.com/Donkie/Spoolman" target="_blank" rel="noreferrer">
                {React.cloneElement(Icons.external, { size: 12 } as React.SVGProps<SVGSVGElement>)} Spoolman on GitHub
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// =========================================================================
// Data & backup page
// =========================================================================

interface DataSettings {
  autoBackup: boolean;
  backupFrequency: string;
  keepCompletedJobs: number;
}

function DataBackupPage() {
  const [s, set] = useState<DataSettings>({
    autoBackup: true,
    backupFrequency: 'weekly',
    keepCompletedJobs: 90,
  });
  const update = (patch: Partial<DataSettings>) => set(prev => ({ ...prev, ...patch }));

  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Data & backup" sub="Where Themis keeps your library, and how to move it around." />

      <FieldRow label="Automatic backup"
                hint="Snapshot your library to disk at the cadence below.">
        <Toggle checked={s.autoBackup} onChange={v => update({ autoBackup: v })} />
      </FieldRow>
      <FieldRow label="Backup frequency">
        <Segmented value={s.backupFrequency} onChange={v => update({ backupFrequency: v })}
                   options={[{value:'daily',label:'Daily'},{value:'weekly',label:'Weekly'},{value:'monthly',label:'Monthly'}]} />
      </FieldRow>
      <FieldRow label="Keep completed jobs"
                hint="How long to retain completed jobs before archiving.">
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          <input className="input num" type="number" min="7" max="365"
                 value={s.keepCompletedJobs}
                 onChange={e => update({ keepCompletedJobs: Number(e.target.value) })}
                 style={{ width: 80 }}/>
          <span className="small muted">days</span>
        </div>
      </FieldRow>

      <div style={{ marginTop: 28, paddingTop: 20, borderTop: '1px solid var(--border-1)' }}>
        <div style={{ fontSize: 12, color: 'var(--text-4)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500, marginBottom: 14 }}>
          Manual actions
        </div>
        <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
          <button className="btn sm">{Icons.upload} Export library (.json)</button>
          <button className="btn sm">{Icons.upload} Export jobs (.csv)</button>
          <button className="btn sm">{Icons.copy} Import library…</button>
          <button className="btn ghost sm" style={{ color: 'var(--err)' }}>
            {Icons.trash} Clear completed jobs
          </button>
        </div>
        <div className="tiny muted" style={{ marginTop: 14, lineHeight: 1.5, maxWidth: 540 }}>
          Library exports include all orders, jobs, files, filament profiles, process presets, tags, and your settings.
        </div>
      </div>
    </div>
  );
}

// =========================================================================
// About page
// =========================================================================

function AboutTile({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div style={{ padding: '12px 14px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 8 }}>
      <div className="tag-key">{k}</div>
      <div style={{ marginTop: 4, fontSize: 14, fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', color: 'var(--text-1)' }}>{v}</div>
    </div>
  );
}

function AboutPage() {
  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="About Themis" />
      <div className="row gap-4" style={{ alignItems: 'center', marginBottom: 24 }}>
        <div style={{
          width: 64, height: 64, borderRadius: 14,
          background: 'linear-gradient(135deg, var(--accent-hi), var(--accent-lo))',
          boxShadow: '0 0 24px var(--accent-glow)',
          border: '1px solid var(--border-2)',
          display: 'grid', placeItems: 'center',
          color: 'white', fontWeight: 700, fontSize: 24, letterSpacing: '-0.02em',
          fontFamily: 'var(--font-sans)',
        }}>θ</div>
        <div className="col">
          <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em' }}>themis<span style={{ color: 'var(--text-3)', fontWeight: 400 }}>.farm</span></div>
          <div className="small muted">Workshop print-farm manager · single-user build</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <AboutTile k="Version"      v="0.7.2" mono />
        <AboutTile k="Released"     v="2026-05-22" mono />
        <AboutTile k="Channel"      v="Stable" />
        <AboutTile k="Storage used" v="18.4 MB" mono />
      </div>

      <div style={{ marginTop: 28, paddingTop: 20, borderTop: '1px solid var(--border-1)' }}>
        <div style={{ fontSize: 12, color: 'var(--text-4)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500, marginBottom: 12 }}>
          Links
        </div>
        <div className="col gap-2">
          {([
            ['Release notes',      'View what\'s new in each build'],
            ['Documentation',      'How everything works under the hood'],
            ['Report an issue',    'Send a bug or feature request'],
            ['Keyboard shortcuts', 'Reference card'],
          ] as [string, string][]).map(([label, sub]) => (
            <a key={label} href="#" onClick={e => e.preventDefault()}
               style={{
                 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                 padding: '10px 14px',
                 background: 'var(--bg-1)',
                 border: '1px solid var(--border-1)',
                 borderRadius: 8,
                 textDecoration: 'none',
                 color: 'var(--text-1)',
               }}>
              <div className="col">
                <div className="small" style={{ fontWeight: 500 }}>{label}</div>
                <div className="tiny muted" style={{ marginTop: 2 }}>{sub}</div>
              </div>
              <span style={{ color: 'var(--text-3)', display: 'inline-flex' }}>
                {React.cloneElement(Icons.external, { size: 14 } as React.SVGProps<SVGSVGElement>)}
              </span>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

// =========================================================================
// Settings screen shell
// =========================================================================

type PageId = 'general' | 'tags' | 'print' | 'spoolman' | 'notifications' | 'data' | 'about';

interface NavItem {
  id: PageId;
  label: string;
  icon: React.ReactElement;
  sub: string;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

export function SettingsScreen() {
  const [activePage, setActivePage] = useState<PageId>('general');

  const sections: NavSection[] = [
    {
      label: 'Workshop',
      items: [
        { id: 'general',       label: 'General',        icon: Icons.settings,       sub: 'Units, defaults, workshop name' },
        { id: 'tags',          label: 'Tags',           icon: SettingsIcons.tag,     sub: 'Manage labels across files & jobs' },
        { id: 'print',         label: 'Print defaults', icon: Icons.printer,         sub: 'Slicing, drying, low-spool' },
      ],
    },
    {
      label: 'Integrations',
      items: [
        { id: 'spoolman',      label: 'Spoolman',       icon: SettingsIcons.spoolman, sub: 'Sync filament inventory' },
      ],
    },
    {
      label: 'System',
      items: [
        { id: 'notifications', label: 'Notifications',  icon: Icons.bell,            sub: 'Alerts for jobs & spools' },
        { id: 'data',          label: 'Data & backup',  icon: SettingsIcons.backup,  sub: 'Export, import, cleanup' },
        { id: 'about',         label: 'About',          icon: SettingsIcons.info,    sub: 'Version & links' },
      ],
    },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 18, minHeight: 0 }}>
      {/* sub-nav */}
      <aside style={{
        position: 'sticky', top: 0, height: 'fit-content',
        background: 'var(--bg-2)',
        border: '1px solid var(--border-1)',
        borderRadius: 12,
        padding: 8,
      }}>
        {sections.map((sec, si) => (
          <div key={sec.label} style={{ marginTop: si === 0 ? 0 : 12 }}>
            <div className="nav-section-label" style={{ padding: '8px 12px 4px' }}>{sec.label}</div>
            <div className="col" style={{ gap: 1 }}>
              {sec.items.map(item => (
                <button key={item.id}
                        onClick={() => setActivePage(item.id)}
                        className={`nav-item ${activePage === item.id ? 'active' : ''}`}
                        style={{ borderRadius: 8 }}>
                  {item.icon}
                  <span className="label">{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </aside>

      {/* page content */}
      <div style={{ minWidth: 0 }}>
        {activePage === 'general'       && <GeneralPage />}
        {activePage === 'tags'          && <TagsPage />}
        {activePage === 'print'         && <PrintDefaultsPage />}
        {activePage === 'spoolman'      && <SpoolmanPage />}
        {activePage === 'notifications' && <NotificationsPage />}
        {activePage === 'data'          && <DataBackupPage />}
        {activePage === 'about'         && <AboutPage />}
      </div>
    </div>
  );
}
