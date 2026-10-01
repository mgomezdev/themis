import type { Severity } from '../api/alarms';

export const SEVERITY_COLOR: Record<Severity, string> = {
  info: 'var(--text-3)', warning: 'var(--warn)', error: 'var(--err)', fatal: 'var(--err)',
};
