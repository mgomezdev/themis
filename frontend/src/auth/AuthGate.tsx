import React, { useEffect, useState } from 'react';
import { getApiKey, setApiKey, clearApiKey } from './apiKeyStore';
import { setUnauthorizedHandler } from '../api/client';

type GateState = 'checking' | 'ready' | 'manual';

// Module-level (not component-level) so React StrictMode's dev-only double-invoke of
// effects — mount, cleanup, mount — shares a single in-flight bootstrap POST instead of
// firing two, which would otherwise mint two "Browser" keys during the open bootstrap
// window (the table is still empty for both concurrent requests).
let bootstrapPromise: Promise<string | null> | null = null;

function bootstrapKey(): Promise<string | null> {
  if (!bootstrapPromise) {
    bootstrapPromise = fetch('/api/v1/api-keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Browser' }),
    })
      .then(async (resp) => {
        if (!resp.ok) return null;
        const data = await resp.json();
        return typeof data?.key === 'string' ? data.key : null;
      })
      .catch(() => null);
  }
  return bootstrapPromise;
}

/** Wraps the app shell. Bootstraps a full-access "Browser" API key on first load (like a
 *  device credential), stores it in localStorage, and injects it via `apiFetch` on every
 *  subsequent request. Falls back to a manual key-entry form if bootstrap can't proceed
 *  (table already non-empty) or if this browser's key gets revoked elsewhere. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>(() => (getApiKey() ? 'ready' : 'checking'));
  const [manualKey, setManualKey] = useState('');

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearApiKey();
      setState('manual');
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    if (getApiKey()) {
      setState('ready');
      return;
    }
    let alive = true;
    bootstrapKey().then((key) => {
      if (!alive) return;
      if (key) {
        setApiKey(key);
        setState('ready');
      } else {
        setState('manual');
      }
    });
    return () => { alive = false; };
  }, []);

  function submitManualKey(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = manualKey.trim();
    if (!trimmed) return;
    setApiKey(trimmed);
    setManualKey('');
    setState('ready');
  }

  if (state === 'ready') return <>{children}</>;

  if (state === 'checking') {
    return (
      <div className="col" style={{
        alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--bg-0)',
      }}>
        <div className="muted small">Connecting…</div>
      </div>
    );
  }

  return (
    <div className="col" style={{
      alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--bg-0)',
    }}>
      <form onSubmit={submitManualKey} className="card" style={{ padding: 28, width: 360, maxWidth: '90vw' }}>
        <h2 style={{ margin: '0 0 6px', fontSize: 17, fontWeight: 600 }}>Enter your API key</h2>
        <p className="muted small" style={{ marginTop: 0, marginBottom: 16, lineHeight: 1.5 }}>
          Themis couldn't automatically set up access for this browser. Paste an existing API key
          (Settings → API Keys) to continue.
        </p>
        <input
          className="input"
          type="password"
          autoFocus
          value={manualKey}
          onChange={(e) => setManualKey(e.target.value)}
          placeholder="thm_..."
          style={{ width: '100%', marginBottom: 12 }}
        />
        <button type="submit" className="btn primary" disabled={!manualKey.trim()} style={{ width: '100%' }}>
          Continue
        </button>
      </form>
    </div>
  );
}
