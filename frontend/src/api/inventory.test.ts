import { describe, it, expect } from 'vitest';
import {
  activeSlotRef, askRef, materialAsk, materialDisplayName, slotPatchForSpool, spoolDisplayName, syncTone,
  type InvSpool, type SyncStatus,
} from './inventory';
import { slotBinding } from './printers';

const spool = (over: Partial<InvSpool> = {}): InvSpool => ({
  ref: '7', material_ref: '3', label: 'Acme PLA', remaining_g: 412, initial_g: 1000, location: null, archived: false, unsynced: false, url: null,
  material: { ref: '3', name: 'PLA Red', material: 'PLA', color_hex: '#FF0000', vendor: 'Acme', density: null, diameter: null,
              profile_links: { 'Bambu X1C': ['Bambu PLA'], 'Prusa MK4': ['A', 'B'] }, archived: false },
  ...over,
});

const status = (over: Partial<SyncStatus> = {}): SyncStatus => ({
  provider: 'p', capabilities: [], enabled: true, interval_minutes: 15, last_sync_at: new Date().toISOString(),
  last_attempt_at: null, last_error: null, last_error_code: null, disconnected_since: null, max_disconnect_minutes: null,
  disconnect_alerted: false, pending_count: 0, cache_as_of: null, ...over,
});

describe('slot bindings', () => {
  it('prefers the inventory pair, falls back to the legacy mirror, and honours an explicit unbind', () => {
    expect(slotBinding({ inventory: { provider: 'local', spool_ref: 'x1' }, spoolman_spool_id: '9' })).toEqual({ provider: 'local', ref: 'x1' });
    expect(slotBinding({ spoolman_spool_id: '9' })).toEqual({ provider: 'spoolman', ref: '9' });
    expect(slotBinding({ inventory: null, spoolman_spool_id: '9' })).toBeNull();      // unbound by the user, stale mirror echoed
    expect(slotBinding({})).toBeNull();
  });

  it("a binding to another provider means nothing to the active one", () => {
    const slot = { inventory: { provider: 'local', spool_ref: 'x1' } };
    expect(activeSlotRef(slot, 'local')).toBe('x1');
    expect(activeSlotRef(slot, 'spoolman')).toBeNull();
    expect(activeSlotRef({ spoolman_spool_id: '9' }, 'spoolman')).toBe('9');
  });
});

describe('slotPatchForSpool', () => {
  it('binds the spool with the active provider and copies type/colour/name', () => {
    const p = slotPatchForSpool(spool(), 'local', null);
    expect(p).toMatchObject({ inventory: { provider: 'local', spool_ref: '7' }, type: 'PLA', color: '#FF0000', name: 'Acme PLA Red' });
    expect(p).not.toHaveProperty('spoolman_spool_id');                                 // the server mirrors the legacy key
  });

  it('auto-selects the mapped profile only when the printer preset has exactly one', () => {
    expect(slotPatchForSpool(spool(), 'p', 'Bambu X1C').filament_profile).toBe('Bambu PLA');
    expect(slotPatchForSpool(spool(), 'p', 'Prusa MK4', { color: '', filament_profile: 'keep' }).filament_profile).toBe('keep');
    expect(slotPatchForSpool(spool(), 'p', 'Unknown').filament_profile).toBeNull();
  });
});

describe('naming and asks', () => {
  it('builds display names with and without a vendor / material', () => {
    expect(materialDisplayName({ name: 'PLA Red', vendor: 'Acme' })).toBe('Acme PLA Red');
    expect(materialDisplayName({ name: 'PLA Red', vendor: null })).toBe('PLA Red');
    expect(spoolDisplayName(spool({ material: null }))).toBe('Acme PLA');
    expect(spoolDisplayName(spool({ material: null, label: '' }))).toBe('Spool 7');
  });

  it('a material ask is the provider-namespaced pair with no legacy id, and null when cleared', () => {
    expect(materialAsk({ ref: '3' }, 'local')).toEqual({ filament_id: null, material_provider: 'local', material_ref: '3' });
    expect(materialAsk(null, 'local')).toEqual({ filament_id: null, material_provider: null, material_ref: null });
    expect(askRef({ material_ref: 'm-1', filament_id: 5 })).toBe('m-1');
    expect(askRef({ filament_id: 5 })).toBe('5');
    expect(askRef({})).toBeNull();
  });
});

describe('syncTone', () => {
  it('is red for an open outage or a failing sync, orange when overdue, green otherwise', () => {
    expect(syncTone(status())).toBe('success');
    expect(syncTone(status({ disconnected_since: '2026-01-01T00:00:00Z', last_error: 'x' }))).toBe('disconnected');
    expect(syncTone(status({ last_error: 'boom' }))).toBe('fail');
    expect(syncTone(status({ last_sync_at: null }))).toBe('stale');
    expect(syncTone(status({ last_sync_at: new Date(Date.now() - 3 * 15 * 60_000).toISOString() }))).toBe('stale');
  });
});
