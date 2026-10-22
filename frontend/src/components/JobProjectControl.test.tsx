import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { JobProjectControl } from './JobProjectControl';
import { stubFetch, Reply } from '../test/fetchStub';

afterEach(() => vi.unstubAllGlobals());

const proj = (id: number, name: string, stage: string) => ({ id, name, stage });
const job = (project_id: number | null) => ({ id: 7, project_id, order_id: null });

function setup(projectId: number | null, routes: Record<string, unknown>, onChanged = vi.fn()) {
  const api = stubFetch(routes);
  render(
    <MemoryRouter><JobProjectControl jobId={7} projectId={projectId} projectName="Bracket run" onChanged={onChanged} /></MemoryRouter>,
  );
  return { api, onChanged };
}

describe('JobProjectControl', () => {
  it('links to an existing project, offering only non-draft ones', async () => {
    const user = userEvent.setup();
    const { api, onChanged } = setup(null, {
      'GET /api/v1/projects': [proj(1, 'Draft thing', 'draft'), proj(2, 'Shelf', 'queued')],
      'PATCH /api/v1/jobs/7/project': job(2),
    });
    await user.click(screen.getByText('Link to project…'));
    await waitFor(() => expect(screen.getByRole('option', { name: 'Shelf' })).toBeTruthy());
    expect(screen.queryByRole('option', { name: 'Draft thing' })).toBeNull();
    await user.selectOptions(screen.getByLabelText('Existing project'), '2');
    await user.click(screen.getByRole('button', { name: 'Link' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(job(2)));
    expect(api.to('PATCH', '/api/v1/jobs/7/project')[0].body).toEqual({ project_id: 2 });
  });

  it('creates a new project then links the job to it', async () => {
    const user = userEvent.setup();
    const { api, onChanged } = setup(null, {
      'GET /api/v1/projects': [],
      'POST /api/v1/projects': proj(9, 'Fresh', 'queued'),
      'PATCH /api/v1/jobs/7/project': job(9),
    });
    await user.click(screen.getByText('Link to project…'));
    await user.type(screen.getByLabelText('New project name'), '  Fresh ');
    await user.click(screen.getByRole('button', { name: 'Create & link' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(job(9)));
    expect((api.to('POST', '/api/v1/projects')[0].body as { name: string }).name).toBe('Fresh');
    expect(api.to('PATCH', '/api/v1/jobs/7/project')[0].body).toEqual({ project_id: 9 });
  });

  it('shows the linked project and unlinks it', async () => {
    const user = userEvent.setup();
    const { api, onChanged } = setup(3, { 'PATCH /api/v1/jobs/7/project': job(null) });
    expect(screen.getByText('Bracket run')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Unlink' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(job(null)));
    expect(api.to('PATCH', '/api/v1/jobs/7/project')[0].body).toEqual({ project_id: null });
  });

  it('surfaces the server error and does not report a change', async () => {
    const user = userEvent.setup();
    const { onChanged } = setup(3, { 'PATCH /api/v1/jobs/7/project': new Reply(409, { detail: 'nope' }) });
    await user.click(screen.getByRole('button', { name: 'Unlink' }));
    await waitFor(() => expect(screen.getByText(/nope/)).toBeTruthy());
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('does not create a second project when the link fails and is retried', async () => {
    const user = userEvent.setup();
    let patches = 0;
    const { api, onChanged } = setup(null, {
      'GET /api/v1/projects': [],
      'POST /api/v1/projects': proj(9, 'Fresh', 'queued'),
      'PATCH /api/v1/jobs/7/project': () => (++patches === 1 ? new Reply(409, { detail: 'busy' }) : job(9)),
    });
    await user.click(screen.getByText('Link to project…'));
    await user.type(screen.getByLabelText('New project name'), 'Fresh');
    await user.click(screen.getByRole('button', { name: 'Create & link' }));
    await waitFor(() => expect(screen.getByText(/busy/)).toBeTruthy());
    await user.click(screen.getByRole('button', { name: 'Create & link' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(job(9)));
    expect(api.to('POST', '/api/v1/projects')).toHaveLength(1);
  });

  it('shows an empty list and the error when projects fail to load, and retries on reopen', async () => {
    const user = userEvent.setup();
    let gets = 0;
    setup(null, { 'GET /api/v1/projects': () => (++gets === 1 ? new Reply(500, { detail: 'down' }) : [proj(2, 'Shelf', 'queued')]) });
    await user.click(screen.getByText('Link to project…'));
    await waitFor(() => expect(screen.getByText(/down/)).toBeTruthy());
    expect(screen.queryByText('Loading…')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getByText('Link to project…'));
    await waitFor(() => expect(screen.getByRole('option', { name: 'Shelf' })).toBeTruthy());
  });
});
