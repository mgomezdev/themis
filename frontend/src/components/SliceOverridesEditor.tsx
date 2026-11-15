import { useEffect, useState } from 'react';
import type { OverrideSetting } from '../api/queue';
import { loadOverrideSettings } from './overrideSettings';

function defaultValue(s: OverrideSetting): string {
  if (s.kind === 'bool') return '1';
  return s.options[0] ?? '';
}

function optionLabel(s: OverrideSetting, v: string): string {
  if (s.kind === 'bool') return v === '1' ? 'Yes' : v === '0' ? 'No' : v;
  return v;
}

interface Props {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  title: string;
  hint?: string;
  /** Prefix for data-testids (several editors can share a page). */
  testId?: string;
}

/**
 * Pick slicer setting overrides (supports, brim type, ...) from the provider's curated list. Used for a job, a project's
 * generated jobs and a printer. When slicing: job > the 3MF's own changes > printer.
 */
export function SliceOverridesEditor({ value, onChange, title, hint, testId = 'slice-overrides' }: Props) {
  const [settings, setSettings] = useState<OverrideSetting[] | null>(null);

  useEffect(() => {
    let alive = true;
    loadOverrideSettings().then(s => { if (alive) setSettings(s); }).catch(() => { if (alive) setSettings([]); });
    return () => { alive = false; };
  }, []);

  if (!settings || settings.length === 0) return null;
  const byKey = new Map(settings.map(s => [s.key, s]));
  const chosen = Object.keys(value).filter(k => byKey.has(k));
  const available = settings.filter(s => !(s.key in value));

  function set(key: string, v: string) { onChange({ ...value, [key]: v }); }
  function remove(key: string) {
    const next = { ...value };
    delete next[key];
    onChange(next);
  }

  return (
    <div data-testid={testId} style={{
      border: '1px solid var(--border-1)', borderRadius: 10, padding: '14px 16px', background: 'var(--bg-1)',
    }}>
      <div className="label" style={{ marginBottom: 6 }}>{title}</div>
      {hint && <div className="tiny muted" style={{ marginBottom: 10, lineHeight: 1.5 }}>{hint}</div>}
      <div className="col gap-2">
        {chosen.map(key => {
          const s = byKey.get(key)!;
          const v = value[key];
          return (
            <div key={key} className="row gap-2" style={{ alignItems: 'center' }}>
              <span className="small" style={{ flex: 1, minWidth: 0 }}>{s.label}</span>
              {s.options.length > 0 ? (
                <select className="input" style={{ width: 'auto', minWidth: 140 }} aria-label={s.label}
                        data-testid={`${testId}-value-${key}`} value={v} onChange={e => set(key, e.target.value)}>
                  {!s.options.includes(v) && <option value={v}>{v}</option>}
                  {s.options.map(o => <option key={o} value={o}>{optionLabel(s, o)}</option>)}
                </select>
              ) : (
                <input className="input mono" style={{ width: 140 }} aria-label={s.label}
                       data-testid={`${testId}-value-${key}`} value={v} onChange={e => set(key, e.target.value)} />
              )}
              <button type="button" className="btn ghost sm" aria-label={`Remove ${s.label}`}
                      data-testid={`${testId}-remove-${key}`} onClick={() => remove(key)}>×</button>
            </div>
          );
        })}
        {available.length > 0 && (
          <select className="input" aria-label="Add override" data-testid={`${testId}-add`} value=""
                  onChange={e => { const s = byKey.get(e.target.value); if (s) set(s.key, defaultValue(s)); }}>
            <option value="">+ Add override…</option>
            {available.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        )}
      </div>
    </div>
  );
}
