import type { ConnectionField, DiscoveredPrinter, PrinterType } from '../api/printers';

/** How a scan row follows its new printer's connection test (it runs in the background after the create): ~20 s. */
export const POLL = { intervalMs: 2000, tries: 10 };

/** The inputs a scan row shows: the fields the printer's plugin marks `quick_add` (the IP comes from the scan). */
export function quickAddFields(t: PrinterType): ConnectionField[] {
  return t.connection_fields.filter(f => f.quick_add);
}

/** `cfg` without blank values: a field left empty is absent, never sent as "". */
export function stripBlank(cfg: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(cfg).filter(([, v]) =>
    v !== null && v !== undefined && !(typeof v === 'string' && v.trim() === '')));
}

/** Labels of the required quick-add fields `values` leaves blank. */
export function missingQuickAdd(t: PrinterType, values: Record<string, string>): string[] {
  return quickAddFields(t).filter(f => f.required && !(values[f.name] ?? '').trim()).map(f => f.label);
}

/** The scan's own name for the printer, else "<model> <last IP octet>" (e.g. "P1S 30"). */
export function defaultNickname(p: DiscoveredPrinter, t: PrinterType | null): string {
  if (p.name) return p.name;
  const octet = p.ip.split('.').pop();
  return `${t?.display_name ?? p.model ?? p.display_name} ${octet}`;
}

export type RowStatus =
  | 'ready' | 'missing' | 'testing' | 'test_ok' | 'test_failed' | 'adding' | 'added_configured' | 'added_unconfigured' | 'exists';

export function statusLabel(s: RowStatus, detail?: string): string {
  switch (s) {
    case 'ready': return 'Ready to add';
    case 'missing': return `Missing ${detail ?? ''}`.trim();
    case 'testing': return 'Testing…';
    case 'test_ok': return 'Connection OK';
    case 'test_failed': return 'Test failed';
    case 'adding': return 'Adding…';
    case 'added_configured': return 'Added · configured';
    case 'added_unconfigured': return detail ? `Added · not configured: ${detail}` : 'Added · not configured';
    case 'exists': return 'Already added';
  }
}

/** The chip tone for a status (colours come from the `--ok` / `--warn` / `--err` tokens). */
export function statusTone(s: RowStatus): 'ok' | 'warn' | 'err' | 'muted' {
  if (s === 'test_ok' || s === 'added_configured' || s === 'ready') return 'ok';
  if (s === 'missing' || s === 'added_unconfigured' || s === 'exists') return 'warn';
  if (s === 'test_failed') return 'err';
  return 'muted';
}

/** `fn` over `items`, at most `limit` at a time; every item settles (one failure never stops the rest). */
export async function runPool<T, R>(items: T[], limit: number, fn: (t: T) => Promise<R>): Promise<PromiseSettledResult<R>[]> {
  const out: PromiseSettledResult<R>[] = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const i = next++;
      try {
        out[i] = { status: 'fulfilled', value: await fn(items[i]) };
      } catch (reason) {
        out[i] = { status: 'rejected', reason };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}
