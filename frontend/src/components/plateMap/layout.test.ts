import { describe, expect, it } from 'vitest';
import { PATTERNS, clientToView, computePlateLayout, formatNum, hitTest, pickLabel, truncateLabel, validatePlate } from './layout';
import type { Point } from './layout';
import { caseNamed, clone, corpus } from '../../dev/plateMapCorpus';

const close = (actual: number, expected: number) => expect(Math.abs(actual - expected)).toBeLessThan(1e-3);
const closeAll = (actual: readonly number[], expected: readonly number[]) => {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((v, i) => close(v, expected[i]));
};
const closePoints = (actual: readonly Point[], expected: readonly Point[]) => {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((p, i) => closeAll(p, expected[i]));
};

// Every tricky part named in BIZ-405 has a fixture; dropping one should fail here, not silently lose coverage.
const REQUIRED = ['square-front-left', 'centre-origin', 'back-right-origin', 'scale-wide-large', 'scale-small-portrait',
  'tiny-object', 'overlap', 'concave-l', 'rotated-rect', 'off-bed', 'labels', 'six-states', 'mixed-selectable',
  'missing-footprint', 'no-geometry', 'hostile-states'];

describe('plate-map corpus (backend/tests/services/test_plate_map.py runs the same cases)', () => {
  it('covers every tricky part', () => {
    const names = corpus.cases.map(c => c.name);
    for (const n of REQUIRED) expect(names).toContain(n);
  });

  it.each(corpus.cases.map(c => [c.name, c] as const))('%s is valid', (_n, c) => {
    expect(validatePlate(clone(c.plate))).toBeNull();
  });

  it.each(corpus.cases.map(c => [c.name, c] as const))('%s: layout matches the expected numbers', (_n, c) => {
    const layout = computePlateLayout(clone(c.plate));
    const e = c.expect;
    if (e.list_only !== undefined) expect(layout.geometry === null).toBe(e.list_only);
    if (e.view_box) closeAll(layout.geometry!.viewBox, e.view_box);
    if (e.draw_order) expect(layout.drawOrder.map(o => o.label)).toEqual(e.draw_order);
    for (const [label, want] of Object.entries(e.objects ?? {})) {
      const got = layout.objects.find(o => o.label === label)!;
      expect(got, label).toBeDefined();
      if (want.points) closePoints(got.points!, want.points);
      if (want.off_bed !== undefined) expect(got.offBed, `${label} off_bed`).toBe(want.off_bed);
      if (want.hit !== undefined) {
        if (want.hit === null) expect(got.hit).toBeNull();
        else closeAll(got.hit!, want.hit);
      }
      if (want.anchor) closeAll(got.anchor!, want.anchor);
      if (want.pattern) expect(got.pattern).toBe(want.pattern);
      if (want.fill !== undefined) expect(got.fill).toBe(want.fill);
      if (want.on_map !== undefined) expect(got.points !== null).toBe(want.on_map);
    }
    for (const [label, want] of Object.entries(e.truncated ?? {})) expect(truncateLabel(label)).toBe(want);
  });

  const hitCases = corpus.cases.flatMap(c => (c.expect.hits ?? []).map(h => [c.name, h.at.join(','), h.label, c.plate, h.at] as const));
  it.each(hitCases)('%s: a click at bed (%s) reports %s', (_n, _at, label, plate, at) => {
    const layout = computePlateLayout(clone(plate));
    expect(pickLabel(layout, layout.toView(at))).toBe(label);
  });

  it.each(corpus.invalid.map(c => [c.name, c] as const))('rejects %s', (_n, c) => {
    expect(validatePlate(clone(c.plate))).toContain(c.message);
  });
});

describe('layout rules beyond the corpus numbers', () => {
  it('the front of the bed is at the bottom of the screen and left stays left', () => {
    const layout = computePlateLayout(caseNamed('square-front-left').plate);
    const front = layout.toView([0, 0]), back = layout.toView([0, 256]), right = layout.toView([256, 0]);
    expect(front[1]).toBeGreaterThan(back[1]);
    expect(right[0]).toBeGreaterThan(front[0]);
  });

  it('every state gets a different pattern, so states differ by more than colour', () => {
    const layout = computePlateLayout(caseNamed('six-states').plate);
    expect(new Set(layout.objects.map(o => o.pattern)).size).toBe(6);
    expect(new Set(PATTERNS).size).toBe(6);
  });

  it('the concave marker sits inside the shape (where the centroid would not)', () => {
    const layout = computePlateLayout(caseNamed('concave-l').plate);
    const l = layout.objects[0];
    expect(hitTest(layout, l.anchor!)?.label).toBe('L');
  });

  it('hitTest returns the topmost object even when it is not selectable; pickLabel then reports nothing', () => {
    const layout = computePlateLayout(caseNamed('mixed-selectable').plate);
    const p = layout.toView([80, 190]);
    expect(hitTest(layout, p)?.label).toBe('Lid');
    expect(pickLabel(layout, p)).toBeNull();
  });

  it('list-only layouts have no geometry and no hits', () => {
    const layout = computePlateLayout(caseNamed('no-geometry').plate);
    expect(layout.geometry).toBeNull();
    expect(layout.objects.every(o => o.points === null)).toBe(true);
    expect(hitTest(layout, [0, 0])).toBeNull();
  });

  it('clientToView undoes xMidYMid meet letterboxing', () => {
    // A 1080×280 viewBox shown in a 540×540 box: scale 0.5, content 140px tall, centred (200px bars top and bottom).
    const vb = [-40, -40, 1080, 280] as const;
    const rect = { left: 10, top: 20, width: 540, height: 540 };
    closeAll(clientToView(rect, vb, 10, 220), [-40, -40]);
    closeAll(clientToView(rect, vb, 550, 360), [1040, 240]);
    // A tall viewBox in a wide box: bars left and right.
    const tall = [0, 0, 100, 400] as const;
    closeAll(clientToView({ left: 0, top: 0, width: 400, height: 400 }, tall, 150, 0), [0, 0]);
    closeAll(clientToView({ left: 0, top: 0, width: 400, height: 400 }, tall, 250, 400), [100, 400]);
  });

  it('formats numbers with at most 3 decimals and no negative zero', () => {
    expect(formatNum(10)).toBe('10');
    expect(formatNum(-10.24)).toBe('-10.24');
    expect(formatNum(1 / 3)).toBe('0.333');
    expect(formatNum(-0.0001)).toBe('0');
  });

  it('truncateLabel keeps short labels and counts code points', () => {
    expect(truncateLabel('abc')).toBe('abc');
    expect(truncateLabel('x'.repeat(41))).toBe(`${'x'.repeat(40)}…`);
    expect(truncateLabel('x'.repeat(40))).toBe('x'.repeat(40));
  });
});
