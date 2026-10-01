/** Helpers for a job's scheduled start (`not_before`, a UTC ISO instant). */

/** "in 2h 15m" / "in 40m" / "in 3d 4h" for a future instant; null when absent or already due. */
export function startsIn(notBefore: string | null | undefined, nowMs: number = Date.now()): string | null {
  if (!notBefore) return null;
  const ms = Date.parse(notBefore) - nowMs;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const mins = Math.max(1, Math.ceil(ms / 60000));
  const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
  if (d > 0) return `in ${d}d ${h}h`;
  if (h > 0) return `in ${h}h ${m}m`;
  return `in ${m}m`;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** UTC ISO → value for `<input type="datetime-local">` (browser-local wall time). */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** `<input type="datetime-local">` value (browser-local) → UTC ISO; '' → null. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
