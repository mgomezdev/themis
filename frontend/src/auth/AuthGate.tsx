import React, { useEffect, useState } from 'react';
import { getApiKey, setApiKey, clearApiKey } from './apiKeyStore';
import { setUnauthorizedHandler } from '../api/client';

type GateState = 'checking' | 'ready' | 'manual';
type BootstrapResult = { key: string } | { key: null; reason: 'already-bootstrapped' | 'error' };

// Module-level (not component-level) so React StrictMode's dev-only double-invoke of
// effects — mount, cleanup, mount — shares a single in-flight bootstrap POST instead of
// firing two, which would otherwise mint two "Browser" keys during the open bootstrap
// window (the table is still empty for both concurrent requests).
let bootstrapPromise: Promise<BootstrapResult> | null = null;

function bootstrapKey(): Promise<BootstrapResult> {
  if (!bootstrapPromise) {
    bootstrapPromise = fetch('/api/v1/api-keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Browser' }),
    })
      .then(async (resp): Promise<BootstrapResult> => {
        if (!resp.ok) {
          if (resp.status === 400) {
            return { key: null, reason: 'already-bootstrapped' } as const;
          }
          return { key: null, reason: 'error' } as const;
        }
        const data = await resp.json();
        if (typeof data?.key === 'string') {
          return { key: data.key };
        }
        return { key: null, reason: 'error' } as const;
      })
      .catch((): BootstrapResult => ({ key: null, reason: 'error' }));
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
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [bootstrapReason, setBootstrapReason] = useState<'already-bootstrapped' | 'error' | null>(null);
  const [retryCount, setRetryCount] = useState(0);

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
    bootstrapKey().then((result) => {
      if (!alive) return;
      if (result.key) {
        setApiKey(result.key);
        setState('ready');
      } else {
        setBootstrapReason((result as { key: null; reason: 'already-bootstrapped' | 'error' }).reason);
        setState('manual');
      }
    });
    return () => { alive = false; };
  }, [retryCount]);

  async function validateKey(key: string) {
    setValidationError(null);
    setIsValidating(true);
    try {
      const response = await fetch('/api/v1/api-keys', {
        headers: { 'Authorization': `Bearer ${key}` },
      });

      if (response.ok || response.status === 403) {
        // 200: valid key with apikeys:read scope
        // 403: valid key (authenticated), but lacks apikeys:read scope (fine—scope enforced per-action)
        setApiKey(key);
        setManualKey('');
        setState('ready');
      } else if (response.status === 401) {
        setValidationError('API key not recognized');
      } else {
        setValidationError('Server unreachable');
      }
    } catch {
      setValidationError('Server unreachable');
    } finally {
      setIsValidating(false);
    }
  }

  function submitManualKey(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = manualKey.trim();
    if (!trimmed) return;
    validateKey(trimmed);
  }

  function handleRetry() {
    bootstrapPromise = null;
    setBootstrapReason(null);
    setRetryCount((c) => c + 1);
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
        {bootstrapReason === 'error' ? (
          <>
            <p className="muted small" style={{ color: 'var(--error)', marginTop: 0, marginBottom: 12, lineHeight: 1.5 }}>
              Couldn't reach the Themis server. Check your connection and try again.
            </p>
            <button
              type="button"
              className="btn primary"
              onClick={handleRetry}
              style={{ width: '100%', marginBottom: 12 }}
            >
              Retry
            </button>
          </>
        ) : (
          <p className="muted small" style={{ marginTop: 0, marginBottom: 16, lineHeight: 1.5 }}>
            Themis couldn't automatically set up access for this browser. Paste an existing API key
            (Settings → API Keys) to continue.
          </p>
        )}
        <input
          className="input"
          type="password"
          autoFocus
          value={manualKey}
          onChange={(e) => setManualKey(e.target.value)}
          placeholder="thm_..."
          style={{ width: '100%', marginBottom: validationError ? 6 : 12 }}
        />
        {validationError && (
          <p className="muted small" style={{ color: 'var(--error)', margin: '0 0 12px', lineHeight: 1.5 }}>
            {validationError}
          </p>
        )}
        <button
          type="submit"
          className="btn primary"
          disabled={!manualKey.trim() || isValidating}
          style={{ width: '100%' }}
        >
          {isValidating ? 'Validating…' : 'Continue'}
        </button>
      </form>
    </div>
  );
}
