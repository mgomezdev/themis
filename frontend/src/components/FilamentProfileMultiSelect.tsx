import { useMemo, useRef, useState } from 'react';

export function FilamentProfileMultiSelect({ profiles, selected, onChange, placeholder = 'Search profiles…', emptyText }: {
  profiles: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder?: string;
  emptyText?: string;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return profiles.filter(p => !selected.includes(p) && p.toLowerCase().includes(q));
  }, [profiles, selected, query]);

  function add(p: string) {
    onChange([...selected, p]);
    setQuery('');
    inputRef.current?.focus();
  }

  function remove(p: string) {
    onChange(selected.filter(s => s !== p));
  }

  return (
    <div className="col gap-1">
      {selected.length > 0 && (
        <div className="row gap-1" style={{ flexWrap: 'wrap', marginBottom: 4 }}>
          {selected.map(p => (
            <span key={p} style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              padding: '2px 8px 2px 10px', borderRadius: 999, fontSize: 11,
              background: 'var(--accent-glow)', border: '1px solid var(--accent-lo)',
              color: 'var(--accent-hi)', fontWeight: 500,
            }}>
              {p}
              <button
                onClick={() => remove(p)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--accent-hi)', padding: 0, lineHeight: 1, fontSize: 13,
                  display: 'flex', alignItems: 'center',
                }}
                title={`Remove ${p}`}
              >×</button>
            </span>
          ))}
        </div>
      )}
      <div style={{ position: 'relative' }}>
        <input
          ref={inputRef}
          className="input"
          value={query}
          placeholder={selected.length > 0 ? 'Add another…' : placeholder}
          onChange={e => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          style={{ fontSize: 12 }}
        />
        {open && (filtered.length > 0 || (query && emptyText)) && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
            background: 'var(--bg-2)', border: '1px solid var(--border-2)',
            borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            maxHeight: 200, overflowY: 'auto', marginTop: 2,
          }}>
            {filtered.length > 0 ? filtered.map(p => (
              <div
                key={p}
                onMouseDown={() => add(p)}
                style={{
                  padding: '7px 12px', fontSize: 12, cursor: 'pointer',
                  color: 'var(--text-1)', borderBottom: '1px solid var(--border-1)',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >{p}</div>
            )) : (
              <div style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text-3)' }}>
                {emptyText ?? 'No compatible profiles found'}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
