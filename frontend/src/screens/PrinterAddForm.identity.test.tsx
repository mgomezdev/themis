import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrinterAddForm } from './PrintersScreen';
import type { PrinterType } from '../api/printers';
import { stubFetch } from '../test/fetchStub';
import { printerType, IP_FIELD } from '../test/printerTypes';

const SERIAL_FIELD = { ...IP_FIELD, name: 'serial_number', label: 'Serial Number' };
const PORT_FIELD = { ...IP_FIELD, name: 'port', label: 'Port Number', required: false };

const BAMBU_P1S = printerType({
  plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S',
  connection_fields: [IP_FIELD, SERIAL_FIELD],
});
const BAMBU_X1C = printerType({
  plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'x1c', display_name: 'X1 Carbon',
  connection_fields: [IP_FIELD, SERIAL_FIELD],
});
const ELEGOO = printerType({
  plugin_id: 'elegoo_centauri', manufacturer_id: 'elegoo', manufacturer_name: 'Elegoo', model_id: 'centauri', display_name: 'Centauri Carbon',
  connection_fields: [IP_FIELD, PORT_FIELD],
});
const DISABLED = printerType({
  plugin_id: 'prusa', manufacturer_id: 'prusa', manufacturer_name: 'Prusa', model_id: 'mk4', display_name: 'MK4',
  connection_fields: [IP_FIELD], plugin_enabled: false,
});

const ROUTES = { 'GET /api/v1/printers/orca-machine-catalog': [] };

afterEach(() => vi.unstubAllGlobals());

const select = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;
const optionTexts = (el: HTMLElement) => within(el).getAllByRole('option').map(o => o.textContent);
const selectedText = (el: HTMLSelectElement) => el.selectedOptions[0]?.textContent;
const field = (label: string) => screen.getByText(label).parentElement!.querySelector('input') as HTMLInputElement;

function renderForm(types: PrinterType[]) {
  return render(<PrinterAddForm types={types} onCancel={() => {}} onCreated={() => {}} />);
}

describe('PrinterAddForm — step 1 manufacturer/model', () => {
  it('offers only enabled entries; manufacturers unique in order of first appearance; first entry preselected', () => {
    stubFetch(ROUTES);
    renderForm([BAMBU_P1S, DISABLED, ELEGOO, BAMBU_X1C]);
    expect(optionTexts(select('Manufacturer'))).toEqual(['Bambu Lab', 'Elegoo']);
    expect(selectedText(select('Manufacturer'))).toBe('Bambu Lab');
    expect(optionTexts(select('Model'))).toEqual(['P1S', 'X1 Carbon']);
    expect(selectedText(select('Model'))).toBe('P1S');
    expect(screen.queryByText('Prusa')).toBeNull();
    expect(screen.queryByText('MK4')).toBeNull();
  });

  it('skips a leading disabled entry when choosing the initial selection', () => {
    stubFetch(ROUTES);
    renderForm([DISABLED, ELEGOO]);
    expect(optionTexts(select('Manufacturer'))).toEqual(['Elegoo']);
    expect(selectedText(select('Model'))).toBe('Centauri Carbon');
  });

  it('changing manufacturer selects its first model and clears typed connection values', async () => {
    const user = userEvent.setup();
    stubFetch(ROUTES);
    renderForm([BAMBU_P1S, BAMBU_X1C, ELEGOO]);
    await user.selectOptions(select('Model'), 'X1 Carbon');
    await user.click(screen.getByRole('button', { name: /^Next/ }));
    await user.type(field('IP Address'), '10.0.0.5');
    expect(field('IP Address').value).toBe('10.0.0.5');
    await user.click(screen.getByRole('button', { name: /Back/i }));

    await user.selectOptions(select('Manufacturer'), 'Elegoo');
    expect(selectedText(select('Model'))).toBe('Centauri Carbon');
    await user.click(screen.getByRole('button', { name: /^Next/ }));
    expect(field('IP Address').value).toBe('');
  });

  it('changing back to the first manufacturer selects its first model, not the previously chosen one', async () => {
    const user = userEvent.setup();
    stubFetch(ROUTES);
    renderForm([BAMBU_P1S, BAMBU_X1C, ELEGOO]);
    await user.selectOptions(select('Model'), 'X1 Carbon');
    await user.selectOptions(select('Manufacturer'), 'Elegoo');
    await user.selectOptions(select('Manufacturer'), 'Bambu Lab');
    expect(selectedText(select('Model'))).toBe('P1S');
  });

  it('labels a model offered by several enabled plugins with its plugin id, others plainly', () => {
    stubFetch(ROUTES);
    const dupA = printerType({ ...BAMBU_P1S, plugin_id: 'bambu_a' });
    const dupB = printerType({ ...BAMBU_P1S, plugin_id: 'bambu_b' });
    renderForm([dupA, dupB, BAMBU_X1C]);
    expect(optionTexts(select('Manufacturer'))).toEqual(['Bambu Lab']);
    expect(optionTexts(select('Model'))).toEqual(['P1S (bambu_a)', 'P1S (bambu_b)', 'X1 Carbon']);
  });

  it('does not treat a disabled duplicate as ambiguous', () => {
    stubFetch(ROUTES);
    const off = printerType({ ...BAMBU_P1S, plugin_id: 'bambu_off', plugin_enabled: false });
    renderForm([BAMBU_P1S, off]);
    expect(optionTexts(select('Model'))).toEqual(['P1S']);
  });

  it('with no enabled entry shows a notice and disables Next', () => {
    stubFetch(ROUTES);
    renderForm([DISABLED]);
    expect(screen.getByText(/No printer plugin is enabled/)).toBeTruthy();
    expect((screen.getByRole('button', { name: /^Next/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('Next shows "Connect to <model>" with the selected entry\'s connection fields', async () => {
    const user = userEvent.setup();
    stubFetch(ROUTES);
    renderForm([BAMBU_P1S, ELEGOO]);
    await user.selectOptions(select('Manufacturer'), 'Elegoo');
    await user.click(screen.getByRole('button', { name: /^Next/ }));
    expect(screen.getByText('Connect to Centauri Carbon')).toBeTruthy();
    expect(screen.getByText(/Port Number/)).toBeTruthy();
    expect(screen.queryByText('Serial Number')).toBeNull();
  });
});

describe('PrinterAddForm — create body', () => {
  async function finish(user: ReturnType<typeof userEvent.setup>, nickname?: string) {
    if (nickname) await user.type(screen.getByPlaceholderText('e.g. Atlas, Forge, Iris'), nickname);
    await user.click(screen.getByRole('button', { name: /^Next/ }));      // → connect
    await user.type(field('IP Address'), '10.0.0.9');
    await user.click(screen.getByRole('button', { name: /^Next/ }));      // → profile
    await user.click(screen.getByRole('button', { name: /^Next/ }));      // → review
    await user.click(screen.getByRole('button', { name: /Finish/i }));
  }

  it('POSTs plugin_id/manufacturer_id/model_id of the selection, no printer_type, default name from manufacturer + model', async () => {
    const user = userEvent.setup();
    const { to } = stubFetch({ ...ROUTES, 'POST /api/v1/printers': { id: 1 } });
    renderForm([BAMBU_P1S, ELEGOO]);
    await user.selectOptions(select('Manufacturer'), 'Elegoo');
    await finish(user);
    await waitFor(() => expect(to('POST', '/api/v1/printers')).toHaveLength(1));
    const body = to('POST', '/api/v1/printers')[0].body as Record<string, unknown>;
    expect(body).toMatchObject({
      plugin_id: 'elegoo_centauri', manufacturer_id: 'elegoo', model_id: 'centauri',
      name: 'Elegoo Centauri Carbon',
    });
    expect((body.connection_config as Record<string, unknown>).ip_address).toBe('10.0.0.9');
    expect(body).not.toHaveProperty('printer_type');
  });

  it('uses the typed nickname as name', async () => {
    const user = userEvent.setup();
    const { to } = stubFetch({ ...ROUTES, 'POST /api/v1/printers': { id: 1 } });
    renderForm([BAMBU_P1S, BAMBU_X1C]);
    await user.selectOptions(select('Model'), 'X1 Carbon');
    await finish(user, 'Forge');
    await waitFor(() => expect(to('POST', '/api/v1/printers')).toHaveLength(1));
    const body = to('POST', '/api/v1/printers')[0].body as Record<string, unknown>;
    expect(body).toMatchObject({ name: 'Forge', plugin_id: 'bambu', manufacturer_id: 'bambu', model_id: 'x1c' });
    expect(body).not.toHaveProperty('printer_type');
  });

  it('disambiguated duplicate: body carries the plugin the user picked', async () => {
    const user = userEvent.setup();
    const { to } = stubFetch({ ...ROUTES, 'POST /api/v1/printers': { id: 1 } });
    const dupA = printerType({ ...BAMBU_P1S, plugin_id: 'bambu_a' });
    const dupB = printerType({ ...BAMBU_P1S, plugin_id: 'bambu_b' });
    renderForm([dupA, dupB]);
    await user.selectOptions(select('Model'), 'P1S (bambu_b)');
    await finish(user);
    await waitFor(() => expect(to('POST', '/api/v1/printers')).toHaveLength(1));
    expect((to('POST', '/api/v1/printers')[0].body as Record<string, unknown>).plugin_id).toBe('bambu_b');
  });
});

describe('PrinterAddForm — discovery pre-fill by plugin', () => {
  const found = (over: Record<string, unknown>) => ({
    ranges: ['192.168.7.0/24'], scanned: 254, truncated: false,
    found: [{
      printer_type: 'bambu', plugin_id: 'bambu', display_name: 'Bambu Lab', ip: '192.168.7.20', model: 'P1S', name: 'Bambu-P1S',
      serial: 'SER1', connection_config: { ip_address: '192.168.7.20', serial_number: 'SER1' },
      note: null, already_added: false, ...over,
    }],
  });

  async function scanAndUse(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: /scan network for printers/i }));
    await user.type(screen.getByLabelText('Network ranges'), '192.168.7.0/24');
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.click(await screen.findByRole('button', { name: 'Use 192.168.7.20' }));
  }

  it('selects the plugin entry whose display_name matches the discovered model (case-insensitive)', async () => {
    const user = userEvent.setup();
    stubFetch({ ...ROUTES, 'POST /api/v1/printers/discover': found({ model: 'x1 carbon' }) });
    renderForm([ELEGOO, BAMBU_P1S, BAMBU_X1C]);
    await scanAndUse(user);
    expect(screen.getByText('Connect to X1 Carbon')).toBeTruthy();
    expect(field('IP Address').value).toBe('192.168.7.20');
    expect(field('Serial Number').value).toBe('SER1');
  });

  it('falls back to the plugin\'s first enabled entry when no display_name matches, pre-filling nickname', async () => {
    const user = userEvent.setup();
    stubFetch({ ...ROUTES, 'POST /api/v1/printers/discover': found({ model: 'Unknown Thing' }) });
    renderForm([ELEGOO, BAMBU_P1S, BAMBU_X1C]);
    await scanAndUse(user);
    expect(screen.getByText('Connect to P1S')).toBeTruthy();
    expect(field('IP Address').value).toBe('192.168.7.20');
    await user.click(screen.getByRole('button', { name: /Back/i }));
    expect((screen.getByPlaceholderText('e.g. Atlas, Forge, Iris') as HTMLInputElement).value).toBe('Bambu-P1S');
    expect(selectedText(select('Manufacturer'))).toBe('Bambu Lab');
  });

  it('a discovered printer whose plugin has no enabled entry does nothing', async () => {
    const user = userEvent.setup();
    stubFetch({ ...ROUTES, 'POST /api/v1/printers/discover': found({ plugin_id: 'prusa', printer_type: 'prusa', model: 'MK4' }) });
    renderForm([BAMBU_P1S, DISABLED]);
    await scanAndUse(user);
    expect(screen.queryByText(/^Connect to/)).toBeNull();
    expect(screen.getByLabelText('Manufacturer')).toBeTruthy();
    expect(selectedText(select('Model'))).toBe('P1S');
  });
});
