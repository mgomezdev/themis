import { useState, useEffect } from 'react';
import type { LoadedFilament } from './printers';
import type { Printer } from '../data/types';

export interface FleetPrinter {
  id: number;
  name: string;
  printer_type: string;
  enabled: boolean;
  connected: boolean;
  awaiting_plate_clear: boolean;
  loaded_filaments: LoadedFilament[];
  state: string;
  progress: number;
  remaining_time: number;
  layer_num: number | null;
  total_layers: number | null;
  temperatures: { nozzle?: number; bed?: number; chamber?: number; bed_target?: number };
  capabilities: Record<string, boolean>;
  current_print: string | null;
  fan_model: number;
  fan_aux: number;
  fan_box: number;
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
  if (p.awaiting_plate_clear) return 'claiming';
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
    bedTemp: p.temperatures?.bed ?? 0,
    chamberTemp: p.temperatures?.chamber ?? null,
    material: mat
      ? { name: mat.name || '—', type: mat.type || '—', color: mat.color || '#475472' }
      : { name: '—', type: '—', color: '#475472' },
    currentJobId: p.current_print ?? null,
    accent: ACCENT[p.printer_type] ?? '#888888',
    fanModel: p.fan_model ?? 0,
    fanAux: p.fan_aux ?? 0,
    fanBox: p.fan_box ?? 0,
    bedTempTarget: p.temperatures?.bed_target ?? 0,
  };
}

async function fetchFleetPrinters(): Promise<FleetPrinter[]> {
  const resp = await fetch('/api/v1/fleet');
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export function useFleetData(): Printer[] {
  const [raw, setRaw] = useState<FleetPrinter[]>([]);

  useEffect(() => {
    let alive = true;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${window.location.host}/ws`);

    fetchFleetPrinters()
      .then(data => {
        if (!alive) return;
        setRaw(data);
        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data) as { type: string; data: FleetPrinter };
            if (msg.type === 'printer_state' && typeof msg.data?.id === 'number') {
              setRaw(prev =>
                prev.map(p => (p.id === msg.data.id ? { ...p, ...msg.data } : p)),
              );
            }
          } catch {
            // ignore malformed frames
          }
        };
      })
      .catch(console.error);

    return () => {
      alive = false;
      ws.close();
    };
  }, []);

  return raw.map(toFleetPrinter);
}
