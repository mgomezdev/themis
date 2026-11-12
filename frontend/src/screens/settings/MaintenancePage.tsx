// Settings → Maintenance: the maintenance item library.
import { useState, useEffect } from 'react';
import { Icons } from '../../components/icons';
import { PageHeader, Toggle } from '../../components/settingsUi';
import { MaintenanceItemForm, TRIGGER_LABEL, triggerChipText, emptyDraft, draftFromTemplate, type ItemDraft } from '../../components/MaintenanceItemForm';
import {
  useMaintenanceItems,
  createMaintenanceItem,
  updateMaintenanceItem,
  setMaintenanceTriggers,
  deleteMaintenanceItem,
  getMaintenanceTemplates,
  useFleetVendorModels,
  type MaintenanceItem,
  type MaintenanceTemplate,
} from '../../api/maintenance';

function MaintenanceItemRow({ item, onEdit, onDelete, onToggle }: {
  item: MaintenanceItem; onEdit: () => void; onDelete: () => void; onToggle: (v: boolean) => void;
}) {
  return (
    <div className="row gap-3" style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-1)', alignItems: 'center' }}>
      <div className="grow">
        <div className="fw-500">{item.name}</div>
        <div className="tiny muted">
          {item.scope === 'general' ? 'General' : `${item.machine_vendor} ${item.machine_model}`}
        </div>
      </div>
      <div className="row gap-1" style={{ flex: 1, flexWrap: 'wrap' }}>
        {item.triggers.map(t => (
          <span key={t.id} title={TRIGGER_LABEL[t.trigger_type]} style={{
            display: 'inline-flex', alignItems: 'center',
            fontSize: 11.5, padding: '3px 9px', borderRadius: 0,
            background: 'var(--bg-2)', color: 'var(--text-3)', border: '1px solid var(--border-2)',
            whiteSpace: 'nowrap',
          }}>{triggerChipText(t)}</span>
        ))}
      </div>
      <Toggle checked={item.enabled} onChange={onToggle} />
      <button className="btn ghost sm" onClick={onEdit}>Edit</button>
      <button className="btn ghost sm" onClick={onDelete}>Delete</button>
    </div>
  );
}

export function MaintenancePage() {
  const { items, refetch } = useMaintenanceItems();
  const [templates, setTemplates] = useState<MaintenanceTemplate[]>([]);
  const catalog = useFleetVendorModels();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<ItemDraft>(emptyDraft());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMaintenanceTemplates().then(setTemplates).catch(console.error);
  }, []);

  function startCreate(fromTemplate?: MaintenanceTemplate) {
    setDraft(fromTemplate ? draftFromTemplate(fromTemplate) : emptyDraft());
    setEditingId(null);
    setCreating(true);
  }

  function startEdit(item: MaintenanceItem) {
    setDraft({
      name: item.name, scope: item.scope,
      machine_vendor: item.machine_vendor ?? '', machine_model: item.machine_model ?? '',
      triggers: item.triggers,
    });
    setCreating(false);
    setEditingId(item.id);
  }

  async function handleSave() {
    setError(null);
    try {
      if (editingId != null) {
        await updateMaintenanceItem(editingId, {
          name: draft.name, scope: draft.scope,
          machine_vendor: draft.scope === 'model' ? draft.machine_vendor : null,
          machine_model: draft.scope === 'model' ? draft.machine_model : null,
        });
        await setMaintenanceTriggers(editingId, draft.triggers);
      } else {
        await createMaintenanceItem({
          name: draft.name, scope: draft.scope,
          machine_vendor: draft.scope === 'model' ? draft.machine_vendor : null,
          machine_model: draft.scope === 'model' ? draft.machine_model : null,
          triggers: draft.triggers,
        });
      }
      setCreating(false);
      setEditingId(null);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(id: number) {
    setError(null);
    try {
      await deleteMaintenanceItem(id);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleToggle(item: MaintenanceItem, enabled: boolean) {
    setError(null);
    try {
      await updateMaintenanceItem(item.id, { enabled });
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const generalItems = items.filter(i => i.scope === 'general');
  const modelItems = items.filter(i => i.scope === 'model');

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <PageHeader
          title="Maintenance"
          sub="Track recurring maintenance for your fleet. Each item can have multiple triggers — calendar time, print hours, or job count — and is due the moment any one of them fires."
          actions={<button className="btn primary sm" onClick={() => startCreate()}>{Icons.plus} New item</button>}
        />

        {error && (
          <div style={{ marginBottom: 14, padding: '10px 14px', background: 'var(--bg-0)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
            {error}
          </div>
        )}

        {templates.length > 0 && (
          <div className="col gap-2" style={{ marginBottom: 20 }}>
            <div className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>Suggested items</div>
            <div className="row gap-2 wrap">
              {templates.map(t => (
                <button key={t.name} className="btn sm" title={t.description} onClick={() => startCreate(t)}>
                  {Icons.plus} Add {t.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {(creating || editingId != null) && (
          <div style={{ marginBottom: 16 }}>
            <MaintenanceItemForm draft={draft} catalog={catalog} onChange={setDraft}
                                  onSave={handleSave} onCancel={() => { setCreating(false); setEditingId(null); }} />
          </div>
        )}

        <div className="col gap-1">
          <div className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 8 }}>General</div>
          <div style={{ border: '1px solid var(--border-1)', borderRadius: 8, background: 'var(--bg-0)' }}>
            {generalItems.length === 0 && <div className="small muted" style={{ padding: 16 }}>No general maintenance items yet.</div>}
            {generalItems.map(item => (
              <MaintenanceItemRow key={item.id} item={item} onEdit={() => startEdit(item)}
                                  onDelete={() => handleDelete(item.id)}
                                  onToggle={v => handleToggle(item, v)} />
            ))}
          </div>

          <div className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 16 }}>Model-specific</div>
          <div style={{ border: '1px solid var(--border-1)', borderRadius: 8, background: 'var(--bg-0)' }}>
            {modelItems.length === 0 && <div className="small muted" style={{ padding: 16 }}>No model-specific items yet.</div>}
            {modelItems.map(item => (
              <MaintenanceItemRow key={item.id} item={item} onEdit={() => startEdit(item)}
                                  onDelete={() => handleDelete(item.id)}
                                  onToggle={v => handleToggle(item, v)} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// =========================================================================
// API Keys page
// =========================================================================
