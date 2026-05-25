export function fmtTime(mins: number | null | undefined): string {
  if (mins == null) return '—';
  if (mins < 1) return '<1m';
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

export function fmtClock(mins: number | null | undefined): string {
  if (mins == null) return '--:--';
  const h = Math.floor(mins / 60);
  const m = Math.floor(mins % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export function matColor(material: string): string {
  const m = material.toLowerCase();
  if (m.includes('pa-cf') || m.includes('pacf')) return '#94a3b8';
  if (m.includes('petg')) return '#67e8f9';
  if (m.includes('abs')) return '#a78bfa';
  if (m.includes('asa')) return '#f87171';
  if (m.includes('tpu')) return '#f87171';
  if (m.includes('pc') && !m.includes('pacf')) return '#fbbf24';
  if (m.includes('pla')) return '#60a5fa';
  return '#475472';
}

export function matTypeBg(t: string): string {
  switch (t) {
    case 'PLA': return 'rgba(96,165,250,0.12)';
    case 'PETG': return 'rgba(103,232,249,0.12)';
    case 'PA-CF': return 'rgba(148,163,184,0.14)';
    case 'ABS': return 'rgba(167,139,250,0.14)';
    case 'ASA': return 'rgba(244,114,182,0.12)';
    case 'PC': return 'rgba(251,191,36,0.10)';
    case 'TPU': return 'rgba(248,113,113,0.14)';
    default: return 'rgba(148,163,184,0.10)';
  }
}

export function matTypeFg(t: string): string {
  switch (t) {
    case 'PLA': return '#93c5fd';
    case 'PETG': return '#7dd3fc';
    case 'PA-CF': return '#cbd5e1';
    case 'ABS': return '#c4b5fd';
    case 'ASA': return '#f9a8d4';
    case 'PC': return '#fcd34d';
    case 'TPU': return '#fca5a5';
    default: return 'var(--text-2)';
  }
}

export function matTypeBorder(t: string): string {
  switch (t) {
    case 'PLA': return 'rgba(96,165,250,0.30)';
    case 'PETG': return 'rgba(103,232,249,0.30)';
    case 'PA-CF': return 'rgba(148,163,184,0.30)';
    case 'ABS': return 'rgba(167,139,250,0.30)';
    case 'ASA': return 'rgba(244,114,182,0.30)';
    case 'PC': return 'rgba(251,191,36,0.25)';
    case 'TPU': return 'rgba(248,113,113,0.30)';
    default: return 'var(--border-1)';
  }
}

export function shade(hex: string, amt: number): string {
  const n = parseInt(hex.replace('#', ''), 16);
  const r = Math.max(0, Math.min(255, (n >> 16) + amt));
  const g = Math.max(0, Math.min(255, ((n >> 8) & 0xff) + amt));
  const b = Math.max(0, Math.min(255, (n & 0xff) + amt));
  return '#' + ((r << 16) | (g << 8) | b).toString(16).padStart(6, '0');
}

export function darken(hex: string): string {
  return shade(hex, -30);
}
