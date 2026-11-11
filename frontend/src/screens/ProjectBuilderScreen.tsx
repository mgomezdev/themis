// Create or edit a project (`/projects/new`, `/projects/:id/edit`): assemble its parts from the STL library,
// links, non-printed parts and header fields, save, and generate print jobs. State lives in useProjectDraft
// (lib/projectBuilderState); the sections are components/projectBuilder/*.
import { useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { parseGenerateError } from '../lib/projectDraft';
import type { HeaderFields } from '../lib/projectBuilderState';
import { useAutoDismiss } from '../components/useAutoDismiss';
import { useProjectDraft } from '../components/projectBuilder/useProjectDraft';
import { StlPicker } from '../components/projectBuilder/StlPicker';
import { ProjectMetaFields } from '../components/projectBuilder/ProjectMetaFields';
import { ProjectLinksEditor } from '../components/projectBuilder/ProjectLinksEditor';
import { ProjectPartsEditor } from '../components/projectBuilder/ProjectPartsEditor';
import { ProjectItemsTable } from '../components/projectBuilder/ProjectItemsTable';
import { GenerateError, GeneratePicker, GenerateSuccess } from '../components/projectBuilder/GeneratePanel';
import { SavedToast, UndoToast } from '../components/projectBuilder/Toasts';
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
  const { name, items } = draft;
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

  const { files: allFiles } = useFiles({});

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
      <StlPicker files={allFiles} onAdd={file => dispatch({ type: 'addFile', file })} />

      {/* Right: project form + items */}
      <div className="card" style={{ padding: 20, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <ProjectMetaFields fields={draft} projectId={projectId} onChange={setFields} />
        <ProjectLinksEditor links={draft.links} dispatch={dispatch} />
        <ProjectPartsEditor parts={draft.parts} dispatch={dispatch} />
        <ProjectItemsTable
          items={items} serverItems={serverItems} materials={materials} catalogEnabled={catalogEnabled} dispatch={dispatch}
        />

        {/* Printer picker (shown when Create… is clicked) */}
        {showPrinterPicker && !generating && !saving && (
          <GeneratePicker
            eligiblePrinterIds={eligiblePrinterIds} setEligiblePrinterIds={setEligiblePrinterIds}
            processPreset={processPreset} setProcessPreset={setProcessPreset}
            disabled={!canSave || items.length === 0}
            onCancel={() => setShowPrinterPicker(false)}
            onGenerate={handleGenerate}
          />
        )}
        {generateResult && (
          <GenerateSuccess
            jobCount={generateResult.jobCount}
            onOpenQueue={() => navigate('/queue')}
            onOpenProject={projectId ? () => navigate(`/projects/${projectId}`) : undefined}
          />
        )}
        {generateError && (
          <GenerateError message={generateError} busy={generating || saving} onRetry={() => handleGenerate(eligiblePrinterIds)} />
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

      {saveOk && <SavedToast />}
      {lastRemoved && <UndoToast label={lastRemoved.label || lastRemoved.kind} onUndo={() => dispatch({ type: 'undo' })} />}
    </div>
  );
}
