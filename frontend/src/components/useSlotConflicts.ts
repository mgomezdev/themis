import { useEffect, useState } from 'react';
import { fetchSlotConflicts } from '../api/printers';

/** The slot indexes of a printer whose bound spool differs from what the printer reports (none while unknown or unreachable).
 *  Re-read when `key` changes (e.g. after the slots are saved). */
export function useSlotConflicts(printerId: number, key: unknown = null): number[] {
  const [conflicts, setConflicts] = useState<number[]>([]);
  useEffect(() => {
    let alive = true;
    fetchSlotConflicts(printerId).then(r => { if (alive) setConflicts(Array.isArray(r?.conflicts) ? r.conflicts : []); }).catch(() => { if (alive) setConflicts([]); });
    return () => { alive = false; };
  }, [printerId, key]);
  return conflicts;
}
