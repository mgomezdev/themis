import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { PrinterConsoleScreen } from './PrinterConsoleScreen';
import type { FleetPrinter } from '../api/fleet';
import * as fleetApi from '../api/fleet';
import * as printersApi from '../api/printers';

vi.mock('../api/fleet', async (orig) => ({ ...(await orig<typeof fleetApi>()), useFleetRaw: vi.fn() }));
vi.mock('../api/printers', async (orig) => {
  const actual = await orig<typeof printersApi>();
  return {
    ...actual,
    jogAxis: vi.fn(), homePrinter: vi.fn(), setNozzleTemp: vi.fn(), setBedTemp: vi.fn(),
    setChamberTemp: vi.fn(), uploadToPrinter: vi.fn(),
  };
});

const ALL_CAPS = { axis_jog: true, home_axes: true, nozzle_temp: true, temp_control: true, chamber_temp: true, direct_upload: true };

function printer(over: Partial<FleetPrinter> = {}): FleetPrinter {
  return {
    id: 1, name: 'Atlas', printer_type: 'bambu', enabled: true, queue_on: true, connected: true,
    awaiting_plate_clear: false, no_snapshots_while_idle: false, loaded_filaments: [], state: 'IDLE', progress: 0,
    remaining_time: 0, layer_num: null, total_layers: null,
    temperatures: { nozzle: 25, nozzle_target: 0, bed: 24, bed_target: 0, chamber: 22 },
    capabilities: ALL_CAPS, current_print: null, fan_model: 0, fan_aux: 0, fan_box: 0, ...over,
  };
}

function show(fleet: FleetPrinter[], path = '/fleet/1/console') {
  vi.mocked(fleetApi.useFleetRaw).mockReturnValue([fleet, vi.fn()]);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/fleet/:id/console" element={<PrinterConsoleScreen />} />
        <Route path="/fleet" element={<div>fleet page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(printersApi.jogAxis).mockResolvedValue(undefined);
  vi.mocked(printersApi.homePrinter).mockResolvedValue(undefined);
  vi.mocked(printersApi.setNozzleTemp).mockResolvedValue(undefined);
  vi.mocked(printersApi.setBedTemp).mockResolvedValue(undefined);
  vi.mocked(printersApi.setChamberTemp).mockResolvedValue(undefined);
  vi.mocked(printersApi.uploadToPrinter).mockResolvedValue({ ok: true, filename: 'a.gcode', started: false });
});

describe('PrinterConsoleScreen — capability gating', () => {
  it('offers only what the printer reports it can do', () => {
    show([printer({ capabilities: { temp_control: true, direct_upload: true } })]);

    expect(screen.getByRole('button', { name: 'Z+' })).toBeTruthy();                 // baseline
    expect(screen.getByRole('button', { name: 'Home all' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'X+' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Home X' })).toBeNull();
    expect(screen.queryByLabelText('Nozzle setpoint')).toBeNull();
    expect(screen.queryByLabelText('Chamber setpoint')).toBeNull();
    expect(screen.getByLabelText('Bed setpoint')).toBeTruthy();
    expect(screen.getByLabelText('File to send')).toBeTruthy();
  });

  it('shows every control when every capability is on', () => {
    show([printer()]);
    for (const name of ['X−', 'X+', 'Y−', 'Y+', 'Z−', 'Z+', 'Home all', 'Home X', 'Home Y', 'Home Z']) {
      expect(screen.getByRole('button', { name })).toBeTruthy();
    }
    for (const label of ['Nozzle setpoint', 'Bed setpoint', 'Chamber setpoint']) expect(screen.getByLabelText(label)).toBeTruthy();
  });

  it('hides the upload card without direct_upload', () => {
    show([printer({ capabilities: { ...ALL_CAPS, direct_upload: false } })]);
    expect(screen.queryByLabelText('File to send')).toBeNull();
  });
});

describe('PrinterConsoleScreen — motion', () => {
  it('jogs by the selected step in the pressed direction', async () => {
    const user = userEvent.setup();
    show([printer()]);

    await user.click(screen.getByRole('button', { name: '1 mm' }));
    await user.click(screen.getByRole('button', { name: 'Y−' }));
    await user.click(screen.getByRole('button', { name: 'Z+' }));

    expect(printersApi.jogAxis).toHaveBeenNthCalledWith(1, 1, 'Y', -1);
    expect(printersApi.jogAxis).toHaveBeenNthCalledWith(2, 1, 'Z', 1);
    expect((await screen.findByRole('status')).textContent).toMatch(/sent/);
  });

  it('homes all axes or a single one', async () => {
    const user = userEvent.setup();
    show([printer()]);
    await user.click(screen.getByRole('button', { name: 'Home all' }));
    await user.click(screen.getByRole('button', { name: 'Home Y' }));
    expect(printersApi.homePrinter).toHaveBeenNthCalledWith(1, 1, 'all');
    expect(printersApi.homePrinter).toHaveBeenNthCalledWith(2, 1, 'Y');
  });

  it.each([['printing', 'RUNNING'], ['paused', 'PAUSE']])('locks motion while %s', async (_n, state) => {
    show([printer({ state })]);
    for (const name of ['X+', 'Z−', 'Home all']) expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/motion is locked/)).toBeTruthy();
    expect(printersApi.jogAxis).not.toHaveBeenCalled();
  });

  it('locks everything while offline', () => {
    show([printer({ connected: false })]);
    expect((screen.getByRole('button', { name: 'Z+' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('offline')).toBeTruthy();
  });

  it('shows a failure inline', async () => {
    vi.mocked(printersApi.jogAxis).mockRejectedValue(new Error('409 Printer is printing'));
    const user = userEvent.setup();
    show([printer()]);
    await user.click(screen.getByRole('button', { name: 'X+' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Jog X\+10 failed: 409 Printer is printing/);
  });
});

describe('PrinterConsoleScreen — temperatures', () => {
  it('sets and clears a setpoint', async () => {
    const user = userEvent.setup();
    show([printer()]);
    const row = screen.getByLabelText('Nozzle setpoint');
    const set = row.parentElement!.querySelectorAll('button')[0] as HTMLButtonElement;
    expect(set.disabled).toBe(true);                                                   // nothing typed yet

    await user.type(row, '210');
    await user.click(set);
    expect(printersApi.setNozzleTemp).toHaveBeenCalledWith(1, 210);

    await user.click(row.parentElement!.querySelectorAll('button')[1]);               // Off
    expect(printersApi.setNozzleTemp).toHaveBeenLastCalledWith(1, 0);
  });

  it('routes each setpoint to its own endpoint', async () => {
    const user = userEvent.setup();
    show([printer()]);
    await user.type(screen.getByLabelText('Bed setpoint'), '60');
    await user.click(screen.getByLabelText('Bed setpoint').parentElement!.querySelectorAll('button')[0]);
    await user.type(screen.getByLabelText('Chamber setpoint'), '35');
    await user.click(screen.getByLabelText('Chamber setpoint').parentElement!.querySelectorAll('button')[0]);
    expect(printersApi.setBedTemp).toHaveBeenCalledWith('1', 60);
    expect(printersApi.setChamberTemp).toHaveBeenCalledWith(1, 35);
  });

  it('shows current → target readings', () => {
    show([printer({ temperatures: { nozzle: 198.6, nozzle_target: 200, bed: 24, bed_target: 0 } })]);
    expect(screen.getByText(/199°C/)).toBeTruthy();
    expect(screen.getByText(/→ 200°C/)).toBeTruthy();
  });
});

describe('PrinterConsoleScreen — upload', () => {
  const pick = (name = 'part.gcode') => {
    const file = new File(['G28'], name);
    fireEvent.change(screen.getByLabelText('File to send'), { target: { files: [file] } });
    return file;
  };

  it('needs a file, then uploads without starting', async () => {
    const user = userEvent.setup();
    show([printer()]);
    expect((screen.getByRole('button', { name: /upload only/i }) as HTMLButtonElement).disabled).toBe(true);

    const file = pick();
    await user.click(screen.getByRole('button', { name: /upload only/i }));

    expect(printersApi.uploadToPrinter).toHaveBeenCalledWith(1, file, false);
    expect((await screen.findByRole('status')).textContent).toBe('Uploaded a.gcode');
  });

  it('asks before starting a print, and does nothing if declined', async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    show([printer()]);
    pick();

    await user.click(screen.getByRole('button', { name: /upload & print/i }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('bypasses the queue'));
    expect(printersApi.uploadToPrinter).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    vi.mocked(printersApi.uploadToPrinter).mockResolvedValue({ ok: true, filename: 'part.gcode', started: true });
    await user.click(screen.getByRole('button', { name: /upload & print/i }));
    await waitFor(() => expect(printersApi.uploadToPrinter).toHaveBeenCalledWith(1, expect.any(File), true));
    expect((await screen.findByRole('status')).textContent).toBe('Uploaded and started part.gcode');
  });

  it('cannot start a print while one is running, but can still upload', () => {
    show([printer({ state: 'RUNNING' })]);
    pick();
    expect((screen.getByRole('button', { name: /upload & print/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /upload only/i }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('reports a rejected upload', async () => {
    vi.mocked(printersApi.uploadToPrinter).mockRejectedValue(new Error('502 Printer rejected the upload'));
    const user = userEvent.setup();
    show([printer()]);
    pick();
    await user.click(screen.getByRole('button', { name: /upload only/i }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Upload failed: 502 Printer rejected the upload/);
  });
});

describe('PrinterConsoleScreen — switching printers', () => {
  const fleet = [printer({ id: 1, name: 'Atlas' }), printer({ id: 2, name: 'Borealis' }), printer({ id: 3, name: 'Cirrus' })];

  it('steps to the next and previous printer, wrapping at the ends', async () => {
    const user = userEvent.setup();
    show(fleet, '/fleet/3/console');
    expect(screen.getByRole('heading', { name: 'Cirrus' })).toBeTruthy();
    expect(screen.getByText(/3 of 3/)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Next printer' }));
    expect(screen.getByRole('heading', { name: 'Atlas' })).toBeTruthy();             // wraps to the first

    await user.click(screen.getByRole('button', { name: 'Previous printer' }));
    expect(screen.getByRole('heading', { name: 'Cirrus' })).toBeTruthy();            // and back
  });

  it('has no switcher targets with a single printer', () => {
    show([printer()]);
    expect((screen.getByRole('button', { name: 'Next printer' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('says so for an unknown printer id', () => {
    show(fleet, '/fleet/99/console');
    expect(screen.getByText(/Printer not found/)).toBeTruthy();
  });
});

describe('PrinterConsoleScreen — temperature chart', () => {
  it('starts with a placeholder (one sample is not a trend)', () => {
    show([printer()]);
    expect(screen.getByTestId('temp-chart-empty')).toBeTruthy();
  });
});
