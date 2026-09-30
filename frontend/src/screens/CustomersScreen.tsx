import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import { fmtMoney } from '../data/helpers';
import {
  listCustomers, createCustomer, getUnlinkedProjects, linkProjects, portalStatus,
  type CustomerListItem, type UnlinkedProject,
} from '../api/customers';

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

/**
 * Projects that name a customer in their free-text field but aren't linked to an account —
 * e.g. created before customer accounts existed. Exact name/company/email matches are
 * pre-selected; staff confirm before anything is linked.
 */
function LinkProjectsPanel({ unlinked, customers, onLinked }: {
  unlinked: UnlinkedProject[]; customers: CustomerListItem[]; onLinked: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<Record<number, number | null>>(
    () => Object.fromEntries(unlinked.map(u => [u.project_id, u.suggested_customer_id])));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const suggested = unlinked.filter(u => u.suggested_customer_id != null).length;
  const selected = unlinked.filter(u => choice[u.project_id] != null);

  async function link() {
    setSaving(true);
    setError(null);
    try {
      await linkProjects(selected.map(u => ({ project_id: u.project_id, customer_id: choice[u.project_id]! })));
      onLinked();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--warn)' }}>{Icons.alert}</span>
        <span style={{ fontSize: 13, flex: 1 }}>
          {unlinked.length} project{unlinked.length !== 1 ? 's name a customer' : ' names a customer'} but
          {unlinked.length !== 1 ? ' aren’t' : ' isn’t'} linked to an account, so {unlinked.length !== 1 ? 'they' : 'it'} won’t
          show in customer totals.{suggested > 0 && ` ${suggested} match${suggested !== 1 ? '' : 'es'} an existing customer.`}
        </span>
        <button className="btn sm" onClick={() => setOpen(o => !o)}>{open ? 'Hide' : 'Review'}</button>
      </div>
      {open && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead><tr><th>Project</th><th>Customer name on project</th><th>Link to account</th></tr></thead>
              <tbody>
                {unlinked.map(u => (
                  <tr key={u.project_id} style={{ cursor: 'default' }}>
                    <td><Link to={`/projects/${u.project_id}`} style={{ color: 'var(--text-1)' }}>{u.project_name}</Link></td>
                    <td>{u.customer_text}</td>
                    <td>
                      <select className="select" aria-label={`Customer for ${u.project_name}`}
                              value={choice[u.project_id] ?? ''}
                              onChange={e => setChoice({ ...choice, [u.project_id]: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Don’t link</option>
                        {customers.map(c => <option key={c.id} value={c.id}>{c.name}{c.company ? ` (${c.company})` : ''}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="small muted">
            No match? Create the customer first with “New customer”, then pick them here.
          </div>
          {error && <div className="small" style={{ color: 'var(--err)' }}>{error}</div>}
          <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
            <button className="btn primary sm" disabled={saving || selected.length === 0} onClick={link}>
              {saving ? 'Linking…' : `Link ${selected.length} project${selected.length !== 1 ? 's' : ''}`}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export function CustomersScreen() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const showNew = searchParams.get('new') === '1';
  const [customers, setCustomers] = useState<CustomerListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const [unlinked, setUnlinked] = useState<UnlinkedProject[]>([]);

  const reload = useCallback(() => {
    listCustomers()
      .then(setCustomers)
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
    getUnlinkedProjects().then(setUnlinked).catch(() => setUnlinked([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

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

      {customers && customers.length > 0 && unlinked.length > 0 && (
        // Keyed on the row set so a reload after linking resets the pre-selections.
        <LinkProjectsPanel key={unlinked.map(u => u.project_id).join(',')}
                           unlinked={unlinked} customers={customers} onLinked={reload} />
      )}

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
                  <td>{(() => { const s = portalStatus(c); return s.tone === 'ok' ? s.label : <span className="muted">{s.label}</span>; })()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
