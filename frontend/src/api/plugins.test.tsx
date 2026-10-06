import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { invalidatePlugins, pluginPath, resetPluginStore, setExtensionSlot, useActivePlugin, useCapability, usePlugins } from './plugins';
import { Reply, stubFetch } from '../test/fetchStub';
import { mkPlugin, pluginsBody } from '../test/inventoryFixtures';

function Probe({ kind = 'filament_inventory', cap = 'LABEL_SCAN' }: { kind?: string; cap?: string }) {
  const { loaded } = usePlugins();
  const active = useActivePlugin(kind);
  const has = useCapability(kind, cap);
  return <div data-testid="p">{`${loaded ? 'loaded' : 'loading'}|${active?.id ?? 'none'}|${has}`}</div>;
}

describe('plugin store and capability hooks', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('exposes the active plugin of a kind and whether it has a capability', async () => {
    stubFetch({ 'GET /api/v1/plugins': pluginsBody(mkPlugin({ id: 'a', capabilities: ['LABEL_SCAN'] })) });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|a|true'));
  });

  it('no active plugin, a disabled one, or a missing capability all read as "no"', async () => {
    stubFetch({ 'GET /api/v1/plugins': { plugins: [mkPlugin({ id: 'a', active: false, enabled: false, capabilities: ['LABEL_SCAN'] }), mkPlugin({ id: 'b', active: true, capabilities: [] })], slots: {} } });
    render(<><Probe cap="LABEL_SCAN" /></>);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|b|false'));     // b is active but lacks the capability
  });

  it('while loading, and when the list cannot be fetched, nothing is claimed', async () => {
    stubFetch({ 'GET /api/v1/plugins': new Reply(403, { detail: 'no' }) });
    render(<Probe />);
    expect(screen.getByTestId('p').textContent).toBe('loading|none|false');
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|none|false'));
  });

  it('every consumer shares one request, and a slot change refreshes all of them', async () => {
    let active = 'a';
    const api = stubFetch({
      'GET /api/v1/plugins': () => ({ plugins: [mkPlugin({ id: 'a', active: active === 'a' }), mkPlugin({ id: 'b', active: active === 'b' })], slots: {} }),
      'PUT /api/v1/extension-slots/filament_inventory': () => { active = 'b'; return { kind: 'filament_inventory', plugin_id: 'b' }; },
    });
    render(<><Probe /><Probe /></>);
    await waitFor(() => expect(screen.getAllByTestId('p').map(e => e.textContent)).toEqual(['loaded|a|true', 'loaded|a|true']));
    expect(api.to('GET', '/api/v1/plugins')).toHaveLength(1);

    await act(async () => { await setExtensionSlot('filament_inventory', 'b'); });

    await waitFor(() => expect(screen.getAllByTestId('p').map(e => e.textContent)).toEqual(['loaded|b|true', 'loaded|b|true']));
  });

  it('invalidatePlugins refetches', async () => {
    let n = 0;
    stubFetch({ 'GET /api/v1/plugins': () => pluginsBody(mkPlugin({ id: `p${++n}` })) });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|p1|true'));
    act(() => invalidatePlugins());
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|p2|true'));
  });
});

describe('pluginPath', () => {
  it('addresses the plugin\'s own API and fills {vars} from the row, URL-encoded', () => {
    expect(pluginPath('demo', 'materials/{id}/archive', { id: 'a/b' })).toBe('/api/v1/plugins/demo/materials/a%2Fb/archive');
    expect(pluginPath('demo', '/materials')).toBe('/api/v1/plugins/demo/materials');
  });
});
