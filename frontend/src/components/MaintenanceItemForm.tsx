// frontend/src/components/MaintenanceItemForm.tsx
import { Icons } from './icons';
import type { MaintenanceTrigger, MaintenanceTemplate } from '../api/maintenance';
import type { FleetVendorModel } from '../api/maintenance';

export const TRIGGER_LABEL: Record<MaintenanceTrigger['trigger_type'], string> = {
  calendar: 'Calendar', job_time: 'Print time', job_count: 'Job count',
};

export function triggerChipText(t: MaintenanceTrigger): string {
  if (t.trigger_type === 'job_count') return `${t.amount} jobs`;
  if (t.trigger_type === 'job_time') return `${t.amount}h operating`;
  return `${t.amount} ${t.unit ?? 'months'}`;
}

export interface ItemDraft {
  name: string;
  scope: 'general' | 'model';
  machine_vendor: string;
  machine_model: string;
  triggers: MaintenanceTrigger[];
}

export function emptyDraft(): ItemDraft {
  return { name: '', scope: 'general', machine_vendor: '', machine_model: '', triggers: [] };
}

export function draftFromTemplate(t: MaintenanceTemplate): ItemDraft {
  return { name: t.name, scope: 'general', machine_vendor: '', machine_model: '', triggers: t.triggers };
}

export function TriggerRow({ trigger, onChange, onRemove }: {
  trigger: MaintenanceTrigger; onChange: (t: MaintenanceTrigger) => void; onRemove: () => void;
}) {
  return (
    <div className="row gap-2" style={{ alignItems: 'center' }}>
      <select className="select sm" value={trigger.trigger_type}
              onChange={e => onChange({ ...trigger, trigger_type: e.target.value as MaintenanceTrigger['trigger_type'], unit: e.target.value === 'calendar' ? 'months' : null })}>
        <option value="calendar">Calendar</option>
        <option value="job_time">Print time (hours)</option>
        <option value="job_count">Job count</option>
      </select>
      <input type="number" min={0} className="input sm" style={{ width: 80 }}
             value={trigger.amount}
             onChange={e => onChange({ ...trigger, amount: Number(e.target.value) })} />
      {trigger.trigger_type === 'calendar' && (
        <select className="select sm" value={trigger.unit ?? 'months'}
                onChange={e => onChange({ ...trigger, unit: e.target.value as MaintenanceTrigger['unit'] })}>
          <option value="hours">hours</option>
          <option value="days">days</option>
          <option value="weeks">weeks</option>
          <option value="months">months</option>
        </select>
      )}
      <button type="button" className="btn ghost icon sm" onClick={onRemove}>{Icons.x}</button>
    </div>
  );
}

export function MaintenanceItemForm({ draft, catalog, onChange, onSave, onCancel }: {
  draft: ItemDraft; catalog: FleetVendorModel[];
  onChange: (d: ItemDraft) => void; onSave: () => void; onCancel: () => void;
}) {
  const vendors = Array.from(new Set(catalog.map(c => c.vendor))).sort();
  const models = Array.from(new Set(catalog.filter(c => c.vendor === draft.machine_vendor).map(c => c.printer_model))).sort();

  return (
    <div className="col gap-2" style={{ padding: 16, border: '1px solid var(--border-2)', borderRadius: 8, background: 'var(--bg-2)' }}>
      <input className="input" placeholder="Maintenance item name" value={draft.name}
             onChange={e => onChange({ ...draft, name: e.target.value })} />

      <div className="row gap-2">
        <label className="row gap-1" style={{ alignItems: 'center' }}>
          <input type="radio" checked={draft.scope === 'general'}
                 onChange={() => onChange({ ...draft, scope: 'general' })} />
          General (any printer)
        </label>
        <label className="row gap-1" style={{ alignItems: 'center' }}>
          <input type="radio" checked={draft.scope === 'model'}
                 onChange={() => onChange({ ...draft, scope: 'model' })} />
          Model-specific
        </label>
      </div>

      {draft.scope === 'model' && (
        <div className="col gap-1">
          <div className="row gap-2">
            <select className="select" value={draft.machine_vendor}
                    onChange={e => onChange({ ...draft, machine_vendor: e.target.value, machine_model: '' })}>
              <option value="">Vendor…</option>
              {vendors.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
            <select className="select" value={draft.machine_model} disabled={!draft.machine_vendor}
                    onChange={e => onChange({ ...draft, machine_model: e.target.value })}>
              <option value="">Model…</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          {catalog.length === 0 && (
            <div className="tiny muted">No printers in your fleet have a resolved machine profile yet.</div>
          )}
        </div>
      )}

      <div className="col gap-1">
        <div className="tiny muted">Triggers (due on whichever fires first)</div>
        {draft.triggers.map((t, i) => (
          <TriggerRow key={i} trigger={t}
                      onChange={next => onChange({ ...draft, triggers: draft.triggers.map((x, j) => j === i ? next : x) })}
                      onRemove={() => onChange({ ...draft, triggers: draft.triggers.filter((_, j) => j !== i) })} />
        ))}
        <button type="button" className="btn ghost sm" style={{ alignSelf: 'flex-start' }}
                onClick={() => onChange({ ...draft, triggers: [...draft.triggers, { trigger_type: 'job_count', amount: 10, unit: null }] })}>
          {Icons.plus} Add trigger
        </button>
      </div>

      <div className="row gap-2">
        <button className="btn primary sm" onClick={onSave}>Save</button>
        <button className="btn sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
