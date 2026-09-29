import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import { fmtMoney } from '../data/helpers';
import { listCustomers, createCustomer, type CustomerListItem } from '../api/customers';

const EMPTY_FORM = { name: '', email: '', phone: '', company: '', password: '' };

function NewCustomerForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: (id: number) => void }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const canCreate = form.name.trim() !== '' && form.email.trim() !== '' && !saving;
  const field = (k: keyof typeof EMPTY_FORM) => ({
    value: form[k],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value }),
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canCreate) return;
    setSaving(true);
    setError(null);
    try {
      const c = await createCustomer({ ...form, password: form.password || undefined });
      onCreated(c.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  }

  return (
    <form className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }} onSubmit={submit}>
      <div style={{ fontSize: 14, fontWeight: 600 }}>New customer</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        <label className="col" style={{ gap: 4 }}><span className="label">Name *</span>
          <input className="input" autoFocus {...field('name')} /></label>
        <label className="col" style={{ gap: 4 }}><span className="label">Email *</span>
          <input className="input" type="email" {...field('email')} /></label>
        <label className="col" style={{ gap: 4 }}><span className="label">Company</span>
          <input className="input" {...field('company')} /></label>
        <label className="col" style={{ gap: 4 }}><span className="label">Phone</span>
          <input className="input" type="tel" {...field('phone')} /></label>
        <label className="col" style={{ gap: 4 }}><span className="label">Portal password</span>
          <input className="input" type="password" placeholder="Optional" autoComplete="new-password" {...field('password')} /></label>
      </div>
      <div className="small muted">Leave the password blank if this customer doesn't need to sign in to the portal yet.</div>
      {error && <div className="small" style={{ color: 'var(--err)' }}>{error}</div>}
      <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
        <button type="button" className="btn sm" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn primary sm" disabled={!canCreate}>{Icons.plus} Create customer</button>
      </div>
    </form>
  );
}

export function CustomersScreen() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const showNew = searchParams.get('new') === '1';
  const [customers, setCustomers] = useState<CustomerListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  useEffect(() => {
    let alive = true;
    listCustomers()
      .then(c => { if (alive) setCustomers(c); })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, []);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!customers || !q) return customers ?? [];
    return customers.filter(c =>
      [c.name, c.email, c.company, c.phone].some(v => v?.toLowerCase().includes(q)));
  }, [customers, query]);

  const totalOutstanding = (customers ?? []).reduce((s, c) => s + c.outstanding, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 1100 }}>
      {showNew && (
        <NewCustomerForm
          onCancel={() => setSearchParams({})}
          onCreated={id => navigate(`/customers/${id}`)}
        />
      )}

      <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="input" placeholder="Search name, email, company…" value={query}
               onChange={e => setQuery(e.target.value)} style={{ maxWidth: 320, flex: 1 }} />
        <div className="spacer" style={{ flex: 1 }} />
        {customers && customers.length > 0 && (
          <span className="small muted">
            {customers.length} customer{customers.length !== 1 ? 's' : ''} · {fmtMoney(totalOutstanding)} outstanding
          </span>
        )}
      </div>

      {error && <div className="small" style={{ color: 'var(--err)' }}>{error}</div>}

      {customers === null ? (
        !error && <div style={{ padding: 24, color: 'var(--text-3)' }}>Loading…</div>
      ) : customers.length === 0 ? (
        <Empty icon={Icons.user} title="No customers yet" sub="Add one to track their projects and payments." />
      ) : visible.length === 0 ? (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-4)', fontSize: 13 }}>No matches</div>
      ) : (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Name</th><th>Email</th><th>Phone</th>
                <th style={{ textAlign: 'right' }}>Projects</th>
                <th style={{ textAlign: 'right' }}>Outstanding</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(c => (
                <tr key={c.id} onClick={() => navigate(`/customers/${c.id}`)}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{c.name}</div>
                    {c.company && <div className="small muted">{c.company}</div>}
                  </td>
                  <td>{c.email}</td>
                  <td>{c.phone ?? <span className="muted">—</span>}</td>
                  <td style={{ textAlign: 'right' }}>
                    {c.project_count === 0 ? <span className="muted">—</span>
                      : <>{c.active_project_count} current · {c.project_count} total</>}
                  </td>
                  <td style={{ textAlign: 'right', color: c.outstanding > 0 ? 'var(--warn)' : undefined }}>
                    {c.outstanding > 0 ? fmtMoney(c.outstanding) : <span className="muted">—</span>}
                  </td>
                  <td>{c.enabled ? 'Enabled' : <span className="muted">Disabled</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
