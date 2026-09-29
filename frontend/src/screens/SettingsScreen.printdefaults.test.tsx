import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SettingsScreen } from './SettingsScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const CONFIG = { check_interval_minutes: 5, snapshot_interval_seconds: 2, operator_name: 'Workshop Lead', estimates_enabled: false };
const QUEUE = 'PUT /api/v1/settings/queue';

function open(over: Record<string, unknown> = {}) {
  const api = stubFetch({
    'GET /api/v1/settings/queue': CONFIG,
    'GET /api/v1/settings/spoolman': { enabled: false, url: null, has_api_key: false },
    'GET /api/v1/laminus/catalog/status': { laminus_configured: false, laminus: null, cached: false, cached_bytes: 0 },
    [QUEUE]: CONFIG,
    ...over,
  });
  render(
    <MemoryRouter initialEntries={['/settings/print']}>
      <Routes><Route path="/settings/*" element={<SettingsScreen />} /></Routes>
    </MemoryRouter>,
  );
  return api;
}
const puts = (api: ReturnType<typeof stubFetch>) => api.to('PUT', '/api/v1/settings/queue').map(c => c.body);
const interval = () => screen.getAllByRole('spinbutton')[0] as HTMLInputElement;
const snapshot = () => screen.getAllByRole('spinbutton')[1] as HTMLInputElement;
const nameBox = () => screen.getByPlaceholderText('e.g. Workshop Lead') as HTMLInputElement;
const estimates = () => screen.getByRole('switch') as HTMLButtonElement;
const alertText = () => screen.queryByRole('alert')?.textContent ?? null;

afterEach(() => vi.unstubAllGlobals());

describe('Print defaults - loading', () => {
  it('shows what the server has stored', async () => {
    open({ 'GET /api/v1/settings/queue': { ...CONFIG, check_interval_minutes: 12, snapshot_interval_seconds: 7, estimates_enabled: true } });

    await waitFor(() => expect(interval().value).toBe('12'));
    expect(snapshot().value).toBe('7');
    expect(nameBox().value).toBe('Workshop Lead');
    expect(estimates().getAttribute('aria-checked')).toBe('true');
  });
});

describe('Print defaults - saving', () => {
  it('saves the queue check interval on blur, rounded and never below 1', async () => {
    const api = open();
    await waitFor(() => expect(interval().value).toBe('5'));

    fireEvent.change(interval(), { target: { value: '7.6' } });
    fireEvent.blur(interval());
    await waitFor(() => expect(puts(api)).toEqual([{ check_interval_minutes: 8 }]));
    expect(interval().value).toBe('8');

    fireEvent.change(interval(), { target: { value: '0' } });
    fireEvent.blur(interval());

    await waitFor(() => expect(puts(api)).toEqual([{ check_interval_minutes: 8 }, { check_interval_minutes: 1 }]));
    expect(interval().value).toBe('1');
    expect(alertText()).toBeNull();
  });

  it('rounds the snapshot interval and keeps it at 1 second or more', async () => {
    const api = open();
    await waitFor(() => expect(snapshot().value).toBe('2'));

    fireEvent.change(snapshot(), { target: { value: '0' } });
    fireEvent.blur(snapshot());
    await waitFor(() => expect(snapshot().value).toBe('1'));
    fireEvent.change(snapshot(), { target: { value: '4.4' } });
    fireEvent.blur(snapshot());

    await waitFor(() => expect(puts(api)).toEqual([{ snapshot_interval_seconds: 1 }, { snapshot_interval_seconds: 4 }]));
  });

  it('falls back to 2 seconds when the server does not report a snapshot interval', async () => {
    open({ 'GET /api/v1/settings/queue': { check_interval_minutes: 9, operator_name: null } });

    await waitFor(() => expect(interval().value).toBe('9'));
    expect(snapshot().value).toBe('2');
  });

  it('saves the snapshot interval and the display name (trimmed) on blur', async () => {
    const api = open();
    await waitFor(() => expect(snapshot().value).toBe('2'));

    fireEvent.change(snapshot(), { target: { value: '10' } });
    fireEvent.blur(snapshot());
    fireEvent.change(nameBox(), { target: { value: '  Print Lab  ' } });
    fireEvent.blur(nameBox());

    await waitFor(() => expect(puts(api)).toEqual([{ snapshot_interval_seconds: 10 }, { operator_name: 'Print Lab' }]));
  });

  it('saves the estimate toggle immediately', async () => {
    const api = open();
    await waitFor(() => expect(estimates().getAttribute('aria-checked')).toBe('false'));

    await userEvent.click(estimates());

    await waitFor(() => expect(puts(api)).toEqual([{ estimates_enabled: true }]));
    expect(estimates().getAttribute('aria-checked')).toBe('true');
    expect(alertText()).toBeNull();
  });
});

describe('Print defaults - a refused save', () => {
  it('puts the queue check interval back and says why', async () => {
    open({ [QUEUE]: new Reply(422, { detail: 'must be at least 1' }) });
    await waitFor(() => expect(interval().value).toBe('5'));

    fireEvent.change(interval(), { target: { value: '9' } });
    fireEvent.blur(interval());

    await waitFor(() => expect(alertText()).toBe('Couldn\'t save the queue check interval: 422 {"detail":"must be at least 1"}'));
    expect(interval().value).toBe('5');                                  // not left showing a value the server never stored
  });

  it('puts the snapshot interval back and says why', async () => {
    open({ [QUEUE]: new Reply(500, 'db locked') });
    await waitFor(() => expect(snapshot().value).toBe('2'));

    fireEvent.change(snapshot(), { target: { value: '30' } });
    fireEvent.blur(snapshot());

    await waitFor(() => expect(alertText()).toBe("Couldn't save the snapshot interval: 500 db locked"));
    expect(snapshot().value).toBe('2');
  });

  it('puts the display name back and says why', async () => {
    open({ [QUEUE]: new Reply(500, 'db locked') });
    await waitFor(() => expect(nameBox().value).toBe('Workshop Lead'));

    fireEvent.change(nameBox(), { target: { value: 'Someone Else' } });
    fireEvent.blur(nameBox());

    await waitFor(() => expect(alertText()).toBe("Couldn't save the display name: 500 db locked"));
    expect(nameBox().value).toBe('Workshop Lead');
  });

  it('flips the estimate toggle back and says why', async () => {
    open({ [QUEUE]: new Reply(500, 'db locked') });
    await waitFor(() => expect(estimates().getAttribute('aria-checked')).toBe('false'));

    await userEvent.click(estimates());

    await waitFor(() => expect(alertText()).toBe("Couldn't save estimate generation: 500 db locked"));
    expect(estimates().getAttribute('aria-checked')).toBe('false');
  });

  it.each([
    ['queue check interval', interval, '6', '9'],
    ['snapshot interval', snapshot, '6', '9'],
  ] as const)('reverts the %s to the last value that did save, not to the page-load value', async (_name, field, first, second) => {
    let attempts = 0;
    open({ [QUEUE]: () => (++attempts === 2 ? new Reply(500, 'flaky') : CONFIG) });
    await waitFor(() => expect(field().value).not.toBe(''));

    fireEvent.change(field(), { target: { value: first } });
    fireEvent.blur(field());                                             // saved
    await waitFor(() => expect(attempts).toBe(1));
    fireEvent.change(field(), { target: { value: second } });
    fireEvent.blur(field());                                             // refused

    await waitFor(() => expect(alertText()).toContain('500 flaky'));
    expect(field().value).toBe(first);
  });

  it('reverts the display name to the last name that did save', async () => {
    let attempts = 0;
    open({ [QUEUE]: () => (++attempts === 2 ? new Reply(500, 'flaky') : CONFIG) });
    await waitFor(() => expect(nameBox().value).toBe('Workshop Lead'));

    fireEvent.change(nameBox(), { target: { value: 'Print Lab' } });
    fireEvent.blur(nameBox());
    await waitFor(() => expect(attempts).toBe(1));
    fireEvent.change(nameBox(), { target: { value: 'Someone Else' } });
    fireEvent.blur(nameBox());

    await waitFor(() => expect(alertText()).toContain('500 flaky'));
    expect(nameBox().value).toBe('Print Lab');
  });

  it('flips the estimate toggle back to the last state that did save', async () => {
    let attempts = 0;
    open({ [QUEUE]: () => (++attempts === 2 ? new Reply(500, 'flaky') : CONFIG) });
    await waitFor(() => expect(estimates().getAttribute('aria-checked')).toBe('false'));

    await userEvent.click(estimates());                                  // on: saved
    await waitFor(() => expect(attempts).toBe(1));
    await userEvent.click(estimates());                                  // off: refused

    await waitFor(() => expect(alertText()).toContain('500 flaky'));
    expect(estimates().getAttribute('aria-checked')).toBe('true');
  });

  it('clears the message when the next save starts', async () => {
    let attempts = 0;
    open({ [QUEUE]: () => (++attempts === 1 ? new Reply(500, 'flaky') : CONFIG) });
    await waitFor(() => expect(estimates().getAttribute('aria-checked')).toBe('false'));
    await userEvent.click(estimates());
    await waitFor(() => expect(alertText()).toContain('500 flaky'));

    await userEvent.click(estimates());

    await waitFor(() => expect(alertText()).toBeNull());
    expect(estimates().getAttribute('aria-checked')).toBe('true');
  });
});

describe('Print defaults - profile maintenance', () => {
  const rescan = () => screen.getByRole('button', { name: /rescan profiles/i });

  it('reports how many printer presets the rescan found', async () => {
    const api = open({ 'POST /api/v1/printers/rescan-profiles': { machine_presets: 14 } });
    await waitFor(() => expect(interval().value).toBe('5'));

    await userEvent.click(rescan());

    expect(await screen.findByText('Found 14 printer presets.')).toBeTruthy();
    expect(api.to('POST', '/api/v1/printers/rescan-profiles')).toHaveLength(1);
  });

  it('drops the previous rescan result while a new rescan is running', async () => {
    let release!: () => void;
    const gate = new Promise<void>(r => { release = r; });
    let calls = 0;
    open({ 'POST /api/v1/printers/rescan-profiles': () => (++calls === 1 ? { machine_presets: 14 } : gate.then(() => ({ machine_presets: 3 }))) });
    await waitFor(() => expect(interval().value).toBe('5'));
    await userEvent.click(rescan());
    await screen.findByText('Found 14 printer presets.');

    await userEvent.click(rescan());

    expect(screen.queryByText('Found 14 printer presets.')).toBeNull();
    expect(screen.getByRole('button', { name: /Rescanning…/ }).hasAttribute('disabled')).toBe(true);
    release();
    expect(await screen.findByText('Found 3 printer presets.')).toBeTruthy();
  });

  it('says so when the profile rescan fails', async () => {
    open({ 'POST /api/v1/printers/rescan-profiles': new Reply(500, 'no config dir') });
    await waitFor(() => expect(interval().value).toBe('5'));

    await userEvent.click(rescan());

    expect(await screen.findByText('Rescan failed — is OrcaSlicer config reachable?')).toBeTruthy();
    expect(rescan().hasAttribute('disabled')).toBe(false);               // free to try again
  });

  it.each([
    ['Refresh catalog', 'refresh', 'Catalog refreshed — 2 KB cached.', 'Refresh failed: 502'],
    ['Rescan & rebuild', 'rescan', 'Rescan complete — 2 KB cached.', 'Rescan failed: 502'],
  ])('%s: reports success, and a failure in red', async (button, path, success, failure) => {
    let ok = true;
    open({ [`POST /api/v1/laminus/catalog/${path}`]: () => (ok ? { status: 'ok', bytes: 2048 } : new Reply(502, 'sidecar down')) });
    await waitFor(() => expect(interval().value).toBe('5'));

    await userEvent.click(screen.getByRole('button', { name: new RegExp(button) }));
    const okMsg = await screen.findByText(success);
    expect(okMsg.style.color).toBe('var(--ok)');

    ok = false;
    await userEvent.click(screen.getByRole('button', { name: new RegExp(button) }));

    const failMsg = await screen.findByText(failure);
    expect(failMsg.style.color).toBe('var(--err)');
    expect(screen.queryByText(success)).toBeNull();
  });
});
