import { useEffect, useState } from 'react';
import { sameVariant, variantLabel, type NozzleVariant } from '../api/printers';
import { fetchModelSlicerProfiles, variantOf, type ModelSlicerProfile } from '../api/slicerProfiles';

/**
 * The physical nozzle of one printer (BIZ-294). Lists only the variants its MODEL has a slicer profile for; the default entry is
 * "same as every other printer of the model". A printer needs this only when its hardware differs (0.6 mm, high-flow).
 */
export function NozzleVariantPicker({ modelUuid, value, onChange, resolvedProfile, reason }: {
  modelUuid: string | null;
  value: NozzleVariant | null;
  onChange: (v: NozzleVariant | null) => void;
  resolvedProfile?: string | null;      // what the printer slices with now, shown for reference
  reason?: string | null;
}) {
  const [rows, setRows] = useState<ModelSlicerProfile[] | null>(null);

  useEffect(() => {
    if (!modelUuid) { setRows([]); return; }
    let alive = true;
    fetchModelSlicerProfiles(modelUuid).then(r => { if (alive) setRows(r); }).catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, [modelUuid]);

  const variants: NozzleVariant[] = [];
  for (const r of rows ?? []) {
    const v = variantOf(r);
    if (!variants.some(x => sameVariant(x, v))) variants.push(v);
  }
  variants.sort((a, b) => a.diameter_mm - b.diameter_mm || (a.flow === b.flow ? 0 : a.flow === 'standard' ? -1 : 1));   // standard before high-flow
  const defaultRow = (rows ?? []).find(r => r.is_default);
  const key = (v: NozzleVariant) => `${v.diameter_mm}|${v.flow}`;
  const stale = value && !variants.some(v => sameVariant(v, value));     // a saved variant the model no longer maps

  return (
    <div className="col gap-2">
      <div className="tag-key">Nozzle</div>
      {!modelUuid ? (
        <div className="tiny muted">This printer has no registered model, so it has no nozzle variants.</div>
      ) : (
        <>
          <label className="label" htmlFor="nozzle-variant">Nozzle fitted</label>
          <select id="nozzle-variant" className="select" style={{ maxWidth: 260 }} value={value ? key(value) : ''}
                  onChange={e => {
                    const next = variants.find(v => key(v) === e.target.value) ?? null;
                    onChange(next);
                  }}>
            <option value="">{defaultRow ? `Model default (${variantLabel(variantOf(defaultRow))})` : 'Model default'}</option>
            {stale && value && <option value={key(value)}>{variantLabel(value)} (not mapped)</option>}
            {variants.map(v => <option key={key(v)} value={key(v)}>{variantLabel(v)}</option>)}
          </select>
          <div className="tiny muted">
            {resolvedProfile
              ? <>Slices with <span className="mono">{resolvedProfile}</span>.</>
              : <>{reason ?? 'No slicer profile is set for this model yet'} — set it under Settings → Printer models.</>}
            {' '}Only needed when this printer&apos;s nozzle differs from the other printers of its model.
          </div>
        </>
      )}
    </div>
  );
}
