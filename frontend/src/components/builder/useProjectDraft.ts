import { useEffect, useReducer } from 'react';
import { getProject } from '../../api/projects';
import { syncProject } from '../../api/projectSync';
import { draftSnapshot } from '../../lib/projectDraft';
import { builderReducer, initialBuilderState, isDirty } from '../../lib/projectBuilderState';
import { useAutoDismiss } from '../useAutoDismiss';

/** How long the "Removed … Undo" toast stays up. */
export const UNDO_TIMEOUT_MS = 6000;

/**
 * The Project Builder's draft state (`lib/projectBuilderState`), loaded from the server for an existing project,
 * with the undo toast timing out by itself and `save()` persisting the draft.
 */
export function useProjectDraft(projectId: number | null, newForCustomerId: number | null) {
  const [state, dispatch] = useReducer(builderReducer, newForCustomerId, initialBuilderState);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    getProject(projectId)
      .then(project => { if (alive) dispatch({ type: 'loaded', project }); })
      .catch(console.error);
    return () => { alive = false; };
  }, [projectId]);

  // Keyed on the removal count, so a save that updates the undo snapshot doesn't restart the clock.
  useAutoDismiss(state.lastRemoved ? state.removalSeq : null, () => dispatch({ type: 'dismissUndo' }), UNDO_TIMEOUT_MS);

  /**
   * Persist the draft as it is now; resolves to the project id. Each created/deleted row is recorded as its
   * request succeeds (so a retry after a partial failure doesn't repeat it), and on success what was saved
   * becomes the clean baseline. Edits made while the save runs stay unsaved.
   */
  async function save(): Promise<number> {
    const saving = state.draft;
    const pid = await syncProject(projectId, saving, state.deleted, event => dispatch({ type: 'synced', event }));
    dispatch({ type: 'saved', snap: draftSnapshot(saving) });
    return pid;
  }

  return { state, dispatch, isDirty: isDirty(state, !!projectId), save };
}
