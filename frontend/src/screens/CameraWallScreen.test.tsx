import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { CameraWallScreen, planWall } from './CameraWallScreen';
import { stubFetch } from '../test/fetchStub';
import type { FleetPrinter } from '../api/fleet';
import { toFleetPrinter } from '../api/fleet';

class MockWS {
  onmessage: ((e: { data: string }) => void) | null = null;
  close = vi.fn();
}

function fp(id: number, state: string, extra: Partial<FleetPrinter> & Record<string, unknown> = {}): FleetPrinter {
  return {
    id, name: `P${id}`, printer_type: 'bambu', enabled: true, queue_on: true, connected: true,
    awaiting_plate_clear: false, no_snapshots_while_idle: false, loaded_filaments: [], state,
    progress: 0, remaining_time: 0, layer_num: null, total_layers: null, temperatures: {},
    capabilities: { camera: true }, current_print: null, fan_model: 0, fan_aux: 0, fan_box: 0, ...extra,
  } as FleetPrinter;
}

const FLEET = [
  fp(1, 'RUNNING'), fp(2, 'IDLE'), fp(3, 'PAUSE'), fp(4, 'FAILED', { alarm_count: 2, alarm_severity: 'fatal' }),
  fp(5, 'IDLE', { connected: false }), fp(6, 'RUNNING', { capabilities: {} }),               // 6: no camera
];

const img = (el: HTMLElement) => el.querySelector('img') as HTMLImageElement;

function show(fleet: FleetPrinter[] = FLEET) {
  stubFetch({ 'GET /api/v1/fleet': fleet });
  return render(<MemoryRouter><CameraWallScreen /></MemoryRouter>);
}

beforeEach(() => { vi.stubGlobal('WebSocket', MockWS); localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe('planWall', () => {
  const printers = FLEET.map(toFleetPrinter);

  it('only includes printers with a camera and applies the status filter', () => {
    expect(planWall(printers, 'all', 0).shown.map(p => p.id)).toEqual(['1', '2', '3', '4', '5']);
    expect(planWall(printers, 'printing', 0).shown.map(p => p.id)).toEqual(['1']);
    expect(planWall(printers, 'offline', 0).shown.map(p => p.id)).toEqual(['5']);
  });

  it('gives the live slots to printing, then paused, then error, then the rest — never to offline', () => {
    expect([...planWall(printers, 'all', 2).liveIds]).toEqual(['1', '3']);
    expect([...planWall(printers, 'all', 3).liveIds].sort()).toEqual(['1', '3', '4']);
    expect(planWall(printers, 'all', 99).liveIds.has('5')).toBe(false);
    expect(planWall(printers, 'all', 0).liveIds.size).toBe(0);
  });
});

describe('CameraWallScreen', () => {
  it('shows a tile per camera printer, links each to its console, and marks live vs snapshot', async () => {
    show();
    const tiles = await screen.findAllByTestId('wall-tile');
    expect(tiles).toHaveLength(5);
    expect(screen.getByRole('link', { name: 'Open P2 console' }).getAttribute('href')).toBe('/fleet/2/console');
    expect(screen.queryByRole('link', { name: 'Open P6 console' })).toBeNull();
    expect(screen.getAllByTestId('wall-live')).toHaveLength(4);                       // limit 4; offline P5 is never live
    const live = img(screen.getByRole('link', { name: 'Open P1 console' }));
    expect(live.getAttribute('src')).toMatch(/^\/api\/v1\/printers\/1\/camera/);
    expect(within(screen.getByRole('link', { name: 'Open P5 console' })).getByTestId('wall-snapshot')).toBeTruthy();
    expect(img(screen.getByRole('link', { name: 'Open P5 console' }))).toBeNull();             // offline: no request at all
    await userEvent.selectOptions(screen.getByLabelText('Live streams'), '0');
    expect(img(screen.getByRole('link', { name: 'Open P2 console' })).getAttribute('src')).toMatch(/^\/api\/v1\/printers\/2\/snapshot/);
  });

  it('shows the alarm badge only on printers with unacknowledged alarms', async () => {
    show();
    const badges = await screen.findAllByTestId('wall-alarm');
    expect(badges).toHaveLength(1);
    expect(within(screen.getByRole('link', { name: 'Open P4 console' })).getByTestId('wall-alarm').textContent).toContain('2');
  });

  it('filters by status with counts, and says so when nothing matches', async () => {
    const user = userEvent.setup();
    show([fp(1, 'RUNNING'), fp(2, 'IDLE')]);
    expect(await screen.findByRole('button', { name: 'Printing · 1' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Printing · 1' }));
    expect(screen.getAllByTestId('wall-tile')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Error · 0' }));
    expect(screen.queryAllByTestId('wall-tile')).toHaveLength(0);
    expect(screen.getByRole('status').textContent).toMatch(/match this filter/);
  });

  it('says when there are no cameras at all', async () => {
    show([fp(1, 'RUNNING', { capabilities: {} })]);
    expect((await screen.findByRole('status')).textContent).toMatch(/No printers with a camera/);
  });

  it('changes density and the live limit, and remembers both', async () => {
    const user = userEvent.setup();
    const { unmount } = show();
    await screen.findAllByTestId('wall-tile');
    await user.selectOptions(screen.getByLabelText('Columns'), '8');
    expect((screen.getByTestId('wall-grid') as HTMLElement).style.gridTemplateColumns).toBe('repeat(8, minmax(0, 1fr))');
    await user.selectOptions(screen.getByLabelText('Live streams'), '0');
    expect(screen.queryAllByTestId('wall-live')).toHaveLength(0);
    unmount();

    show();
    await screen.findAllByTestId('wall-tile');
    expect((screen.getByLabelText('Columns') as HTMLSelectElement).value).toBe('8');
    expect((screen.getByLabelText('Live streams') as HTMLSelectElement).value).toBe('0');
  });

  it('falls back to snapshots for a tile whose live stream fails', async () => {
    show();
    const tile = await screen.findByRole('link', { name: 'Open P1 console' });
    expect(within(tile).getByTestId('wall-live')).toBeTruthy();
    fireEvent.error(img(tile));
    await waitFor(() => expect(within(tile).getByTestId('wall-snapshot')).toBeTruthy());
    expect(img(tile).getAttribute('src')).toMatch(/\/snapshot/);
  });

  it('ignores corrupt saved preferences', async () => {
    localStorage.setItem('themis.cameraWall', '{nope');
    show();
    await screen.findAllByTestId('wall-tile');
    expect((screen.getByLabelText('Columns') as HTMLSelectElement).value).toBe('4');
  });
});
