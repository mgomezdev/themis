import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { MouseEvent, ReactNode } from 'react';
import { PATTERNS, clientToView, computePlateLayout, formatNum, formatPoints, pickLabel, safeColor, validatePlate } from './layout';
import type { LaidOutObject, PatternName, Plate } from './layout';

export interface PlateMapProps {
  plate: Plate;
  /** Called with the object's label when a selectable object is clicked on the map or in the list. */
  onObjectClick: (label: string) => void;
}

/** Pattern tile content for a tile of side `t` (user units). 'solid' has no overlay. Mirrored in plate_map.py. */
function patternShapes(name: PatternName, t: number): ReactNode {
  const w = t * 0.16;
  switch (name) {
    case 'diagonal':
      return <path className="plate-map-ink-line" strokeWidth={w} d={`M0,${t} L${t},0 M${-t / 4},${t / 4} L${t / 4},${-t / 4} M${(3 * t) / 4},${(5 * t) / 4} L${(5 * t) / 4},${(3 * t) / 4}`} />;
    case 'dots':
      return <circle className="plate-map-ink" cx={t / 2} cy={t / 2} r={t * 0.2} />;
    case 'cross':
      return <path className="plate-map-ink-line" strokeWidth={w} d={`M0,0 L${t},${t} M${t},0 L0,${t}`} />;
    case 'horizontal':
      return <path className="plate-map-ink-line" strokeWidth={w * 1.5} d={`M0,${t / 2} H${t}`} />;
    case 'checker':
      return <><rect className="plate-map-ink" width={t / 2} height={t / 2} /><rect className="plate-map-ink" x={t / 2} y={t / 2} width={t / 2} height={t / 2} /></>;
    default:
      return null;
  }
}

function PatternDefs({ prefix, tile }: { prefix: string; tile: number }) {
  return (
    <defs>
      {PATTERNS.filter(p => p !== 'solid').map(p => (
        <pattern key={p} id={`${prefix}-${p}`} patternUnits="userSpaceOnUse" width={tile} height={tile}>{patternShapes(p, tile)}</pattern>
      ))}
    </defs>
  );
}

function Swatch({ prefix, color, pattern }: { prefix: string; color: string | null; pattern: PatternName }) {
  const id = `${prefix}-sw-${pattern}`;
  return (
    <svg className="plate-map-swatch" viewBox="0 0 24 16" aria-hidden="true">
      <PatternDefs prefix={id} tile={6} />
      <rect className={color ? 'plate-map-swatch-fill' : 'plate-map-swatch-fill neutral'} width={24} height={16} fill={color ?? undefined} data-pattern={pattern} />
      {pattern !== 'solid' && <rect width={24} height={16} fill={`url(#${id}-${pattern})`} />}
    </svg>
  );
}

/** A plate drawn from neutral geometry, with a legend and an object list. The map and the list highlight each other. */
export function PlateMap({ plate, onObjectClick }: PlateMapProps) {
  const error = useMemo(() => validatePlate(plate), [plate]);
  const layout = useMemo(() => (error ? null : computePlateLayout(plate)), [plate, error]);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const prefix = `pm${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const rows = useRef(new Map<string, HTMLButtonElement>());

  useEffect(() => {
    if (highlighted) rows.current.get(highlighted)?.scrollIntoView?.({ block: 'nearest' });
  }, [highlighted]);

  if (error || !layout) return <div className="notice warn" role="alert">Plate map unavailable: {error}</div>;

  const select = (label: string) => { setHighlighted(label); onObjectClick(label); };
  const geo = layout.geometry;
  const lit = layout.drawOrder.find(o => o.label === highlighted);

  const onMapClick = (e: MouseEvent<SVGSVGElement>) => {
    if (!geo) return;
    const label = pickLabel(layout, clientToView(e.currentTarget.getBoundingClientRect(), geo.viewBox, e.clientX, e.clientY));
    if (label) select(label);
  };

  const objectClass = (o: LaidOutObject) =>
    ['plate-map-object', o.fill ? '' : 'neutral', o.selectable ? 'selectable' : 'locked', o.offBed ? 'off-bed' : ''].filter(Boolean).join(' ');

  return (
    <div className={geo ? 'plate-map' : 'plate-map list-only'}>
      {geo && (
        <div className="plate-map-canvas">
          <svg
            className="plate-map-svg"
            viewBox={geo.viewBox.map(formatNum).join(' ')}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={`Plate map, ${layout.drawOrder.length} objects. Front of the bed at the bottom. Use the list to select.`}
            onClick={onMapClick}
          >
            <PatternDefs prefix={prefix} tile={geo.scale * 0.025} />
            <rect className="plate-map-bed" x={0} y={0} width={geo.bed[2]} height={geo.bed[3]} />
            <text className="plate-map-front" x={formatNum(geo.bed[2] / 2)} y={formatNum(geo.bed[3] + geo.scale * 0.03)}
              fontSize={formatNum(geo.scale * 0.024)} textAnchor="middle" dominantBaseline="middle">Front</text>
            {layout.drawOrder.map(o => {
              const pts = formatPoints(o.points!);
              return (
                <g key={o.label}>
                  <title>{o.label}</title>
                  <polygon className={objectClass(o)} points={pts} fill={o.fill ?? undefined} data-label={o.label}
                    data-pattern={o.pattern} data-off-bed={String(o.offBed)} data-selectable={String(o.selectable)} />
                  {o.pattern !== 'solid' && <polygon className="plate-map-pattern" points={pts} fill={`url(#${prefix}-${o.pattern})`} />}
                  {o.hit && <rect className="plate-map-hit" data-index={o.index} x={formatNum(o.hit[0])} y={formatNum(o.hit[1])}
                    width={formatNum(o.hit[2])} height={formatNum(o.hit[3])} />}
                </g>
              );
            })}
            {layout.objects.filter(o => o.anchor).map(o => (
              <text key={o.label} className="plate-map-marker" x={formatNum(o.anchor![0])} y={formatNum(o.anchor![1])}
                fontSize={formatNum(geo.scale * 0.035)} strokeWidth={formatNum(geo.scale * 0.008)} textAnchor="middle" dominantBaseline="central">
                {o.index + 1}
              </text>
            ))}
            {lit && <polygon className="plate-map-highlight" points={formatPoints(lit.points!)} data-label={lit.label} />}
          </svg>
        </div>
      )}
      <div className="plate-map-side">
        <ul className="plate-map-legend" aria-label="States">
          {plate.states.map((s, i) => (
            <li key={s.id} className="plate-map-legend-item">
              <Swatch prefix={prefix} color={safeColor(s.color)} pattern={PATTERNS[i]} />
              <span>{s.name}</span>
            </li>
          ))}
        </ul>
        <ul className="plate-map-list" aria-label="Objects">
          {layout.objects.map(o => (
            <li key={o.label}>
              <button
                type="button"
                ref={el => { if (el) rows.current.set(o.label, el); else rows.current.delete(o.label); }}
                className={o.label === highlighted ? 'plate-map-row selected' : 'plate-map-row'}
                data-label={o.label}
                title={o.label}
                aria-current={o.label === highlighted ? true : undefined}
                disabled={!o.selectable}
                onClick={() => select(o.label)}
              >
                <span className="plate-map-num num">{o.index + 1}</span>
                <Swatch prefix={`${prefix}-r${o.index}`} color={o.fill} pattern={o.pattern} />
                <span className="plate-map-label">{o.label}</span>
                <span className="plate-map-state tiny muted">{o.state.name}</span>
                {o.offBed && <span className="pill warn">Off bed</span>}
                {geo && !o.points && <span className="pill idle">Not on map</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
