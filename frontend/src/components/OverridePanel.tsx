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
      background: 'var(--bg-1)',
    }}>
      <div className="label" style={{ marginBottom: 6 }}>3MF Embedded Settings</div>
      <div className="tiny muted" style={{ marginBottom: 10, lineHeight: 1.5 }}>
        The file has these settings baked in. Check the ones you want to apply — unchecked ones use the profile default.
      </div>
      <div className="col gap-2">
        {settings.map(s => {
          const checked = s.key in value;
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
              <span className="tiny muted mono" style={{ flexShrink: 0 }}>{s.value}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
