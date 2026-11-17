// Files: fallback colours for a plate with no filament data, and the one-line summary of a sliced version.

// fallback gradient color for files without a thumbnail, derived from the id
const FALLBACK_COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#14b8a6', '#ec4899'];

export const fallbackColor = (id: number) => FALLBACK_COLORS[id % FALLBACK_COLORS.length];

export function versionSummary(v: { machine_preset: string; filament_type: string; filament_color: string }): string {
  const filament = [v.filament_type, v.filament_color].filter(x => x && x !== 'any').join(' ');
  return [v.machine_preset, filament].filter(Boolean).join(' · ');
}
