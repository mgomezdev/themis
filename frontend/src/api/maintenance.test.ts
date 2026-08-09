// frontend/src/api/maintenance.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  getMaintenanceItems, createMaintenanceItem, deleteMaintenanceItem,
  getMaintenanceTemplates, getMaintenanceStatus, completeMaintenanceItem,
  resolveVendorModelForProfile, resolveFleetVendorModels,
} from './maintenance';

beforeEach(() => vi.restoreAllMocks());

describe('maintenance api', () => {
  it('getMaintenanceItems fetches the list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{
        id: 1, name: 'Wash plate', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ id: 1, trigger_type: 'job_count', amount: 10, unit: null }],
      }]), { status: 200 })));
    const items = await getMaintenanceItems();
    expect(items[0].name).toBe('Wash plate');
    expect(items[0].triggers[0].trigger_type).toBe('job_count');
  });

  it('createMaintenanceItem posts JSON', async () => {
    const f = vi.fn(async () =>
      new Response(JSON.stringify({
        id: 2, name: 'Clean fans', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null, triggers: [],
      }), { status: 201 }));
    vi.stubGlobal('fetch', f);
    const item = await createMaintenanceItem({ name: 'Clean fans', scope: 'general', triggers: [] });
    expect(item.id).toBe(2);
    expect(f).toHaveBeenCalledWith('/api/v1/maintenance/items', expect.objectContaining({ method: 'POST' }));
  });

  it('deleteMaintenanceItem sends DELETE', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ deleted: 3 }), { status: 200 }));
    vi.stubGlobal('fetch', f);
    const result = await deleteMaintenanceItem(3);
    expect(result.deleted).toBe(3);
    expect(f).toHaveBeenCalledWith('/api/v1/maintenance/items/3', expect.objectContaining({ method: 'DELETE' }));
  });

  it('getMaintenanceTemplates fetches suggestions', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{ name: 'Wash build plate', description: 'x', triggers: [] }]), { status: 200 })));
    const templates = await getMaintenanceTemplates();
    expect(templates[0].name).toBe('Wash build plate');
  });

  it('getMaintenanceStatus fetches due rows', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{
        printer_id: 1, printer_name: 'P1', item_id: 1, item_name: 'Wash plate',
        due: true, last_done_at: '2026-01-01T00:00:00',
      }]), { status: 200 })));
    const rows = await getMaintenanceStatus();
    expect(rows[0].due).toBe(true);
  });

  it('completeMaintenanceItem posts to the complete endpoint', async () => {
    const f = vi.fn(async () =>
      new Response(JSON.stringify({ printer_id: 1, item_id: 2, last_done_at: '2026-01-01T00:00:00' }), { status: 200 }));
    vi.stubGlobal('fetch', f);
    const result = await completeMaintenanceItem(1, 2);
    expect(result.last_done_at).toBe('2026-01-01T00:00:00');
    expect(f).toHaveBeenCalledWith('/api/v1/maintenance/printers/1/items/2/complete', expect.objectContaining({ method: 'POST' }));
  });
});

describe('resolveVendorModelForProfile', () => {
  const catalog = [
    { name: 'Elegoo Centauri Carbon 0.4 nozzle', vendor: 'Elegoo', printer_model: 'Centauri Carbon', nozzle: '0.4', source: 'system' as const },
    { name: 'Bambu Lab X1 Carbon 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'X1 Carbon', nozzle: '0.4', source: 'system' as const },
  ];

  it('resolves a matching profile name to its vendor/model', () => {
    expect(resolveVendorModelForProfile('Elegoo Centauri Carbon 0.4 nozzle', catalog))
      .toEqual({ vendor: 'Elegoo', printer_model: 'Centauri Carbon' });
  });

  it('returns null for an unset or unmatched profile', () => {
    expect(resolveVendorModelForProfile(null, catalog)).toBeNull();
    expect(resolveVendorModelForProfile('Unknown Preset', catalog)).toBeNull();
  });
});

describe('resolveFleetVendorModels', () => {
  const catalog = [
    { name: 'Elegoo Centauri Carbon 0.4 nozzle', vendor: 'Elegoo', printer_model: 'Centauri Carbon', nozzle: '0.4', source: 'system' as const },
    { name: 'Bambu Lab X1 Carbon 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'X1 Carbon', nozzle: '0.4', source: 'system' as const },
  ];

  it('dedupes fleet printers to a sorted, unique vendor/model list', () => {
    const printers = [
      { current_orca_printer_profile: 'Elegoo Centauri Carbon 0.4 nozzle' },
      { current_orca_printer_profile: 'Elegoo Centauri Carbon 0.4 nozzle' }, // duplicate printer, same model
      { current_orca_printer_profile: 'Bambu Lab X1 Carbon 0.4 nozzle' },
      { current_orca_printer_profile: null }, // unset profile, ignored
      { current_orca_printer_profile: 'Totally Unknown Printer' }, // unresolvable, ignored
    ];
    expect(resolveFleetVendorModels(printers, catalog)).toEqual([
      { vendor: 'Bambu Lab', printer_model: 'X1 Carbon' },
      { vendor: 'Elegoo', printer_model: 'Centauri Carbon' },
    ]);
  });

  it('returns an empty list when the fleet has no resolvable profiles', () => {
    expect(resolveFleetVendorModels([{ current_orca_printer_profile: null }], catalog)).toEqual([]);
  });
});
