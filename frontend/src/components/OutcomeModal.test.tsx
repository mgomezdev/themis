import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OutcomeModal } from './OutcomeModal';
import { Reply, stubFetch } from '../test/fetchStub';

const PROJECT = {
  id: 5,
  items: [
    { id: 11, file_name: 'gear.stl' },
    { id: 12, file_name: 'lid.stl' },
    { id: 13, file_name: 'not-on-this-plate.stl' },
  ],
};
const JOB = { id: 77, project_id: 5, project_item_quantities: { '11': 3, '12': 2 } };

function renderModal(overrides: Partial<typeof JOB> = {}) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<OutcomeModal job={{ ...JOB, ...overrides }} onClose={onClose} onSaved={onSaved} />);
  return { onClose, onSaved };
}

const row = (name: string) => within(screen.getByText(name).closest('tr') as HTMLElement);
const failedInput = (name: string) => row(name).getByRole('spinbutton') as HTMLInputElement;
const save = () => screen.getByRole('button', { name: /^save$/i }) as HTMLButtonElement;

afterEach(() => vi.unstubAllGlobals());

describe('OutcomeModal', () => {
  it('lists only the parts on the plate, with their on-plate quantities and zero failures', async () => {
    stubFetch({ 'GET /api/v1/projects/5': PROJECT });
    renderModal();

    await screen.findByText('gear.stl');
    expect(screen.queryByText('not-on-this-plate.stl')).toBeNull();
    expect(row('gear.stl').getByText('3')).toBeTruthy();
    expect(row('lid.stl').getByText('2')).toBeTruthy();
    expect(failedInput('gear.stl').value).toBe('0');
    expect(failedInput('lid.stl').value).toBe('0');
  });

  it('saving with nothing failed sends an empty failures list, then reports saved and closes', async () => {
    const api = stubFetch({ 'GET /api/v1/projects/5': PROJECT, 'PUT /api/v1/jobs/77/outcome': { failures: [] } });
    const { onSaved, onClose } = renderModal();
    await screen.findByText('gear.stl');

    await userEvent.click(save());

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(api.to('PUT', '/api/v1/jobs/77/outcome')[0].body).toEqual({ failures: [] });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('sends only the parts with failures, by project_item_id', async () => {
    const api = stubFetch({ 'GET /api/v1/projects/5': PROJECT, 'PUT /api/v1/jobs/77/outcome': { failures: [] } });
    renderModal();
    await screen.findByText('gear.stl');

    await userEvent.clear(failedInput('gear.stl'));
    await userEvent.type(failedInput('gear.stl'), '1');
    await userEvent.click(save());

    await waitFor(() => expect(api.to('PUT', '/api/v1/jobs/77/outcome')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/jobs/77/outcome')[0].body).toEqual({
      failures: [{ project_item_id: 11, quantity_failed: 1 }],
    });
  });

  it('clamps a failed count to the quantity on the plate', async () => {
    stubFetch({ 'GET /api/v1/projects/5': PROJECT });
    renderModal();
    await screen.findByText('gear.stl');

    await userEvent.clear(failedInput('gear.stl'));
    await userEvent.type(failedInput('gear.stl'), '9');

    expect(failedInput('gear.stl').value).toBe('3');
  });

  it('"Mark All Failed" fails every plate quantity and "Mark All Good" resets them', async () => {
    const api = stubFetch({ 'GET /api/v1/projects/5': PROJECT, 'PUT /api/v1/jobs/77/outcome': { failures: [] } });
    renderModal();
    await screen.findByText('gear.stl');

    await userEvent.click(screen.getByRole('button', { name: /mark all failed/i }));
    expect([failedInput('gear.stl').value, failedInput('lid.stl').value]).toEqual(['3', '2']);
    await userEvent.click(save());
    await waitFor(() => expect(api.to('PUT', '/api/v1/jobs/77/outcome')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/jobs/77/outcome')[0].body).toEqual({
      failures: [{ project_item_id: 11, quantity_failed: 3 }, { project_item_id: 12, quantity_failed: 2 }],
    });

    await userEvent.click(screen.getByRole('button', { name: /mark all good/i }));
    expect([failedInput('gear.stl').value, failedInput('lid.stl').value]).toEqual(['0', '0']);
  });

  it('a failed save shows the error, keeps the modal open and re-enables Save', async () => {
    stubFetch({
      'GET /api/v1/projects/5': PROJECT,
      'PUT /api/v1/jobs/77/outcome': new Reply(400, 'This job has no project items to mark'),
    });
    const { onSaved, onClose } = renderModal();
    await screen.findByText('gear.stl');

    await userEvent.click(save());

    await screen.findByText(/400 This job has no project items to mark/);
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(save().disabled).toBe(false);
  });

  it('shows the error and cannot save when the project fails to load', async () => {
    stubFetch({ 'GET /api/v1/projects/5': new Reply(500, 'db down') });
    renderModal();

    await screen.findByText(/500 db down/);
    expect(save().disabled).toBe(true);
  });

  it('a job with no project makes no request and cannot be saved', () => {
    const api = stubFetch({});
    renderModal({ project_id: null as unknown as number });

    expect(api.calls).toHaveLength(0);
    expect(save().disabled).toBe(true);
  });

  it('Cancel, the close button and a backdrop click close without saving', async () => {
    const api = stubFetch({ 'GET /api/v1/projects/5': PROJECT });
    const { onClose, onSaved } = renderModal();
    await screen.findByText('gear.stl');

    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await userEvent.click(screen.getByRole('button', { name: '✕' }));
    await userEvent.click(screen.getByText('Mark Job Outcome').closest('div[style*="fixed"]') as HTMLElement);

    expect(onClose).toHaveBeenCalledTimes(3);
    expect(onSaved).not.toHaveBeenCalled();
    expect(api.calls.filter(c => c.method === 'PUT')).toHaveLength(0);
  });
});
