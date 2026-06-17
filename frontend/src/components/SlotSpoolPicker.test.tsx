import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SlotSpoolPicker } from './SlotSpoolPicker';
import type { LoadedFilament } from '../api/printers';
import type { ApiSpool, ApiFilament } from '../api/spoolman';

const baseSlot: LoadedFilament = {
  slot: 0, filament_id: null, name: 'Slot 1',
  type: '', color: '', filament_profile: null, spoolman_spool_id: null,
};

const spool2: ApiSpool = {
  id: 2, remaining_weight: 324, used_weight: 76,
  filament: { id: 10, vendor: { name: 'ELEGOO' }, name: 'Sky Blue PLA', material: 'PLA', color_hex: '87CEEB' },
};

const spool5: ApiSpool = {
  id: 5, remaining_weight: 980, used_weight: 20,
  filament: { id: 11, vendor: { name: 'Bambu' }, name: 'Basic Black PETG', material: 'PETG', color_hex: '111111' },
};

const filament10: ApiFilament = {
  id: 10, name: 'Sky Blue PLA', vendor: { id: 1, name: 'ELEGOO' }, material: 'PLA', color_hex: '87CEEB',
  extra: { orca_profiles: JSON.stringify(JSON.stringify({ 'Bambu X1': ['ELEGOO PLA @BBL X1C'] })) },
};

const noSpools: ApiSpool[] = [];
const spools = [spool2, spool5];
const filaments = [filament10];
const filamentProfiles = ['Generic PLA', 'Bambu PLA Basic @BBL X1C', 'ELEGOO PLA @BBL X1C'];

describe('SlotSpoolPicker', () => {
  it('shows Custom fields when no spools provided (Spoolman off)', () => {
    const onChange = vi.fn();
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={noSpools}
        filaments={[]} filamentProfiles={filamentProfiles} onChange={onChange} />
    );
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
    expect(screen.getByPlaceholderText('Color (#hex)')).toBeTruthy();
    expect(screen.queryByPlaceholderText('Search spools…')).toBeNull();
  });

  it('shows spool combobox when spools are available', () => {
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByPlaceholderText('Search spools…')).toBeTruthy();
  });

  it('filters spools by name/vendor/material as user types', async () => {
    const user = userEvent.setup();
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    await user.click(screen.getByPlaceholderText('Search spools…'));
    await user.type(screen.getByPlaceholderText('Search spools…'), 'ELEGOO');
    expect(screen.getByText('#2 ELEGOO Sky Blue PLA PLA')).toBeTruthy();
    expect(screen.queryByText('#5 Bambu Basic Black PETG PETG')).toBeNull();
  });

  it('calls onChange with spool fields when a spool is selected', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={onChange} />
    );
    await user.click(screen.getByPlaceholderText('Search spools…'));
    fireEvent.mouseDown(screen.getByText('#2 ELEGOO Sky Blue PLA PLA'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      spoolman_spool_id: '2',
      type: 'PLA',
      color: '#87CEEB',
    }));
  });

  it('shows selected spool with remaining weight after picking', () => {
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '2', type: 'PLA', color: '#87CEEB' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByText(/324g remaining/)).toBeTruthy();
    expect(screen.getByText(/#2 ELEGOO Sky Blue PLA PLA/)).toBeTruthy();
  });

  it('calls onChange with spoolman_spool_id: null when cleared', async () => {
    const onChange = vi.fn();
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '2', type: 'PLA', color: '#87CEEB' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={onChange} />
    );
    await userEvent.setup().click(screen.getByLabelText('Clear spool selection'));
    expect(onChange).toHaveBeenCalledWith({ spoolman_spool_id: null, filament_profile: null });
  });

  it('shows warning badge and Custom fields in degraded mode (spool not in list)', () => {
    const slotWithMissingSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '99', type: 'PLA', color: '#ff0000' };
    render(
      <SlotSpoolPicker slot={slotWithMissingSpool} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByText(/Spool #99 not found in Spoolman/)).toBeTruthy();
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
  });

  it('shows resolved orca profiles dropdown when printerPreset matches', () => {
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '2', type: 'PLA', color: '#87CEEB' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset="Bambu X1" spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByRole('option', { name: 'ELEGOO PLA @BBL X1C' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'Generic PLA' })).toBeNull();
  });

  it('shows Custom fields and combobox when spools are available but no spool selected', () => {
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    // Combobox present (Spoolman enabled)
    expect(screen.getByPlaceholderText('Search spools…')).toBeTruthy();
    // Custom fields also present (no spool selected yet)
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
    expect(screen.getByPlaceholderText('Color (#hex)')).toBeTruthy();
  });

  it('falls back to full filamentProfiles when no orca_profiles match', () => {
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '5', type: 'PETG', color: '#111111' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset="Bambu X1" spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByText(/No mapped profiles/)).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Generic PLA' })).toBeTruthy();
  });
});
