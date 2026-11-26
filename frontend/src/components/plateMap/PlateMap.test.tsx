import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { PlateMap } from './PlateMap';
import { computePlateLayout } from './layout';
import type { Plate, Point } from './layout';
import { caseNamed, clone, corpus } from '../../dev/plateMapCorpus';

const parsePoints = (s: string | null): Point[] =>
  (s ?? '').trim().split(/\s+/).map(pair => pair.split(',').map(Number) as Point);
const objectPolygons = (c: HTMLElement) => [...c.querySelectorAll<SVGPolygonElement>('polygon.plate-map-object')];
const polygonFor = (c: HTMLElement, label: string) => objectPolygons(c).find(p => p.getAttribute('data-label') === label);
const rowFor = (label: string) => screen.getAllByRole('button').find(b => b.getAttribute('data-label') === label)!;

/** Render with the SVG laid out 1:1 with its viewBox at the page origin, so a view point is a client point (minus vb x/y). */
function renderPlate(plate: Plate, onObjectClick = vi.fn()) {
  const utils = render(<PlateMap plate={plate} onObjectClick={onObjectClick} />);
  const svg = utils.container.querySelector('svg.plate-map-svg') as SVGSVGElement | null;
  const layout = computePlateLayout(plate);
  if (svg && layout.geometry) {
    const [, , w, h] = layout.geometry.viewBox;
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: w, height: h, right: w, bottom: h, x: 0, y: 0, toJSON: () => ({}) });
  }
  const clickBed = (at: Point) => {
    const [vx, vy] = layout.toView(at);
    const [x0, y0] = layout.geometry!.viewBox;
    fireEvent.click(svg!, { clientX: vx - x0, clientY: vy - y0 });
  };
  return { ...utils, svg, layout, clickBed, onObjectClick };
}

describe('PlateMap renders the corpus (the Python snapshot is checked against the same numbers)', () => {
  const drawn = corpus.cases.filter(c => c.expect.view_box || c.expect.objects || c.expect.draw_order);
  it.each(drawn.map(c => [c.name, c] as const))('%s: rendered markup matches the expected layout', (_n, c) => {
    const { container, svg } = renderPlate(clone(c.plate));
    const e = c.expect;
    if (e.view_box) {
      const vb = svg!.getAttribute('viewBox')!.split(' ').map(Number);
      vb.forEach((v, i) => expect(Math.abs(v - e.view_box![i])).toBeLessThan(1e-3));
      expect(svg!.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet');
    }
    if (e.draw_order) expect(objectPolygons(container).map(p => p.getAttribute('data-label'))).toEqual(e.draw_order);
    for (const [label, want] of Object.entries(e.objects ?? {})) {
      const poly = polygonFor(container, label);
      if (want.on_map === false) { expect(poly).toBeUndefined(); continue; }
      expect(poly, label).toBeDefined();
      if (want.points) {
        const got = parsePoints(poly!.getAttribute('points'));
        expect(got).toHaveLength(want.points.length);
        got.forEach((p, i) => p.forEach((v, j) => expect(Math.abs(v - want.points![i][j])).toBeLessThan(1e-3)));
      }
      if (want.off_bed !== undefined) expect(poly!.getAttribute('data-off-bed')).toBe(String(want.off_bed));
      if (want.pattern) expect(poly!.getAttribute('data-pattern')).toBe(want.pattern);
      if (want.fill) expect(poly!.getAttribute('fill')).toBe(want.fill);
      if (want.fill === null) { expect(poly!.hasAttribute('fill')).toBe(false); expect(poly).toHaveClass('neutral'); }
      if (want.hit) expect(container.querySelector(`rect.plate-map-hit[data-index]`)).not.toBeNull();
    }
  });

  const hitCases = corpus.cases.flatMap(c => (c.expect.hits ?? []).map(h => [c.name, h.at.join(','), h.label, c.plate, h.at] as const));
  it.each(hitCases)('%s: clicking the map at bed (%s) reports %s', (_n, _at, label, plate, at) => {
    const { clickBed, onObjectClick } = renderPlate(clone(plate));
    clickBed(at);
    if (label === null) expect(onObjectClick).not.toHaveBeenCalled();
    else expect(onObjectClick).toHaveBeenCalledExactlyOnceWith(label);
  });
});

describe('PlateMap interaction', () => {
  it('a map click highlights the list row; a list click highlights the map (both report the label)', () => {
    const { container, clickBed, onObjectClick } = renderPlate(caseNamed('overlap').plate);
    clickBed([100, 100]);
    expect(onObjectClick).toHaveBeenLastCalledWith('Small');
    expect(rowFor('Small')).toHaveAttribute('aria-current', 'true');
    expect(rowFor('Large')).not.toHaveAttribute('aria-current');
    expect(container.querySelector('.plate-map-highlight')?.getAttribute('data-label')).toBe('Small');

    fireEvent.click(rowFor('Twin A'));               // under Twin B on the map: reachable through the list
    expect(onObjectClick).toHaveBeenLastCalledWith('Twin A');
    expect(container.querySelector('.plate-map-highlight')?.getAttribute('data-label')).toBe('Twin A');
    expect(rowFor('Twin A')).toHaveAttribute('aria-current', 'true');
    expect(rowFor('Small')).not.toHaveAttribute('aria-current');
  });

  it('every object is reachable through the list, tiny and overlapped ones included', () => {
    for (const name of ['overlap', 'tiny-object']) {
      const plate = caseNamed(name).plate;
      const onObjectClick = vi.fn();
      const { unmount } = render(<PlateMap plate={plate} onObjectClick={onObjectClick} />);
      for (const o of plate.objects) fireEvent.click(rowFor(o.label));
      expect(onObjectClick.mock.calls.map(c => c[0])).toEqual(plate.objects.map(o => o.label));
      unmount();
    }
  });

  it('non-selectable objects look different and ignore clicks on the map and in the list', () => {
    const { container, clickBed, onObjectClick } = renderPlate(caseNamed('mixed-selectable').plate);
    expect(polygonFor(container, 'Locked')).toHaveAttribute('data-selectable', 'false');
    expect(polygonFor(container, 'Locked')).toHaveClass('locked');
    expect(polygonFor(container, 'Pickable')).not.toHaveClass('locked');
    expect(rowFor('Locked')).toBeDisabled();
    fireEvent.click(rowFor('Locked'));
    clickBed([180, 70]);
    expect(onObjectClick).not.toHaveBeenCalled();
    expect(container.querySelector('.plate-map-highlight')).toBeNull();
  });

  it('off-bed objects are drawn and flagged in the list', () => {
    const { container } = renderPlate(caseNamed('off-bed').plate);
    expect(polygonFor(container, 'Beyond')).toHaveClass('off-bed');
    expect(within(rowFor('Beyond')).getByText('Off bed')).toBeInTheDocument();
    expect(within(rowFor('Half off')).getByText('Off bed')).toBeInTheDocument();
    expect(within(rowFor('On')).queryByText('Off bed')).toBeNull();
  });

  it('tiny objects get a visible hit ring', () => {
    const { container } = renderPlate(caseNamed('tiny-object').plate);
    const rings = [...container.querySelectorAll('rect.plate-map-hit')];
    expect(rings).toHaveLength(1);
    const [x, y, w, h] = ['x', 'y', 'width', 'height'].map(a => Number(rings[0].getAttribute(a)));
    [x, y, w, h].forEach((v, i) => expect(Math.abs(v - [93.32, 147.32, 15.36, 15.36][i])).toBeLessThan(1e-3));
  });

  it('numbers each object on the map and in the list', () => {
    const { container } = renderPlate(caseNamed('overlap').plate);
    expect([...container.querySelectorAll('text.plate-map-marker')].map(t => t.textContent)).toEqual(['1', '2', '3', '4']);
    expect(within(rowFor('Small')).getByText('1')).toBeInTheDocument();
  });

  it('labels the bed front so the orientation is explicit', () => {
    const { container } = renderPlate(caseNamed('square-front-left').plate);
    const front = container.querySelector('text.plate-map-front')!;
    expect(front.textContent).toBe('Front');
    expect(Number(front.getAttribute('y'))).toBeGreaterThan(256);  // below the bed, which ends at view y = depth
  });
});

describe('PlateMap labels and states', () => {
  it('labels are text, never markup', () => {
    const plate = caseNamed('labels').plate;
    const { container } = renderPlate(plate);
    expect(container.querySelector('script, img, foreignObject')).toBeNull();
    for (const o of plate.objects) {
      const row = rowFor(o.label);
      expect(row).toHaveAttribute('title', o.label);
      expect(within(row).getByText(o.label)).toBeInTheDocument();
      expect(polygonFor(container, o.label)?.parentElement?.querySelector('title')?.textContent).toBe(o.label);
    }
  });

  it('the legend names every state with its pattern swatch', () => {
    const plate = caseNamed('six-states').plate;
    render(<PlateMap plate={plate} onObjectClick={() => {}} />);
    const legend = screen.getByRole('list', { name: 'States' });
    for (const s of plate.states) expect(within(legend).getByText(s.name)).toBeInTheDocument();
    const patterns = [...legend.querySelectorAll('[data-pattern]')].map(e => e.getAttribute('data-pattern'));
    expect(new Set(patterns).size).toBe(6);
  });

  it('each list row names its state in words, not only a colour', () => {
    renderPlate(caseNamed('square-front-left').plate);
    expect(within(rowFor('Left')).getByText('Failed')).toBeInTheDocument();
    expect(within(rowFor('Front')).getByText('Printed')).toBeInTheDocument();
  });

  it('a non-hex colour never reaches the markup', () => {
    const { container } = renderPlate(caseNamed('six-states').plate);
    expect(container.innerHTML).not.toContain('alert(1)');
  });
});

describe('PlateMap without geometry', () => {
  it('no bed: the list alone', () => {
    const { container } = render(<PlateMap plate={caseNamed('no-geometry').plate} onObjectClick={() => {}} />);
    expect(container.querySelector('svg.plate-map-svg')).toBeNull();
    expect(rowFor('One')).toBeInTheDocument();
    expect(rowFor('Two')).toBeInTheDocument();
  });

  it('an object without a footprint is listed as not on the map', () => {
    const { container } = renderPlate(caseNamed('missing-footprint').plate);
    expect(polygonFor(container, 'Unmapped')).toBeUndefined();
    expect(within(rowFor('Unmapped')).getByText('Not on map')).toBeInTheDocument();
    expect(within(rowFor('Mapped')).queryByText('Not on map')).toBeNull();
  });

  it('an invalid plate shows why instead of a map', () => {
    const bad = clone(corpus.invalid.find(c => c.name === 'duplicate label')!.plate) as Plate;
    const { container } = render(<PlateMap plate={bad} onObjectClick={() => {}} />);
    expect(screen.getByRole('alert')).toHaveTextContent('objects: duplicate label');
    expect(container.querySelector('svg.plate-map-svg')).toBeNull();
  });
});
