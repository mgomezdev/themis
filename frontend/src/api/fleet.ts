import { useState, useEffect, useCallback, useRef } from 'react';
import type { LoadedFilament } from './printers';
import type { Printer } from '../data/types';
import { apiFetch, openLiveSocket } from './client';
import { UNKNOWN_COLOR } from '../data/helpers';

export interface FleetPrinter {
  id: number;
  name: string;
  printer_type: string;
  plugin_id: string | null;
  dormant?: boolean;                    // its plugin is disabled or removed
  dormant_reason?: string | null;
  enabled: boolean;
  queue_on: boolean;
  configured?: boolean;                 // BIZ-369: a connection confirmed its setup (only configured printers take work)
  config_error?: string | null;         // why not: last test failure / missing field
  connected: boolean;
  awaiting_plate_clear: boolean;
  no_snapshots_while_idle: boolean;
  loaded_filaments: LoadedFilament[];
  state: string;
  progress: number;
  remaining_time: number;
  layer_num: number | null;
  total_layers: number | null;
  temperatures: {
    nozzle?: number; bed?: number; chamber?: number;
    nozzle_target?: number; bed_target?: number; chamber_target?: number;
    extruders?: { index: number; temp: number; target: number }[];
  };
  capabilities: Record<string, boolean>;
  current_print: string | null;
  fan_model: number;
  fan_aux: number;
  fan_box: number;
  alarm_count?: number;                 // active, unacknowledged
  alarm_severity?: 'info' | 'warning' | 'error' | 'fatal' | null;
  waived_parts?: number;                // BIZ-365: parts it no longer gets
  waiver_flag?: boolean;                // reached its flag threshold: wash the plate / do maintenance
  waiver_stopped?: boolean;             // reached its stop threshold: takes no queue work
}

// A printer's badge and accent are derived from its type key, never looked up per vendor: shared UI names no plugin
// (pluginIdGuard.test.ts), and a type added by a new plugin gets a stable colour and badge with no change here.
const ACCENTS = ['#22d3ee', '#3b82f6', '#a78bfa', '#34d399', '#fb923c', '#f472b6', '#facc15', '#94a3b8'];

/** Stable per-type accent: the same type key always maps to the same palette entry. */
export function typeAccent(printerType: string): string {
  let h = 0;
  for (const ch of printerType) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return ACCENTS[h % ACCENTS.length];
}

/** Short badge for a type key: initials of a multi-word key (`acme_core_one` → ACO), else its first three letters. */
export function typeBadge(printerType: string): string {
  const words = printerType.split(/[_\s-]+/).filter(Boolean);
  return (words.length > 1 ? words.map(w => w[0]).join('').slice(0, 4) : printerType.slice(0, 3)).toUpperCase();
}

const DORMANT_TEXT: Record<string, string> = {
  plugin_disabled: 'Its plugin is disabled',
  plugin_removed: 'Its plugin is not installed',
};

function mapStatus(p: FleetPrinter): Printer['status'] {
  if (!p.connected) return 'offline';
  // awaiting_plate_clear is surfaced as its own field/cue, not a status — it can be
  // true while actively printing (set on print start), so it must not mask the state.
  switch (p.state) {
    case 'RUNNING': return 'printing';
    case 'PAUSE': return 'paused';
    case 'FAILED': return 'error';
    default: return 'idle';
  }
}

export function toFleetPrinter(p: FleetPrinter): Printer {
  const mat = p.loaded_filaments[0];
  return {
    id: String(p.id),
    name: p.name,
    nickname: p.name,
    model: p.printer_type,
    pluginId: p.plugin_id ?? undefined,
    dormantReason: p.dormant ? (DORMANT_TEXT[p.dormant_reason ?? ''] ?? 'Plugin unavailable') : undefined,
    badge: typeBadge(p.printer_type),
    buildVolume: '',
    capabilities: Object.entries(p.capabilities ?? {})
      .filter(([, v]) => v)
      .map(([k]) => k.replace(/_/g, ' ')),
    chamber: false,
    status: mapStatus(p),
    progress: Math.round(p.progress ?? 0),
    timeRemaining: p.remaining_time ?? 0,
    timeElapsed: 0,
    layer:
      p.layer_num != null && p.total_layers != null
        ? { now: p.layer_num, total: p.total_layers }
        : null,
    nozzleTemp: p.temperatures?.nozzle ?? 0,
    nozzleTempTarget: p.temperatures?.nozzle_target ?? 0,
    nozzles: p.temperatures?.extruders,
    bedTemp: p.temperatures?.bed ?? 0,
    chamberTemp: p.temperatures?.chamber ?? null,
    material: mat
      ? { name: mat.name || '—', type: mat.type || '—', color: mat.color || UNKNOWN_COLOR }
      : { name: '—', type: '—', color: UNKNOWN_COLOR },
    materials: p.loaded_filaments.map(m => ({
      name: m.name || '—', type: m.type || '—', color: m.color || UNKNOWN_COLOR,
    })),
    currentJobId: p.current_print ?? null,
    accent: typeAccent(p.printer_type),
    fanModel: p.fan_model ?? 0,
    fanAux: p.fan_aux ?? 0,
    fanBox: p.fan_box ?? 0,
    bedTempTarget: p.temperatures?.bed_target ?? 0,
    queueOn: p.queue_on ?? true,
    configured: p.configured ?? true,
    configError: p.config_error ?? null,
    awaitingPlateClear: p.awaiting_plate_clear ?? false,
    noSnapshotsWhileIdle: p.no_snapshots_while_idle ?? false,
    alarmCount: p.alarm_count ?? 0,
    alarmSeverity: p.alarm_severity ?? null,
    waivedParts: p.waived_parts ?? 0,
    waiverFlag: p.waiver_flag ?? false,
    waiverStopped: p.waiver_stopped ?? false,
  };
}

async function fetchFleetPrinters(): Promise<FleetPrinter[]> {
  const resp = await apiFetch('/api/v1/fleet');
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

/** Live fleet in the API's own shape (raw temperatures + capability flags), kept fresh over the WebSocket. */
export function useFleetRaw(): [FleetPrinter[], () => void] {
  const [raw, setRaw] = useState<FleetPrinter[]>([]);
  const [fetchTick, setFetchTick] = useState(0);

  const refetch = useCallback(() => setFetchTick(t => t + 1), []);
  const knownIds = useRef(new Set<number>());
  knownIds.current = new Set(raw.map(p => p.id));

  useEffect(() => {
    let alive = true;
    fetchFleetPrinters()
      .then(data => { if (alive) setRaw(data); })
      .catch(console.error);
    return () => { alive = false; };
  }, [fetchTick]);

  useEffect(() => {
    const requested = new Set<number>();   // unknown ids already refetched, so a chatty new printer can't storm /fleet
    // After a reconnect, refetch: printer states pushed while the socket was down are gone.
    return openLiveSocket((e) => {
      try {
        const msg = JSON.parse(e.data) as { type: string; data: FleetPrinter };
        if (msg.type === 'printer_configured') {
          // a connection test finished or the first live connection confirmed the setup (BIZ-369)
          const d = msg.data as unknown as { printer_id: number; configured: boolean; config_error: string | null };
          setRaw(prev => prev.map(p => (p.id === d.printer_id ? { ...p, configured: d.configured, config_error: d.config_error } : p)));
          return;
        }
        if (msg.type === 'printer_state' && typeof msg.data?.id === 'number') {
          // A live frame is only vendor telemetry (no name, loaded_filaments, enabled…), so an unknown
          // printer can't be built from it: fetch the full row once instead of rendering the fragment.
          if (!knownIds.current.has(msg.data.id)) {
            if (!requested.has(msg.data.id)) {
              requested.add(msg.data.id);
              refetch();
            }
            return;
          }
          setRaw(prev => prev.map(p => (p.id === msg.data.id ? { ...p, ...msg.data } : p)));
        }
      } catch {
        // ignore malformed frames
      }
    }, refetch);
  }, [refetch]);

  return [raw, refetch];
}

export function useFleetData(): [Printer[], () => void] {
  const [raw, refetch] = useFleetRaw();
  return [raw.map(toFleetPrinter), refetch];
}
