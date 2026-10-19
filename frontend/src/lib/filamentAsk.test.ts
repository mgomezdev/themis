import { describe, expect, it } from 'vitest';
import {
  ANY, NO_ASK, STANDARD, askFromMaterial, askIsUndefined, describeAsk, firstMatchingSlot, normalizeAsk, slotIsLoaded, splitType,
  type FilamentAsk,
} from './filamentAsk';

const ask = (m: string | null, t: string | null, s: string | null, c: string | null): FilamentAsk => ({
  filament_manufacturer: m, filament_type: t, filament_subtype: s, filament_color: c,
});
// Spec example slots: T0 Acme PLA standard red, T1 Acme PLA silk red, T2 (unknown maker) PETG standard green.
const SLOTS = [
  { type: 'PLA', subtype: 'standard', color: '#FF0000', manufacturer: 'Acme' },
  { type: 'PLA', subtype: 'silk', color: '#FF0000', manufacturer: 'Acme' },
  { type: 'PETG', subtype: 'standard', color: '#00FF00', manufacturer: null },
];

describe('firstMatchingSlot (the spec example table)', () => {
  it.each([
    [ask(null, null, null, null), 0],
    [ask(null, 'PLA', ANY, '#ff0000'), 0],
    [ask(null, 'PLA', 'silk', '#FF0000'), 1],
    [ask(null, 'PLA', STANDARD, '#FF0000'), 0],
    [ask('acme', 'pla', 'standard', 'FF0000'), 0],
    [ask('Bambu Lab', 'PLA', ANY, '#FF0000'), null],
    [ask('Acme', 'PETG', STANDARD, '#00FF00'), null],     // the slot's manufacturer is unknown
    [ask(null, 'PETG', STANDARD, '#00FF00'), 2],
    [ask(null, 'PLA', ANY, '#FF0001'), null],             // colour is exact
  ])('%j -> %s', (a, expected) => {
    expect(firstMatchingSlot(a, SLOTS)).toBe(expected);
  });

  it('skips a conflicting slot and an empty tool placeholder', () => {
    expect(firstMatchingSlot(ask(null, 'PLA', ANY, null), SLOTS, [0])).toBe(1);
    expect(firstMatchingSlot(NO_ASK, [{ type: '', color: '', empty: true }, ...SLOTS])).toBe(1);
    expect(firstMatchingSlot(NO_ASK, [{ type: '', color: '', empty: true }])).toBeNull();
  });

  it('reads a slot that was never migrated through the suffix rules', () => {
    expect(firstMatchingSlot(ask(null, 'PLA', 'plus', null), [{ type: 'PLA+', color: '#fff' }])).toBe(0);
    expect(firstMatchingSlot(ask(null, 'PLA', STANDARD, null), [{ type: 'PLA+', color: '#fff' }])).toBeNull();
  });
});

describe('helpers', () => {
  it('splits a combined type with the same rules as the server', () => {
    expect(splitType('PLA+')).toEqual({ type: 'PLA', subtype: 'plus' });
    expect(splitType('PLA-CF')).toEqual({ type: 'PLA', subtype: 'cf' });
    expect(splitType('PLA Silk')).toEqual({ type: 'PLA', subtype: 'silk' });
    expect(splitType('PC-ABS')).toEqual({ type: 'PC-ABS', subtype: 'standard' });       // a blend of two base materials is one type
    expect(splitType('PETG-CF')).toEqual({ type: 'PETG', subtype: 'cf' });
    expect(splitType('PETG')).toEqual({ type: 'PETG', subtype: STANDARD });
    expect(splitType('')).toEqual({ type: null, subtype: null });
  });

  it('turns an inventory material into the four parts (plain = standard, colour with #)', () => {
    expect(askFromMaterial({ vendor: 'Acme', material: 'PLA', subtype: 'silk', color_hex: 'ff0000' })).toEqual(ask('Acme', 'PLA', 'silk', '#ff0000'));
    expect(askFromMaterial({ vendor: null, material: 'PETG', subtype: null, color_hex: null })).toEqual(ask(null, 'PETG', STANDARD, null));
    expect(askFromMaterial({ vendor: 'X', material: null, subtype: null, color_hex: '#000' })).toEqual(ask('X', null, null, '#000'));
  });

  it('a type always gets a subtype; blanks are undefined', () => {
    expect(normalizeAsk(ask(' ', 'PLA', null, ''))).toEqual(ask(null, 'PLA', ANY, null));
    expect(askIsUndefined(normalizeAsk(ask('', '', '', '')))).toBe(true);
  });

  it('describes an ask for people', () => {
    expect(describeAsk(ask('Acme', 'PLA', 'silk', '#ff0000'))).toBe('Acme PLA silk #FF0000');
    expect(describeAsk(NO_ASK)).toBe('any filament');
  });

  it('a hand-entered typeless slot is loaded, an empty placeholder is not', () => {
    expect(slotIsLoaded({ type: '', color: '' })).toBe(true);
    expect(slotIsLoaded({ type: '', color: '', empty: true })).toBe(false);
  });
});
