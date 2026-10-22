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

  it('does not offer a model the user disabled in the registry, while its enabled siblings stay', () => {
    stubFetch(ROUTES);
    renderForm([BAMBU_P1S, printerType({ ...BAMBU_X1C, model_enabled: false })]);
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

describe('PrinterAddForm — model-specific form defaults and custom models (BIZ-148)', () => {
  const TOOLHEADS = (n: number) => ({ ...IP_FIELD, name: 'toolheads', label: 'Toolheads', required: false, default: n });
  const DUAL = printerType({ plugin_id: 'moonraker', manufacturer_id: 'acme', manufacturer_name: 'Acme', model_id: 'dual', display_name: 'Dual',
    toolheads: 2, connection_fields: [IP_FIELD, TOOLHEADS(2)] });
  const CUSTOM = printerType({ plugin_id: 'moonraker', manufacturer_id: 'generic', manufacturer_name: 'Generic', model_id: 'custom_klipper',
    display_name: 'Custom Klipper printer', custom: true, bed_mm: [250, 250], connection_fields: [IP_FIELD, TOOLHEADS(1)] });

  async function run(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: /^Next/ }));      // → connect
    await user.type(field('IP Address'), '10.0.0.9');
  }
  const finishFromConnect = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole('button', { name: /^Next/ }));      // → profile
    await user.click(screen.getByRole('button', { name: /^Next/ }));      // → review
    await user.click(screen.getByRole('button', { name: /Finish/i }));
  };

  it('stores the default the form shows (a declared model\'s toolhead count) even if the user never touches the field', async () => {
    const user = userEvent.setup();
    const { to } = stubFetch({ ...ROUTES, 'POST /api/v1/printers': { id: 1 } });
    renderForm([DUAL]);
    await run(user);

    expect(field('Toolheads (optional)').value).toBe('2');
    await finishFromConnect(user);

    await waitFor(() => expect(to('POST', '/api/v1/printers')).toHaveLength(1));
    const body = to('POST', '/api/v1/printers')[0].body as Record<string, any>;
    expect(body.connection_config).toEqual({ ip_address: '10.0.0.9', toolheads: '2' });
    expect(body).not.toHaveProperty('bed_x_mm');                                    // a declared model's bed comes from the declaration
  });

  it('a custom model asks for the bed size, starts at the placeholder and sends exactly what the user states', async () => {
    const user = userEvent.setup();
    const { to } = stubFetch({ ...ROUTES, 'POST /api/v1/printers': { id: 1 } });
    renderForm([CUSTOM]);
    await run(user);

    const x = screen.getByLabelText('Bed width X (mm)') as HTMLInputElement;
    expect(x.value).toBe('250');
    await user.clear(x);
    await user.type(x, '410');
    await user.clear(screen.getByLabelText('Bed depth Y (mm)'));
    await user.type(screen.getByLabelText('Bed depth Y (mm)'), '205.5');
    await finishFromConnect(user);

    await waitFor(() => expect(to('POST', '/api/v1/printers')).toHaveLength(1));
    expect(to('POST', '/api/v1/printers')[0].body).toMatchObject({ model_id: 'custom_klipper', bed_x_mm: 410, bed_y_mm: 205.5 });
  });

  it('a nonsense bed value falls back to the placeholder instead of sending 0 or NaN', async () => {
    const user = userEvent.setup();
    const { to } = stubFetch({ ...ROUTES, 'POST /api/v1/printers': { id: 1 } });
    renderForm([CUSTOM]);
    await run(user);
    await user.clear(screen.getByLabelText('Bed width X (mm)'));
    await finishFromConnect(user);

    await waitFor(() => expect(to('POST', '/api/v1/printers')).toHaveLength(1));
    expect(to('POST', '/api/v1/printers')[0].body).toMatchObject({ bed_x_mm: 250, bed_y_mm: 250 });
  });

  it('any plugin\'s form defaults are stored, not only Moonraker\'s: a Bambu default the form shows is the value sent', async () => {
    const user = userEvent.setup();
    const { to } = stubFetch({ ...ROUTES, 'POST /api/v1/printers': { id: 1 } });
    const bambu = printerType({ ...BAMBU_P1S, connection_fields: [IP_FIELD, { ...IP_FIELD, name: 'use_ams', label: 'Use AMS', required: false, default: 1 },
                                                                   { ...IP_FIELD, name: 'timelapse', label: 'Timelapse', required: false, default: 0 }] });
    renderForm([bambu]);
    await run(user);
    await user.clear(field('Use AMS (optional)'));                                        // a field the user blanks stays blank
    await user.click(screen.getByRole('button', { name: /^Next/ }));
    await user.click(screen.getByRole('button', { name: /^Next/ }));
    await user.click(screen.getByRole('button', { name: /Finish/i }));

    await waitFor(() => expect(to('POST', '/api/v1/printers')).toHaveLength(1));
    expect((to('POST', '/api/v1/printers')[0].body as Record<string, unknown>).connection_config)
      .toEqual({ ip_address: '10.0.0.9', use_ams: '', timelapse: '0' });
  });

  it('a declared (non-custom) model never shows the bed inputs', async () => {
    const user = userEvent.setup();
    stubFetch(ROUTES);
    renderForm([DUAL]);
    await run(user);
    expect(screen.queryByLabelText('Bed width X (mm)')).toBeNull();
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

  it('picks the declared model an announcement names among several ("X1 / X1 Carbon" -> X1 Carbon, longest contained name wins)', async () => {
    const user = userEvent.setup();
    stubFetch({ ...ROUTES, 'POST /api/v1/printers/discover': found({ model: 'X1 / X1 Carbon' }) });
    renderForm([ELEGOO, BAMBU_P1S, BAMBU_X1C]);
    await scanAndUse(user);
    expect(screen.getByText('Connect to X1 Carbon')).toBeTruthy();
  });

  it('does not guess when no declared model matches: stays on the printer step with the plugin selected and asks for the model', async () => {
    const user = userEvent.setup();
    stubFetch({ ...ROUTES, 'POST /api/v1/printers/discover': found({ model: 'C13' }) });   // a raw, unmapped model code
    renderForm([ELEGOO, BAMBU_P1S, BAMBU_X1C]);
    await scanAndUse(user);

    expect(screen.queryByText(/^Connect to/)).toBeNull();                                  // still step 1
    expect(screen.getByRole('status').textContent).toMatch(/choose the model/i);
    expect(selectedText(select('Manufacturer'))).toBe('Bambu Lab');                         // the plugin is known, the model is not
    expect((screen.getByPlaceholderText('e.g. Atlas, Forge, Iris') as HTMLInputElement).value).toBe('Bambu-P1S');

    // choosing the model keeps what discovery found (same plugin, same connection form) and clears the notice
    await user.selectOptions(select('Model'), 'bambu/x1c');
    expect(screen.queryByRole('status')).toBeNull();
    await user.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText('Connect to X1 Carbon')).toBeTruthy();
    expect(field('IP Address').value).toBe('192.168.7.20');
    expect(field('Serial Number').value).toBe('SER1');
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
