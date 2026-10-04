import { useState, useEffect, useCallback, useRef } from 'react';
import type { LoadedFilament } from './printers';
import type { Printer } from '../data/types';
import { apiFetch, openLiveSocket } from './client';

export interface FleetPrinter {
  id: number;
  name: string;
  printer_type: string;
  enabled: boolean;
  queue_on: boolean;
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
}

const ACCENT: Record<string, string> = {
  elegoo_centauri: '#22d3ee',
  bambu: '#3b82f6',
};

const BADGE: Record<string, string> = {
  elegoo_centauri: 'ECC',
  bambu: 'P1S',
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
    badge: BADGE[p.printer_type] ?? p.printer_type.slice(0, 3).toUpperCase(),
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
      ? { name: mat.name || '—', type: mat.type || '—', color: mat.color || '#475472' }
      : { name: '—', type: '—', color: '#475472' },
    materials: p.loaded_filaments.map(m => ({
      name: m.name || '—', type: m.type || '—', color: m.color || '#475472',
    })),
    currentJobId: p.current_print ?? null,
    accent: ACCENT[p.printer_type] ?? '#888888',
    fanModel: p.fan_model ?? 0,
    fanAux: p.fan_aux ?? 0,
    fanBox: p.fan_box ?? 0,
    bedTempTarget: p.temperatures?.bed_target ?? 0,
    queueOn: p.queue_on ?? true,
    awaitingPlateClear: p.awaiting_plate_clear ?? false,
    noSnapshotsWhileIdle: p.no_snapshots_while_idle ?? false,
    alarmCount: p.alarm_count ?? 0,
    alarmSeverity: p.alarm_severity ?? null,
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
