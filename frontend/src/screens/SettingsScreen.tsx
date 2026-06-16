import React, { useState, useMemo, useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { getSpoolmanConfig, saveSpoolmanConfig, testSpoolmanConnection, useSpools, useSpoolmanConfig } from '../api/spoolman';
import { getQueueConfig, saveQueueConfig } from '../api/queue';
import { rescanProfiles } from '../api/printers';
import { useTags, createTag, updateTag, deleteTag, type Tag } from '../api/tags';
import { Icons, Icon } from '../components/icons';
import { SpoolmanMappingsPage } from './SpoolmanMappingsPage';

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

  const previewTag: Tag = { id: -1, name: name || 'new tag', color, category, usage_count: 0 };

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
  const { tags, refetch } = useTags();
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    inUse: tags.filter(t => t.usage_count > 0).length,
    orphan: tags.filter(t => t.usage_count === 0).length,
  };

  function reportError(e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    setError(msg);
  }

  async function handleDelete(id: number) {
    setError(null);
    try {
      await deleteTag(id);
      if (editingId === id) setEditingId(null);
      refetch();
    } catch (e) {
      reportError(e);
    }
  }

  async function handleSave(id: number, patch: Partial<Tag>) {
    setError(null);
    try {
      await updateTag(id, {
        name: patch.name,
        color: patch.color,
        category: patch.category,
      });
      setEditingId(null);
      refetch();
    } catch (e) {
      reportError(e);
    }
  }

  async function handleCreate(draft: Partial<Tag>) {
    if (!draft.name?.trim()) return;
    setError(null);
    try {
      await createTag({
        name: draft.name.trim(),
        color: draft.color || TAG_COLOR_PALETTE[tags.length % TAG_COLOR_PALETTE.length],
        category: draft.category?.trim() || 'Custom',
      });
      setCreating(false);
      refetch();
    } catch (e) {
      reportError(e);
    }
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

        {error && (
          <div style={{ marginBottom: 14, padding: '10px 14px', background: 'var(--bg-1)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
            {error}
          </div>
        )}

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
                          onSave={handleCreate} onCancel={() => setCreating(false)} />
          )}
          {filtered.length === 0 && !creating && (
            <div className="col" style={{ alignItems: 'center', padding: '40px 20px', color: 'var(--text-3)' }}>
              <div className="small">No tags match.</div>
            </div>
          )}
          {filtered.map(tag => (
            editingId === tag.id ? (
              <TagEditorRow key={tag.id} initial={tag}
                            onSave={(patch) => handleSave(tag.id, patch)}
                            onCancel={() => setEditingId(null)} />
            ) : (
              <TagRow key={tag.id} tag={tag} onEdit={() => setEditingId(tag.id)} onDelete={() => handleDelete(tag.id)} />
            )
          ))}
        </div>
      </div>
    </div>
  );
}

// =========================================================================
// Print defaults page
// =========================================================================

function PrintDefaultsPage() {
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

  // Operator display name — shown in the Sidebar footer. Blank hides it entirely.
  const [operatorName, setOperatorName] = useState<string>('');
  const [savingName, setSavingName] = useState(false);
  const nameTouchedRef = useRef(false);
  useEffect(() => {
    getQueueConfig().then(c => { if (!nameTouchedRef.current) setOperatorName(c.operator_name ?? ''); }).catch(console.error);
  }, []);
  async function commitOperatorName(name: string) {
    setSavingName(true);
    try { await saveQueueConfig({ operator_name: name.trim() }); }
    finally { setSavingName(false); }
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

      <FieldRow label="Display name" hint="Shown in the sidebar. Leave blank to hide it.">
        <input className="input" value={operatorName}
               onChange={e => { nameTouchedRef.current = true; setOperatorName(e.target.value); }}
               onBlur={e => commitOperatorName(e.target.value)}
               placeholder="e.g. Workshop Lead" style={{ width: '100%' }} />
        {savingName && <span className="muted small">saving…</span>}
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
        <AboutTile k="Version" v={__APP_VERSION__} mono />
      </div>
    </div>
  );
}

// =========================================================================
// Settings screen shell
// =========================================================================

type PageId = 'tags' | 'print' | 'spoolman' | 'spoolman-mappings' | 'about';

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

const PAGE_IDS: PageId[] = ['tags', 'print', 'spoolman', 'spoolman-mappings', 'about'];

function pageFromPath(pathname: string): PageId {
  const seg = pathname.replace(/^\/settings\/?/, '').split('/')[0];
  return (PAGE_IDS as string[]).includes(seg) ? (seg as PageId) : 'tags';
}

export function SettingsScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const activePage = pageFromPath(location.pathname);
  const setActivePage = (id: PageId) => navigate(`/settings/${id}`);
  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanEnabled = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);

  const sections: NavSection[] = [
    {
      label: 'Workshop',
      items: [
        { id: 'tags',          label: 'Tags',           icon: SettingsIcons.tag,     sub: 'Manage labels across files & jobs' },
        { id: 'print',         label: 'Print defaults', icon: Icons.printer,         sub: 'Queue interval & profile rescan' },
      ],
    },
    {
      label: 'Integrations',
      items: [
        { id: 'spoolman',          label: 'Spoolman',         icon: SettingsIcons.spoolman, sub: 'Sync filament inventory' },
        ...(spoolmanEnabled ? [{ id: 'spoolman-mappings' as PageId, label: 'Filament Mappings', icon: SettingsIcons.spoolman, sub: 'orca_profiles per printer model' }] : []),
      ],
    },
    {
      label: 'System',
      items: [
        { id: 'about',         label: 'About',          icon: SettingsIcons.info,    sub: 'Version' },
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
        {activePage === 'tags'          && <TagsPage />}
        {activePage === 'print'         && <PrintDefaultsPage />}
        {activePage === 'spoolman'          && <SpoolmanPage />}
        {activePage === 'spoolman-mappings' && <SpoolmanMappingsPage />}
        {activePage === 'about'             && <AboutPage />}
      </div>
    </div>
  );
}
