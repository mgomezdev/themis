import { useEffect, useReducer } from 'react';
import { getProject } from '../../api/projects';
import { syncProject } from '../../api/projectSync';
import { draftSnapshot } from '../../lib/projectDraft';
import { builderReducer, initialBuilderState, isDirty, withServerIds } from '../../lib/projectBuilderState';
import { useAutoDismiss } from '../useAutoDismiss';

/** How long the "Removed … Undo" toast stays up. */
export const UNDO_TIMEOUT_MS = 6000;

/**
 * The Project builder's state (`lib/projectBuilderState`), loaded from the server for an existing project, with
 * the undo toast timing out by itself and `save()` persisting the draft.
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

  useAutoDismiss(state.lastRemoved, () => dispatch({ type: 'dismissUndo' }), UNDO_TIMEOUT_MS);

  /**
   * Persist the draft as it is now; resolves to the project id. Each created/deleted row is recorded as its
   * request succeeds (so a retry after a partial failure doesn't repeat it), and on success what was saved
   * becomes the clean baseline. Edits made while the save runs stay unsaved.
   */
  async function save(): Promise<number> {
    const saving = state.draft;
    const ids = new Map<string, number>();
    const pid = await syncProject(projectId, saving, state.deleted, event => {
      if (event.type === 'created') ids.set(event.localId, event.id);
      dispatch({ type: 'synced', event });
    });
    dispatch({
      type: 'saved',
      snap: draftSnapshot({
        ...saving,
        items: withServerIds(saving.items, ids), links: withServerIds(saving.links, ids), parts: withServerIds(saving.parts, ids),
      }),
    });
    return pid;
  }

  return { state, dispatch, isDirty: isDirty(state, projectId != null), save };
}
