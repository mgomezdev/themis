import { useState } from 'react';

const STORAGE_KEY = 'themis.gcodeWarningDismissed';

function remembered(): boolean {
  try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch { return false; }
}

/**
 * Shown above a pre-sliced .gcode job: Themis can't check the gcode against a printer, so the operator
 * owns the match. Dismissible; "Don't show again" is remembered per browser.
 */
export function GcodeWarning() {
  const [hidden, setHidden] = useState(remembered);
  const [remember, setRemember] = useState(false);
  if (hidden) return null;

  function dismiss() {
    if (remember) {
      try { localStorage.setItem(STORAGE_KEY, '1'); } catch { /* private mode: dismissed for this view only */ }
    }
    setHidden(true);
  }

  return (
    <div role="note" data-testid="gcode-warning" style={{
      padding: '10px 14px', borderRadius: 8, fontSize: 13, lineHeight: 1.5,
      background: 'rgba(251,191,36,0.10)', border: '1px solid rgba(251,191,36,0.30)', color: 'var(--text-1)',
    }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>Pre-sliced gcode</div>
      <div>
        Printers interpret gcode differently, so Themis can't check that this file suits the printers you pick.
        Choose the printer type(s) it was sliced for — making sure they match is up to you. Slicing settings and
        overrides don't apply to gcode and are ignored.
      </div>
      <div className="row gap-3" style={{ marginTop: 8, alignItems: 'center' }}>
        <label className="tiny row gap-2" style={{ alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
          Don't show this again
        </label>
        <button className="btn ghost sm" onClick={dismiss}>Dismiss</button>
      </div>
    </div>
  );
}
