import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCapabilityList } from './capabilities';
import { resetPluginStore } from './plugins';
import { Reply, stubFetch } from '../test/fetchStub';

function Probe() {
  const { items, loaded, error } = useCapabilityList();
  return <div data-testid="p">{`${loaded ? 'loaded' : 'loading'}|${items.map(i => i.id).join(',')}|${error ?? ''}`}</div>;
}

describe('useCapabilityList', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('loads GET /api/v1/capabilities', async () => {
    stubFetch({ 'GET /api/v1/plugins': { plugins: [], selections: {}, pending: [] },
      'GET /api/v1/capabilities': { capabilities: [{ id: 'inventory.filament' }, { id: 'acme.reports' }] } });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded|inventory.filament,acme.reports|'));
  });

  it('reports a load failure instead of an empty list', async () => {
    stubFetch({ 'GET /api/v1/plugins': { plugins: [], selections: {}, pending: [] }, 'GET /api/v1/capabilities': new Reply(403, { detail: 'no scope' }) });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('loaded||no scope'));
  });
});

describe('useCapabilityList loop guard', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('does not refetch in a loop when the plugin list itself cannot be loaded', async () => {
    const api = stubFetch({ 'GET /api/v1/plugins': new Reply(403, { detail: 'no' }), 'GET /api/v1/capabilities': { capabilities: [] } });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('p').textContent).toContain('loaded'));
    await new Promise(r => setTimeout(r, 50));
    expect(api.to('GET', '/api/v1/capabilities').length).toBeLessThanOrEqual(2);
  });
});
