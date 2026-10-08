import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CapabilitiesPage } from './CapabilitiesPage';
import { resetPluginStore } from '../api/plugins';
import { Reply, stubFetch } from '../test/fetchStub';

const prov = (plugin_id: string, name: string, over: Record<string, unknown> = {}) =>
  ({ plugin_id, name, version: 1, enabled: true, status: 'not_selected', waiting_on: [], ...over });
const cap = (over: Record<string, unknown> & { id: string }) => ({
  version: 1, label: over.id, description: '', definer: null, features: [], required_methods: [], selected: null, explicit: false,
  status: 'none_selected', waiting_on: [], error: null, providers: [], requires_by: [], ...over,
});
const INV = cap({ id: 'inventory.filament', label: 'Filament inventory', selected: 'spoolman', status: 'serving',
  providers: [prov('spoolman', 'Spoolman'), prov('local_inventory', 'Local inventory')] });
const PLUGINS = { plugins: [], selections: {}, pending: [] };
const PUT = 'PUT /api/v1/capabilities/inventory.filament/provider';

describe('CapabilitiesPage', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('lists every capability with a provider dropdown and status chips', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [
      INV,
      cap({ id: 'acme.reports', label: 'Reports', definer: 'acme', providers: [prov('acme', 'Acme')] }),
      cap({ id: 'old.thing', label: 'old.thing', status: 'dormant', selected: 'ghost' }) ] } });
    render(<CapabilitiesPage />);
    expect(await screen.findByRole('combobox', { name: 'Filament inventory provider' })).toHaveValue('spoolman');
    expect(screen.getByText('Serving')).toBeInTheDocument();
    expect(screen.getByText(/Defined by acme/)).toBeInTheDocument();
    expect(screen.getByText('Dormant')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Reports provider' })).toHaveValue('');
  });

  it('shows what a provider is waiting on', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [
      cap({ id: 'acme.report', label: 'Report', selected: 'acme', status: 'waiting', waiting_on: ['acme.notes'], providers: [prov('acme', 'Acme', { status: 'waiting', waiting_on: ['acme.notes'] })] }) ] } });
    render(<CapabilitiesPage />);
    expect(await screen.findByText('Waiting on acme.notes')).toBeInTheDocument();
  });

  it('changing the dropdown PUTs the provider and refetches the list', async () => {
    const api = stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [INV] },
      [PUT]: { capability: 'inventory.filament', plugin_id: 'local_inventory', explicit: true } });
    render(<CapabilitiesPage />);
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Filament inventory provider' }), 'local_inventory');
    await waitFor(() => expect(api.to('PUT', '/api/v1/capabilities/inventory.filament/provider')[0].body).toEqual({ plugin_id: 'local_inventory' }));
    await waitFor(() => expect(api.to('GET', '/api/v1/capabilities').length).toBeGreaterThanOrEqual(2));
  });

  it('choosing None sends plugin_id null', async () => {
    const api = stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [INV] },
      [PUT]: { capability: 'inventory.filament', plugin_id: null, explicit: true } });
    render(<CapabilitiesPage />);
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Filament inventory provider' }), '');
    await waitFor(() => expect(api.to('PUT', '/api/v1/capabilities/inventory.filament/provider')[0].body).toEqual({ plugin_id: null }));
  });

  it('shows the server error text when the change is rejected', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [INV] },
      [PUT]: new Reply(422, { detail: 'selecting x would create a requirement cycle' }) });
    render(<CapabilitiesPage />);
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Filament inventory provider' }), 'local_inventory');
    expect(await screen.findByRole('alert')).toHaveTextContent('requirement cycle');
  });

  it('disables the select and says so when no plugin provides the capability', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': { capabilities: [cap({ id: 'acme.lonely', label: 'Lonely', status: 'no_provider' })] } });
    render(<CapabilitiesPage />);
    expect(await screen.findByRole('combobox', { name: 'Lonely provider' })).toBeDisabled();
    expect(screen.getByText('No plugin provides this')).toBeInTheDocument();
  });

  it('shows the load failure instead of an empty page', async () => {
    stubFetch({ 'GET /api/v1/plugins': PLUGINS, 'GET /api/v1/capabilities': new Reply(403, { detail: 'missing scope' }) });
    render(<CapabilitiesPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('missing scope');
  });
});
