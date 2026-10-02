import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  pausePrinter,
  resumePrinter,
  stopPrinter,
  setLight,
  jogZ,
  setFanSpeed,
  setBedTemp,
  jogAxis,
  homePrinter,
  setNozzleTemp,
  setChamberTemp,
  uploadToPrinter,
} from './printers';

function mockOkFetch() {
  vi.stubGlobal('fetch', vi.fn(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }),
  ));
}

function stubFetch() { return vi.mocked(fetch); }

beforeEach(() => mockOkFetch());
afterEach(() => vi.unstubAllGlobals());

describe('pausePrinter', () => {
  it('POSTs to /api/v1/printers/{id}/pause', async () => {
    await pausePrinter('5');
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/5/pause',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('throws on non-OK response', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: false, status: 503, text: () => Promise.resolve('Not connected') }),
    ));
    await expect(pausePrinter('5')).rejects.toThrow('503');
  });
});

describe('resumePrinter', () => {
  it('POSTs to /api/v1/printers/{id}/resume', async () => {
    await resumePrinter('7');
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/7/resume',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('stopPrinter', () => {
  it('POSTs to /api/v1/printers/{id}/stop', async () => {
    await stopPrinter('3');
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/3/stop',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('setLight', () => {
  it('POSTs to /api/v1/printers/{id}/light with on:true', async () => {
    await setLight('1', true);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/1/light',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ on: true }),
      }),
    );
  });

  it('POSTs with on:false', async () => {
    await setLight('1', false);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ on: false });
  });
});

describe('jogZ', () => {
  it('POSTs to /api/v1/printers/{id}/jog-z with distance_mm', async () => {
    await jogZ('2', 10);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/2/jog-z',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ distance_mm: 10 }),
      }),
    );
  });

  it('sends negative distance for downward jog', async () => {
    await jogZ('2', -10);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ distance_mm: -10 });
  });
});

describe('setFanSpeed', () => {
  it('POSTs to /api/v1/printers/{id}/fan with fan and speed_pct', async () => {
    await setFanSpeed('4', 'model', 80);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/4/fan',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ fan: 'model', speed_pct: 80 }),
      }),
    );
  });

  it('sends auxiliary fan', async () => {
    await setFanSpeed('4', 'auxiliary', 60);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ fan: 'auxiliary', speed_pct: 60 });
  });

  it('sends box fan', async () => {
    await setFanSpeed('4', 'box', 40);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ fan: 'box', speed_pct: 40 });
  });
});

describe('setBedTemp', () => {
  it('POSTs to /api/v1/printers/{id}/bed-temp with celsius', async () => {
    await setBedTemp('6', 95);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/6/bed-temp',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ celsius: 95 }),
      }),
    );
  });

  it('sends 0 for off', async () => {
    await setBedTemp('6', 0);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ celsius: 0 });
  });
});

describe('console commands', () => {
  const call = () => stubFetch().mock.calls[0] as [string, RequestInit];

  it('jogAxis POSTs axis and signed distance', async () => {
    await jogAxis(4, 'X', -10);
    const [url, init] = call();
    expect(url).toBe('/api/v1/printers/4/jog');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ axis: 'X', distance_mm: -10 });
  });

  it('homePrinter defaults to all axes, or names one', async () => {
    await homePrinter(4);
    expect(JSON.parse(call()[1].body as string)).toEqual({ axes: 'all' });
    stubFetch().mockClear();
    await homePrinter('4', 'Z');
    expect(call()[0]).toBe('/api/v1/printers/4/home');
    expect(JSON.parse(call()[1].body as string)).toEqual({ axes: 'Z' });
  });

  it.each([[setNozzleTemp, 'nozzle-temp'], [setChamberTemp, 'chamber-temp']])('setpoint %# POSTs celsius', async (fn, path) => {
    await fn(4, 55);
    expect(call()[0]).toBe(`/api/v1/printers/4/${path}`);
    expect(JSON.parse(call()[1].body as string)).toEqual({ celsius: 55 });
  });

  it('uploadToPrinter sends multipart with the file and the start flag (and no JSON content type)', async () => {
    const file = new File(['G28'], 'a.gcode');
    await uploadToPrinter(4, file, true);
    const [url, init] = call();
    expect(url).toBe('/api/v1/printers/4/upload');
    const form = init.body as FormData;
    expect(form.get('file')).toBeInstanceOf(File);
    expect((form.get('file') as File).name).toBe('a.gcode');
    expect(form.get('start')).toBe('true');
    expect(new Headers(init.headers).has('Content-Type')).toBe(false);   // the browser must set the multipart boundary itself
  });
});
