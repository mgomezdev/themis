import { useEffect, useState } from 'react';
import { fetchSlotConflicts } from '../api/printers';
import { slotBinding, type LoadedFilament } from '../api/printers';

type Slots = readonly LoadedFilament[] | null | undefined;

/** What decides a slot's conflict: the tuple and the bound spool. */
const slotKey = (s: LoadedFilament | undefined): string =>
  s ? JSON.stringify([s.manufacturer ?? null, s.type, s.subtype ?? null, s.color, slotBinding(s), !!s.reported]) : '';

/** The slot indexes of a printer whose bound spool differs from what the printer reports (none while unknown or unreachable).
 *  `stored` = the slots as the server has them: they are re-read whenever those change (a save, a telemetry refresh). `edited` = the
 *  slots as the form holds them: a slot edited since (tuple or spool) is left out, since the server's verdict was about the stored one. */
export function useSlotConflicts(printerId: number, stored?: Slots, edited?: Slots, enabled = true): number[] {
  const [conflicts, setConflicts] = useState<number[]>([]);
  const storedKey = JSON.stringify((stored ?? []).map(slotKey));
  useEffect(() => {
    if (!enabled || !Number.isFinite(printerId)) { setConflicts([]); return; }
    let alive = true;
    fetchSlotConflicts(printerId).then(r => { if (alive) setConflicts(Array.isArray(r?.conflicts) ? r.conflicts : []); }).catch(() => { if (alive) setConflicts([]); });
    return () => { alive = false; };
  }, [printerId, storedKey, enabled]);
  if (!edited || !stored) return conflicts;
  return conflicts.filter(i => slotKey(edited[i]) === slotKey(stored[i]));
}
