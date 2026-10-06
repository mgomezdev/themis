import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PluginsPage } from './PluginsPage';
import { Reply, stubFetch, type Call } from '../test/fetchStub';
import { mkPlugin } from '../test/inventoryFixtures';
import { RECONNECT, resetPluginStore, type InstallPreview, type PluginInstall, type PluginSummary } from '../api/plugins';

const preview = (over: Partial<InstallPreview> = {}): InstallPreview => ({
  token: 'tok1', id: 'acme_inv', name: 'Acme inventory', version: '1.0.0', kind: 'filament_inventory', publisher: 'Acme',
  description: 'Acme stock', source: 'upload', source_url: 'acme.zip', ref: null, commit_sha: null, archive_sha256: 'ab'.repeat(32), min_themis: null, ...over,
});
const install = (over: Partial<PluginInstall> = {}): PluginInstall => ({
  status: 'active', version: '1.0.0', previous_version: null, publisher: 'Acme', source_url: 'acme.zip', ref: null, commit_sha: null,
  archive_sha256: 'x', installed_at: 't', error: null, can_rollback: false, can_check_updates: false, ...over,
});
const installed = (over: Partial<PluginSummary> & { install?: PluginInstall } = {}) =>
  mkPlugin({ id: 'acme_inv', name: 'Acme inventory', source: 'upload', loaded: true, active: false, enabled: true,
             ui: { mode: 'section', nav_label: 'Acme', nav_placement: 'settings', nav_icon: null, tabs: [] }, install: install(), ...over });

const list = (plugins: PluginSummary[], pending: unknown[] = []) => ({ plugins, slots: {}, pending });
const show = () => render(<MemoryRouter><PluginsPage /></MemoryRouter>);
const GH = 'https://github.com/acme/inv';

describe('plugin installation UI', () => {
  const reload = vi.fn();
  beforeEach(() => {
    resetPluginStore();
    reload.mockReset();
    RECONNECT.pollMs = 5;
    Object.defineProperty(window, 'location', { configurable: true, value: { ...window.location, reload } });
  });
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('upload: review shows source, publisher, sha256 and the full-trust warning; Install needs the trust checkbox; then commits', async () => {
    const api = stubFetch({
      'GET /api/v1/plugins': list([]),
      'POST /api/v1/plugins/install?preview=true': { preview: preview() },
      'POST /api/v1/plugins/install/tok1/commit': { id: 'acme_inv', status: 'pending_restart' },
    });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Install plugin' }));
    await userEvent.upload(screen.getByLabelText('Plugin archive'), new File(['x'], 'acme.zip', { type: 'application/zip' }));
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));

    const pv = await screen.findByTestId('install-preview');
    expect(pv.textContent).toContain('Acme inventory');
    expect(pv.textContent).toContain('Acme');
    expect(pv.textContent).toContain('not verified');
    expect(pv.textContent).toContain('file acme.zip');
    expect(pv.textContent).toContain('ab'.repeat(32));
    expect(screen.getByRole('alert').textContent).toMatch(/full access to Themis/);
    const go = screen.getByRole('button', { name: 'Install' }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);                                             // the warning must be accepted first
    expect(api.to('POST', '/api/v1/plugins/install/tok1/commit')).toEqual([]);
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(go);
    await waitFor(() => expect(api.to('POST', '/api/v1/plugins/install/tok1/commit')).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const sent = api.to('POST', '/api/v1/plugins/install?preview=true')[0].body as FormData;
    expect((sent.get('file') as File).name).toBe('acme.zip');
  });

  it('GitHub: sends the repo, ref and subdirectory as a preview request and shows the resolved commit', async () => {
    const api = stubFetch({
      'GET /api/v1/plugins': list([]),
      'POST /api/v1/plugins/install-from-github': { preview: preview({ source: 'github', source_url: GH, ref: 'v1', commit_sha: 'c0ffee'.padEnd(40, '0') }) },
    });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Install plugin' }));
    await userEvent.click(screen.getByRole('button', { name: 'GitHub repository' }));
    await userEvent.type(screen.getByLabelText('Repository URL'), GH);
    await userEvent.type(screen.getByLabelText('Ref'), 'v1');
    await userEvent.type(screen.getByLabelText('Subdirectory'), 'plugins/x');
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect((await screen.findByTestId('install-preview')).textContent).toContain(`${GH} @ v1`);
    expect(screen.getByTestId('install-preview').textContent).toContain('c0ffee000000');
    expect(api.to('POST', '/api/v1/plugins/install-from-github')[0].body).toEqual({ repo_url: GH, ref: 'v1', subdir: 'plugins/x', preview: true });
  });

  it('a rejected package shows the server reason and installs nothing; cancelling a preview discards the staged package', async () => {
    const api = stubFetch({
      'GET /api/v1/plugins': list([]),
      'POST /api/v1/plugins/install-from-github': (c: Call) => (c.body as { repo_url: string }).repo_url.includes('bad')
        ? new Reply(400, { detail: 'unsafe path in archive' }) : { preview: preview() },
      'DELETE /api/v1/plugins/install/tok1': new Reply(204, null),
    });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Install plugin' }));
    await userEvent.click(screen.getByRole('button', { name: 'GitHub repository' }));
    await userEvent.type(screen.getByLabelText('Repository URL'), 'https://github.com/bad/one');
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect((await screen.findAllByRole('alert'))[0].textContent).toBe('unsafe path in archive');
    expect(screen.queryByTestId('install-preview')).toBeNull();

    await userEvent.clear(screen.getByLabelText('Repository URL'));
    await userEvent.type(screen.getByLabelText('Repository URL'), GH);
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    await screen.findByTestId('install-preview');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(api.to('DELETE', '/api/v1/plugins/install/tok1')).toHaveLength(1));
    expect(api.calls.some(c => c.url.endsWith('/commit'))).toBe(false);
  });

  it('asks for a file or URL before it will review', async () => {
    stubFetch({ 'GET /api/v1/plugins': list([]) });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Install plugin' }));
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Choose a .zip/);
    await userEvent.click(screen.getByRole('button', { name: 'GitHub repository' }));
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Enter the repository URL/);
  });

  it('pending changes stack in ONE banner; restart checks printers first and needs a second click when something is printing', async () => {
    const api = stubFetch({
      'GET /api/v1/plugins': list([installed({ loaded: false, enabled: false, install: install({ status: 'pending_restart' }) })], [
        { plugin_id: 'acme_inv', name: 'Acme inventory', version: '1.0.0', change: 'install' },
        { plugin_id: 'old_one', name: 'Old one', version: '2.0.0', change: 'uninstall' },
        { plugin_id: 'up_one', name: 'Up one', version: '3.1.0', change: 'update' },
      ]),
      'GET /api/v1/system/restart': { pending: [], printing: ['Bench P1S'] },
      'POST /api/v1/system/restart': (c: Call) => ((c.body as { force: boolean }).force ? { restarting: true } : new Reply(409, { detail: { error: 'printing', printers: ['Bench P1S'] } })),
    });
    show();
    const banner = await screen.findByTestId('restart-banner');
    expect(banner.textContent).toContain('Restart Themis to apply 3 pending changes');
    expect(banner.textContent).toContain('Install Acme inventory v1.0.0');
    expect(banner.textContent).toContain('Uninstall Old one');
    expect(banner.textContent).toContain('Update Up one v3.1.0');
    expect(api.to('POST', '/api/v1/system/restart')).toEqual([]);              // never automatic

    await userEvent.click(screen.getByRole('button', { name: 'Restart Themis' }));
    const confirm = await screen.findByTestId('restart-confirm');
    expect(confirm.textContent).toContain('Bench P1S');
    expect(api.to('POST', '/api/v1/system/restart')).toEqual([]);              // warned, not yet restarted
    await userEvent.click(within(confirm).getByRole('button', { name: 'Restart anyway' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/system/restart')[0].body).toEqual({ force: true }));
    expect((await screen.findByTestId('restart-banner')).textContent).toContain('Restarting Themis');
  });

  it('restart with nothing printing needs no force; "Not now" backs out', async () => {
    const api = stubFetch({
      'GET /api/v1/plugins': list([], [{ plugin_id: 'a_b_c', name: 'ABC', version: '1.0.0', change: 'install' }]),
      'GET /api/v1/system/restart': { pending: [], printing: [] },
      'POST /api/v1/system/restart': { restarting: true },
    });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Restart Themis' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Not now' }));
    expect(screen.queryByTestId('restart-confirm')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Restart Themis' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Restart now' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/system/restart')[0].body).toEqual({ force: false }));
  });

  it('no banner without pending changes', async () => {
    stubFetch({ 'GET /api/v1/plugins': list([installed()]) });
    show();
    await screen.findByTestId('plugin-acme_inv');
    expect(screen.queryByTestId('restart-banner')).toBeNull();
  });

  it('a package that is staged or failed to load has no Enable/Settings; it says why', async () => {
    stubFetch({ 'GET /api/v1/plugins': list([
      installed({ id: 'staged_one', name: 'Staged', loaded: false, enabled: false, install: install({ status: 'pending_restart' }) }),
      installed({ id: 'broken_one', name: 'Broken', loaded: false, enabled: false, error: 'ImportError: no module named x',
                  install: install({ status: 'error', error: 'ImportError: no module named x' }) }),
    ]) });
    show();
    const staged = await screen.findByTestId('plugin-staged_one');
    expect(staged.textContent).toContain('Starts after restart');
    expect(within(staged).queryByRole('button', { name: 'Enable' })).toBeNull();
    const broken = screen.getByTestId('plugin-broken_one');
    expect(broken.textContent).toContain('ImportError: no module named x');
    expect(within(broken).queryByRole('button', { name: 'Enable' })).toBeNull();
    expect(within(broken).getByRole('button', { name: 'Uninstall' })).toBeTruthy();         // a broken plugin can still be removed
  });

  it('bundled plugins can not be uninstalled', async () => {
    stubFetch({ 'GET /api/v1/plugins': list([mkPlugin({ id: 'spoolman', loaded: true, install: null })]) });
    show();
    const row = await screen.findByTestId('plugin-spoolman');
    expect(within(row).queryByRole('button', { name: 'Uninstall' })).toBeNull();
  });

  it('uninstall confirms, keeps data unless the box is ticked, and shows the removal as pending', async () => {
    const api = stubFetch({
      'GET /api/v1/plugins': list([installed()]),
      'DELETE /api/v1/plugins/acme_inv?remove_data=false': { status: 'pending_removal' },
      'DELETE /api/v1/plugins/acme_inv?remove_data=true': { status: 'pending_removal' },
    });
    show();
    const row = await screen.findByTestId('plugin-acme_inv');
    await userEvent.click(within(row).getByRole('button', { name: 'Uninstall' }));
    const box = screen.getByTestId('uninstall-confirm-acme_inv');
    expect(api.calls.some(c => c.method === 'DELETE')).toBe(false);             // confirmation first
    await userEvent.click(within(box).getByRole('button', { name: 'Uninstall' }));
    await waitFor(() => expect(api.to('DELETE', '/api/v1/plugins/acme_inv?remove_data=false')).toHaveLength(1));

    await userEvent.click(within(await screen.findByTestId('plugin-acme_inv')).getByRole('button', { name: 'Uninstall' }));
    const box2 = screen.getByTestId('uninstall-confirm-acme_inv');
    await userEvent.click(within(box2).getByRole('checkbox'));
    await userEvent.click(within(box2).getByRole('button', { name: 'Uninstall' }));
    await waitFor(() => expect(api.to('DELETE', '/api/v1/plugins/acme_inv?remove_data=true')).toHaveLength(1));
  });

  it('a failed action shows its reason on the plugin row', async () => {
    stubFetch({
      'GET /api/v1/plugins': list([installed({ install: install({ can_rollback: true, previous_version: '0.9.0' }) })]),
      'POST /api/v1/plugins/acme_inv/rollback': new Reply(400, { detail: 'cannot roll back to 0.9.0: migration(s) [2]' }),
    });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Roll back' }));
    expect((await screen.findByTestId('plugin-notice-acme_inv')).textContent).toContain('cannot roll back');
  });

  it('GitHub plugins: Check for updates says "Up to date", or opens the upgrade review with what it replaces', async () => {
    let latest = 'a'.repeat(40);
    const api = stubFetch({
      'GET /api/v1/plugins': list([installed({ source: 'github', install: install({ source_url: GH, ref: 'main', commit_sha: 'a'.repeat(40), can_check_updates: true }) })]),
      'GET /api/v1/plugins/acme_inv/updates': () => ({ update_available: latest !== 'a'.repeat(40), ref: 'main', current_commit: 'a'.repeat(40), latest_commit: latest }),
      'POST /api/v1/plugins/acme_inv/upgrade': { preview: preview({ version: '1.1.0', source: 'github', source_url: GH, ref: 'main', commit_sha: 'b'.repeat(40), token: 'tok2' }) },
      'POST /api/v1/plugins/install/tok2/commit': { status: 'pending_restart' },
    });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    expect((await screen.findByTestId('plugin-notice-acme_inv')).textContent).toBe('Up to date');
    expect(screen.queryByRole('dialog')).toBeNull();

    latest = 'b'.repeat(40);
    await userEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
    expect((await screen.findByRole('dialog', { name: 'Install plugin' })).textContent).toContain('Upgrade plugin');
    expect(api.to('POST', '/api/v1/plugins/acme_inv/upgrade')[0].body).toEqual({ preview: true });
    expect(screen.getByTestId('install-replaces').textContent).toContain('Replaces the installed v1.0.0');
    expect(screen.getByTestId('install-preview').textContent).toContain('v1.1.0');
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Upgrade' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/plugins/install/tok2/commit')).toHaveLength(1));
  });

  it('shows what version a staged upgrade will run after the restart', async () => {
    stubFetch({ 'GET /api/v1/plugins': list([installed({ version: '1.0.0', install: install({ status: 'pending_restart', version: '1.1.0', previous_version: '1.0.0', can_rollback: true }) })]) });
    show();
    expect((await screen.findByTestId('plugin-source-acme_inv')).textContent).toContain('v1.1.0 after restart');
  });

  it('after a restart it waits for Themis to go down and come back, then reloads the page', async () => {
    let health = 0;
    stubFetch({
      'GET /api/v1/plugins': list([], [{ plugin_id: 'a_b_c', name: 'ABC', version: '1.0.0', change: 'install' }]),
      'GET /api/v1/system/restart': { pending: [], printing: [] },
      'POST /api/v1/system/restart': { restarting: true },
      'GET /api/v1/health': () => { health += 1; if (health <= 2) throw new Error('connection refused'); return { status: 'ok' }; },
    });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Restart Themis' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Restart now' }));
    expect((await screen.findByTestId('restart-banner')).textContent).toContain('Restarting Themis');
    expect(reload).not.toHaveBeenCalled();                                  // still down
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(health).toBe(3);                                                 // two failed polls, then the one that came back
  });

  it('a failed install returns to the source step with the reason (the staged package is spent); Cancel/close are inert while it is in flight', async () => {
    let release: (v: unknown) => void = () => {};
    const gate = new Promise(r => { release = r; });
    const api = stubFetch({
      'GET /api/v1/plugins': list([]),
      'POST /api/v1/plugins/install-from-github': { preview: preview() },
      'POST /api/v1/plugins/install/tok1/commit': new Reply(400, { detail: 'the plugin failed to import: boom' }),
      'DELETE /api/v1/plugins/install/tok1': new Reply(204, null),
    });
    const inner = globalThis.fetch as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal('fetch', (u: string, i?: RequestInit) => (u.endsWith('/commit') ? gate.then(() => inner(u, i)) : inner(u, i)));   // hold the commit open
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Install plugin' }));
    await userEvent.click(screen.getByRole('button', { name: 'GitHub repository' }));
    await userEvent.type(screen.getByLabelText('Repository URL'), GH);
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    await screen.findByTestId('install-preview');
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Install' }));
    const cancel = await screen.findByRole('button', { name: 'Cancel' }) as HTMLButtonElement;
    expect(cancel.disabled).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(api.to('DELETE', '/api/v1/plugins/install/tok1')).toEqual([]);     // not discarded under the in-flight commit
    release(null);
    expect(await screen.findByText('the plugin failed to import: boom')).toBeTruthy();
    expect(screen.queryByTestId('install-preview')).toBeNull();               // back to choosing a source
    expect(screen.getByLabelText('Repository URL')).toBeTruthy();
  });
});
