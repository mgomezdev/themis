/** Plate map layout: the one set of rules shared by the interactive view (PlateMap.tsx) and the server snapshot
 *  (backend/app/services/plate_map.py). Spec: docs/superpowers/plans/2026-11-26-biz-405-plate-map.md § Layout spec.
 *  Both implementations are checked against contracts/plate-map-cases.json, so change the spec, the corpus and both
 *  implementations together.
 *
 *  Input is neutral: a bed, objects with unique labels and footprints in bed millimetres, caller-supplied states.
 *  Bed +X points right, +Y points away from the operator; `origin` is where (0,0) sits, in mm from the bed's
 *  front-left corner. View coordinates are millimetres too (so the aspect ratio is exact) with the front at the bottom. */

export type Point = [number, number];
/** [x, y, width, height] */
export type Rect = [number, number, number, number];

export interface PlateBed { width: number; depth: number; origin?: { x: number; y: number } }
export interface PlateState { id: string; name: string; color: string }
export type Footprint = { polygon: Point[] } | { box: [number, number, number, number] };
export interface PlateObject { label: string; state: string; selectable: boolean; footprint?: Footprint | null }
export interface Plate { bed: PlateBed | null; states: PlateState[]; objects: PlateObject[] }

export const PATTERNS = ['solid', 'diagonal', 'dots', 'cross', 'horizontal', 'checker'] as const;
export type PatternName = (typeof PATTERNS)[number];

export const MARGIN_RATIO = 0.04;
export const MIN_HIT_RATIO = 0.06;
export const LABEL_MAX = 40;
const EPS = 1e-6;
/** Every number (mm) must be within ±MAX_MM: keeps formatting out of exponent notation and Python out of OverflowError. */
export const MAX_MM = 100000;
/** XML 1.0 Char: a label or state name outside it would make the snapshot unparseable (or, for a lone surrogate, unencodable). */
const XML_TEXT = /^[\t\n\r\x20-\uD7FF\uE000-\uFFFD\u{10000}-\u{10FFFF}]*$/u;
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** A caller colour if it is #rgb / #rrggbb, else null (render the neutral fill). Nothing else reaches an attribute. */
export const safeColor = (color: string): string | null => (HEX.test(color) ? color : null);

export interface LaidOutObject {
  label: string;
  /** Input order; the map marker shows index + 1. */
  index: number;
  state: PlateState;
  pattern: PatternName;
  /** The state's colour when it is #rgb / #rrggbb, else null (the renderer uses a neutral fill). */
  fill: string | null;
  selectable: boolean;
  /** View-space polygon, or null when the object has no footprint (list only). */
  points: Point[] | null;
  /** Enlarged hit rect for a tiny object, else null. */
  hit: Rect | null;
  anchor: Point | null;
  offBed: boolean;
  area: number;
}

export interface PlateLayout {
  /** null = list only (no bed, or no object has a footprint). */
  geometry: { viewBox: Rect; bed: Rect; scale: number } | null;
  /** Input order. */
  objects: LaidOutObject[];
  /** Bottom to top: area descending, ties by input order. */
  drawOrder: LaidOutObject[];
  toView: (p: Point) => Point;
}

// ── validation (messages are shared with plate_map.py; the corpus checks substrings) ───────────────────────────────

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= MAX_MM;
const unknownKey = (o: Obj, allowed: string[]) => Object.keys(o).find(k => !allowed.includes(k));

function polygonArea(pts: readonly Point[]): number {
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

function validateFootprint(fp: unknown): string | null {
  if (fp === null || fp === undefined) return null;
  if (!isObj(fp)) return 'footprint: needs polygon or box';
  const extra = unknownKey(fp, ['polygon', 'box']);
  if (extra) return `unknown key ${extra}`;
  if (('polygon' in fp) === ('box' in fp)) return 'footprint: needs polygon or box';
  if ('box' in fp) {
    const b = fp.box;
    if (!Array.isArray(b) || b.length !== 4 || !b.every(isNum) || !(b[0] < b[2]) || !(b[1] < b[3]))
      return 'footprint: box must be [x0, y0, x1, y1] with x0 < x1 and y0 < y1';
    return null;
  }
  const poly = fp.polygon;
  if (!Array.isArray(poly) || poly.length < 3) return 'footprint: polygon needs at least 3 points';
  if (!poly.every(p => Array.isArray(p) && p.length === 2 && p.every(isNum))) return 'footprint: coordinates must be finite numbers';
  if (Math.abs(polygonArea(poly as Point[])) < EPS) return 'footprint: zero area';
  return null;
}

/** null when the plate is usable, else the first problem found. */
export function validatePlate(raw: unknown): string | null {
  if (!isObj(raw)) return 'plate: must be an object';
  const extra = unknownKey(raw, ['bed', 'states', 'objects']);
  if (extra) return `unknown key ${extra}`;
  const bed = raw.bed;
  if (bed !== null && bed !== undefined) {
    if (!isObj(bed)) return 'bed: width and depth must be positive numbers';
    const bx = unknownKey(bed, ['width', 'depth', 'origin']);
    if (bx) return `unknown key ${bx}`;
    if (!isNum(bed.width) || !isNum(bed.depth) || bed.width <= 0 || bed.depth <= 0) return 'bed: width and depth must be positive numbers';
    if (bed.origin !== undefined) {
      if (!isObj(bed.origin)) return 'bed: origin must be finite numbers';
      const ox = unknownKey(bed.origin, ['x', 'y']);
      if (ox) return `unknown key ${ox}`;
      if (!isNum(bed.origin.x) || !isNum(bed.origin.y)) return 'bed: origin must be finite numbers';
    }
  }
  const states = raw.states;
  if (!Array.isArray(states) || states.length < 1 || states.length > PATTERNS.length) return 'states: 1 to 6 states required';
  const ids = new Set<string>();
  for (const s of states) {
    if (!isObj(s)) return 'states: id, name and color must be strings';
    const sx = unknownKey(s, ['id', 'name', 'color']);
    if (sx) return `unknown key ${sx}`;
    if (typeof s.id !== 'string' || typeof s.name !== 'string' || typeof s.color !== 'string') return 'states: id, name and color must be strings';
    if (!XML_TEXT.test(s.name)) return 'states: name has invalid characters';
    if (ids.has(s.id)) return 'states: duplicate id';
    ids.add(s.id);
  }
  const objects = raw.objects;
  if (!Array.isArray(objects)) return 'objects: must be a list';
  const labels = new Set<string>();
  for (const o of objects) {
    if (!isObj(o)) return 'objects: must be a list';
    const ox = unknownKey(o, ['label', 'state', 'selectable', 'footprint']);
    if (ox) return `unknown key ${ox}`;
    if (typeof o.label !== 'string' || o.label === '') return 'objects: label must be a non-empty string';
    if (!XML_TEXT.test(o.label)) return 'objects: label has invalid characters';
    if (labels.has(o.label)) return 'objects: duplicate label';
    labels.add(o.label);
    if (typeof o.state !== 'string' || !ids.has(o.state)) return 'objects: unknown state';
    if (typeof o.selectable !== 'boolean') return 'objects: selectable must be a boolean';
    const fp = validateFootprint(o.footprint);
    if (fp) return fp;
  }
  return null;
}

// ── layout ──────────────────────────────────────────────────────────────────────────────────────────────────────────

const footprintPoints = (fp: Footprint): Point[] =>
  'box' in fp
    ? [[fp.box[0], fp.box[1]], [fp.box[2], fp.box[1]], [fp.box[2], fp.box[3]], [fp.box[0], fp.box[3]]]
    : fp.polygon.map(([x, y]) => [x, y] as Point);

function bbox(pts: readonly Point[]): Rect {
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys);
  return [x0, y0, Math.max(...xs) - x0, Math.max(...ys) - y0];
}

/** Area centroid's y; on that horizontal line, the midpoint of the widest span inside the polygon. */
function anchorOf(pts: readonly Point[]): Point {
  const a = polygonArea(pts);
  let cx = 0, cy = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
    const f = x1 * y2 - x2 * y1;
    cx += (x1 + x2) * f;
    cy += (y1 + y2) * f;
  }
  cx /= 6 * a;
  cy /= 6 * a;
  const xs: number[] = [];
  for (let i = 0; i < pts.length; i++) {
    const [ax, ay] = pts[i], [bx, by] = pts[(i + 1) % pts.length];
    if ((ay <= cy && cy < by) || (by <= cy && cy < ay)) xs.push(ax + ((cy - ay) * (bx - ax)) / (by - ay));
  }
  xs.sort((p, q) => p - q);
  let best: Point = [cx, cy], width = -1;
  for (let i = 0; i + 1 < xs.length; i += 2) {
    if (xs[i + 1] - xs[i] > width) { width = xs[i + 1] - xs[i]; best = [(xs[i] + xs[i + 1]) / 2, cy]; }
  }
  return best;
}

export function computePlateLayout(plate: Plate): PlateLayout {
  const bed = plate.bed;
  const ox = bed?.origin?.x ?? 0, oy = bed?.origin?.y ?? 0;
  const maxY = (bed?.depth ?? 0) - oy;
  const toView = ([x, y]: Point): Point => [x + ox, maxY - y];
  const stateIndex = new Map(plate.states.map((s, i) => [s.id, i]));
  const hasGeometry = !!bed && plate.objects.some(o => o.footprint);
  const M = bed ? Math.max(bed.width, bed.depth) : 0;
  const minHit = MIN_HIT_RATIO * M;

  const objects: LaidOutObject[] = plate.objects.map((o, index) => {
    const si = stateIndex.get(o.state) ?? 0;
    const state = plate.states[si];
    const base = {
      label: o.label, index, state, pattern: PATTERNS[si], fill: safeColor(state.color),
      selectable: o.selectable,
    };
    if (!hasGeometry || !o.footprint) return { ...base, points: null, hit: null, anchor: null, offBed: false, area: 0 };
    const points = footprintPoints(o.footprint).map(toView);
    const [bx, by, bw, bh] = bbox(points);
    const hit: Rect | null = bw < minHit || bh < minHit
      ? (() => { const w = Math.max(bw, minHit), h = Math.max(bh, minHit); return [bx + bw / 2 - w / 2, by + bh / 2 - h / 2, w, h]; })()
      : null;
    const offBed = points.some(([x, y]) => x < -EPS || x > bed!.width + EPS || y < -EPS || y > bed!.depth + EPS);
    return { ...base, points, hit, anchor: anchorOf(points), offBed, area: Math.abs(polygonArea(points)) };
  });

  const drawOrder = objects.filter(o => o.points).sort((p, q) => q.area - p.area || p.index - q.index);
  if (!hasGeometry) return { geometry: null, objects, drawOrder, toView };

  let x0 = 0, y0 = 0, x1 = bed!.width, y1 = bed!.depth;
  for (const o of drawOrder) {
    const [bx, by, bw, bh] = bbox(o.points!);
    x0 = Math.min(x0, bx); y0 = Math.min(y0, by); x1 = Math.max(x1, bx + bw); y1 = Math.max(y1, by + bh);
  }
  const m = MARGIN_RATIO * M;
  return {
    geometry: { viewBox: [x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m], bed: [0, 0, bed!.width, bed!.depth], scale: M },
    objects, drawOrder, toView,
  };
}

// ── hit testing ─────────────────────────────────────────────────────────────────────────────────────────────────────

function insidePolygon([px, py]: Point, pts: readonly Point[]): boolean {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

const insideRect = ([px, py]: Point, [x, y, w, h]: Rect) => px >= x && px <= x + w && py >= y && py <= y + h;

/** The topmost object under a view point (real shape, or a tiny object's hit rect), selectable or not. */
export function hitTest(layout: PlateLayout, p: Point): LaidOutObject | null {
  for (let i = layout.drawOrder.length - 1; i >= 0; i--) {
    const o = layout.drawOrder[i];
    if (insidePolygon(p, o.points!) || (o.hit && insideRect(p, o.hit))) return o;
  }
  return null;
}

/** What a click reports: the topmost object's label, or null when it is not selectable (clicks do not fall through). */
export function pickLabel(layout: PlateLayout, p: Point): string | null {
  const o = hitTest(layout, p);
  return o && o.selectable ? o.label : null;
}

/** Client (CSS px) → view coordinates for an SVG drawn with preserveAspectRatio="xMidYMid meet". */
export function clientToView(
  rect: { left: number; top: number; width: number; height: number }, viewBox: readonly number[], clientX: number, clientY: number,
): Point {
  const [vx, vy, vw, vh] = viewBox;
  const s = Math.min(rect.width / vw, rect.height / vh);
  const offX = (rect.width - vw * s) / 2, offY = (rect.height - vh * s) / 2;
  return [vx + (clientX - rect.left - offX) / s, vy + (clientY - rect.top - offY) / s];
}

// ── formatting ──────────────────────────────────────────────────────────────────────────────────────────────────────

/** At most 3 decimals, trailing zeros dropped, never "-0". */
export function formatNum(n: number): string {
  const s = n.toFixed(3).replace(/\.?0+$/, '');
  return s === '-0' ? '0' : s;
}

export const formatPoints = (pts: readonly Point[]) => pts.map(([x, y]) => `${formatNum(x)},${formatNum(y)}`).join(' ');

/** First LABEL_MAX code points (not UTF-16 units, to match Python) plus an ellipsis. */
export function truncateLabel(label: string, max = LABEL_MAX): string {
  const cps = Array.from(label);
  return cps.length > max ? `${cps.slice(0, max).join('')}…` : label;
}
