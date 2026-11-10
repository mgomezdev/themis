import { type SlicedVersion } from '../../api/files';
import { staleLabels } from '../../components/SliceCache';
import { fmtDuration } from './versions';

// New job: the offer to print an already-sliced version of a plate.

/** "This model has cached gcode for this plate — use it, or reslice?" (BIZ-194). */
export function CachedVersionPrompt({ plate, multiPlate, versions, useLatest, onUse, onReslice }: {
  plate: number; multiPlate: boolean; versions: SlicedVersion[]; useLatest: boolean;
  onUse: (v: SlicedVersion) => void; onReslice: () => void;
}) {
  return (
    <section className="card col gap-2" aria-label="Cached sliced versions" style={{ padding: '12px 14px' }}>
      <div className="row between">
        <span className="fw-600">Cached gcode for plate {plate}</span>
        <button className="btn ghost sm" onClick={onReslice}>Reslice</button>
      </div>
      <span className="tiny muted">This model was sliced before. Print a saved version instead of slicing again?</span>
      {multiPlate && (
        <span className="tiny muted">Using one queues just that plate&apos;s gcode — the other plates of this model aren&apos;t queued.</span>
      )}
      {versions.map(v => {
        const chips = [
          ...(v.source_changed ? ['Model changed since slicing'] : []),
          ...(v.stale ? staleLabels(v.stale_reasons) : []),
          ...(!v.printable_now ? ['No matching printer'] : []),
        ];
        return (
          <div key={v.id} className="row between" data-testid="cached-version" style={{ gap: 12, padding: '6px 0', borderTop: '1px solid var(--border-1)' }}>
            <div className="col" style={{ minWidth: 0, gap: 2 }}>
              <span className="small" style={{ fontWeight: 500, wordBreak: 'break-word' }}>{v.name}</span>
              <span className="tiny muted">
                {v.machine_preset} · {v.process_preset} · {v.filament_type}
                {v.filament_color && v.filament_color !== 'any' && (
                  <span title={v.filament_color} style={{ display: 'inline-block', width: 9, height: 9, marginLeft: 4, background: v.filament_color, border: '1px solid var(--border-2)', verticalAlign: 'middle' }} />
                )}
                {fmtDuration(v.estimated_seconds) && ` · ${fmtDuration(v.estimated_seconds)}`}
                {v.filament_grams != null && ` · ${v.filament_grams.toFixed(1)} g`}
                {` · ${new Date(v.created_at).toLocaleDateString()}`}
              </span>
              {chips.length > 0 && (
                <span className="row gap-1 wrap">
                  {chips.map(c => <span key={c} className="pill" style={{ color: 'var(--warn)', border: '1px solid currentColor', background: 'transparent', fontSize: 10.5 }}>{c}</span>)}
                </span>
              )}
              {v.stale && useLatest && <span className="tiny muted">Automatic reuse would reslice this.</span>}
            </div>
            <button className="btn sm" onClick={() => onUse(v)}>Use this version</button>
          </div>
        );
      })}
    </section>
  );
}
