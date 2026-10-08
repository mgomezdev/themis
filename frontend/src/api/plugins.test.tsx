import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { invalidatePlugins, pluginPath, resetPluginStore, setCapabilityProvider, useCapabilityProvider, useFeature, usePlugins } from './plugins';
import { Reply, stubFetch } from '../test/fetchStub';
import { mkPlugin, pluginsBody } from '../test/inventoryFixtures';

function Probe({ capability = 'inventory.filament', cap = 'LABEL_SCAN' }: { capability?: string; cap?: string }) {
  const { loaded } = usePlugins();
  const active = useCapabilityProvider(capability);
  const has = useFeature(capability, cap);
  return <div data-testid="p">{`${loaded ? 'loaded' : 'loading'}|${active?.id ?? 'none'}|${has}`}</div>;
}

describe('plugin store and capability hooks', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('exposes the plugin serving a capability and whether it has a feature', async () => {
    stubFetch({ 'GET /api/v1/plugins': pluginsBody(mkPlugin({ id: 'a', capabilities: ['LABEL_SCAN'] })) });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|a|true'));
  });

  it('no serving plugin, a disabled one, or a missing feature all read as "no"', async () => {
    stubFetch({ 'GET /api/v1/plugins': { plugins: [mkPlugin({ id: 'a', active: false, enabled: false, capabilities: ['LABEL_SCAN'] }), mkPlugin({ id: 'b', active: true, capabilities: [] })], selections: {} } });
    render(<><Probe cap="LABEL_SCAN" /></>);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|b|false'));     // b is active but lacks the capability
  });

  it('while loading, and when the list cannot be fetched, nothing is claimed', async () => {
    stubFetch({ 'GET /api/v1/plugins': new Reply(403, { detail: 'no' }) });
    render(<Probe />);
    expect(screen.getByTestId('p').textContent).toBe('loading|none|false');
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|none|false'));
  });

  it('every consumer shares one request, and a provider change refreshes all of them', async () => {
    let active = 'a';
    const api = stubFetch({
      'GET /api/v1/plugins': () => ({ plugins: [mkPlugin({ id: 'a', active: active === 'a' }), mkPlugin({ id: 'b', active: active === 'b' })], selections: {} }),
      'PUT /api/v1/capabilities/inventory.filament/provider': () => { active = 'b'; return { capability: 'inventory.filament', plugin_id: 'b', explicit: true }; },
    });
    render(<><Probe /><Probe /></>);
    await waitFor(() => expect(screen.getAllByTestId('p').map(e => e.textContent)).toEqual(['loaded|a|true', 'loaded|a|true']));
    expect(api.to('GET', '/api/v1/plugins')).toHaveLength(1);

    await act(async () => { await setCapabilityProvider('inventory.filament', 'b'); });

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
