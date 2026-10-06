import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SlotSpoolPicker } from './SlotSpoolPicker';
import type { LoadedFilament } from '../api/printers';
import { mkSpool } from '../test/inventoryFixtures';

const baseSlot: LoadedFilament = {
  slot: 0, filament_id: null, name: 'Slot 1', type: '', color: '', filament_profile: null, inventory: null,
};
const bound = (ref: string, provider = 'p1'): LoadedFilament => ({ ...baseSlot, inventory: { provider, spool_ref: ref }, type: 'PLA' });

const spool2 = mkSpool('2', {
  remaining_g: 324,
  material: { vendor: 'ELEGOO', name: 'Sky Blue PLA', material: 'PLA', color_hex: '#87CEEB', profile_links: { 'Bambu X1': ['ELEGOO PLA @BBL X1C'] } },
});
const spool5 = mkSpool('5', { remaining_g: 980, material: { vendor: 'Bambu', name: 'Basic Black PETG', material: 'PETG', color_hex: '#111111' } });

const spools = [spool2, spool5];
const filamentProfiles = ['Generic PLA', 'Bambu PLA Basic @BBL X1C', 'ELEGOO PLA @BBL X1C'];
const picker = (slot: LoadedFilament, over: Partial<React.ComponentProps<typeof SlotSpoolPicker>> = {}) => (
  <SlotSpoolPicker slot={slot} printerPreset={null} provider="p1" spools={spools} filamentProfiles={filamentProfiles} onChange={vi.fn()} {...over} />
);

describe('SlotSpoolPicker', () => {
  it('shows Custom fields when no spools provided (no inventory provider)', () => {
    render(picker(baseSlot, { spools: [] }));
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
    expect(screen.getByPlaceholderText('Color (#hex)')).toBeTruthy();
    expect(screen.queryByPlaceholderText('Search spools…')).toBeNull();
  });

  it('shows spool combobox when spools are available', () => {
    render(picker(baseSlot));
    expect(screen.getByPlaceholderText('Search spools…')).toBeTruthy();
  });

  it('filters spools by name/vendor/material as user types', async () => {
    const user = userEvent.setup();
    render(picker(baseSlot));
    await user.click(screen.getByPlaceholderText('Search spools…'));
    await user.type(screen.getByPlaceholderText('Search spools…'), 'ELEGOO');
    expect(screen.getByText('#2 ELEGOO Sky Blue PLA PLA')).toBeTruthy();
    expect(screen.queryByText('#5 Bambu Basic Black PETG PETG')).toBeNull();
  });

  it('binds the picked spool as {provider, spool_ref} with its type and colour', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(picker(baseSlot, { onChange }));
    await user.click(screen.getByPlaceholderText('Search spools…'));
    fireEvent.mouseDown(screen.getByText('#2 ELEGOO Sky Blue PLA PLA'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      inventory: { provider: 'p1', spool_ref: '2' }, type: 'PLA', color: '#87CEEB',
    }));
  });

  it('shows the selected spool with its remaining weight', () => {
    render(picker(bound('2')));
    expect(screen.getByText(/324g left/)).toBeTruthy();
    expect(screen.getByText(/#2 ELEGOO Sky Blue PLA PLA/)).toBeTruthy();
  });

  it('marks a weight the provider has not received yet', () => {
    render(picker(bound('2'), { spools: [{ ...spool2, unsynced: true }, spool5] }));
    expect(screen.getByText(/324g left · not yet synced/)).toBeTruthy();
  });

  it('unbinds with inventory: null when cleared', async () => {
    const onChange = vi.fn();
    render(picker(bound('2'), { onChange }));
    await userEvent.setup().click(screen.getByLabelText('Clear spool selection'));
    expect(onChange).toHaveBeenCalledWith({ inventory: null, filament_profile: null });
  });

  it('shows a warning badge and Custom fields in degraded mode (spool not in list)', () => {
    render(picker(bound('99')));
    expect(screen.getByText(/Spool #99 not found in the inventory/)).toBeTruthy();
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
  });

  it("explains a spool that belongs to another inventory instead of matching its ref here", () => {
    render(picker(bound('2', 'other')));                      // ref "2" exists in this provider, but it is not THIS spool
    expect(screen.getByText(/belongs to another inventory \(other\)/)).toBeTruthy();
    expect(screen.queryByText(/324g left/)).toBeNull();
  });

  it('still reads a slot bound only through the legacy mirror key', () => {
    render(picker({ ...baseSlot, inventory: undefined, spoolman_spool_id: '2' }, { provider: 'spoolman' }));
    expect(screen.getByText(/324g left/)).toBeTruthy();
  });

  it('shows resolved orca profiles dropdown when printerPreset matches', () => {
    render(picker(bound('2'), { printerPreset: 'Bambu X1' }));
    expect(screen.getByRole('option', { name: 'ELEGOO PLA @BBL X1C' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'Generic PLA' })).toBeNull();
  });

  it('shows Custom fields and combobox when spools are available but no spool selected', () => {
    render(picker(baseSlot));
    expect(screen.getByPlaceholderText('Search spools…')).toBeTruthy();
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
    expect(screen.getByPlaceholderText('Color (#hex)')).toBeTruthy();
  });

  it('falls back to full filamentProfiles when no profile links match', () => {
    render(picker(bound('5'), { printerPreset: 'Bambu X1' }));
    expect(screen.getByText(/No mapped profiles/)).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Generic PLA' })).toBeTruthy();
  });

  it('shows where each spool is stored and what is left, in the list and once chosen', async () => {
    const located = [{ ...spool2, location: ' Shelf B · Bin 3 ' }, spool5];
    const { rerender } = render(picker(baseSlot, { spools: located }));
    await userEvent.click(screen.getByPlaceholderText('Search spools…'));
    expect(screen.getByText('Shelf B · Bin 3 · 324g left')).toBeTruthy();   // trimmed location + grams
    expect(screen.getByText('980g left')).toBeTruthy();                      // no location → grams only

    rerender(picker(bound('2'), { spools: located }));
    expect(screen.getByTestId('spool-location').textContent).toBe('Stored at Shelf B · Bin 3');
  });

  it('shows no location line for a spool without one', () => {
    render(picker(bound('5')));
    expect(screen.queryByTestId('spool-location')).toBeNull();
  });
});
