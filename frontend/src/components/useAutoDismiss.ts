import { useEffect, useRef } from 'react';

/**
 * Call `onExpire` `ms` after `value` becomes truthy. A new truthy value (by identity) restarts the wait; a falsy
 * one or unmounting cancels it. Used for toasts (the Project builder's "Saved" and undo-removal toasts).
 */
export function useAutoDismiss(value: unknown, onExpire: () => void, ms: number) {
  const expire = useRef(onExpire);
  expire.current = onExpire;
  useEffect(() => {
    if (!value) return;
    const t = window.setTimeout(() => expire.current(), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
}
