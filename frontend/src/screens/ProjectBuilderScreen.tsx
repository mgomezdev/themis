import { useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { FilamentAskEditor } from '../components/FilamentAskEditor';
import { buildStlTree, isPickableStl, stlFilesInView } from '../lib/stlTree';
import { askOf, parseGenerateError } from '../lib/projectDraft';
import type { HeaderFields } from '../lib/projectBuilderState';
import { StlFolderTree } from '../components/StlFolderTree';
import { PrinterEligibilityPicker } from '../components/PrinterEligibilityPicker';
import { ProcessPresetPicker } from '../components/ProcessPresetPicker';
import { CustomerPicker } from '../components/CustomerPicker';
import { useAutoDismiss } from '../components/useAutoDismiss';
import { useProjectDraft } from '../components/projectBuilder/useProjectDraft';
import { useFiles } from '../api/files';
import { useInventory, useMaterials } from '../api/inventory';
import { generateProject } from '../api/projects';

// ---------------------------------------------------------------------------
// ProjectBuilderScreen
// ---------------------------------------------------------------------------

export function ProjectBuilderScreen() {
  const { id } = useParams<{ id: string }>();
  // "New project" from a customer's page links here with ?customer=<id>; used on create only.
  const [searchParams] = useSearchParams();
  const newForCustomerId = id ? null : Number(searchParams.get('customer')) || null;
  const projectId = id ? parseInt(id) : null;
  const navigate = useNavigate();

  // Inventory catalog (materials), when a provider is active
  const inventory = useInventory();
  const catalogEnabled = !!inventory.plugin;
  const materials = useMaterials(catalogEnabled);

  const { state, dispatch, isDirty, save } = useProjectDraft(projectId, newForCustomerId);
  const { draft, lastRemoved, serverItems, isDraftStage: isDraft } = state;
  const { name, customer, customerId, orderType, onHold, dueDate, notes, price, items, links, parts } = draft;
  const setFields = (fields: Partial<HeaderFields>) => dispatch({ type: 'setFields', fields });

  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generateResult, setGenerateResult] = useState<{ jobCount: number } | null>(null);
  const [showPrinterPicker, setShowPrinterPicker] = useState(false);
  const [eligiblePrinterIds, setEligiblePrinterIds] = useState<number[]>([]);
  const [processPreset, setProcessPreset] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState(false);
  useAutoDismiss(saveOk, () => setSaveOk(false), 2000);

  // File tree state
  const { files: allFiles } = useFiles({});
  const stlFiles = allFiles.filter(isPickableStl);
  const [selectedFolder, setSelectedFolder] = useState('');
  const [search, setSearch] = useState('');
  const searchLower = search.trim().toLowerCase();
  const folderTree = buildStlTree(stlFiles);
  const folderFiles = stlFilesInView(stlFiles, selectedFolder, search);

  async function handleSave() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const pid = await save();
      setSaveOk(true);
      if (!projectId) navigate(`/projects/${pid}`, { replace: true });
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }

  async function handleGenerate(printerIds: number[]) {
    if (!name.trim() || items.length === 0) return;
    setSaving(true);
    setGenerateError('');
    setGenerateResult(null);
    setShowPrinterPicker(false);
    try {
      const pid = await save();
      // Stay in the builder (its /edit route keeps this component mounted) so the generate result or
      // error is shown here and a retry reuses the saved project instead of creating a second one.
      if (!projectId) navigate(`/projects/${pid}/edit`, { replace: true });
      setGenerating(true);
      const result = await generateProject(pid, printerIds, processPreset);
      setGenerateResult({ jobCount: result.jobs.length });
    } catch (e) {
      setGenerateError(parseGenerateError(e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
      setGenerating(false);
    }
  }

  const canSave = name.trim().length > 0 && !saving && !generating;
  const showSave = isDirty && canSave;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, height: '100%' }}>
      {/* Left: STL file tree */}
      <div className="card" style={{ padding: 10, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 0 }}>
        <div style={{ padding: '4px 6px 8px', fontSize: 11, fontWeight: 600,
                      color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          STL Files
        </div>
        <input
          className="input"
          placeholder="Search files…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ marginBottom: 6, fontSize: 12 }}
        />
        {!searchLower && (
          <StlFolderTree node={folderTree} depth={0} selected={selectedFolder} onSelect={setSelectedFolder} />
        )}
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 6 }}>
          {folderFiles.length === 0 && (
            <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--text-4)' }}>
              {searchLower ? 'No matching STL files' : 'No STL files here'}
            </div>
          )}
          {folderFiles.map(f => (
            <button
              key={f.id}
              onClick={() => dispatch({ type: 'addFile', file: f })}
              title={`Add ${f.original_filename}`}
              style={{
                width: '100%', padding: '5px 10px',
                background: 'transparent', border: 'none',
                color: 'var(--text-2)', cursor: 'pointer',
                textAlign: 'left', fontSize: 12,
                display: 'flex', alignItems: 'center', gap: 6, borderRadius: 4,
              }}
            >
              <span style={{ color: 'var(--text-4)', flexShrink: 0 }}>{Icons.files}</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {f.original_filename}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Right: project form + items */}
      <div className="card" style={{ padding: 20, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 16 }}>

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
              onChange={e => setFields({ name: e.target.value })}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Type</label>
            <select
              className="select"
              value={orderType}
              onChange={e => setFields({ orderType: e.target.value as HeaderFields['orderType'] })}
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
                onChange={e => setFields({ dueDate: e.target.value })}
                style={{ flex: 1 }}
              />
              {dueDate && (
                <button
                  className="btn ghost icon sm"
                  onClick={() => setFields({ dueDate: '' })}
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
                onChange={v => setFields({ customerId: v.customerId, customer: v.customerText })}
              />
            </div>
          )}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Notes</label>
            <input
              className="input"
              placeholder="Optional notes"
              value={notes}
              onChange={e => setFields({ notes: e.target.value })}
            />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                          fontSize: 13, color: onHold ? 'var(--warn)' : 'var(--text-2)',
                          paddingBottom: 4, whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={onHold}
              onChange={e => setFields({ onHold: e.target.checked })}
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
              onChange={e => setFields({ price: e.target.value })}
            />
          </div>
          <div className="small muted" style={{ flex: 2, paddingBottom: 6 }}>
            {projectId
              ? 'Payments received are recorded on the project page — amount paid and payment status follow from them.'
              : 'Record payments on the project page once it is saved.'}
          </div>
        </div>

        {/* Links editor */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
              Links ({links.length})
            </span>
            <button
              className="btn ghost sm"
              onClick={() => dispatch({ type: 'addLink' })}
            >
              + Add link
            </button>
          </div>
          {links.map(lk => (
            <div key={lk.localId} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input
                className="input"
                placeholder="https://..."
                value={lk.url}
                onChange={e => dispatch({ type: 'updateLink', localId: lk.localId, patch: { url: e.target.value } })}
                style={{ flex: 2 }}
              />
              <input
                className="input"
                placeholder="Label (optional)"
                value={lk.label}
                onChange={e => dispatch({ type: 'updateLink', localId: lk.localId, patch: { label: e.target.value } })}
                style={{ flex: 1 }}
              />
              <button
                className="btn ghost icon sm"
                onClick={() => dispatch({ type: 'remove', kind: 'link', localId: lk.localId })}
                title="Remove link"
              >
                {Icons.x}
              </button>
            </div>
          ))}
        </div>

        {/* Non-printed parts editor (hardware, e.g. magnets/screws) */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
              Non-printed parts ({parts.length})
            </span>
            <button
              className="btn ghost sm"
              onClick={() => dispatch({ type: 'addPart' })}
            >
              + Add part
            </button>
          </div>
          {parts.map(pt => (
            <div key={pt.localId} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input
                className="input"
                placeholder="e.g. 3mm magnet"
                value={pt.name}
                onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { name: e.target.value } })}
                style={{ flex: 2 }}
              />
              <input
                type="number"
                className="input"
                min={1}
                value={pt.quantity}
                onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { quantity: Math.max(1, parseInt(e.target.value) || 1) } })}
                style={{ width: 64, textAlign: 'center' }}
              />
              <input
                type="number"
                className="input"
                min={0}
                step="0.01"
                placeholder="$ each"
                aria-label="Unit cost"
                value={pt.unitCost}
                onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { unitCost: e.target.value } })}
                style={{ width: 84 }}
              />
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                              fontSize: 12, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                <input
                  type="checkbox"
                  checked={pt.allocated}
                  onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { allocated: e.target.checked } })}
                />
                Allocated
              </label>
              <button
                className="btn ghost icon sm"
                onClick={() => dispatch({ type: 'remove', kind: 'part', localId: pt.localId })}
                title="Remove part"
              >
                {Icons.x}
              </button>
            </div>
          ))}
        </div>

        {/* Part items table */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
              Parts ({items.length})
            </span>
          </div>
          {items.length === 0 ? (
            <div style={{
              border: '1px dashed var(--border)', borderRadius: 6,
              padding: '24px 16px', textAlign: 'center',
              color: 'var(--text-4)', fontSize: 13,
            }}>
              Click an STL file on the left to add parts
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {/* Column headers */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: '1fr 64px 340px auto',
                gap: 8, padding: '0 4px',
                fontSize: 11, fontWeight: 500, color: 'var(--text-4)',
                textTransform: 'uppercase', letterSpacing: '0.05em',
              }}>
                <span>File</span>
                <span>Qty</span>
                <span>Filament</span>
                <span />
              </div>
              {items.map((it) => (
                <div key={it.localId} style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 64px 340px auto',
                  gap: 8, alignItems: 'start',
                }}>
                  {/* File name + progress */}
                  <span style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, paddingTop: 4 }}>
                    <span style={{
                      fontSize: 13, color: 'var(--text-1)',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }} title={it.file_name}>
                      {it.file_name}
                    </span>
                    {it.serverId && (() => {
                      const si = serverItems.get(it.serverId!);
                      if (!si || (si.quantity_completed === 0 && si.quantity_failed === 0)) return null;
                      return (
                        <span style={{ fontSize: 11, color: si.quantity_failed > 0 ? 'var(--err)' : 'var(--text-3)', whiteSpace: 'nowrap' }}>
                          {si.quantity_completed}/{si.quantity_completed + si.quantity_failed} ok
                          {si.quantity_failed > 0 && ` · ${si.quantity_failed} failed`}
                        </span>
                      );
                    })()}
                  </span>

                  {/* Quantity */}
                  <input
                    type="number"
                    className="input"
                    min={1} max={99}
                    value={it.quantity}
                    onChange={e => dispatch({ type: 'updateItem', localId: it.localId, patch: { quantity: Math.max(1, parseInt(e.target.value) || 1) } })}
                    style={{ textAlign: 'center' }}
                  />

                  {/* Filament requirement */}
                  <FilamentAskEditor
                    id={`item-ask-${it.localId}`}
                    value={askOf(it)}
                    onChange={req => dispatch({ type: 'updateItem', localId: it.localId, patch: askOf(req) })}
                    materials={materials}
                    catalogEnabled={catalogEnabled}
                  />

                  {/* Apply to all + Remove */}
                  <span style={{ display: 'flex', gap: 4 }}>
                    {items.length > 1 && (
                      <button
                        className="btn ghost icon sm"
                        onClick={() => dispatch({ type: 'applyFilamentToAll', ask: askOf(it) })}
                        title="Apply this filament to every part"
                      >
                        {Icons.copy}
                      </button>
                    )}
                    <button
                      className="btn ghost icon sm"
                      onClick={() => dispatch({ type: 'remove', kind: 'item', localId: it.localId })}
                      title="Remove"
                    >
                      {Icons.x}
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Printer picker (shown when Create… is clicked) */}
        {showPrinterPicker && !generating && !saving && (
          <div style={{
            border: '1px solid var(--border)', borderRadius: 8,
            padding: '14px 16px', background: 'var(--bg-2)',
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-3)',
                          textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
              Eligible printers
            </div>
            <PrinterEligibilityPicker selected={eligiblePrinterIds} onChange={setEligiblePrinterIds} />
            <div style={{ marginTop: 12 }}>
              <ProcessPresetPicker printerIds={eligiblePrinterIds} value={processPreset} onChange={setProcessPreset} />
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
              <button className="btn sm" onClick={() => setShowPrinterPicker(false)}>Cancel</button>
              <button
                className={eligiblePrinterIds.length === 0 ? 'btn sm' : 'btn primary sm'}
                onClick={() => handleGenerate(eligiblePrinterIds)}
                disabled={!canSave || items.length === 0}
                title={eligiblePrinterIds.length === 0
                  ? 'No printers selected — jobs will be created but not dispatched'
                  : undefined}
              >
                {eligiblePrinterIds.length === 0 ? 'Create without dispatch' : 'Create'}
              </button>
            </div>
          </div>
        )}

        {/* Generation success */}
        {generateResult && (
          <div style={{
            border: '1px solid var(--ok)', borderRadius: 8,
            padding: '12px 16px', background: 'oklch(55% 0.15 142 / 0.06)',
            display: 'flex', alignItems: 'center', gap: 12,
          }}>
            <span style={{ color: 'var(--ok)', flexShrink: 0 }}>{Icons.check}</span>
            <span style={{ fontSize: 14, flex: 1 }}>
              {generateResult.jobCount} job{generateResult.jobCount !== 1 ? 's' : ''} added to queue
            </span>
            <button className="btn sm" onClick={() => navigate('/queue')}>Queue</button>
            {projectId && (
              <button className="btn sm" onClick={() => navigate(`/projects/${projectId}`)}>
                Details
              </button>
            )}
          </div>
        )}

        {/* Error banner */}
        {generateError && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
            borderRadius: 6, padding: '10px 14px',
          }}>
            <span style={{ color: 'var(--err)', flexShrink: 0 }}>{Icons.alert}</span>
            <span style={{ fontSize: 13, color: 'var(--err)', flex: 1 }}>{generateError}</span>
            <button className="btn sm" onClick={() => handleGenerate(eligiblePrinterIds)} disabled={generating || saving}>Retry</button>
          </div>
        )}

        {/* Footer actions */}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 'auto', paddingTop: 4 }}>
          <button className="btn sm" onClick={() => navigate(projectId ? `/projects/${projectId}` : '/projects')} disabled={saving || generating}>
            Cancel
          </button>
          {showSave && (
            <button className="btn sm" onClick={handleSave} disabled={saving}>
              {saving && !generating ? 'Saving…' : (projectId ? 'Save changes' : 'Save')}
            </button>
          )}
          <button
            className="btn primary sm"
            onClick={() => { setShowPrinterPicker(v => !v); setGenerateError(''); setGenerateResult(null); }}
            disabled={!canSave || items.length === 0 || isDraft}
            title={isDraft ? 'Promote to planning before creating jobs' : undefined}
          >
            {generating ? 'Creating…' : 'Create…'}
          </button>
        </div>
      </div>

      {/* Saved toast */}
      {saveOk && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 1000,
          background: 'var(--ok)', color: '#fff',
          padding: '10px 18px', borderRadius: 8,
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 14, fontWeight: 500,
          boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
          pointerEvents: 'none',
        }}>
          {Icons.check} Saved
        </div>
      )}

      {/* Undo-removal toast */}
      {lastRemoved && (
        <div style={{
          position: 'fixed', bottom: 24, left: 24, zIndex: 1000,
          background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--text-1)',
          padding: '10px 12px 10px 16px', borderRadius: 8,
          display: 'flex', alignItems: 'center', gap: 10,
          fontSize: 13, boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
        }}>
          <span>Removed {lastRemoved.label || lastRemoved.kind}</span>
          <button className="btn sm" onClick={() => dispatch({ type: 'undo' })}>Undo</button>
        </div>
      )}
    </div>
  );
}
