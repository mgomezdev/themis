import type { InvMaterial, InvSpool, SyncStatus } from '../api/inventory';
import type { PluginSummary } from '../api/plugins';

export const mkMaterial = (over: Partial<InvMaterial> & { ref: string }): InvMaterial => ({
  name: 'PLA', material: 'PLA', color_hex: null, vendor: null, density: null, diameter: null, profile_links: null, archived: false, ...over,
});

export const mkSpool = (ref: string, over: Partial<Omit<InvSpool, 'material'>> & { material?: Partial<InvMaterial> | null } = {}): InvSpool => {
  const { material, ...rest } = over;
  const m = material === null ? null : mkMaterial({ ref: rest.material_ref ?? `m${ref}`, ...material });
  return {
    ref, material_ref: m?.ref ?? null, material: m, remaining_g: 500, initial_g: 1000, location: null,
    label: m ? `${m.vendor ?? ''} ${m.name}`.trim() : `Spool ${ref}`, archived: false, unsynced: false, url: null, ...rest,
  };
};

export const mkStatus = (over: Partial<SyncStatus> = {}): SyncStatus => ({
  provider: 'spoolman', capabilities: [], enabled: true, interval_minutes: 15, last_sync_at: new Date().toISOString(),
  last_attempt_at: null, last_error: null, last_error_code: null, disconnected_since: null, max_disconnect_minutes: null,
  disconnect_alerted: false, pending_count: 0, cache_as_of: null, ...over,
});

export const ALL_CAPS = ['TRACKS_WEIGHT', 'WRITE_WEIGHT', 'PROFILE_LINKS_READ', 'PROFILE_LINKS_WRITE', 'LABEL_SCAN', 'REMOTE'];

export const mkPlugin = (over: Partial<PluginSummary> & { id: string } = { id: 'spoolman' }): PluginSummary => ({
  name: over.id, kind: 'filament_inventory', version: '1', description: '', docs_url: null, source: 'bundled',
  capabilities: ALL_CAPS, enabled: true, active: true, error: null,
  ui: { mode: 'page', nav_label: over.id, nav_placement: 'settings', nav_icon: null, tabs: [{ id: 'connection', label: 'Connection', renderer: 'default' }] },
  ...over,
});

/** `GET /api/v1/plugins` body with `plugin` selected (or nothing selected when null). */
export const pluginsBody = (plugin: PluginSummary | null, others: PluginSummary[] = []) => ({
  plugins: [...(plugin ? [plugin] : []), ...others],
  slots: { filament_inventory: plugin?.active ? plugin.id : null },
});

/** Routes for a stubFetch test with an active, fully capable provider listing `spools` / `materials`. */
export function inventoryRoutes(opts: { plugin?: PluginSummary | null; spools?: InvSpool[]; materials?: InvMaterial[]; stale?: boolean } = {}) {
  const plugin = opts.plugin === undefined ? mkPlugin({ id: 'spoolman' }) : opts.plugin;
  const list = <T,>(items: T[]) => ({ provider: plugin?.id ?? '', stale: !!opts.stale, as_of: '2026-01-01T00:00:00Z', items });
  return {
    'GET /api/v1/plugins': pluginsBody(plugin),
    'GET /api/v1/inventory/spools': list(opts.spools ?? []),
    'GET /api/v1/inventory/materials': list(opts.materials ?? []),
    'GET /api/v1/inventory/sync-status': mkStatus({ provider: plugin?.id ?? null, capabilities: plugin?.capabilities ?? [] }),
  };
}

import type { InventoryProvider } from '../api/inventory';

/** `useInventory()` stand-ins for tests that mock the hook. */
export const activeInventory = (id = 'spoolman', caps: string[] = ALL_CAPS): InventoryProvider =>
  ({ plugin: mkPlugin({ id, capabilities: caps }), id, has: (c: string) => caps.includes(c) });
export const noInventory = (): InventoryProvider => ({ plugin: null, id: null, has: () => false });
