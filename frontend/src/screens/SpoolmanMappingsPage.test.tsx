import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SpoolmanMappingsPage } from './SpoolmanMappingsPage';
import { Reply, stubFetch } from '../test/fetchStub';

const orca = (profiles: Record<string, string[]>) => ({ orca_profiles: JSON.stringify(JSON.stringify(profiles)) }); // double-encoded, like Spoolman
const SPOOLMAN_ON = { enabled: true, url: 'http://spoolman.test', has_api_key: false, sync_interval_minutes: 15 };

const FILAMENTS = [
  { id: 1, name: 'PLA Red', vendor: { id: 1, name: 'Elegoo' }, material: 'PLA', color_hex: 'FF0000', extra: orca({ 'Machine A': ['Generic PLA @A'] }) },
  { id: 2, name: 'PETG Black', vendor: { id: 1, name: 'Elegoo' }, material: 'PETG', color_hex: '000000', extra: {} },
  { id: 3, name: 'Silk Gold', material: 'PLA', extra: orca({ 'Retired Machine': ['Old Silk'] }) },
];
const PRINTERS = [
  { id: 10, name: 'Forge', current_orca_printer_profile: 'Machine A' },
  { id: 11, name: 'Anvil', current_orca_printer_profile: 'Machine A' },   // same preset: one field, one profiles fetch
  { id: 12, name: 'Kiln', current_orca_printer_profile: 'Machine B' },
  { id: 13, name: 'No preset', current_orca_printer_profile: null },
];

function routes(over: Record<string, unknown> = {}) {
  return {
    'GET /api/v1/settings/spoolman': SPOOLMAN_ON,
    'GET /api/v1/spoolman/filaments': FILAMENTS,
    'GET /api/v1/printers': PRINTERS,
    'GET /api/v1/printers/10/profiles': { print_profiles: [], filament_profiles: ['Generic PLA @A', 'Generic PETG @A', 'Silk PLA @A'] },
    'GET /api/v1/printers/12/profiles': { print_profiles: [], filament_profiles: ['Generic PETG @B'] },
    ...over,
  };
}

/** The page renders its "not configured" notice before the settings request even returns, so a notice
 *  assertion only means something once the config, filaments, printers and profiles have all loaded. */
async function loaded(api: ReturnType<typeof stubFetch>) {
  await waitFor(() => expect(api.to('GET', '/api/v1/printers/10/profiles')).toHaveLength(1));
  await act(async () => {});
}

const card = (name: string) => within(screen.getByText(name).closest('div[style*="border-radius: 10px"]') as HTMLElement);

describe('SpoolmanMappingsPage', () => {
  it('asks the operator to configure Spoolman first instead of showing mappings', async () => {
    // (the page still fires its filament/printer fetches while disabled; the notice wins over what they return)
    const api = stubFetch(routes({ 'GET /api/v1/settings/spoolman': { ...SPOOLMAN_ON, enabled: false } }));
    render(<SpoolmanMappingsPage />);
    await loaded(api);

    expect(screen.getByText(/Spoolman is not configured/)).toBeTruthy();
    expect(screen.queryByText('Filament Profile Mappings')).toBeNull();
  });

  it('treats Spoolman as not configured when it is enabled but has no URL', async () => {
    const api = stubFetch(routes({ 'GET /api/v1/settings/spoolman': { ...SPOOLMAN_ON, url: '' } }));
    render(<SpoolmanMappingsPage />);
    await loaded(api);

    expect(screen.getByText(/Spoolman is not configured/)).toBeTruthy();
    expect(screen.queryByText('Filament Profile Mappings')).toBeNull();
  });

  it('explains what is missing when no printer has an Orca machine preset', async () => {
    stubFetch(routes({ 'GET /api/v1/printers': [{ id: 13, name: 'No preset', current_orca_printer_profile: null }] }));
    render(<SpoolmanMappingsPage />);

    expect(await screen.findByText(/No printers with an OrcaSlicer machine preset configured/)).toBeTruthy();
  });

  it('shows the load error instead of an empty page', async () => {
    stubFetch(routes({ 'GET /api/v1/spoolman/filaments': new Reply(502, 'spoolman down') }));
    render(<SpoolmanMappingsPage />);

    expect(await screen.findByText('502 spoolman down')).toBeTruthy();
  });

  it('starts with the filaments that already have mappings, collapsed, and fetches each machine preset once', async () => {
    const api = stubFetch(routes());
    render(<SpoolmanMappingsPage />);

    expect(await screen.findByText('Elegoo PLA Red')).toBeTruthy();
    expect(screen.getByText('Silk Gold')).toBeTruthy();
    expect(screen.queryByText('Elegoo PETG Black')).toBeNull();          // no mapping yet: only reachable via search
    expect(card('Elegoo PLA Red').getByText('1 preset mapped')).toBeTruthy();
    expect(screen.queryByText('Machine A')).toBeNull();                  // collapsed: no per-preset editors yet
    expect(api.to('GET', '/api/v1/printers/10/profiles')).toHaveLength(1);
    expect(api.to('GET', '/api/v1/printers/11/profiles')).toEqual([]);   // Anvil shares Forge's preset
    expect(api.to('GET', '/api/v1/printers/12/profiles')).toHaveLength(1);
    expect(api.to('GET', '/api/v1/printers/13/profiles')).toEqual([]);   // no preset, nothing to look up
  });

  it('adds an unmapped filament through search, expanded, and stops offering it', async () => {
    stubFetch(routes());
    render(<SpoolmanMappingsPage />);
    await screen.findByText('Elegoo PLA Red');
    const search = screen.getByPlaceholderText('Search filaments to configure…');

    await userEvent.type(search, 'petg');
    expect(screen.getByText('Elegoo PETG Black')).toBeTruthy();
    expect(screen.queryByText('Elegoo PLA Red', { selector: 'span[style*="font-weight: 500"]' })).toBeNull(); // already active: not offered

    await userEvent.click(screen.getByText('Elegoo PETG Black'));

    expect((search as HTMLInputElement).value).toBe('');
    const petg = card('Elegoo PETG Black');
    expect(petg.getByText('Machine A')).toBeTruthy();                    // opens expanded: it has no mappings yet
    expect(petg.getByText('Machine B')).toBeTruthy();
    expect(petg.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true);   // nothing to save yet
    await userEvent.type(search, 'petg');
    expect(screen.queryByText('Elegoo PETG Black', { selector: 'span' })).toBeNull();
  });

  it('matches search on the display name (which includes the vendor) and on the material', async () => {
    // "Matte" is only findable by its material; a plain name/vendor search must not surface it
    stubFetch(routes({ 'GET /api/v1/spoolman/filaments': [...FILAMENTS, { id: 4, name: 'Matte', material: 'ASA', extra: {} }] }));
    render(<SpoolmanMappingsPage />);
    await screen.findByText('Elegoo PLA Red');
    const search = screen.getByPlaceholderText('Search filaments to configure…');
    const offered = (name: string) => screen.queryByText(name, { selector: 'span' });

    await userEvent.type(search, 'elegoo');                 // vendor, via the display name
    expect(offered('Elegoo PETG Black')).toBeTruthy();
    expect(offered('Matte')).toBeNull();
    await userEvent.clear(search);
    await userEvent.type(search, 'ASA');                    // material only, case-insensitive
    expect(offered('Matte')).toBeTruthy();
    expect(offered('Elegoo PETG Black')).toBeNull();
    await userEvent.clear(search);
    await userEvent.type(search, 'zzz');
    expect(offered('Matte')).toBeNull();
  });

  it('offers at most 8 search results', async () => {
    const bulk = Array.from({ length: 10 }, (_, i) => ({ id: 100 + i, name: `Bulk ${i}`, material: 'PLA', extra: {} }));
    stubFetch(routes({ 'GET /api/v1/spoolman/filaments': [...FILAMENTS, ...bulk] }));
    render(<SpoolmanMappingsPage />);
    await screen.findByText('Elegoo PLA Red');

    await userEvent.type(screen.getByPlaceholderText('Search filaments to configure…'), 'bulk');

    expect(screen.getAllByText(/^Bulk \d$/, { selector: 'span' })).toHaveLength(8);
  });

  it('saves a new mapping with the exact PATCH body, then disables Save until the next edit', async () => {
    const api = stubFetch(routes({ 'PATCH /api/v1/spoolman/filaments/2': { id: 2 } }));
    render(<SpoolmanMappingsPage />);
    await screen.findByText('Elegoo PLA Red');
    await userEvent.type(screen.getByPlaceholderText('Search filaments to configure…'), 'petg');
    await userEvent.click(screen.getByText('Elegoo PETG Black'));
    const petg = card('Elegoo PETG Black');
    const [machineAField] = petg.getAllByPlaceholderText('Search profiles…');

    await userEvent.click(machineAField);
    await userEvent.click(await screen.findByText('Generic PETG @A'));
    expect(petg.getByText('Generic PETG @A')).toBeTruthy();               // shown as a chip
    await userEvent.click(petg.getByRole('button', { name: 'Save' }));

    expect(await petg.findByText('Saved')).toBeTruthy();
    expect(api.to('PATCH', '/api/v1/spoolman/filaments/2')[0].body).toEqual({ orca_profiles: { 'Machine A': ['Generic PETG @A'] } });
    expect(petg.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true);
    expect(petg.getByText('1 preset mapped')).toBeTruthy();
  });

  it('removing a chip makes the card dirty and saving sends the reduced mapping', async () => {
    const api = stubFetch(routes({ 'PATCH /api/v1/spoolman/filaments/1': { id: 1 } }));
    render(<SpoolmanMappingsPage />);
    await userEvent.click(await screen.findByText('Elegoo PLA Red'));
    const red = card('Elegoo PLA Red');
    const save = () => red.getByRole('button', { name: 'Save' });
    expect(save().hasAttribute('disabled')).toBe(true);

    await userEvent.click(red.getByTitle('Remove Generic PLA @A'));
    expect(save().hasAttribute('disabled')).toBe(false);
    await userEvent.click(save());

    // emptying the last preset removes the filament from the page after a successful save
    await waitFor(() => expect(screen.queryByText('Elegoo PLA Red')).toBeNull());
    expect(api.to('PATCH', '/api/v1/spoolman/filaments/1')[0].body).toEqual({ orca_profiles: {} });
  });

  it('a failed save shows the reason and leaves the edit unsaved', async () => {
    stubFetch(routes({ 'PATCH /api/v1/spoolman/filaments/1': new Reply(503, 'Spoolman unreachable') }));
    render(<SpoolmanMappingsPage />);
    await userEvent.click(await screen.findByText('Elegoo PLA Red'));
    const red = card('Elegoo PLA Red');
    await userEvent.click(red.getByTitle('Remove Generic PLA @A'));

    await userEvent.click(red.getByRole('button', { name: 'Save' }));

    expect(await red.findByText('503 Spoolman unreachable')).toBeTruthy();
    expect(red.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(false);   // still dirty: can retry
    expect(screen.getByText('Elegoo PLA Red')).toBeTruthy();                                    // and not removed
  });

  it('flags a saved mapping for a machine preset no printer uses any more', async () => {
    stubFetch(routes());
    render(<SpoolmanMappingsPage />);
    await userEvent.click(await screen.findByText('Silk Gold'));

    const gold = card('Silk Gold');
    expect(gold.getByText('(no matching printer registered)')).toBeTruthy();
    expect(gold.getByText('Saved: Old Silk')).toBeTruthy();
  });

  it('falls back to an empty profile list when one printer\'s profiles cannot be loaded', async () => {
    stubFetch(routes({ 'GET /api/v1/printers/12/profiles': new Reply(500, 'nope') }));
    render(<SpoolmanMappingsPage />);
    await screen.findByText('Elegoo PLA Red');
    await userEvent.type(screen.getByPlaceholderText('Search filaments to configure…'), 'petg');
    await userEvent.click(screen.getByText('Elegoo PETG Black'));
    const petg = card('Elegoo PETG Black');
    const fields = petg.getAllByPlaceholderText('Search profiles…');

    await userEvent.type(fields[1], 'x');   // Machine B's field: nothing to offer

    expect(await screen.findByText('No compatible filament presets found for this printer')).toBeTruthy();
  });
});
