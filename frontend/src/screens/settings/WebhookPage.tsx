// Settings → Webhooks: the legacy single-destination webhook.
import { useState, useEffect } from 'react';
import { getWebhookConfig, saveWebhookConfig } from '../../api/settings';
import { FieldRow, PageHeader, Toggle } from '../../components/settingsUi';

// Raised by the inventory host: spool tracking suspended/restored, a remote provider unreachable/back.
const INVENTORY_EVENTS = ['inventory.tracking_unavailable', 'inventory.tracking_restored', 'inventory.disconnected', 'inventory.reconnected', 'inventory.weight_conflict', 'inventory.no_spool_tracked'];

const ALL_WEBHOOK_EVENTS = ['job.complete', 'job.failed', 'job.blocked', 'spool.low', 'printer.alarm', ...INVENTORY_EVENTS];

export function WebhookPage() {
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [secretTouched, setSecretTouched] = useState(false);
  const [hasSecret, setHasSecret] = useState(false);
  const [events, setEvents] = useState<string[]>(ALL_WEBHOOK_EVENTS);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getWebhookConfig()
      .then(cfg => {
        setUrl(cfg.url ?? '');
        setHasSecret(cfg.has_secret);
        setEvents(cfg.events.length ? cfg.events : ALL_WEBHOOK_EVENTS);
      })
      .catch(console.error);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    setSaving(true);
    setSaved(false);
    try {
      const cfg = await saveWebhookConfig({
        url: url.trim() || null,
        secret: secretTouched ? secret.trim() : undefined,
        events,
      });
      setHasSecret(cfg.has_secret);
      setSecret('');
      setSecretTouched(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }

  function toggleEvent(ev: string) {
    setEvents(prev => prev.includes(ev) ? prev.filter(e => e !== ev) : [...prev, ev]);
  }

  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader
        title="Webhooks"
        sub="Receive an HMAC-signed POST when a job changes state."
      />
      <FieldRow label="Endpoint URL" hint="POST requests are sent here for enabled events.">
        <input
          className="input"
          placeholder="https://example.com/webhook"
          value={url}
          onChange={e => setUrl(e.target.value)}
          style={{ width: '100%' }}
        />
      </FieldRow>
      <FieldRow label="Secret" hint={hasSecret && !secretTouched
        ? 'A secret is saved. Leave blank to keep it, or type a new one to replace it.'
        : 'Optional. Included as X-Webhook-Signature: sha256=<hmac>.'}>
        <input
          className="input"
          type="password"
          placeholder={hasSecret && !secretTouched ? '••••••••' : 'webhook-secret'}
          value={secret}
          onChange={e => { setSecret(e.target.value); setSecretTouched(true); }}
          style={{ width: '100%' }}
        />
      </FieldRow>
      <FieldRow label="Events" hint="Which events fire a request: job state changes, and spools running low.">
        <div className="col" style={{ gap: 10 }}>
          {ALL_WEBHOOK_EVENTS.map(ev => (
            <label key={ev} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
              <Toggle checked={events.includes(ev)} onChange={() => toggleEvent(ev)} />
              <span style={{ fontSize: 13, fontFamily: 'monospace', color: 'var(--text-1)' }}>{ev}</span>
            </label>
          ))}
        </div>
      </FieldRow>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
        {saved && <span className="pill ok" style={{ alignSelf: 'center' }}>Saved</span>}
        <button className="btn primary" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}

// =========================================================================
// Maintenance page
// =========================================================================
