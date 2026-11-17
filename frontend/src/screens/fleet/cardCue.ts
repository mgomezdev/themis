// Fleet: the border / glow that marks a printer card needing attention.
import React from 'react';
import type { Printer } from '../../data/types';

// Border/shadow that flags a card as needing attention (awaiting clear) or
// manually held out of the queue.
export function cardCueStyle(p: Printer): React.CSSProperties {
  if (p.awaitingPlateClear) {
    return { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' };
  }
  if (p.configured === false) {                        // BIZ-369: the system holds it out until a connection confirms setup
    return { borderColor: 'var(--warn)', boxShadow: '0 0 0 1px var(--warn)' };
  }
  if (!p.queueOn) {
    return { borderColor: 'var(--border-2)', boxShadow: '0 0 0 1px var(--border-3)' };
  }
  return {};
}
