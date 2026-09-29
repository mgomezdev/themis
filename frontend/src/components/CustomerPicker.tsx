import { useEffect, useState } from 'react';
import { listCustomers, createCustomer, type Customer } from '../api/customers';

export interface CustomerChoice {
  /** Linked customer account, or null for a typed name only. */
  customerId: number | null;
  /** Display name stored on the project (the account's name when one is linked). */
  customerText: string;
}

const OTHER = '__other';
const NEW = '__new';

/**
 * Pick the customer account a project belongs to, create one inline, or fall back to a typed
 * name ("Other") for someone without an account. Falls back to the typed name alone when the
 * key can't list customers (no `customers:read`).
 */
export function CustomerPicker({ value, onChange }: { value: CustomerChoice; onChange: (v: CustomerChoice) => void }) {
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [other, setOther] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: '', email: '' });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    listCustomers()
      .then(c => { if (alive) setCustomers(c); })
      .catch(() => { if (alive) setUnavailable(true); });
    return () => { alive = false; };
  }, []);

  if (unavailable) {
    return (
      <input className="input" placeholder="Customer name" aria-label="Customer name"
             value={value.customerText}
             onChange={e => onChange({ customerId: null, customerText: e.target.value })} />
    );
  }

  const selectValue = creating ? NEW
    : value.customerId != null ? String(value.customerId)
    : other || value.customerText ? OTHER : '';

  function select(v: string) {
    setError(null);
    setCreating(v === NEW);
    setOther(v === OTHER);
    if (v === NEW) return;
    if (v === OTHER) { onChange({ customerId: null, customerText: value.customerId != null ? '' : value.customerText }); return; }
    if (v === '') { onChange({ customerId: null, customerText: '' }); return; }
    const c = customers?.find(x => x.id === Number(v));
    onChange({ customerId: Number(v), customerText: c?.name ?? value.customerText });
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const c = await createCustomer({ name: draft.name.trim(), email: draft.email.trim() });
      setCustomers(prev => [...(prev ?? []), c].sort((a, b) => a.name.localeCompare(b.name)));
      setCreating(false);
      setDraft({ name: '', email: '' });
      onChange({ customerId: c.id, customerText: c.name });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const linkedMissing = value.customerId != null && customers !== null && !customers.some(c => c.id === value.customerId);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <select className="select" aria-label="Customer" value={selectValue} onChange={e => select(e.target.value)}>
        <option value="">Choose a customer…</option>
        {linkedMissing && <option value={value.customerId!}>{value.customerText || `Customer #${value.customerId}`}</option>}
        {customers?.map(c => (
          <option key={c.id} value={c.id}>{c.name}{c.company ? ` (${c.company})` : ''}</option>
        ))}
        <option value={NEW}>+ New customer…</option>
        <option value={OTHER}>Other (no account)</option>
      </select>
      {selectValue === OTHER && (
        <input className="input" placeholder="Customer name" aria-label="Customer name"
               value={value.customerText}
               onChange={e => onChange({ customerId: null, customerText: e.target.value })} />
      )}
      {creating && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="input" placeholder="Name" aria-label="New customer name" style={{ flex: 1, minWidth: 120 }}
                 value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} />
          <input className="input" type="email" placeholder="Email" aria-label="New customer email"
                 style={{ flex: 1, minWidth: 160 }}
                 value={draft.email} onChange={e => setDraft({ ...draft, email: e.target.value })} />
          <button type="button" className="btn primary sm" onClick={create}
                  disabled={busy || !draft.name.trim() || !draft.email.trim()}>
            {busy ? 'Creating…' : 'Create'}
          </button>
        </div>
      )}
      {error && <div className="small" style={{ color: 'var(--err)' }}>{error}</div>}
    </div>
  );
}
