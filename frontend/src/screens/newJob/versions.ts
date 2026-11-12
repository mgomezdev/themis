// New job: wording helpers for a cached sliced version.
import { type PerPrinterCfg } from '../../components/PerPrinterConfig';

/** What a saved slice stands for as an ask: its base type (any variant) and exact colour; "any"/blank = undefined. */
export function versionAsk(v: { filament_type: string; filament_color: string }): Partial<PerPrinterCfg> {
  const type = v.filament_type && v.filament_type.toLowerCase() !== 'any' ? v.filament_type : null;
  const color = v.filament_color && v.filament_color.toLowerCase() !== 'any' ? v.filament_color : null;
  return { filamentType: type, filamentSubtype: type ? 'any' : null, filamentColor: color };
}

export function fmtDuration(seconds: number | null): string | null {
  if (seconds == null) return null;
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
}
