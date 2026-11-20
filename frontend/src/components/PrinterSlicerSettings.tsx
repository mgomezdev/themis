import { useEffect, useId, useState } from 'react';
import { fetchPlugins, type PluginList } from '../api/plugins';
import { fetchMachineCatalog, sameVariant, variantLabel, type ApiPrinter, type PrinterSlicerDraft, type NozzleVariant } from '../api/printers';
import { fetchModelSlicerProfiles, variantOf, type ModelSlicerProfile } from '../api/slicerProfiles';

export function PrinterSlicerSettings({ printer, value, onChange }: {
  printer: ApiPrinter;
  value: PrinterSlicerDraft;
  onChange: (next: PrinterSlicerDraft) => void;
}) {
  const id = useId();
  const [rows, setRows] = useState<ModelSlicerProfile[] | null>(null);
  const [plugins, setPlugins] = useState<PluginList | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [showOverride, setShowOverride] = useState(false);
  const [catalog, setCatalog] = useState<{ provider: string; names: string[] } | null>(null);
  const preferred = value.preferred_slicer_plugin_id;
  const nozzle = value.nozzle_variant;
  const override = value.machine_profile_override ?? '';

  useEffect(() => {
    let alive = true;
    fetchPlugins().then(list => { if (alive) setPlugins(list); }).catch(() => { if (alive) setLoadError(true); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    if (!printer.model_uuid) return;
    fetchModelSlicerProfiles(printer.model_uuid)
      .then(list => { if (alive) setRows(list); })
      .catch(() => { if (alive) setLoadError(true); });
    return () => { alive = false; };
  }, [printer.model_uuid]);

  const slicers = (plugins?.plugins ?? []).filter(p => p.enabled && p.provides.some(x => x.capability === 'gcode.produce'));
  const selection = plugins?.selections ?? {};
  const globalSlicer = 'gcode.produce' in selection ? selection['gcode.produce'] : (slicers.length === 1 ? slicers[0].id : null);
  const inherited = printer.model_slicer_plugin_id ?? printer.brand_slicer_plugin_id ?? globalSlicer;
  const mappedProviders = [...new Set((rows ?? []).map(row => row.provider_plugin_id))];
  const savedProvider = preferred === (printer.preferred_slicer_plugin_id ?? null) ? printer.slicer_plugin_id : undefined;
  // Older printer responses omit the resolved provider. A single mapped provider can still label their existing presets.
  const provider = preferred || inherited || savedProvider || (printer.slicer_plugin_id === undefined && mappedProviders.length === 1
    ? mappedProviders[0] : null);
  const mappings = (rows ?? []).filter(row => row.provider_plugin_id === provider)
    .sort((a, b) => a.nozzle_diameter_mm - b.nozzle_diameter_mm || Number(a.flow === 'high_flow') - Number(b.flow === 'high_flow'));
  const defaultRow = mappings.find(row => row.is_default);
  const selectedRow = nozzle ? mappings.find(row => sameVariant(variantOf(row), nozzle)) : defaultRow;
  const key = (v: NozzleVariant) => `${v.diameter_mm}|${v.flow}`;
  const unavailable = preferred && !slicers.some(s => s.id === preferred);
  const inheritedUnavailable = inherited && !slicers.some(s => s.id === inherited);
  const inheritedLabel = slicers.find(s => s.id === inherited)?.name ?? inherited;
  const savedSelection = preferred === (printer.preferred_slicer_plugin_id ?? null)
    && ((!nozzle && !printer.nozzle_variant) || sameVariant(nozzle, printer.nozzle_variant));
  const profile = override.trim() || selectedRow?.machine_profile
    || (savedSelection && !printer.machine_profile_override && rows === null ? printer.machine_profile : null);

  useEffect(() => {
    if (!showOverride || !provider) return;
    let alive = true;
    fetchMachineCatalog(provider).then(items => {
      if (alive) setCatalog({ provider, names: [...new Set((Array.isArray(items) ? items : []).map(item => item.name))] });
    }).catch(() => {});
    return () => { alive = false; };
  }, [showOverride, provider]);

  return (
    <div className="col gap-2">
      <div className="tag-key">Slicing defaults</div>
      {printer.print_artifact_kind && <div className="tiny muted">
        Files sent to this printer use {printer.print_artifact_kind === 'gcode_3mf' ? '.gcode.3mf' : '.gcode'}.
      </div>}
      {(slicers.length > 1 || preferred || inheritedUnavailable || (slicers.length === 1 && !inherited)) && (
        <div className="col gap-1">
          <label className="label" htmlFor={`${id}-slicer`}>Slice jobs for this printer with</label>
          <select id={`${id}-slicer`} className="select" value={preferred ?? ''} onChange={e => {
            const next = e.target.value || null;
            onChange({ ...value, preferred_slicer_plugin_id: next,
              machine_profile_override: (next ?? inherited) === provider ? override : null });
          }}>
            <option value="">{printer.model_slicer_plugin_id ? 'Model default slicer'
              : printer.brand_slicer_plugin_id ? 'Brand default converter' : 'Default slicer'}{inheritedLabel ? ` (${inheritedLabel})` : ''}</option>
            {unavailable && <option value={preferred}>{preferred}{plugins ? ' (disabled or removed)' : ''}</option>}
            {slicers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      )}
      {printer.model_uuid ? (
        <div className="col gap-1">
          <label className="label" htmlFor={`${id}-nozzle`}>Nozzle fitted</label>
          <select id={`${id}-nozzle`} className="select" value={nozzle ? key(nozzle) : ''} onChange={e => {
            const row = mappings.find(r => key(variantOf(r)) === e.target.value);
            onChange({ ...value, nozzle_variant: row ? variantOf(row) : null });
          }}>
            <option value="">{defaultRow ? `Model default (${variantLabel(variantOf(defaultRow))})` : 'Model default'}</option>
            {nozzle && !selectedRow && <option value={key(nozzle)}>{variantLabel(nozzle)} (not mapped)</option>}
            {mappings.map(row => {
              const variant = variantOf(row);
              return <option key={row.id} value={key(variant)}>{variantLabel(variant)} · {row.machine_profile}</option>;
            })}
          </select>
        </div>
      ) : <div className="tiny muted">This printer has no registered model, so it has no nozzle variants.</div>}
      {(unavailable || (!preferred && inheritedUnavailable)) && plugins
        ? <div className="tiny text-err">The selected slicer is unavailable. Choose another slicer or restore it in Settings → Plugins.</div>
        : profile ? <div className="tiny muted">Slices with <span className="mono">{profile}</span>.</div>
        : printer.model_uuid && rows !== null && <div className="tiny muted">
          {savedSelection && printer.machine_profile_reason ? printer.machine_profile_reason : 'No machine profile is mapped for this slicer and nozzle'}
          {' '}— set one under Settings → Printer models.
        </div>}
      {loadError && <div className="tiny text-err">Some slicer settings could not be loaded.</div>}
      <details onToggle={e => setShowOverride(e.currentTarget.open)}>
        <summary className="small">Machine profile override</summary>
        <label className="label" htmlFor={`${id}-override`}>Custom machine profile</label>
        <input id={`${id}-override`} className="input" value={override} placeholder="Use the model’s mapped profile"
               list={`${id}-profiles`} disabled={!provider && !override}
               onChange={e => {
                 const name = e.target.value;
                 onChange({ ...value, machine_profile_override: name,
                   preferred_slicer_plugin_id: name.trim() ? preferred ?? provider : preferred });
               }} />
        <datalist id={`${id}-profiles`}>
          {catalog?.provider === provider && catalog?.names.map(name => <option key={name} value={name} />)}
        </datalist>
        <div className="tiny muted">{provider || override
          ? 'A custom profile keeps this printer on the selected slicer. Leave the profile blank to use its model mapping.'
          : 'Choose a slicer before setting a custom machine profile.'}</div>
      </details>
    </div>
  );
}
