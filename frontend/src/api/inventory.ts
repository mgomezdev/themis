import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch } from './client';
import { useActivePlugin, type PluginSummary } from './plugins';
import { askBinding, slotBinding, type LoadedFilament } from './printers';

export const INVENTORY_KIND = 'filament_inventory';

/** Capability names an inventory provider can declare (see docs/provider-interfaces.md). */
export const CAP = {
  TRACKS_WEIGHT: 'TRACKS_WEIGHT', WRITE_WEIGHT: 'WRITE_WEIGHT',
  PROFILE_LINKS_READ: 'PROFILE_LINKS_READ', PROFILE_LINKS_WRITE: 'PROFILE_LINKS_WRITE',
  LABEL_SCAN: 'LABEL_SCAN', REMOTE: 'REMOTE', MANAGE_MATERIALS: 'MANAGE_MATERIALS', MANAGE_SPOOLS: 'MANAGE_SPOOLS',
} as const;

export interface InvMaterial {
  ref: string;
  name: string;
  material: string | null;
  color_hex: string | null;          // "#RRGGBB"
  vendor: string | null;
  density: number | null;
  diameter: number | null;
  /** `{orca printer preset: [orca filament presets]}`; null = the provider has none / cannot read them. */
  profile_links: Record<string, string[]> | null;
  archived: boolean;
}

export interface InvSpool {
  ref: string;
  material_ref: string | null;
  material: InvMaterial | null;
  /** Effective remaining: a queued (not yet synced) deduction already applied. */
  remaining_g: number | null;
  initial_g: number | null;
  location: string | null;
  label: string;
  archived: boolean;
  /** `remaining_g` is a queued value the provider has not received yet. */
  unsynced: boolean;
  /** Deep link into the provider's own UI, when it has one. */
  url: string | null;
}

/** A list read: served from the last-known cache (`stale`, as of `as_of`) while the provider is unreachable. */
export interface InventoryList<T> { provider: string; stale: boolean; as_of: string; items: T[] }

export interface SyncStatus {
  provider: string | null;
  capabilities: string[];
  enabled: boolean;
  interval_minutes: number;
  last_sync_at: string | null;
  last_attempt_at: string | null;
  last_error: string | null;
  last_error_code: string | null;
  disconnected_since: string | null;
  max_disconnect_minutes: number | null;
  disconnect_alerted: boolean;
  pending_count: number;
  cache_as_of: string | null;
}

export interface InventorySettings {
  provider: string | null;
  deduct_on_complete: boolean;
  low_stock: LowStockConfig;
}

export interface LowStockConfig {
  /** Grams below which a spool raises a `spool.low` event; null = no default threshold. */
  default_g: number | null;
  /** Per-material thresholds keyed by material ref; win over the default. */
  overrides: Record<string, number>;
}

export interface PendingWrite {
  id: number; provider: string; spool_ref: string; target_g: number; job_id: number | null; printer_id: number | null;
  source: 'queue' | 'manual_complete'; created_at: string; attempts: number; last_attempt_at: string | null;
  last_error: string | null; status: 'pending' | 'applied' | 'superseded' | 'discarded';
}

export interface SuspendedSpool { spool_ref: string; reason: string; since: string; job_id: number | null }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const raw = await resp.text().catch(() => '');
    let body: unknown = raw;
    try { body = JSON.parse(raw); } catch { /* plain text */ }
    const obj = (body && typeof body === 'object' ? body : {}) as { detail?: unknown; error?: unknown };
    const detail = typeof obj.detail === 'string' ? obj.detail : typeof obj.error === 'string' ? obj.error : null;
    throw new Error(detail ?? (typeof body === 'string' && body ? `${resp.status} ${body}` : `${resp.status}`));
  }
  return resp.json();
}

const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

const BASE = '/api/v1/inventory';

export const fetchMaterialList = (): Promise<InventoryList<InvMaterial>> => request(`${BASE}/materials`);
export const fetchSpoolList = (): Promise<InventoryList<InvSpool>> => request(`${BASE}/spools`);
export const fetchMaterials = async (): Promise<InvMaterial[]> => (await fetchMaterialList()).items;
export const fetchSpools = async (): Promise<InvSpool[]> => (await fetchSpoolList()).items;

export const getSyncStatus = (): Promise<SyncStatus> => request(`${BASE}/sync-status`);
export const syncNow = (): Promise<{ material_count: number; spool_count: number }> => request(`${BASE}/sync-now`, json('POST'));

export const getInventorySettings = (): Promise<InventorySettings> => request(`${BASE}/settings`);
export const saveInventorySettings = (patch: { deduct_on_complete?: boolean; low_stock?: LowStockConfig }): Promise<InventorySettings> =>
  request(`${BASE}/settings`, json('PUT', patch));

/** The spool ref a scanned/typed label code names (null = not a code of the active provider). */
export async function resolveLabel(text: string): Promise<string | null> {
  return (await request<{ spool_ref: string | null }>(`${BASE}/resolve-label`, json('POST', { text }))).spool_ref;
}

export const setProfileLinks = (materialRef: string, links: Record<string, string[]>): Promise<InvMaterial> =>
  request(`${BASE}/materials/${encodeURIComponent(materialRef)}/profile-links`, json('PATCH', { links }));

export const listPendingWrites = (): Promise<{ provider: string | null; items: PendingWrite[] }> => request(`${BASE}/pending-writes`);
export const flushPendingWrites = (): Promise<{ applied: number }> => request(`${BASE}/pending-writes/flush`, json('POST'));
export const discardPendingWrite = (id: number): Promise<PendingWrite> => request(`${BASE}/pending-writes/${id}/discard`, json('POST'));
export const resolvePendingWrite = (id: number, targetG?: number): Promise<PendingWrite> =>
  request(`${BASE}/pending-writes/${id}/resolve`, json('POST', targetG === undefined ? {} : { target_g: targetG }));
export const listSuspended = (): Promise<{ provider: string | null; items: SuspendedSpool[] }> => request(`${BASE}/tracking`);
export const resumeTracking = (spoolRef: string, remainingG?: number): Promise<{ tracking: string }> =>
  request(`${BASE}/spools/${encodeURIComponent(spoolRef)}/resume-tracking`, json('POST', remainingG === undefined ? {} : { remaining_g: remainingG }));

// ---- naming / presentation (neutral: nothing here knows which provider is active) ----

export function materialDisplayName(m: Pick<InvMaterial, 'name' | 'vendor'>): string {
  return m.vendor ? `${m.vendor} ${m.name}` : m.name;
}

export function spoolDisplayName(s: InvSpool): string {
  return s.material ? materialDisplayName(s.material) : s.label || `Spool ${s.ref}`;
}

export function spoolColor(s: InvSpool): string {
  return s.material?.color_hex || '#94a3b8';
}

/** `{preset: [profiles]}` of a material (empty when it has none). */
export function profileLinks(m: Pick<InvMaterial, 'profile_links'> | null | undefined): Record<string, string[]> {
  return m?.profile_links ?? {};
}

/** The slot fields to set when `spool` is loaded into a printer slot (profile resolved for the printer's preset). */
export function slotPatchForSpool(
  spool: InvSpool, provider: string, printerPreset: string | null, current?: Pick<LoadedFilament, 'color' | 'filament_profile'>,
): Partial<LoadedFilament> {
  const profiles = printerPreset ? profileLinks(spool.material)[printerPreset] ?? null : null;
  return {
    inventory: { provider, spool_ref: spool.ref },
    type: spool.material?.material ?? '',
    color: spool.material?.color_hex || current?.color || '',
    filament_profile: profiles?.length === 1 ? profiles[0] : (current?.filament_profile ?? null),
    name: spoolDisplayName(spool),
  };
}

/** What a slot is bound to, only when that is the active provider's spool (a binding to another provider means nothing here). */
export function activeSlotRef(slot: Parameters<typeof slotBinding>[0], provider: string | null): string | null {
  const b = slotBinding(slot);
  return b && (provider === null || b.provider === provider) ? b.ref : null;
}

/** The material ask a picker produces (the provider-namespaced pair; the legacy numeric id is the server's concern). */
export function materialAsk(m: Pick<InvMaterial, 'ref'> | null, provider: string | null): { filament_id: null; material_provider: string | null; material_ref: string | null } {
  return { filament_id: null, material_provider: m && provider ? provider : null, material_ref: m ? m.ref : null };
}

/** The material ref a stored ask names, only when it belongs to the active provider (a ref means nothing to another one). */
export function askRef(ask: Parameters<typeof askBinding>[0], provider: string | null): string | null {
  const b = askBinding(ask);
  return b && (provider === null || b.provider === provider) ? b.ref : null;
}

export type SyncTone = 'success' | 'stale' | 'fail' | 'disconnected';

/** disconnected (red): the provider has been unreachable (an outage is open). fail (red): the last attempt errored.
 *  stale (orange): no error but the last good sync is over two intervals old (or never). success (green) otherwise. */
export function syncTone(s: SyncStatus): SyncTone {
  if (s.disconnected_since) return 'disconnected';
  if (s.last_error) return 'fail';
  if (!s.last_sync_at) return 'stale';
  return Date.now() - new Date(s.last_sync_at).getTime() > 2 * s.interval_minutes * 60_000 ? 'stale' : 'success';
}

// ---- hooks ----

export interface InventoryProvider {
  /** The plugin in use (selected and enabled), or null: nothing inventory-related should be offered. */
  plugin: PluginSummary | null;
  id: string | null;
  has: (capability: string) => boolean;
}

export function useInventory(): InventoryProvider {
  const plugin = useActivePlugin(INVENTORY_KIND);
  const has = useCallback((c: string) => !!plugin && plugin.capabilities.includes(c), [plugin]);
  return useMemo(() => ({ plugin, id: plugin?.id ?? null, has }), [plugin, has]);
}

function useList<T>(enabled: boolean, load: () => Promise<InventoryList<T>>, key: string | null) {
  const [reading, setReading] = useState<{ items: T[]; stale: boolean; as_of: string | null }>({ items: [], stale: false, as_of: null });
  useEffect(() => {
    if (!enabled) { setReading({ items: [], stale: false, as_of: null }); return; }
    let alive = true;
    load()
      .then(r => { if (alive) setReading({ items: Array.isArray(r.items) ? r.items : [], stale: !!r.stale, as_of: r.as_of ?? null }); })
      .catch(() => { if (alive) setReading({ items: [], stale: false, as_of: null }); });
    return () => { alive = false; };
    // `key` (the active provider) re-runs the load when the provider is switched
  }, [enabled, key]); // eslint-disable-line react-hooks/exhaustive-deps
  return reading;
}

export function useMaterialReading(enabled: boolean) {
  const { id } = useInventory();
  return useList<InvMaterial>(enabled, fetchMaterialList, id);
}
export function useSpoolReading(enabled: boolean) {
  const { id } = useInventory();
  return useList<InvSpool>(enabled, fetchSpoolList, id);
}
export const useMaterials = (enabled: boolean): InvMaterial[] => useMaterialReading(enabled).items;
export const useSpools = (enabled: boolean): InvSpool[] => useSpoolReading(enabled).items;

const SYNC_STATUS_POLL_MS = 30000;

export function useSyncStatus(enabled = true): { status: SyncStatus | null; refetch: () => void } {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    if (!enabled) { setStatus(null); return; }
    let alive = true;
    const poll = () => { getSyncStatus().then(s => { if (alive) setStatus(s); }).catch(() => { /* keep the last-known status */ }); };
    poll();
    const id = setInterval(poll, SYNC_STATUS_POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [tick, enabled]);
  return { status, refetch };
}
