// The Builder's header fields (Details tab): name, type, due date, customer, notes, on hold, price.
import { Icons } from '../icons';
import { CustomerPicker } from '../CustomerPicker';
import type { OrderType } from '../../lib/projectDraft';
import type { HeaderFields } from '../../lib/projectBuilderState';

export function ProjectMetaFields({ fields, projectId, onChange }: {
  fields: HeaderFields;
  /** Shown next to the name, and picks the payments hint; null for a new project. */
  projectId: number | null;
  onChange: (patch: Partial<HeaderFields>) => void;
}) {
  const { name, customer, customerId, orderType, onHold, dueDate, notes, price } = fields;
  return (
    <>
      {/* Project metadata fields */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label className="label" htmlFor="proj-name">
          Project name *
          {projectId && (
            <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text-4)', marginLeft: 6 }}>
              #{projectId}
            </span>
          )}
        </label>
          <input
            id="proj-name"
            className="input"
            placeholder="e.g. Gridfinity Tray Set"
            value={name}
            onChange={e => onChange({ name: e.target.value })}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label className="label">Type</label>
          <select
            className="select"
            value={orderType}
            onChange={e => onChange({ orderType: e.target.value as OrderType })}
          >
            <option value="internal">Internal</option>
            <option value="customer">Customer</option>
          </select>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label className="label">Due date</label>
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <input
              type="date"
              className="input"
              value={dueDate}
              onChange={e => onChange({ dueDate: e.target.value })}
              style={{ flex: 1 }}
            />
            {dueDate && (
              <button
                className="btn ghost icon sm"
                onClick={() => onChange({ dueDate: '' })}
                title="Clear due date"
                style={{ flexShrink: 0 }}
              >
                {Icons.x}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Customer + notes row */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
        {orderType === 'customer' && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Customer</label>
            <CustomerPicker
              value={{ customerId, customerText: customer }}
              onChange={v => onChange({ customerId: v.customerId, customer: v.customerText })}
            />
          </div>
        )}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label className="label">Notes</label>
          <input
            className="input"
            placeholder="Optional notes"
            value={notes}
            onChange={e => onChange({ notes: e.target.value })}
          />
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                        fontSize: 13, color: onHold ? 'var(--warn)' : 'var(--text-1)',
                        paddingBottom: 4, whiteSpace: 'nowrap' }}>
          <input
            type="checkbox"
            checked={onHold}
            onChange={e => onChange({ onHold: e.target.checked })}
          />
          On hold
        </label>
      </div>

      {/* Payment row */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label className="label" htmlFor="proj-price">Price</label>
          <input
            id="proj-price"
            type="number" min="0" step="0.01"
            className="input"
            placeholder="Quoted total"
            value={price}
            onChange={e => onChange({ price: e.target.value })}
          />
        </div>
        <div className="small muted" style={{ flex: 2, paddingBottom: 6 }}>
          {projectId
            ? 'Payments received are recorded on the project page — amount paid and payment status follow from them.'
            : 'Record payments on the project page once it is saved.'}
        </div>
      </div>
    </>
  );
}
