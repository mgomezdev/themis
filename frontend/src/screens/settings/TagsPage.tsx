// Settings → Material tags: the tag list, editor row and colour palette.
import React, { useState, useMemo } from 'react';
import { useTags, createTag, updateTag, deleteTag, type Tag } from '../../api/tags';
import { Icons } from '../../components/icons';
import { PageHeader } from '../../components/settingsUi';

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

export function TagsPage() {
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
