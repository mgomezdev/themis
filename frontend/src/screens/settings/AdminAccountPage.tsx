// Settings → Admin account: password and local sign-in.
import { useState, useEffect } from 'react';
import { PageHeader } from '../../components/settingsUi';
import { getAdminAccount, setAdminPassword, setAllowLocalLogin, type AdminAccount } from '../../api/adminAccount';

export function AdminAccountPage() {
  const [acct, setAcct] = useState<AdminAccount | null>(null);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getAdminAccount().then(setAcct).catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function run(fn: () => Promise<AdminAccount>, ok: string) {
    setError(null);
    setNotice(null);
    try { setAcct(await fn()); setNotice(ok); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  const mismatch = confirm !== '' && password !== confirm;
  const tooShort = password !== '' && password.length < 8;

  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Admin account"
                  sub="The admin signs in as “admin” from anywhere. Local-network devices can skip signing in." />
      {error && <div className="small" style={{ color: 'var(--err)', marginBottom: 12 }}>{error}</div>}
      {notice && <div className="small" style={{ color: 'var(--ok)', marginBottom: 12 }}>{notice}</div>}
      {acct && (
        <div className="col gap-3">
          <div className="small">
            Username <b>{acct.username}</b> · password {acct.password_set ? 'set' : <b>not set</b>}
          </div>

          <form className="row gap-2" style={{ flexWrap: 'wrap', alignItems: 'center' }}
                onSubmit={e => {
                  e.preventDefault();
                  if (password && !mismatch)
                    run(async () => { const a = await setAdminPassword(password); setPassword(''); setConfirm(''); return a; },
                        'Admin password saved. Other admin sessions were signed out.');
                }}>
            <input className="input" type="password" autoComplete="new-password" placeholder="New admin password"
                   value={password} onChange={e => setPassword(e.target.value)} />
            <input className="input" type="password" autoComplete="new-password" placeholder="Confirm password"
                   value={confirm} onChange={e => setConfirm(e.target.value)} />
            <button className="btn primary sm" type="submit" disabled={!password || tooShort || password !== confirm}>
              {acct.password_set ? 'Change password' : 'Set password'}
            </button>
            {tooShort && <span className="small" style={{ color: 'var(--err)' }}>At least 8 characters</span>}
            {!tooShort && mismatch && <span className="small" style={{ color: 'var(--err)' }}>Passwords don’t match</span>}
          </form>

          <label className="row gap-2" style={{ alignItems: 'flex-start' }}>
            <input type="checkbox" checked={acct.allow_local_login}
                   disabled={!acct.password_set && acct.allow_local_login}
                   onChange={e => run(() => setAllowLocalLogin(e.target.checked),
                     e.target.checked ? 'Local-network devices no longer need to sign in.'
                                      : 'Sign-in is now required everywhere, including the local network.')} />
            <span className="small">
              Local network devices are admin without signing in
              <div className="tiny muted">
                {acct.password_set
                  ? 'Unchecking makes local-network devices sign in like everyone else. Devices holding an API key keep working.'
                  : 'Set an admin password before turning this off.'}
              </div>
            </span>
          </label>

          {acct.full_access_keys > 0 && (
            <div className="small" style={{ color: 'var(--warn)', lineHeight: 1.5 }}>
              {acct.full_access_keys} API key{acct.full_access_keys === 1 ? '' : 's'} with full access
              (e.g. an older auto-created “Browser” key) still work without signing in. Review them
              under <a href="/settings/api-keys">Settings → API Keys</a> and revoke any you don’t need.
            </div>
          )}

          <div className="tiny muted" style={{ lineHeight: 1.6 }}>
            Forgot the password? No internet needed: use “Forgot admin password?” on the sign-in screen (a one-time
            code is written to the server log), or run{' '}
            <code>docker compose exec themis python -m app.admin reset-password</code> on the server.{' '}
            <code>… allow-local-login</code> turns local access back on.
          </div>
        </div>
      )}
    </div>
  );
}

// =========================================================================
// Settings screen shell
// =========================================================================
