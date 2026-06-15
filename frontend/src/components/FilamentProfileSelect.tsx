import { useMemo, useState } from 'react';

export function FilamentProfileSelect({ profiles, value, onChange, placeholder = '— use printer default —' }: {
  profiles: string[];
  value: string | null;
  onChange: (v: string | null) => void;
  placeholder?: string;
}) {
  const [query, setQuery] = useState('');
  const showSearch = profiles.length > 12;

  const filtered = useMemo(() => {
    if (!query) return profiles;
    const q = query.toLowerCase();
    return profiles.filter(p => p.toLowerCase().includes(q));
  }, [profiles, query]);

  return (
    <div className="col gap-1">
      {showSearch && (
        <input
          className="input"
          placeholder="Search profiles…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          style={{ fontSize: 12 }}
        />
      )}
      <select
        className="select"
        value={value ?? ''}
        onChange={e => onChange(e.target.value || null)}
      >
        <option value="">{placeholder}</option>
        {filtered.map(p => <option key={p} value={p}>{p}</option>)}
        {showSearch && query && filtered.length === 0 && (
          <option disabled>No matches</option>
        )}
      </select>
    </div>
  );
}
