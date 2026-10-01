import { useCallback, useEffect, useState } from 'react';
import { fmtDate, fmtMoney } from '../data/helpers';
import {
  PAYMENT_METHODS, addPayment, deletePayment, listPayments, methodLabel,
  type PaymentMethod, type ProjectPayment,
} from '../api/payments';

const todayLocal = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/** A project's payments: what came in, when and how. Amount paid and payment status are derived from these. */
export function PaymentsCard({ projectId, price, onChanged }: {
  projectId: number; price: number | null; onChanged: () => void;
}) {
  const [payments, setPayments] = useState<ProjectPayment[] | null>(null);
  const [amount, setAmount] = useState('');
  const [receivedOn, setReceivedOn] = useState(todayLocal);
  const [method, setMethod] = useState<PaymentMethod>('cash');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    listPayments(projectId).then(rows => setPayments(Array.isArray(rows) ? rows : [])).catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, [projectId]);
  useEffect(() => { load(); }, [load]);

  const amountNum = Number(amount);
  const canAdd = !busy && amount.trim() !== '' && amountNum > 0 && !!receivedOn;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!canAdd) return;
    setBusy(true); setError('');
    try {
      await addPayment(projectId, { amount: amountNum, received_on: receivedOn, method, note: note.trim() || null });
      setAmount(''); setNote('');
      load(); onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  }

  async function remove(p: ProjectPayment) {
    if (!window.confirm(`Delete the ${fmtMoney(p.amount)} payment from ${fmtDate(p.received_on)}?`)) return;
    setError('');
    try { await deletePayment(projectId, p.id); load(); onChanged(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }

  const total = (payments ?? []).reduce((s, p) => s + p.amount, 0);

  return (
    <div className="card" style={{ padding: 20 }} data-testid="payments-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
          Payments ({payments?.length ?? 0})
        </div>
        <div className="small muted" data-testid="payments-total">
          Received {fmtMoney(total)}{price != null ? ` of ${fmtMoney(price)}` : ''}
        </div>
      </div>

      {payments !== null && payments.length === 0 ? (
        <div style={{ color: 'var(--text-4)', fontSize: 13, marginBottom: 12 }}>No payments recorded yet.</div>
      ) : (
        <div style={{ overflowX: 'auto', marginBottom: 12 }}>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Method</th><th>Note</th><th style={{ textAlign: 'right' }}>Amount</th><th /></tr></thead>
            <tbody>
              {(payments ?? []).map(p => (
                <tr key={p.id} data-testid={`payment-${p.id}`}>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(p.received_on)}</td>
                  <td>{methodLabel(p.method)}</td>
                  <td className="muted">{p.note ?? ''}</td>
                  <td style={{ textAlign: 'right' }}>{fmtMoney(p.amount)}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="btn ghost sm" aria-label={`Delete payment of ${fmtMoney(p.amount)}`}
                            onClick={() => remove(p)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form onSubmit={add} className="row gap-2" style={{ flexWrap: 'wrap', alignItems: 'end' }}>
        <label className="col" style={{ gap: 4 }}>
          <span className="label">Amount</span>
          <input className="input" type="number" min="0" step="0.01" placeholder="0.00" style={{ width: 110 }}
                 value={amount} onChange={e => setAmount(e.target.value)} aria-label="Payment amount" />
        </label>
        <label className="col" style={{ gap: 4 }}>
          <span className="label">Received</span>
          <input className="input" type="date" value={receivedOn} max={todayLocal()}
                 onChange={e => setReceivedOn(e.target.value)} aria-label="Date received" />
        </label>
        <label className="col" style={{ gap: 4 }}>
          <span className="label">Method</span>
          <select className="select" value={method} onChange={e => setMethod(e.target.value as PaymentMethod)} aria-label="Payment method">
            {PAYMENT_METHODS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
        <label className="col" style={{ gap: 4, flex: '1 1 160px' }}>
          <span className="label">Note</span>
          <input className="input" placeholder="e.g. deposit" value={note} maxLength={500}
                 onChange={e => setNote(e.target.value)} aria-label="Payment note" />
        </label>
        <button className="btn primary sm" type="submit" disabled={!canAdd}>Add payment</button>
      </form>
      {error && <div role="alert" style={{ color: 'var(--err)', fontSize: 12, marginTop: 8 }}>{error}</div>}
    </div>
  );
}
