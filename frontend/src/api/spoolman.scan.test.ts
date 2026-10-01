import { describe, expect, it } from 'vitest';
import { parseSpoolCode, slotPatchForSpool, type ApiFilament, type ApiSpool } from './spoolman';

describe('parseSpoolCode', () => {
  it.each([
    ['web+spoolman:s-12', 12],            // what Spoolman's QR labels encode
    ['  WEB+SPOOLMAN:S-7 \n', 7],
    ['s-345', 345],
    ['https://spoolman.lan:7912/spool/show/88', 88],
    ['42', 42],
    ['  9 ', 9],
  ])('%j → %s', (text, id) => expect(parseSpoolCode(text)).toBe(id));

  it.each(['', 'hello', 's-', 'web+spoolman:f-3', '12 34', 'spool-9x', '-5'])('%j is not a spool code', text => {
    expect(parseSpoolCode(text)).toBeNull();
  });
});

describe('slotPatchForSpool', () => {
  const spool = (over: Partial<ApiSpool['filament']> = {}): ApiSpool => ({
    id: 3, remaining_weight: 400, used_weight: 100,
    filament: { id: 10, vendor: { name: 'ELEGOO' }, name: 'Sky Blue PLA', material: 'PLA', color_hex: '87CEEB', ...over },
  });
  const filament = (profiles: Record<string, string[]>): ApiFilament => ({
    id: 10, name: 'Sky Blue PLA', material: 'PLA', extra: { orca_profiles: JSON.stringify(JSON.stringify(profiles)) },
  });

  it('copies material, colour, name and links the spool; picks the profile when the printer preset has exactly one', () => {
    expect(slotPatchForSpool(spool(), [filament({ 'Bambu X1': ['ELEGOO PLA @X1'] })], 'Bambu X1')).toEqual({
      spoolman_spool_id: '3', type: 'PLA', color: '#87CEEB', filament_profile: 'ELEGOO PLA @X1', name: 'ELEGOO Sky Blue PLA',
    });
  });

  it('keeps the slot’s existing profile and colour when it cannot decide', () => {
    const patch = slotPatchForSpool(spool({ color_hex: undefined }), [filament({ 'Bambu X1': ['A', 'B'] })], 'Bambu X1',
      { color: '#123456', filament_profile: 'Existing' });
    expect(patch).toMatchObject({ color: '#123456', filament_profile: 'Existing' });
    expect(slotPatchForSpool(spool(), [], null)).toMatchObject({ filament_profile: null });
  });
});
