/** Rolling temperature history for the console chart (kept in memory; fed by the live telemetry). */
export interface TempSample {
  t: number;                 // epoch ms
  nozzle: number | null;
  bed: number | null;
  chamber: number | null;
}

export const HISTORY_WINDOW_MS = 30 * 60_000;
const MIN_GAP_MS = 5_000;

/** Add a sample, dropping anything older than the window and ignoring samples closer than 5 s to the last. */
export function appendSample(
  history: TempSample[], sample: TempSample, windowMs: number = HISTORY_WINDOW_MS,
): TempSample[] {
  const last = history[history.length - 1];
  if (last && sample.t - last.t < MIN_GAP_MS) return history;
  const cutoff = sample.t - windowMs;
  return [...history.filter(s => s.t >= cutoff), sample];
}
