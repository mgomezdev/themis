import { useEffect, useState } from 'react';
import { pluginRequest } from '../api/plugins';
import { FieldRow } from './settingsUi';

type Choice = { provider_plugin_id: string | null };
type Route = Choice & { input_kind: 'model_3mf' | 'stl'; output_kind: 'gcode' | 'gcode_3mf' };
type Brand = Choice & { id: string; name: string };
type Provider = { id: string; name: string; inputs: string[]; outputs: string[] };
type Settings = { routes: Route[]; brands: Brand[]; providers: Provider[] };

const ENDPOINT = '/api/v1/settings/print-conversion';
const FORMAT = { model_3mf: '.3mf', stl: '.stl', gcode: '.gcode', gcode_3mf: '.gcode.3mf' };

function ProviderSelect({ label, value, fallback, options, busy, onChange }: {
  label: string; value: string | null; fallback: string; options: Provider[]; busy: boolean; onChange: (id: string | null) => void;
}) {
  return <select className="select" aria-label={label} value={value ?? ''} disabled={busy || (!value && !options.length)}
                 onChange={e => onChange(e.target.value || null)}>
    <option value="">{fallback}</option>
    {value && !options.some(p => p.id === value) && <option value={value}>{value} (unavailable)</option>}
    {options.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
  </select>;
}

export function PrintConversionSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    pluginRequest<Settings>(ENDPOINT)
      .then(data => { if (active) setSettings(data); })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, []);

  async function change(path: string, provider_plugin_id: string | null, update: (current: Settings) => Settings) {
    setBusy(true);
    setError(null);
    try {
      await pluginRequest(ENDPOINT + path, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider_plugin_id }),
      });
      setSettings(current => current && update(current));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <FieldRow label="Print file conversion" hint="Choose a service for each model and printer file format. Printer preferences take priority over model, brand, and format defaults.">
      {error && <div role="alert" className="small text-err">{error}</div>}
      {!settings ? (!error && <div className="small muted">Loading…</div>) : (
        <div className="col gap-2">
          {settings.routes.map(route => {
            const key = `${route.input_kind}/${route.output_kind}`;
            const label = `${FORMAT[route.input_kind]} → ${FORMAT[route.output_kind]}`;
            return <label key={key} className="row between gap-2">
              <span className="small mono">{label}</span>
              <ProviderSelect label={label} value={route.provider_plugin_id} fallback="Capability default" busy={busy}
                options={settings.providers.filter(p => p.inputs.includes(route.input_kind) && p.outputs.includes(route.output_kind))}
                onChange={id => void change(`/routes/${key}`, id, current => ({
                  ...current, routes: current.routes.map(r => r.input_kind === route.input_kind && r.output_kind === route.output_kind
                    ? { ...r, provider_plugin_id: id } : r),
                }))} />
            </label>;
          })}
          {settings.brands.length > 0 && <details>
            <summary className="small">Brand preferences</summary>
            <div className="col gap-2" style={{ marginTop: 8 }}>
              {settings.brands.map(brand => <label key={brand.id} className="row between gap-2">
                <span className="small">{brand.name}</span>
                <ProviderSelect label={`Preferred converter for ${brand.name}`} value={brand.provider_plugin_id}
                  fallback="Follow format defaults" options={settings.providers} busy={busy}
                  onChange={id => void change(`/brands/${encodeURIComponent(brand.id)}`, id, current => ({
                    ...current, brands: current.brands.map(b => b.id === brand.id ? { ...b, provider_plugin_id: id } : b),
                  }))} />
              </label>)}
            </div>
          </details>}
        </div>
      )}
    </FieldRow>
  );
}
