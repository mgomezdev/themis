import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CustomerPortal } from './CustomerPortal';
import { Reply, stubFetch } from '../test/fetchStub';
import type { PortalProject } from '../api/customers';

const project = (over: Partial<PortalProject> & { id: number; name: string }): PortalProject => ({
  notes: null, stage: 'draft', due_date: null, created_at: '2026-09-01T00:00:00', updated_at: '2026-09-01T00:00:00',
  items: [], jobs: [], jobs_total: 0, jobs_complete: 0, ...over,
});

const DRAFT = project({ id: 1, name: 'Robot arm', notes: 'need 4 of them', items: [{ id: 5, filename: 'arm.stl', quantity: 4 }] });
const IN_PRODUCTION = project({
  id: 2, name: 'Gearbox', stage: 'queued', notes: 'rush\norder',
  jobs_total: 2, jobs_complete: 1,
  jobs: [
    { id: 30, status: 'complete', plate_number: 1, created_at: '2026-09-02T00:00:00', completed_at: '2026-09-03T12:00:00', estimate_seconds: 60 },
    { id: 31, status: 'printing', plate_number: 2, created_at: '2026-09-02T00:00:00', completed_at: null, estimate_seconds: null },
  ],
});
const PLANNING = project({ id: 3, name: 'Enclosure', stage: 'planning' });

const LIST = '/api/v1/customer/projects';
const NAME = 'Project name';
const NEW_NAME = 'New project request name';

function open(projects: PortalProject[] = [DRAFT, IN_PRODUCTION, PLANNING], over: Record<string, unknown> = {}) {
  const api = stubFetch({ [`GET ${LIST}`]: projects, ...over });
  render(<CustomerPortal />);
  return api;
}
/** The list-pane button for a project. */
const listed = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) });

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe('CustomerPortal - project list', () => {
  it('shows a loading state, then each project with its stage and job progress', async () => {
    open();

    expect(screen.getByText('Loading…')).toBeTruthy();
    expect(await screen.findByText('Robot arm')).toBeTruthy();
    expect(listed('Robot arm').textContent).toContain('Draft · 0/0 jobs');
    expect(listed('Gearbox').textContent).toContain('In production · 1/2 jobs');
    expect(listed('Enclosure').textContent).toContain('Planning · 0/0 jobs');
    expect(screen.getByText('Select a project')).toBeTruthy();
  });

  it('shows a stage it has no label for as-is rather than blank', async () => {
    open([project({ id: 8, name: 'Old job', stage: 'archived' as PortalProject['stage'] })]);

    expect((await screen.findByRole('button', { name: /^Old job/ })).textContent).toContain('archived · 0/0 jobs');
    await userEvent.click(listed('Old job'));
    expect(screen.getByText('archived', { selector: '.pill' })).toBeTruthy();
  });

  it('says so when the customer has no projects yet', async () => {
    open([]);

    expect(await screen.findByText('No projects yet')).toBeTruthy();
  });

  it('shows the server error when the list cannot be loaded, without a stuck project list', async () => {
    open([], { [`GET ${LIST}`]: new Reply(500, { detail: 'database is locked' }) });

    expect(await screen.findByText('database is locked')).toBeTruthy();
  });

  it('falls back to the status code when the error has no detail', async () => {
    open([], { [`GET ${LIST}`]: new Reply(502, 'bad gateway') });

    expect(await screen.findByText('502')).toBeTruthy();
  });

  it('sends the device API key with the request', async () => {
    localStorage.setItem('themis.apiKey', 'sk-portal');
    const api = open();
    await screen.findByText('Robot arm');

    const [call] = api.to('GET', LIST);
    expect(call).toBeTruthy();
    const init = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0][1];
    expect(new Headers(init.headers).get('X-Api-Key')).toBe('sk-portal');
  });
});

describe('CustomerPortal - project detail', () => {
  it('opens a draft in an editor with its name, notes, files and no jobs', async () => {
    open();
    await screen.findByText('Robot arm');

    await userEvent.click(listed('Robot arm'));

    expect(listed('Robot arm').style.borderColor).toBe('var(--accent)');   // the open one is highlighted
    expect(listed('Gearbox').style.borderColor).toBe('');

    expect((screen.getByPlaceholderText(NAME) as HTMLInputElement).value).toBe('Robot arm');
    expect((screen.getByPlaceholderText('Describe what you need') as HTMLTextAreaElement).value).toBe('need 4 of them');
    expect(screen.getByText('arm.stl × 4')).toBeTruthy();
    expect(screen.getByText('No jobs yet')).toBeTruthy();
    expect(screen.getByText('Jobs (0/0 complete)')).toBeTruthy();
    expect(screen.queryByText('Select a project')).toBeNull();
  });

  it('shows a project past draft read-only: notes as text, jobs with status and completion time', async () => {
    open();
    await screen.findByText('Gearbox');

    await userEvent.click(listed('Gearbox'));

    expect(screen.queryByPlaceholderText(NAME)).toBeNull();               // no editor once it is in production
    expect(screen.queryByText('Upload model (.stl / .3mf)')).toBeNull();
    expect(screen.getByText(/rush\s+order/)).toBeTruthy();
    expect(screen.getByText('None yet')).toBeTruthy();                    // no files
    expect(screen.getByText('Jobs (1/2 complete)')).toBeTruthy();
    const rows = screen.getAllByRole('row').slice(1);                     // skip the header row
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('#30 (plate 1)')).toBeTruthy();
    expect(within(rows[0]).getByText('complete')).toBeTruthy();
    expect(within(rows[0]).getByText(new Date('2026-09-03T12:00:00').toLocaleString())).toBeTruthy();
    expect(within(rows[1]).getByText('#31 (plate 2)')).toBeTruthy();
    expect(within(rows[1]).getByText('printing')).toBeTruthy();
    expect(within(rows[1]).getByText('—')).toBeTruthy();                  // not completed yet
  });

  it('shows a planning project with no notes as just its stage', async () => {
    open();
    await screen.findByText('Enclosure');

    await userEvent.click(listed('Enclosure'));

    expect(screen.getByRole('heading', { name: 'Enclosure' })).toBeTruthy();
    expect(screen.getAllByText('Planning').length).toBeGreaterThan(0);
    expect(screen.queryByPlaceholderText(NAME)).toBeNull();
  });

  it('re-seeds the editor when the customer switches to another draft', async () => {
    const other = project({ id: 4, name: 'Bracket', notes: 'M3 holes' });
    open([DRAFT, other]);
    await screen.findByText('Robot arm');

    await userEvent.click(listed('Robot arm'));
    await userEvent.click(listed('Bracket'));

    expect((screen.getByPlaceholderText(NAME) as HTMLInputElement).value).toBe('Bracket');
    expect((screen.getByPlaceholderText('Describe what you need') as HTMLTextAreaElement).value).toBe('M3 holes');
  });
});

describe('CustomerPortal - new request', () => {
  it('only enables "New request" for a non-blank name', async () => {
    open([]);
    await screen.findByText('No projects yet');
    const button = screen.getByRole('button', { name: 'New request' });
    const input = screen.getByPlaceholderText(NEW_NAME);

    expect(button.hasAttribute('disabled')).toBe(true);
    await userEvent.type(input, '   ');
    expect(button.hasAttribute('disabled')).toBe(true);
    await userEvent.type(input, 'x');
    expect(button.hasAttribute('disabled')).toBe(false);
  });

  it('creates a draft with the trimmed name, lists it first, selects it and clears the box', async () => {
    const created = project({ id: 9, name: 'Cable clip' });
    const api = open([DRAFT], { [`POST ${LIST}`]: created });
    await screen.findByText('Robot arm');

    await userEvent.type(screen.getByPlaceholderText(NEW_NAME), '  Cable clip  {Enter}');

    await screen.findByRole('heading', { name: 'Cable clip' });
    expect(api.to('POST', LIST).map(c => c.body)).toEqual([{ name: 'Cable clip' }]);
    expect((screen.getByPlaceholderText(NEW_NAME) as HTMLInputElement).value).toBe('');
    const names = screen.getAllByRole('button').filter(b => b.className.includes('card')).map(b => b.firstElementChild?.textContent);
    expect(names).toEqual(['Cable clip', 'Robot arm']);                  // newest first
    expect((screen.getByPlaceholderText(NAME) as HTMLInputElement).value).toBe('Cable clip');   // ready to edit
  });

  it('does not submit a blank name', async () => {
    const api = open([]);
    await screen.findByText('No projects yet');
    const input = screen.getByPlaceholderText(NEW_NAME);

    await userEvent.type(input, '   {Enter}');
    fireEvent.submit(input.closest('form') as HTMLFormElement);   // the button is disabled; the handler must still refuse

    expect(api.to('POST', LIST)).toEqual([]);
  });

  it('shows why creation failed and keeps what was typed', async () => {
    open([], { [`POST ${LIST}`]: new Reply(403, { detail: 'Customer accounts are disabled' }) });
    await screen.findByText('No projects yet');

    await userEvent.type(screen.getByPlaceholderText(NEW_NAME), 'Retry me');
    await userEvent.click(screen.getByRole('button', { name: 'New request' }));

    expect(await screen.findByText('Customer accounts are disabled')).toBeTruthy();
    expect((screen.getByPlaceholderText(NEW_NAME) as HTMLInputElement).value).toBe('Retry me');
    expect(screen.getByText('No projects yet')).toBeTruthy();
  });
});

describe('CustomerPortal - editing a draft', () => {
  it('saves the edited name and notes, then shows the server\'s version in the list', async () => {
    const saved = { ...DRAFT, name: 'Robot arm v2', notes: 'need 6' };
    const api = open([DRAFT, IN_PRODUCTION], { [`PATCH ${LIST}/1`]: saved });
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    const name = screen.getByPlaceholderText(NAME), notes = screen.getByPlaceholderText('Describe what you need');
    await userEvent.clear(name); await userEvent.type(name, 'Robot arm v2');
    await userEvent.clear(notes); await userEvent.type(notes, 'need 6');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(listed('Robot arm v2')).toBeTruthy());
    expect(api.to('PATCH', `${LIST}/1`).map(c => c.body)).toEqual([{ name: 'Robot arm v2', notes: 'need 6' }]);
    expect(screen.queryByRole('button', { name: /^Robot arm Draft/ })).toBeNull();   // replaced, not duplicated
    expect((screen.getByPlaceholderText(NAME) as HTMLInputElement).value).toBe('Robot arm v2');
  });

  it('will not save a blank name', async () => {
    const api = open();
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    await userEvent.clear(screen.getByPlaceholderText(NAME));

    expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true);
    expect(api.to('PATCH', `${LIST}/1`)).toEqual([]);
  });

  it('shows the server\'s reason when saving fails and keeps the typed text', async () => {
    open([DRAFT], { [`PATCH ${LIST}/1`]: new Reply(409, { detail: 'Project is no longer a draft' }) });
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    await userEvent.type(screen.getByPlaceholderText(NAME), '!');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Project is no longer a draft')).toBeTruthy();
    expect((screen.getByPlaceholderText(NAME) as HTMLInputElement).value).toBe('Robot arm!');
    expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(false);   // free to retry
  });

  it('clears the error once a retry succeeds', async () => {
    let attempts = 0;
    open([DRAFT], { [`PATCH ${LIST}/1`]: () => (++attempts === 1 ? new Reply(500, { detail: 'try again' }) : DRAFT) });
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('try again')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByText('try again')).toBeNull());
    expect(attempts).toBe(2);
  });

  it('uploads the chosen model as multipart "file", refreshes the file list and resets the picker', async () => {
    const withFile = { ...DRAFT, items: [...DRAFT.items, { id: 6, filename: 'hinge.3mf', quantity: 1 }] };
    const api = open([DRAFT], { [`POST ${LIST}/1/files`]: withFile });
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    const picker = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(picker.accept).toBe('.stl,.3mf');
    const file = new File(['solid'], 'hinge.3mf');
    await userEvent.upload(picker, file);

    expect(await screen.findByText('hinge.3mf × 1')).toBeTruthy();
    expect(screen.getByText('arm.stl × 4')).toBeTruthy();
    const [upload] = api.to('POST', `${LIST}/1/files`);
    expect(upload.body).toBeInstanceOf(FormData);
    expect((upload.body as FormData).get('file')).toBe(file);
    expect(picker.value).toBe('');          // the same file can be picked again
  });

  it('shows the reason an upload was rejected and keeps the existing files', async () => {
    open([DRAFT], { [`POST ${LIST}/1/files`]: new Reply(422, { detail: 'Could not read that model file' }) });
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    await userEvent.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File(['x'], 'broken.stl'));

    expect(await screen.findByText('Could not read that model file')).toBeTruthy();
    expect(screen.getByText('arm.stl × 4')).toBeTruthy();
  });
});

describe('CustomerPortal - upload picker', () => {
  it('does nothing when the file dialog is cancelled', async () => {
    const api = open([DRAFT]);
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [] } });

    expect(api.to('POST', `${LIST}/1/files`)).toEqual([]);
  });

  it('ignores a file type the picker does not accept', async () => {
    const api = open([DRAFT]);
    await screen.findByText('Robot arm');
    await userEvent.click(listed('Robot arm'));

    await userEvent.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File(['x'], 'notes.txt'));

    expect(api.to('POST', `${LIST}/1/files`)).toEqual([]);
  });
});

describe('CustomerPortal - sign out', () => {
  it('forgets this browser\'s API key and reloads so the login gate takes over', async () => {
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    localStorage.setItem('themis.apiKey', 'sk-portal');
    open();
    await screen.findByText('Robot arm');

    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));

    expect(localStorage.getItem('themis.apiKey')).toBeNull();
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
