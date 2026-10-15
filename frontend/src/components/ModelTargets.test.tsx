import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ApiPrinter } from '../api/printers';
import { ModelPicker, buildEligibility, isModelKey, modelConfigSource, modelGroups, modelKey, modelLabelOf, modelUuidOf } from './ModelTargets';
import { defaultPerPrinterCfg } from './PerPrinterConfig';

const printer = (id: number, model_uuid: string | null, model_label: string | null, over: Partial<ApiPrinter> = {}): ApiPrinter => ({
  id, name: `P${id}`, printer_type: 'bambu', plugin_id: 'bambu', manufacturer_id: 'bambu', model_id: 'p1s', model_uuid, connection_config: {},
  awaiting_plate_clear: false, orca_printer_profiles: [], machine_profile_override: null, nozzle_variant: null, machine_profile: null,
  machine_profile_reason: null, model_label, enabled: true, queue_on: true, connected: true, loaded_filaments: [{ slot: 0, filament_id: null, name: 'x', type: 'PLA', color: '#fff' }],
  build_plate_type: null, no_snapshots_while_idle: false, bed_x_mm: 256, bed_y_mm: 256, machine_rate_per_hour: null,
  preferred_slicer_plugin_id: null, quiet_start: null, quiet_end: null, ...over,
});
const FLEET = [printer(1, 'm-p1s', 'Bambu Lab P1S'), printer(2, 'm-x1c', 'Bambu Lab X1 Carbon'), printer(3, 'm-p1s', 'Bambu Lab P1S'), printer(4, null, null)];

describe('model targets are keyed by registry model, not by machine profile', () => {
  it('groups printers by model_uuid and leaves a printer without a model out', () => {
    const groups = modelGroups(FLEET);
    expect([...groups.keys()]).toEqual(['m-p1s', 'm-x1c']);
    expect(groups.get('m-p1s')!.map(p => p.id)).toEqual([1, 3]);
  });

  it('two printers of one model group together even when their nozzles (profiles) differ', () => {
    const six = printer(5, 'm-p1s', 'Bambu Lab P1S', { machine_profile: 'P1S 0.6', nozzle_variant: { diameter_mm: 0.6, flow: 'standard' } });
    const four = printer(6, 'm-p1s', 'Bambu Lab P1S', { machine_profile: 'P1S 0.4' });
    expect(modelGroups([six, four]).get('m-p1s')!.map(p => p.id)).toEqual([5, 6]);
  });

  it('keys round-trip the uuid', () => {
    expect(isModelKey(modelKey('m-p1s'))).toBe(true);
    expect(isModelKey('12')).toBe(false);
    expect(modelUuidOf(modelKey('m-p1s'))).toBe('m-p1s');
  });

  it('labels a model from its printers, else from the stored target, else generically', () => {
    expect(modelLabelOf(modelKey('m-x1c'), FLEET)).toBe('Bambu Lab X1 Carbon');
    expect(modelLabelOf(modelKey('gone'), FLEET, 'Stored label')).toBe('Stored label');
    expect(modelLabelOf(modelKey('gone'), FLEET)).toBe('this model');
  });

  it('presents a representative printer as "Any <model>" with no slots, or null when the model has no printer now', () => {
    const src = modelConfigSource(modelKey('m-p1s'), FLEET)!;
    expect(src.printerId).toBe('1');
    expect(src.printers).toEqual([expect.objectContaining({ id: 1, name: 'Any Bambu Lab P1S', loaded_filaments: [] })]);
    expect(modelConfigSource(modelKey('gone'), FLEET)).toBeNull();
  });

  it('the picker shows one tile per model with its printer count and toggles the model key', async () => {
    const toggled: string[] = [];
    render(<ModelPicker printers={FLEET} selected={[modelKey('m-x1c')]} onToggle={k => toggled.push(k)} />);

    expect(screen.getByText('Any Bambu Lab P1S')).toBeTruthy();
    expect(screen.getByText('2 printers now')).toBeTruthy();
    expect(screen.getByText('1 printer now')).toBeTruthy();
    await userEvent.click(screen.getByTestId('model-target-m-p1s'));
    expect(toggled).toEqual([modelKey('m-p1s')]);
  });

  it('the picker is empty when no printer has a model', () => {
    const { container } = render(<ModelPicker printers={[printer(4, null, null)]} selected={[]} onToggle={() => {}} />);
    expect(container.textContent).toBe('');
  });

  it('builds model_targets with model_uuid and printer_configs with printer ids from one mixed selection', () => {
    const cfg = { ...defaultPerPrinterCfg(), printProfile: '0.20mm', filamentType: 'PLA' };
    const { printer_configs, model_targets } = buildEligibility([modelKey('m-p1s'), '2'], { [modelKey('m-p1s')]: cfg, '2': cfg });
    expect(model_targets).toEqual([expect.objectContaining({ model_uuid: 'm-p1s', print_profile: '0.20mm', filament_type: 'PLA' })]);
    expect(model_targets[0]).not.toHaveProperty('machine_profile');
    expect(printer_configs).toEqual([expect.objectContaining({ printer_id: 2 })]);
  });
});
