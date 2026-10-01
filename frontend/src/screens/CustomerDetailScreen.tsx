import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Progress } from '../components/ui';
import { useTopbarOverride } from '../components/topbarOverride';
import { fmtDate, fmtMoney } from '../data/helpers';
import {
  getCustomer, updateCustomer, deleteCustomer, portalStatus,
  type CustomerDetail, type CustomerFields, type CustomerProject, type FinancialSummary, type FinancialWindow,
} from '../api/customers';
import { getCustomerPayments, methodLabel, type CustomerPayment } from '../api/payments';

const WINDOWS: { key: FinancialWindow; label: string }[] = [
  { key: '30d', label: '30 days' },
  { key: '60d', label: '60 days' },
  { key: '90d', label: '90 days' },
  { key: 'all', label: 'All time' },
];

const METRICS: { key: keyof FinancialSummary; label: string; hint: string; money: boolean }[] = [
  { key: 'revenue',       label: 'Revenue',       hint: 'Payments received',                      money: true },
  { key: 'expenses',      label: 'Expenses',      hint: 'Filament cost of jobs',             money: true },
  { key: 'profit',        label: 'Profit',        hint: 'Revenue − expenses',                money: true },
  { key: 'billed',        label: 'Billed',        hint: 'Quoted project prices',             money: true },
  { key: 'outstanding',   label: 'Outstanding',   hint: 'Price − paid, on unpaid projects',  money: true },
  { key: 'project_count', label: 'Projects',      hint: 'Started in the period',             money: false },
];

const STAGE_LABEL = { draft: 'Draft', planning: 'Planning', queued: 'Queued' } as const;
const STATUS_LABEL = { pending: 'Not started', active: 'In progress', completed: 'Completed' } as const;

type ProjectTab = 'current' | 'past' | 'all';

const toFields = (c: CustomerDetail): CustomerFields => ({
  name: c.name, email: c.email, phone: c.phone ?? '', company: c.company ?? '', notes: c.notes ?? '',
});

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="card" style={{ padding: '12px 16px', flex: '1 1 150px' }}>
      <div className="small muted">{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, marginTop: 2, color: tone }}>{value}</div>
    </div>
  );
}

function FinancialSummaryCard({ financials }: { financials: CustomerDetail['financials'] }) {
  const all = financials.windows.all;
  return (
    <div className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>Financial summary</div>
      <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
        <Stat label="Outstanding" value={fmtMoney(all.outstanding)}
              tone={all.outstanding > 0 ? 'var(--warn)' : undefined} />
        <Stat label="Revenue (all time)" value={fmtMoney(all.revenue)} />
        <Stat label="Expenses (all time)" value={fmtMoney(all.expenses)} />
        <Stat label="Profit (all time)" value={fmtMoney(all.profit)}
              tone={all.profit < 0 ? 'var(--err)' : undefined} />
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="tbl" data-testid="financial-table">
          <thead>
            <tr>
              <th />
              {WINDOWS.map(w => <th key={w.key} style={{ textAlign: 'right' }}>{w.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {METRICS.map(m => (
              <tr key={m.key} style={{ cursor: 'default' }}>
                <td>
                  <div>{m.label}</div>
                  <div className="small muted">{m.hint}</div>
                </td>
                {WINDOWS.map(w => {
                  const v = financials.windows[w.key][m.key];
                  return (
                    <td key={w.key} style={{
                      textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                      color: m.key === 'profit' && v < 0 ? 'var(--err)' : undefined,
                    }}>
                      {m.money ? fmtMoney(v) : v}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="small muted">
        Periods are by project start date.
        {financials.unpriced_unpaid > 0 && (
          <span style={{ color: 'var(--warn)' }}>
            {' '}{financials.unpriced_unpaid} unpaid project{financials.unpriced_unpaid !== 1 ? 's have' : ' has'} no
            price set, so {financials.unpriced_unpaid !== 1 ? 'their balances aren’t' : 'its balance isn’t'} counted
            as outstanding.
          </span>
        )}
      </div>
    </div>
  );
}

function ProjectsCard({ customerId, projects }: { customerId: number; projects: CustomerProject[] }) {
  const navigate = useNavigate();
  const current = projects.filter(p => p.status !== 'completed');
  const past = projects.filter(p => p.status === 'completed');
  const [tab, setTab] = useState<ProjectTab>(current.length > 0 || past.length === 0 ? 'current' : 'past');
  const visible = tab === 'current' ? current : tab === 'past' ? past : projects;
  const counts: Record<ProjectTab, number> = { current: current.length, past: past.length, all: projects.length };

  return (
    <div className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)', marginRight: 8 }}>Projects</div>
        {(['current', 'past', 'all'] as ProjectTab[]).map(t => (
          <button key={t} className={`btn sm ${tab === t ? 'primary' : 'ghost'}`}
                  onClick={() => setTab(t)} style={{ textTransform: 'capitalize' }}>
            {t}<span style={{ marginLeft: 5, opacity: 0.7 }}>({counts[t]})</span>
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={() => navigate(`/projects/new?customer=${customerId}`)}>
          {Icons.plus} New project
        </button>
      </div>
      {visible.length === 0 ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-4)', fontSize: 13 }}>
          {projects.length === 0 ? 'No projects for this customer yet.' : `No ${tab} projects.`}
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Project</th><th>Status</th><th>Progress</th><th>Due</th>
                <th style={{ textAlign: 'right' }}>Price</th>
                <th style={{ textAlign: 'right' }}>Paid</th>
                <th style={{ textAlign: 'right' }}>Outstanding</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(p => (
                <tr key={p.id} onClick={() => navigate(`/projects/${p.id}`)}>
                  <td>
                    <Link to={`/projects/${p.id}`} onClick={e => e.stopPropagation()}
                          style={{ color: 'var(--text-1)', fontWeight: 500, textDecoration: 'none' }}>
                      {p.name}
                    </Link>
                    <div className="small muted">
                      #{p.id} · {STAGE_LABEL[p.stage] ?? p.stage}{p.on_hold ? ' · On hold' : ''}
                    </div>
                  </td>
                  <td>{STATUS_LABEL[p.status]}</td>
                  <td style={{ minWidth: 110 }}>
                    {p.jobs_total > 0 ? (
                      <>
                        <div className="small muted">{p.jobs_complete}/{p.jobs_total} jobs</div>
                        <Progress value={(p.jobs_complete / p.jobs_total) * 100}
                                  tone={p.jobs_complete === p.jobs_total ? 'ok' : undefined} />
                      </>
                    ) : <span className="muted">—</span>}
                  </td>
                  <td>{fmtDate(p.due_date) ?? <span className="muted">—</span>}</td>
                  <td style={{ textAlign: 'right' }}>{p.price != null ? fmtMoney(p.price) : <span className="muted">—</span>}</td>
                  <td style={{ textAlign: 'right' }}>
                    {p.amount_paid != null ? fmtMoney(p.amount_paid) : <span className="muted">—</span>}
                    <div className="small muted" style={{ textTransform: 'capitalize' }}>{p.payment_status}</div>
                  </td>
                  <td style={{ textAlign: 'right', color: p.outstanding > 0 ? 'var(--warn)' : undefined }}>
                    {p.outstanding > 0 ? fmtMoney(p.outstanding) : <span className="muted">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Every payment this customer has made across their projects, newest first. */
function PaymentHistoryCard({ customerId }: { customerId: number }) {
  const [payments, setPayments] = useState<CustomerPayment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    getCustomerPayments(customerId)
      .then(p => { if (alive) setPayments(p); })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [customerId]);

  return (
    <div className="card" style={{ padding: 20 }} data-testid="payment-history">
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)', marginBottom: 12 }}>Payment history</div>
      {error ? <div style={{ color: 'var(--err)', fontSize: 13 }}>{error}</div>
        : payments === null ? <div className="muted small">Loading…</div>
        : payments.length === 0 ? <div style={{ color: 'var(--text-4)', fontSize: 13 }}>No payments recorded yet.</div>
        : (
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead><tr><th>Received</th><th>Project</th><th>Method</th><th>Note</th><th style={{ textAlign: 'right' }}>Amount</th></tr></thead>
              <tbody>
                {payments.map(p => (
                  <tr key={p.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(p.received_on)}</td>
                    <td><Link to={`/projects/${p.project_id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>{p.project_name}</Link></td>
                    <td>{methodLabel(p.method)}</td>
                    <td className="muted">{p.note ?? ''}</td>
                    <td style={{ textAlign: 'right' }}>{fmtMoney(p.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}

function DetailsCard({ customer, onSaved }: { customer: CustomerDetail; onSaved: () => void }) {
  const navigate = useNavigate();
  const status = portalStatus(customer);
  const [form, setForm] = useState<CustomerFields>(() => toFields(customer));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const saved = toFields(customer);
  const dirty = (Object.keys(form) as (keyof CustomerFields)[]).some(k => form[k] !== saved[k]);
  const savedKey = JSON.stringify(saved);

  // Reset only when the saved details change — not on a reload after Enable/Set password,
  // which would otherwise wipe unsaved edits.
  useEffect(() => { setForm(JSON.parse(savedKey)); }, [savedKey]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setSaving(true);
    setError(null);
    setNotice(null);
    try { await fn(); setNotice(ok); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }

  function save(e: React.FormEvent) {
    e.preventDefault();
    if (!dirty || !form.name.trim() || !form.email.trim()) return;
    run(() => updateCustomer(customer.id, form), 'Saved');
  }

  function resetPassword() {
    const pw = window.prompt(customer.has_password
      ? `New portal password for ${customer.email} (signs them out everywhere):`
      : `Portal password for ${customer.email} (lets them sign in):`);
    if (pw) run(() => updateCustomer(customer.id, { password: pw }), 'Password updated');
  }

  async function remove() {
    const n = customer.projects.length;
    const msg = `Delete ${customer.name}? This can't be undone.` + (n > 0
      ? `\n\nTheir ${n} project${n !== 1 ? 's are' : ' is'} kept but unlinked (the name stays on each project).`
      : '');
    if (!window.confirm(msg)) return;
    setSaving(true);
    setError(null);
    try { await deleteCustomer(customer.id); navigate('/customers'); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setSaving(false); }
  }

  const field = (k: keyof CustomerFields) => ({
    value: form[k],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value }),
  });

  return (
    <form className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }} onSubmit={save}>
      <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>Details</div>
        <span className={`pill ${status.tone}`} style={{ fontSize: 10 }}>{status.label}</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn ghost sm" disabled={saving} onClick={resetPassword}>
          {customer.has_password ? 'Reset password' : 'Set password'}
        </button>
        <button type="button" className="btn ghost sm" disabled={saving}
                onClick={() => run(() => updateCustomer(customer.id, { enabled: !customer.enabled }),
                                   customer.enabled ? 'Portal access disabled' : 'Portal access enabled')}>
          {customer.enabled ? 'Disable' : 'Enable'}
        </button>
        <button type="button" className="btn ghost sm" disabled={saving} onClick={remove}
                style={{ color: 'var(--err)' }}>
          {Icons.trash} Delete
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        <label className="col" style={{ gap: 4 }}><span className="label">Name</span>
          <input className="input" {...field('name')} /></label>
        <label className="col" style={{ gap: 4 }}><span className="label">Email</span>
          <input className="input" type="email" {...field('email')} /></label>
        <label className="col" style={{ gap: 4 }}><span className="label">Company</span>
          <input className="input" {...field('company')} /></label>
        <label className="col" style={{ gap: 4 }}><span className="label">Phone</span>
          <input className="input" type="tel" {...field('phone')} /></label>
      </div>
      <label className="col" style={{ gap: 4 }}><span className="label">Notes</span>
        <textarea className="textarea" rows={3} {...field('notes')} /></label>
      <div className="row gap-2" style={{ alignItems: 'center', justifyContent: 'flex-end' }}>
        {error && <span className="small" style={{ color: 'var(--err)', marginRight: 'auto' }}>{error}</span>}
        {notice && !dirty && <span className="small" style={{ color: 'var(--ok)' }}>{notice}</span>}
        {dirty && (
          <button type="button" className="btn sm" onClick={() => setForm(saved)} disabled={saving}>Discard</button>
        )}
        <button type="submit" className="btn primary sm"
                disabled={!dirty || saving || !form.name.trim() || !form.email.trim()}>
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
      <div className="small muted">Customer since {new Date(customer.created_at).toLocaleDateString()}</div>
    </form>
  );
}

export function CustomerDetailScreen() {
  const { id } = useParams<{ id: string }>();
  const customerId = id ? parseInt(id) : NaN;
  const [customer, setCustomer] = useState<CustomerDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (Number.isNaN(customerId)) return;
    getCustomer(customerId)
      .then(c => { setCustomer(c); setError(null); })
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, [customerId]);

  useEffect(() => { reload(); }, [reload]);

  const current = customer?.id === customerId ? customer : null;
  useTopbarOverride(current?.name, current ? ['Workshop', { label: 'Customers', to: '/customers' }] : undefined);

  if (error && !customer) {
    return <div style={{ padding: 24, color: 'var(--err)' }}>{error}</div>;
  }
  if (!customer) {
    return <div style={{ padding: 24, color: 'var(--text-3)' }}>Loading…</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 1000 }}>
      <DetailsCard customer={customer} onSaved={reload} />
      <FinancialSummaryCard financials={customer.financials} />
      <ProjectsCard customerId={customer.id} projects={customer.projects} />
      <PaymentHistoryCard customerId={customer.id} />
    </div>
  );
}
