import { describe, it, expect } from 'vitest';
import { toFleetPrinter } from './fleet';
import type { FleetPrinter } from './fleet';

const BASE: FleetPrinter = {
  id: 1,
  name: 'Forge',
  printer_type: 'elegoo_centauri',
  enabled: true,
  queue_on: true,
  connected: true,
  awaiting_plate_clear: false,
  loaded_filaments: [],
  state: 'IDLE',
  progress: 0,
  remaining_time: 0,
  layer_num: null,
  total_layers: null,
  temperatures: {},
  capabilities: {},
  current_print: null,
  fan_model: 0,
  fan_aux: 0,
  fan_box: 0,
};

describe('toFleetPrinter', () => {
  it('converts numeric id to string', () => {
    expect(toFleetPrinter({ ...BASE, id: 42 }).id).toBe('42');
  });

  it('maps RUNNING state to printing status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'RUNNING' }).status).toBe('printing');
  });

  it('maps disconnected printer to offline regardless of state', () => {
    expect(toFleetPrinter({ ...BASE, connected: false, state: 'RUNNING' }).status).toBe('offline');
  });

  it('surfaces awaiting_plate_clear as its own field without masking the real status', () => {
    const p = toFleetPrinter({ ...BASE, state: 'RUNNING', awaiting_plate_clear: true });
    expect(p.awaitingPlateClear).toBe(true);
    expect(p.status).toBe('printing');  // not masked into "claiming"
  });

  it('defaults awaitingPlateClear to false when absent', () => {
    expect(toFleetPrinter({ ...BASE }).awaitingPlateClear).toBe(false);
  });

  it('maps PAUSE state to paused status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'PAUSE' }).status).toBe('paused');
  });

  it('maps FAILED state to error status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'FAILED' }).status).toBe('error');
  });

  it('maps IDLE state to idle status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'IDLE' }).status).toBe('idle');
  });

  it('uses first loaded filament as material', () => {
    const p = toFleetPrinter({
      ...BASE,
      loaded_filaments: [{ slot: 0, filament_id: null, name: 'PA-CF Black', type: 'PA-CF', color: '#0c0c0c' }],
    });
    expect(p.material).toEqual({ name: 'PA-CF Black', type: 'PA-CF', color: '#0c0c0c' });
  });

  it('uses placeholder material when loaded_filaments is empty', () => {
    const p = toFleetPrinter({ ...BASE, loaded_filaments: [] });
    expect(p.material).toEqual({ name: '—', type: '—', color: '#475472' });
  });

  it('extracts nozzle, bed, chamber temperatures', () => {
    const p = toFleetPrinter({ ...BASE, temperatures: { nozzle: 285, bed: 95, chamber: 58 } });
    expect(p.nozzleTemp).toBe(285);
    expect(p.bedTemp).toBe(95);
    expect(p.chamberTemp).toBe(58);
  });

  it('defaults missing temps to 0 and null', () => {
    const p = toFleetPrinter({ ...BASE, temperatures: {} });
    expect(p.nozzleTemp).toBe(0);
    expect(p.bedTemp).toBe(0);
    expect(p.chamberTemp).toBeNull();
  });

  it('maps layer_num + total_layers to layer object', () => {
    const p = toFleetPrinter({ ...BASE, layer_num: 88, total_layers: 312 });
    expect(p.layer).toEqual({ now: 88, total: 312 });
  });

  it('sets layer to null when layer_num is null', () => {
    expect(toFleetPrinter({ ...BASE, layer_num: null, total_layers: null }).layer).toBeNull();
  });

  it('rounds fractional progress', () => {
    expect(toFleetPrinter({ ...BASE, progress: 28.6 }).progress).toBe(29);
  });

  it('uses ECC badge for elegoo_centauri', () => {
    expect(toFleetPrinter({ ...BASE, printer_type: 'elegoo_centauri' }).badge).toBe('ECC');
  });

  it('uses P1S badge for bambu', () => {
    expect(toFleetPrinter({ ...BASE, printer_type: 'bambu' }).badge).toBe('P1S');
  });

  it('uses accent color for elegoo_centauri', () => {
    expect(toFleetPrinter({ ...BASE, printer_type: 'elegoo_centauri' }).accent).toBe('#22d3ee');
  });

  it('maps current_print to currentJobId', () => {
    expect(toFleetPrinter({ ...BASE, current_print: 'arm.gcode' }).currentJobId).toBe('arm.gcode');
  });

  it('maps null current_print to null currentJobId', () => {
    expect(toFleetPrinter({ ...BASE, current_print: null }).currentJobId).toBeNull();
  });

  it('maps fan_model, fan_aux, fan_box to Printer', () => {
    const p = toFleetPrinter({ ...BASE, fan_model: 80, fan_aux: 60, fan_box: 40 });
    expect(p.fanModel).toBe(80);
    expect(p.fanAux).toBe(60);
    expect(p.fanBox).toBe(40);
  });

  it('defaults fan fields to 0 when absent', () => {
    const p = toFleetPrinter(BASE);
    expect(p.fanModel).toBe(0);
    expect(p.fanAux).toBe(0);
    expect(p.fanBox).toBe(0);
  });

  it('maps temperatures.bed_target to bedTempTarget', () => {
    const p = toFleetPrinter({
      ...BASE,
      temperatures: { nozzle: 285, bed: 95, bed_target: 100 },
    });
    expect(p.bedTempTarget).toBe(100);
  });

  it('defaults bedTempTarget to 0 when bed_target absent', () => {
    const p = toFleetPrinter(BASE);
    expect(p.bedTempTarget).toBe(0);
  });
});
