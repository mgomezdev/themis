// Settings → API keys: list, create (with the one-time reveal) and revoke.
import { useState, useEffect, useCallback } from 'react';
import { Icons } from '../../components/icons';
import { PageHeader } from '../../components/settingsUi';
import {
  getApiKeys,
  getApiKeyScopes,
  createApiKey,
  revokeApiKey,
  deleteApiKey,
  SCOPES,
  type ApiKeyOut,
  type ApiKeyCreated,
} from '../../api/apiKeys';
import { StatusPill, Empty } from '../../components/ui';
import type { StatusKey } from '../../data/types';
import { SettingsIcons } from './icons';

const SCOPE_PILL_LIMIT = 4;

function ScopePill({ scope }: { scope: string }) {
  return (
    <span className="pill idle" style={{ fontSize: 10.5, padding: '2px 8px' }}>
      {scope}
    </span>
  );
}

function ApiKeyRow({ item, allScopesLength, onRevoke, onDelete }: {
  item: ApiKeyOut; allScopesLength: number; onRevoke: () => void; onDelete: () => void;
}) {
  return (
    <tr style={{ opacity: item.enabled ? 1 : 0.55 }}>
      <td style={{ fontWeight: 500 }}>{item.name}</td>
      <td><span className="mono small">{item.key_prefix}…</span></td>
      <td>
        <div className="row gap-1" style={{ flexWrap: 'wrap', maxWidth: 280 }}>
          {item.scopes.length === 0 ? (
            <span className="tiny muted">none</span>
          ) : item.scopes.length === allScopesLength ? (
            <span className="pill accent" style={{ fontSize: 10.5, padding: '2px 8px' }} title={item.scopes.join(', ')}>
              All access
            </span>
          ) : item.scopes.length > SCOPE_PILL_LIMIT ? (
            <>
              {item.scopes.slice(0, SCOPE_PILL_LIMIT).map(s => <ScopePill key={s} scope={s} />)}
              <span className="tiny muted" title={item.scopes.slice(SCOPE_PILL_LIMIT).join(', ')}>
                +{item.scopes.length - SCOPE_PILL_LIMIT} more
              </span>
            </>
          ) : (
            item.scopes.map(s => <ScopePill key={s} scope={s} />)
          )}
        </div>
      </td>
      <td className="tiny muted" style={{ whiteSpace: 'nowrap' }}>{new Date(item.created_at).toLocaleString()}</td>
      <td className="tiny muted" style={{ whiteSpace: 'nowrap' }}>
        {item.last_used_at ? new Date(item.last_used_at).toLocaleString() : 'Never'}
      </td>
      <td>
        {item.enabled
          ? <StatusPill status={'idle' as StatusKey} label="Active" />
          : <StatusPill status={'offline' as StatusKey} label="Revoked" />}
      </td>
      <td style={{ textAlign: 'right' }}>
        <div className="row gap-1" style={{ justifyContent: 'flex-end' }}>
          {item.enabled && (
            <button className="btn ghost sm" onClick={onRevoke}>Revoke</button>
          )}
          {!item.enabled && (
            <button className="btn ghost sm" onClick={onDelete} style={{ color: 'var(--err)' }}>Delete</button>
          )}
        </div>
      </td>
    </tr>
  );
}

function CreateKeyModal({ onClose, onCreated, allScopes }: {
  onClose: () => void; onCreated: (k: ApiKeyCreated) => void; allScopes: string[];
}) {
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleScope(s: string) {
    setScopes(prev => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s); else next.add(s);
      return next;
    });
  }

  function selectAllScopes() {
    setScopes(new Set(allScopes));
  }

  function selectReadOnly() {
    const readOnlyScopes = SCOPES.flatMap(g =>
      g.scopes.filter(s => s.label === 'Read').map(s => s.scope)
    );
    setScopes(new Set(readOnlyScopes));
  }

  async function submit() {
    if (!name.trim()) return;
    if (scopes.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      const created = await createApiKey(name.trim(), Array.from(scopes));
      onCreated(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div className="card" style={{
        width: 640, maxWidth: '95vw', maxHeight: '85vh',
        display: 'flex', flexDirection: 'column', padding: 0,
      }}>
        <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border-1)' }}>
          <h2 style={{ margin: 0, fontSize: 17, fontWeight: 600 }}>Create API key</h2>
        </div>
        <div style={{ padding: '20px 24px', overflowY: 'auto', flex: 1 }}>
          <label className="label">Name</label>
          <input className="input" autoFocus value={name}
                 onChange={e => setName(e.target.value)}
                 placeholder="e.g. Ordinus integration"
                 style={{ width: '100%', marginBottom: 20 }} />

          <label className="label">Scopes</label>
          <div className="row gap-2" style={{ marginBottom: 14 }}>
            <button className="btn sm" onClick={selectAllScopes} type="button">All scopes</button>
            <button className="btn sm" onClick={selectReadOnly} type="button">Read-only</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '14px 20px', marginTop: 8 }}>
            {SCOPES.map(group => (
              <div key={group.resource}>
                <div className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
                  {group.label}
                </div>
                <div className="col gap-1">
                  {group.scopes.map(s => (
                    <label key={s.scope} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                      <input type="checkbox" checked={scopes.has(s.scope)} onChange={() => toggleScope(s.scope)} />
                      {s.label}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {error && <div className="small" style={{ color: 'var(--err)', marginTop: 16 }}>{error}</div>}
        </div>
        <div style={{ padding: '14px 24px', borderTop: '1px solid var(--border-1)', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button className="btn sm" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn primary sm" onClick={submit} disabled={saving || !name.trim() || scopes.size === 0}>
            {saving ? 'Creating…' : 'Create key'}
          </button>
        </div>
      </div>
    </div>
  );
}

function RevealKeyDialog({ apiKey, onClose }: { apiKey: ApiKeyCreated; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(apiKey.key);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable — the field is still selectable/copyable by hand
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 1001,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div className="card" style={{ width: 520, maxWidth: '95vw', padding: 28 }}>
        <h2 style={{ margin: '0 0 8px', fontSize: 17, fontWeight: 600 }}>Key created — “{apiKey.name}”</h2>
        <p className="muted small" style={{ marginTop: 0, marginBottom: 16, lineHeight: 1.5 }}>
          Copy this key now — for security, it will not be shown again. Only the prefix
          (<span className="mono">{apiKey.key_prefix}…</span>) stays visible afterward.
        </p>
        <div className="row gap-2" style={{ marginBottom: 16 }}>
          <input
            className="input mono"
            readOnly
            value={apiKey.key}
            onFocus={e => e.currentTarget.select()}
            style={{ flex: 1, fontSize: 12.5 }}
          />
          <button className="btn sm" onClick={copy}>
            {copied ? Icons.check : Icons.copy} {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn primary sm" onClick={onClose}>Done — I've saved it</button>
        </div>
      </div>
    </div>
  );
}

export function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeyOut[]>([]);
  const [allScopes, setAllScopes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [revealKey, setRevealKey] = useState<ApiKeyCreated | null>(null);

  const refetch = useCallback(() => {
    setLoading(true);
    Promise.all([getApiKeys(), getApiKeyScopes()])
      .then(([keys, scopes]) => {
        setKeys(keys);
        setAllScopes(scopes);
      })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refetch(); }, [refetch]);

  async function handleRevoke(id: number, name: string) {
    if (!window.confirm(`Revoke access for the key "${name}"?`)) return;
    setError(null);
    try {
      await revokeApiKey(id);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(id: number, name: string) {
    if (!window.confirm(`Permanently delete the key "${name}"? This can't be undone.`)) return;
    setError(null);
    try {
      await deleteApiKey(id);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function handleCreated(created: ApiKeyCreated) {
    setCreating(false);
    setRevealKey(created);
    refetch();
  }

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <PageHeader
          title="API Keys"
          sub="Every request to Themis's API needs a key. This browser holds its own device key — mint additional scoped keys for external apps and integrations."
          actions={<button className="btn primary sm" onClick={() => setCreating(true)}>{Icons.plus} Create key</button>}
        />

        {error && (
          <div style={{ marginBottom: 14, padding: '10px 14px', background: 'var(--bg-1)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
            {error}
          </div>
        )}

        {!loading && keys.length === 0 && (
          <Empty title="No API keys yet" sub="Create one to let an external app or script talk to Themis." icon={SettingsIcons.apikey} />
        )}

        {(loading || keys.length > 0) && (
          <div style={{ border: '1px solid var(--border-1)', borderRadius: 8, overflowX: 'auto', background: 'var(--bg-1)' }}>
            <table className="tbl" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Prefix</th>
                  <th>Scopes</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th>Status</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {keys.map(k => (
                  <ApiKeyRow
                    key={k.id}
                    item={k}
                    allScopesLength={allScopes.length}
                    onRevoke={() => handleRevoke(k.id, k.name)}
                    onDelete={() => handleDelete(k.id, k.name)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && <CreateKeyModal onClose={() => setCreating(false)} onCreated={handleCreated} allScopes={allScopes} />}
      {revealKey && <RevealKeyDialog apiKey={revealKey} onClose={() => setRevealKey(null)} />}
    </div>
  );
}
