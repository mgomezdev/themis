import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  pausePrinter,
  resumePrinter,
  stopPrinter,
  setLight,
  jogZ,
  setFanSpeed,
  setBedTemp,
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
