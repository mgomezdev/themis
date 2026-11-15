import type { EmbeddedSetting } from '../api/queue';

interface OverridePanelProps {
  settings: EmbeddedSetting[];
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
}

export function OverridePanel({ settings, value, onChange }: OverridePanelProps) {
  if (settings.length === 0) return null;

  function toggle(key: string, settingValue: string) {
    if (key in value) {
      const next = { ...value };
      delete next[key];
      onChange(next);
    } else {
      onChange({ ...value, [key]: settingValue });
    }
  }

  return (
    <div style={{
      border: '1px solid var(--border-1)',
      borderRadius: 10,
      padding: '14px 16px',
      background: 'var(--bg-0)',
    }}>
      <div className="label" style={{ marginBottom: 6 }}>3MF Embedded Settings</div>
      <div className="tiny muted" style={{ marginBottom: 10, lineHeight: 1.5 }}>
        Settings marked <b>carried</b> were changed by the file's author: they are re-applied onto whichever printer's
        preset slices this job. The rest are just the file's preset values — check one to keep it for this job.
        Job overrides win over the file; printer overrides apply under both.
      </div>
      <div className="col gap-2">
        {settings.map(s => {
          const checked = s.key in value;
          if (s.carried && !checked) {
            return (
              <div key={s.key} data-testid={`carried-${s.key}`}
                   style={{ display: 'flex', alignItems: 'center', gap: 10, userSelect: 'none' }}>
                <span style={{ width: 15, flexShrink: 0 }} />
                <span className="small" style={{ flex: 1, minWidth: 0 }}>{s.label}</span>
                <span className="tiny muted">carried</span>
                <span className="tiny muted mono" style={{ flexShrink: 0 }}>{s.value}</span>
              </div>
            );
          }
          return (
            <label
              key={s.key}
              style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', userSelect: 'none' }}
            >
              <input
                data-testid={`override-${s.key}`}
                type="checkbox"
                checked={checked}
                onChange={() => toggle(s.key, s.value)}
                style={{ width: 15, height: 15, accentColor: 'var(--accent)', cursor: 'pointer', flexShrink: 0 }}
              />
              <span className="small" style={{ flex: 1, minWidth: 0 }}>{s.label}</span>
              <span className="tiny muted mono" style={{ flexShrink: 0 }}>{checked ? value[s.key] : s.value}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
