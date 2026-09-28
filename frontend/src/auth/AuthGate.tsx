import React, { useEffect, useState } from 'react';
import { getApiKey, setApiKey, clearApiKey } from './apiKeyStore';
import { setUnauthorizedHandler, setForbiddenHandler } from '../api/client';
import { login, requestRecoveryCode, confirmRecovery } from './session';

type GateState = 'checking' | 'ready' | 'manual';

function ForbiddenToast({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 4000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 16,
        right: 16,
        background: 'var(--error)',
        color: 'white',
        padding: '12px 16px',
        borderRadius: 4,
        fontSize: 14,
        zIndex: 9999,
      }}
    >
      {message}
    </div>
  );
}

/** Wraps the app shell. A stored key (API key or login session) → straight in. Otherwise asks the
 *  server who we are: a local-network device the admin account lets in without signing in →
 *  straight in; anyone else gets the sign-in form (customer email or "admin"), the admin
 *  password-recovery flow, or a pasted API key. There's no automatic key minting any more. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>(() => (getApiKey() ? 'ready' : 'checking'));
  const [manualKey, setManualKey] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [forbiddenMessage, setForbiddenMessage] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [recoveryCode, setRecoveryCode] = useState('');
  const [recoveryPassword, setRecoveryPassword] = useState('');
  const [recoveryMessage, setRecoveryMessage] = useState<string | null>(null);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearApiKey();
      setState('manual');
    });
    setForbiddenHandler((msg) => setForbiddenMessage(msg));
    return () => {
      setUnauthorizedHandler(null);
      setForbiddenHandler(null);
    };
  }, []);

  useEffect(() => {
    if (getApiKey()) {
      setState('ready');
      return;
    }
    let alive = true;
    fetch('/api/v1/auth/me')
      .then(async (r) => {
        if (!alive) return;
        if (!r.ok) throw new Error(String(r.status));
        const me = await r.json();
        setUnreachable(false);
        setState(me?.role ? 'ready' : 'manual');
      })
      .catch(() => {
        if (!alive) return;
        setUnreachable(true);
        setState('manual');
      });
    return () => { alive = false; };
  }, [retryCount]);

  async function validateKey(key: string) {
    setValidationError(null);
    setIsValidating(true);
    try {
      const response = await fetch('/api/v1/api-keys', {
        headers: { 'X-Api-Key': key },
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

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;
    setLoginError(null);
    setIsLoggingIn(true);
    const result = await login(email.trim(), password);
    setIsLoggingIn(false);
    if ('key' in result) {
      setApiKey(result.key);
      setPassword('');
      setState('ready');
    } else {
      setLoginError(result.error);
    }
  }

  function handleRetry() {
    setUnreachable(false);
    setState('checking');
    setRetryCount((c) => c + 1);
  }

  async function sendRecoveryCode() {
    setRecoveryError(null);
    const ok = await requestRecoveryCode();
    setRecoveryMessage(ok
      ? 'A one-time code was written to the Themis server log (valid 15 minutes). On the server: docker compose logs themis'
      : null);
    if (!ok) setRecoveryError('Server unreachable');
  }

  async function submitRecovery(e: React.FormEvent) {
    e.preventDefault();
    setRecoveryError(null);
    const result = await confirmRecovery(recoveryCode.trim(), recoveryPassword);
    if (result === true) {
      setRecovering(false);
      setRecoveryCode('');
      setRecoveryPassword('');
      setRecoveryMessage(null);
      setEmail('admin');
      setLoginError('Admin password set — sign in with it.');
    } else {
      setRecoveryError(result);
    }
  }

  if (state === 'ready') {
    return (
      <>
        {children}
        {forbiddenMessage && <ForbiddenToast message={forbiddenMessage} onDismiss={() => setForbiddenMessage(null)} />}
      </>
    );
  }

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
      <form onSubmit={submitLogin} className="card" style={{ padding: 28, width: 360, maxWidth: '90vw', marginBottom: 16 }}>
        <h2 style={{ margin: '0 0 12px', fontSize: 17, fontWeight: 600 }}>Sign in</h2>
        <input className="input" type="text" autoComplete="username" value={email}
               onChange={(e) => setEmail(e.target.value)} placeholder="Email or username"
               style={{ width: '100%', marginBottom: 8 }} />
        <input className="input" type="password" autoComplete="current-password" value={password}
               onChange={(e) => setPassword(e.target.value)} placeholder="Password"
               style={{ width: '100%', marginBottom: loginError ? 6 : 12 }} />
        {loginError && (
          <p className="muted small" style={{ color: 'var(--error)', margin: '0 0 12px' }}>{loginError}</p>
        )}
        <button type="submit" className="btn primary" disabled={!email.trim() || !password || isLoggingIn}
                style={{ width: '100%' }}>
          {isLoggingIn ? 'Signing in…' : 'Sign in'}
        </button>
        <button type="button" className="btn ghost sm" style={{ width: '100%', marginTop: 8 }}
                onClick={() => { setRecovering(r => !r); setRecoveryError(null); }}>
          Forgot admin password?
        </button>
        <p className="muted small" style={{ margin: '8px 0 0', lineHeight: 1.5 }}>
          New install? The admin has no password yet — open Themis from the local network, or use
          “Forgot admin password?” to set one.
        </p>
      </form>
      {recovering && (
        <form onSubmit={submitRecovery} className="card" style={{ padding: 28, width: 360, maxWidth: '90vw', marginBottom: 16 }}>
          <h2 style={{ margin: '0 0 6px', fontSize: 17, fontWeight: 600 }}>Reset admin password</h2>
          <p className="muted small" style={{ marginTop: 0, marginBottom: 12, lineHeight: 1.5 }}>
            Works offline: the code goes to the server log, not over the network. Or, on the server:{' '}
            <code style={{ fontSize: 'inherit' }}>docker compose exec themis python -m app.admin reset-password</code>
          </p>
          <button type="button" className="btn sm" style={{ width: '100%', marginBottom: 8 }} onClick={sendRecoveryCode}>
            Write a one-time code to the server log
          </button>
          {recoveryMessage && <p className="muted small" style={{ margin: '0 0 8px', lineHeight: 1.5 }}>{recoveryMessage}</p>}
          <input className="input" value={recoveryCode} onChange={(e) => setRecoveryCode(e.target.value)}
                 placeholder="Recovery code" style={{ width: '100%', marginBottom: 8 }} />
          <input className="input" type="password" autoComplete="new-password" value={recoveryPassword}
                 onChange={(e) => setRecoveryPassword(e.target.value)} placeholder="New admin password"
                 style={{ width: '100%', marginBottom: recoveryError ? 6 : 12 }} />
          {recoveryError && (
            <p className="muted small" style={{ color: 'var(--error)', margin: '0 0 12px' }}>{recoveryError}</p>
          )}
          <button type="submit" className="btn primary" disabled={!recoveryCode.trim() || !recoveryPassword}
                  style={{ width: '100%' }}>
            Set admin password
          </button>
        </form>
      )}
      <form onSubmit={submitManualKey} className="card" style={{ padding: 28, width: 360, maxWidth: '90vw' }}>
        <h2 style={{ margin: '0 0 6px', fontSize: 17, fontWeight: 600 }}>Enter your API key</h2>
        {unreachable ? (
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
            For integrations and scripts: paste an API key (Settings → API Keys), or set{' '}
            <code style={{ fontSize: 'inherit' }}>THEMIS_BOOTSTRAP_KEY</code> in <code style={{ fontSize: 'inherit' }}>.env</code> and restart Themis.
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
