import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RemapModal } from './RemapModal';
import type { PendingRemaps } from '../api/laminus';
import { Reply, stubFetch } from '../test/fetchStub';

const CONFIRM = 'POST /api/v1/laminus/catalog/confirm-remap';
const RESULT = { status: 'ok', applied: { printers: 3, jobs: 2, inventory_filaments: 1 }, inventory_failures: [] };

function payload(over: Partial<PendingRemaps> = {}): PendingRemaps {
  return {
    status: 'pending_remaps',
    sync_id: 'sync-1',
    pending: {
      printers: [
        { field: 'current_orca_printer_profile', stale_value: 'Old Machine', options_kind: 'machine', required: true,
          affected_printer_ids: [1, 2], affected_printer_names: ['Forge', 'Anvil'], affected_slots: [null, null] },
        { field: 'filament_profile', stale_value: 'Old PLA', options_kind: 'filament', required: true,
          affected_printer_ids: [1], affected_printer_names: ['Forge'], affected_slots: [0] },
      ],
      jobs: [
        { field: 'print_profile', stale_value: 'Old Process', options_kind: 'process', required: false,
          affected_config_ids: [10], affected_file_names: ['bracket.3mf'] },
      ],
      inventory_filaments: [
        { printer_preset: 'New Machine A', stale_name: 'Old Spool PLA', required: false,
          affected_filament_ids: [5, 6], affected_filament_names: ['Red PLA', 'Blue PLA'] },
      ],
    },
    options: {
      machine: ['New Machine A', 'New Machine B'],
      process: ['0.20mm New', '0.28mm Draft'],
      filament: ['Generic PLA @Test', 'Bambu PETG Basic', 'Polymaker PLA Pro', 'Bambu PLA Basic', 'Weird Thing'],
    },
    inventory_error: null,
    ...over,
  };
}

function renderModal(p: PendingRemaps = payload()) {
  const onDone = vi.fn();
  const onCancel = vi.fn();
  render(<RemapModal payload={p} onDone={onDone} onCancel={onCancel} />);
  return { onDone, onCancel };
}

const confirmButton = () => screen.getByRole('button', { name: /confirm|applying/i }) as HTMLButtonElement;
const printerInputs = () => screen.getAllByPlaceholderText('Search or select a replacement…') as HTMLInputElement[];
const jobInput = () => screen.getByPlaceholderText('Search or leave blank to clear…') as HTMLInputElement;
const datalistValues = (input: HTMLElement) =>
  [...document.querySelectorAll(`datalist#${input.getAttribute('list')} option`)].map(o => (o as HTMLOptionElement).value);

async function resolveRequiredPrinters() {
  const [machine, filament] = printerInputs();
  await userEvent.type(machine, 'New Machine A');
  await userEvent.type(filament, 'Generic PLA @Test');
}

afterEach(() => vi.unstubAllGlobals());

describe('RemapModal', () => {
  it('lists each stale reference with what it affects, one section per kind', () => {
    renderModal();

    expect(screen.getByText('Old Machine').tagName).toBe('S');
    expect(screen.getByText('affects 2 printers')).toBeTruthy();      // two printers share the machine preset
    expect(screen.getByText('Forge')).toBeTruthy();                  // one printer: named
    expect(screen.getByText('bracket.3mf')).toBeTruthy();
    expect(screen.getByText('affects 2 filaments')).toBeTruthy();
    expect(['Printers', 'Queued Jobs', 'Inventory Filaments'].map(h => !!screen.getByRole('heading', { name: h }))).toEqual([true, true, true]);
  });

  it('omits sections that have nothing pending and shows the inventory warning when the check was partial', () => {
    const p = payload({ inventory_error: 'connection refused' });
    p.pending.jobs = [];
    p.pending.inventory_filaments = [];
    renderModal(p);

    expect(screen.queryByRole('heading', { name: 'Queued Jobs' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Inventory Filaments' })).toBeNull();
    expect(screen.getByText(/Inventory references could not be fully checked: connection refused/)).toBeTruthy();
  });

  it('blocks Confirm until every required printer entry names a replacement that exists in the new catalog', async () => {
    renderModal();
    expect(confirmButton().disabled).toBe(true);
    expect(screen.getAllByText('Required — choose from the list')).toHaveLength(2);

    const [machine, filament] = printerInputs();
    await userEvent.type(machine, 'New Machine');                      // a prefix is not a valid choice
    expect(confirmButton().disabled).toBe(true);
    await userEvent.type(machine, ' A');
    expect(screen.getAllByText('Required — choose from the list')).toHaveLength(1);
    expect(confirmButton().disabled).toBe(true);                       // the filament entry is still open

    await userEvent.type(filament, 'Not In Catalog');
    expect(confirmButton().disabled).toBe(true);
    await userEvent.clear(filament);
    await userEvent.type(filament, 'Bambu PETG Basic');
    expect(screen.queryByText('Required — choose from the list')).toBeNull();
    expect(confirmButton().disabled).toBe(false);                      // optional job/inventory entries never block
  });

  it('a printer machine entry only accepts machine presets and a filament entry only filaments', async () => {
    renderModal();
    const [machine, filament] = printerInputs();

    await userEvent.type(machine, 'Bambu PETG Basic');   // a filament name in a machine slot
    await userEvent.type(filament, 'New Machine A');     // a machine name in a filament slot

    expect(confirmButton().disabled).toBe(true);
    expect(datalistValues(machine)).toEqual([]);          // filtered by what was typed
    await userEvent.clear(machine);
    expect(datalistValues(machine)).toEqual(['New Machine A', 'New Machine B']);
  });

  it('submits sync_id and one resolution per pending entry, blanks as null, then reports the result', async () => {
    const api = stubFetch({ [CONFIRM]: RESULT });
    const { onDone } = renderModal();
    await resolveRequiredPrinters();

    await userEvent.click(confirmButton());

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(RESULT));
    expect(api.to('POST', '/api/v1/laminus/catalog/confirm-remap')[0].body).toEqual({
      sync_id: 'sync-1',
      resolutions: {
        printers: [
          { field: 'current_orca_printer_profile', stale_value: 'Old Machine', new_value: 'New Machine A' },
          { field: 'filament_profile', stale_value: 'Old PLA', new_value: 'Generic PLA @Test' },
        ],
        jobs: [{ field: 'print_profile', stale_value: 'Old Process', new_value: null }],
        inventory_filaments: [{ printer_preset: 'New Machine A', stale_name: 'Old Spool PLA', new_name: null, affected_filament_ids: [5, 6] }],
      },
    });
  });

  it('sends the chosen job replacement as typed', async () => {
    const api = stubFetch({ [CONFIRM]: RESULT });
    renderModal();
    await resolveRequiredPrinters();
    await userEvent.type(jobInput(), '0.28mm Draft');
    expect(datalistValues(jobInput())).toEqual(['0.28mm Draft']);

    await userEvent.click(confirmButton());

    await waitFor(() => expect(api.calls).toHaveLength(1));
    expect((api.calls[0].body as any).resolutions.jobs).toEqual(
      [{ field: 'print_profile', stale_value: 'Old Process', new_value: '0.28mm Draft' }]);
  });

  it('narrows inventory replacements by material, then brand, then search, and submits the picked name', async () => {
    const api = stubFetch({ [CONFIRM]: RESULT });
    renderModal();
    await resolveRequiredPrinters();
    const spoolSection = within(screen.getByRole('heading', { name: 'Inventory Filaments' }).closest('section') as HTMLElement);
    const [material] = spoolSection.getAllByRole('combobox');

    // materials are parsed out of the filament names; unknown ones fall under "Other"
    expect([...(material as HTMLSelectElement).options].map(o => o.text)).toEqual(['— material —', 'Other', 'PETG', 'PLA']);
    await userEvent.selectOptions(material, 'PLA');
    const brand = spoolSection.getAllByRole('combobox')[1] as HTMLSelectElement;
    expect([...brand.options].map(o => o.text)).toEqual(['Any Brand', 'Bambu', 'Generic', 'Polymaker']);

    const search = spoolSection.getByPlaceholderText('Search PLA (any brand)…');
    expect(datalistValues(search)).toEqual(['Generic PLA @Test', 'Polymaker PLA Pro', 'Bambu PLA Basic']);
    await userEvent.selectOptions(brand, 'Polymaker');
    expect(datalistValues(spoolSection.getByPlaceholderText('Search PLA by Polymaker…'))).toEqual(['Polymaker PLA Pro']);

    await userEvent.type(spoolSection.getByPlaceholderText('Search PLA by Polymaker…'), 'Polymaker PLA Pro');
    await userEvent.click(confirmButton());

    await waitFor(() => expect(api.calls).toHaveLength(1));
    expect((api.calls[0].body as any).resolutions.inventory_filaments[0].new_name).toBe('Polymaker PLA Pro');
  });

  it('changing the material clears the brand and search choice', async () => {
    renderModal();
    const section = within(screen.getByRole('heading', { name: 'Inventory Filaments' }).closest('section') as HTMLElement);
    await userEvent.selectOptions(section.getAllByRole('combobox')[0], 'PLA');
    await userEvent.selectOptions(section.getAllByRole('combobox')[1], 'Bambu');
    await userEvent.type(section.getByPlaceholderText('Search PLA by Bambu…'), 'Bambu PLA');

    await userEvent.selectOptions(section.getAllByRole('combobox')[0], 'PETG');

    expect((section.getAllByRole('combobox')[1] as HTMLSelectElement).value).toBe('');
    expect((section.getByPlaceholderText('Search PETG (any brand)…') as HTMLInputElement).value).toBe('');
  });

  it('disables both buttons while applying, then hands over the result', async () => {
    let release!: (body: unknown) => void;
    stubFetch({ [CONFIRM]: () => new Promise(res => { release = res; }) });  // a pending body: json() waits for it
    const { onDone } = renderModal();
    await resolveRequiredPrinters();

    await userEvent.click(confirmButton());

    await waitFor(() => expect(screen.getByRole('button', { name: 'Applying…' })).toBeTruthy());
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
    expect(confirmButton().disabled).toBe(true);
    expect(onDone).not.toHaveBeenCalled();

    release(RESULT);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(RESULT));
  });

  it('a 409 tells the operator the sync was superseded and lets them retry', async () => {
    stubFetch({ [CONFIRM]: new Reply(409, { detail: 'Sync superseded or expired' }) });
    const { onDone } = renderModal();
    await resolveRequiredPrinters();

    await userEvent.click(confirmButton());

    expect(await screen.findByText('Sync superseded — run the catalog sync again')).toBeTruthy();
    expect(onDone).not.toHaveBeenCalled();
    expect(confirmButton().disabled).toBe(false);
  });

  it('any other failure shows its message and keeps the modal usable', async () => {
    stubFetch({ [CONFIRM]: new Reply(500, 'boom') });
    const { onDone } = renderModal();
    await resolveRequiredPrinters();

    await userEvent.click(confirmButton());

    expect(await screen.findByText('500')).toBeTruthy();
    expect(onDone).not.toHaveBeenCalled();
    expect(confirmButton().disabled).toBe(false);
  });

  it('Cancel closes without calling the backend', async () => {
    const api = stubFetch({});
    const { onCancel, onDone } = renderModal();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onDone).not.toHaveBeenCalled();
    expect(api.calls).toEqual([]);
  });
});
