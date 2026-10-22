import { useEffect, useMemo, useState } from 'react';
import { fetchMachineCatalog, variantLabel, type MachinePreset } from '../api/printers';
import { fetchPlugins } from '../api/plugins';
import {
  createModelSlicerProfile, deleteModelSlicerProfile, fetchModelSlicerProfiles, fetchProfileSuggestions, updateModelSlicerProfile,
  variantOf, type ModelSlicerProfile, type ProfileSuggestions,
} from '../api/slicerProfiles';
import { MachinePicker } from './MachinePicker';

type Flow = 'standard' | 'high_flow';

/**
 * The slicer machine profile of one printer MODEL (BIZ-294): set once here, every printer of the model uses it. A printer with a
 * different physical nozzle picks another variant row (Edit printer -> Nozzle), so ten identical printers need no per-printer entry.
 * The slicing provider's suggestions are offered when it can make them; picking a profile by hand always works.
 */
export function ModelSlicerProfiles({ modelUuid, modelLabel, onChange }: {
  modelUuid: string;
  modelLabel: string;
  onChange?: (rows: ModelSlicerProfile[]) => void;
}) {
  const [rows, setRows] = useState<ModelSlicerProfile[] | null>(null);
  const [providers, setProviders] = useState<{ id: string; name: string }[] | null>(null);
  const [provider, setProvider] = useState('');
  const [suggestions, setSuggestions] = useState<ProfileSuggestions | null>(null);
  const [catalog, setCatalog] = useState<MachinePreset[]>([]);
  const [chosen, setChosen] = useState('');            // a suggested candidate's name
  const [manualProfile, setManualProfile] = useState('');
  const [diameter, setDiameter] = useState('0.4');
  const [flow, setFlow] = useState<Flow>('standard');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function apply(next: ModelSlicerProfile[]) {
    setRows(next);
    onChange?.(next);
  }

  useEffect(() => {
    let alive = true;
    fetchModelSlicerProfiles(modelUuid)
      .then(r => { if (alive) apply(r); })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : 'Could not load the slicer profiles'); });
    fetchPlugins()
      .then(list => {
        if (!alive) return;
        const found = (list?.plugins ?? []).filter(p => p.enabled && p.provides.some(x => x.capability === 'gcode.produce'));
        setProviders(found.map(p => ({ id: p.id, name: p.name })));
        setProvider(found[0]?.id ?? '');
      })
      .catch(() => { if (alive) setProviders([]); });
    fetchMachineCatalog().then(c => { if (alive) setCatalog(Array.isArray(c) ? c : []); }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelUuid]);

  useEffect(() => {
    if (!provider) return;
    let alive = true;
    setSuggestions(null);
    fetchProfileSuggestions(modelUuid, provider)
      .then(s => { if (alive) setSuggestions(s); })
      .catch(() => { if (alive) setSuggestions({ provider_plugin_id: provider, available: false, candidates: [], variants: [], error: null }); });
    return () => { alive = false; };
  }, [modelUuid, provider]);

  const mine = useMemo(() => (rows ?? []).filter(r => r.provider_plugin_id === provider), [rows, provider]);
  const mapped = (d: number | null, f: Flow) => mine.some(r => r.nozzle_diameter_mm === d && r.flow === f);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      apply(await fetchModelSlicerProfiles(modelUuid));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the slicer profile');
    } finally {
      setBusy(false);
    }
  }

  const add = (machine_profile: string, d: number, f: Flow, is_default = mine.length === 0) =>
    run(() => createModelSlicerProfile(modelUuid, { provider_plugin_id: provider, nozzle_diameter_mm: d, flow: f, machine_profile, is_default }));

  function pickFromCatalog(name: string) {
    setManualProfile(name);
    const nozzle = Number(catalog.find(c => c.name === name)?.nozzle);
    if (Number.isFinite(nozzle) && nozzle > 0) setDiameter(String(nozzle));
  }

  const candidate = suggestions?.candidates.find(c => c.name === chosen);
  const offered = (suggestions?.candidates ?? []).slice(0, 5);
  const otherVariants = (suggestions?.variants ?? []).filter(v => !mapped(v.nozzle_diameter_mm, v.flow) && v.nozzle_diameter_mm != null);

  if (rows === null || providers === null) return <div className="tiny muted">{error ?? 'Loading…'}</div>;
  if (providers.length === 0) {
    return <div className="tiny muted">No slicing provider is enabled — enable one under Settings → Plugins to map a slicer profile.</div>;
  }

  return (
    <div className="col gap-3" data-testid="model-slicer-profiles">
      {error && <div role="alert" style={{ color: 'var(--err)', fontSize: 13 }}>{error}</div>}
      {providers.length > 1 && (
        <div>
          <label className="label" htmlFor={`msp-provider-${modelUuid}`}>Slicing provider</label>
          <select id={`msp-provider-${modelUuid}`} className="select" value={provider} onChange={e => setProvider(e.target.value)}>
            {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      )}

      {mine.length === 0 ? (
        <div className="small" role="status" style={{ color: 'var(--warn)' }}>
          No slicer profile is set for {modelLabel} yet — jobs for its printers stay blocked until you set one.
        </div>
      ) : (
        <div className="col gap-1">
          {mine.map(r => (
            <div key={r.id} className="row gap-2" style={{ alignItems: 'center' }} data-testid={`msp-row-${r.id}`}>
              <span className="small" style={{ minWidth: 110 }}>{variantLabel(variantOf(r))}</span>
              <span className="small mono" style={{ flex: 1 }}>{r.machine_profile}</span>
              {r.is_default
                ? <span className="tiny muted">default</span>
                : <button className="btn ghost sm" disabled={busy} onClick={() => run(() => updateModelSlicerProfile(modelUuid, r.id, { is_default: true }))}>Make default</button>}
              <button className="btn ghost sm" disabled={busy} aria-label={`Remove ${variantLabel(variantOf(r))}`}
                      onClick={() => run(() => deleteModelSlicerProfile(modelUuid, r.id))}>Remove</button>
            </div>
          ))}
        </div>
      )}

      {offered.length > 0 && mine.length === 0 && (
        <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
          <legend className="label">Suggested for {modelLabel}</legend>
          {offered.map(c => (
            <label key={c.ref || c.name} className="row gap-2" style={{ alignItems: 'center' }}>
              <input type="radio" name={`msp-suggest-${modelUuid}`} checked={chosen === c.name} onChange={() => setChosen(c.name)} />
              <span className="small mono">{c.name}</span>
              <span className="tiny muted">{Math.round(c.confidence * 100)}% match</span>
            </label>
          ))}
          <button className="btn primary sm" style={{ marginTop: 8 }} disabled={!candidate || busy}
                  onClick={() => candidate && add(candidate.name, candidate.nozzle_diameter_mm ?? 0.4, candidate.flow, true)}>
            Confirm profile
          </button>
        </fieldset>
      )}
      {suggestions && !suggestions.available && mine.length === 0 && (
        <div className="tiny muted">Suggestions unavailable{suggestions.error ? ` (${suggestions.error})` : ''} — pick the profile below.</div>
      )}

      {mine.length > 0 && otherVariants.length > 0 && (
        <div className="col gap-1">
          <div className="label">Other nozzle variants of {modelLabel}</div>
          {otherVariants.map(v => (
            <div key={v.ref || v.name} className="row gap-2" style={{ alignItems: 'center' }}>
              <span className="small" style={{ minWidth: 110 }}>{variantLabel({ diameter_mm: v.nozzle_diameter_mm as number, flow: v.flow })}</span>
              <span className="small mono" style={{ flex: 1 }}>{v.name}</span>
              <button className="btn ghost sm" disabled={busy} onClick={() => add(v.name, v.nozzle_diameter_mm as number, v.flow, false)}>Add</button>
            </div>
          ))}
        </div>
      )}

      <details>
        <summary className="small" style={{ cursor: 'pointer' }}>{mine.length === 0 ? 'Choose a profile by hand' : 'Add another nozzle variant by hand'}</summary>
        <div className="col gap-2" style={{ marginTop: 8 }}>
          <MachinePicker catalog={catalog} value={manualProfile} onChange={pickFromCatalog} />
          <div className="row gap-2" style={{ alignItems: 'flex-end' }}>
            <div>
              <label className="label" htmlFor={`msp-d-${modelUuid}`}>Nozzle (mm)</label>
              <input id={`msp-d-${modelUuid}`} className="input" type="number" min="0.1" max="2" step="0.1" style={{ width: 90 }}
                     value={diameter} onChange={e => setDiameter(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor={`msp-f-${modelUuid}`}>Flow</label>
              <select id={`msp-f-${modelUuid}`} className="select" value={flow} onChange={e => setFlow(e.target.value as Flow)}>
                <option value="standard">Standard</option>
                <option value="high_flow">High-flow</option>
              </select>
            </div>
            <button className="btn primary sm" disabled={busy || !manualProfile || !(Number(diameter) > 0)}
                    onClick={() => add(manualProfile, Number(diameter), flow)}>
              Set profile
            </button>
          </div>
          <div className="tiny muted">
            Profile names are the slicer&apos;s own. {mine.length === 0 ? 'The first one you set becomes the default for every printer of this model.' : ''}
          </div>
        </div>
      </details>
    </div>
  );
}
