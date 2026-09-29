import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectsScreen } from './ProjectsScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const item = (id: number, quantity: number) => ({
  id, project_id: 1, file_id: id, file_name: `part${id}.stl`, quantity, quantity_completed: 0, quantity_failed: 0,
  filament_type: 'any', filament_color: 'any', filament_id: null, sort_order: id,
});
const project = (id: number, name: string, over: object = {}) => ({
  id, name, customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null, stage: 'queued',
  items: [item(1, 2)], jobs_total: 0, jobs_complete: 0, ...over,
});

const PENDING = project(1, 'Not started', { items: [item(1, 1)] });                                   // 1 part, 1 copy
const ACTIVE = project(2, 'In flight', { customer: 'Vela Robotics', order_type: 'customer', jobs_total: 3, jobs_complete: 1,
                                          items: [item(1, 2), item(2, 3)] });                          // 2 parts, 5 copies
const DONE = project(3, 'All done', { jobs_total: 2, jobs_complete: 2, on_hold: true });
const DRAFT = project(4, 'Customer draft', { stage: 'draft', items: [] });                             // no parts
const ALL = [PENDING, ACTIVE, DONE, DRAFT];

function Where() { return <div data-testid="where">{useLocation().pathname}</div>; }
const where = () => screen.getByTestId('where').textContent;

function open(projects: object[] = ALL, over: Record<string, unknown> = {}) {
  const api = stubFetch({ 'GET /api/v1/projects': projects, ...over });
  render(
    <MemoryRouter initialEntries={['/projects']}>
      <Where />
      <Routes>
        <Route path="/projects" element={<ProjectsScreen />} />
        <Route path="/projects/:id" element={<div>DETAIL PAGE</div>} />
        <Route path="/projects/:id/edit" element={<div>EDIT PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}
const card = (name: string) => within(screen.getByText(name).closest('.card') as HTMLElement);
const tab = (label: RegExp) => screen.getByRole('button', { name: label });

afterEach(() => vi.unstubAllGlobals());

describe('ProjectsScreen - list', () => {
  it('invites the operator to create the first project', async () => {
    open([]);

    expect(await screen.findByText('No projects yet — create one to start batching parts.')).toBeTruthy();
  });

  it('summarises each project: parts and copies, customer, badges, job progress', async () => {
    open();
    await screen.findByText('Not started');

    expect(card('Not started').getByText('1 part · 1 copy')).toBeTruthy();
    expect(card('In flight').getByText('2 parts · 5 copies')).toBeTruthy();
    expect(card('In flight').getByText('Vela Robotics')).toBeTruthy();
    expect(card('In flight').getByText('Customer')).toBeTruthy();
    expect(card('In flight').getByText('1 / 3 jobs')).toBeTruthy();
    expect(card('All done').getByText('On hold')).toBeTruthy();
    expect(card('All done').getByText('2 / 2 jobs')).toBeTruthy();
    expect(card('Customer draft').getByText('No parts yet')).toBeTruthy();
    expect(card('Customer draft').getByText('draft')).toBeTruthy();            // a stage badge for anything not yet queued
    expect(card('Not started').queryByText('queued')).toBeNull();
    expect(card('Not started').queryByText(/jobs$/)).toBeNull();               // no progress bar until jobs exist
    expect(card('Not started').getByText('#1')).toBeTruthy();
  });

  it('shows the due date, in the error colour once it has passed', async () => {
    open([project(1, 'Late', { due_date: '2020-01-02' }), project(2, 'Later', { due_date: '2099-12-31' })]);
    await screen.findByText('Late');

    const late = card('Late').getByText(/^Due /), later = card('Later').getByText(/^Due /);
    expect(late.textContent).toContain('2020');
    expect(later.textContent).toContain('2099');
    expect(late.style.color).toBe('var(--err)');
    expect(later.style.color).toBe('var(--text-3)');
  });
});

describe('ProjectsScreen - filters', () => {
  it('counts each state on its tab and filters the grid', async () => {
    open();
    await screen.findByText('Not started');

    expect(tab(/^all/).textContent).toBe('all(4)');
    expect(tab(/^pending/).textContent).toBe('pending(2)');       // no jobs yet: Not started + the draft
    expect(tab(/^active/).textContent).toBe('active(1)');
    expect(tab(/^completed/).textContent).toBe('completed(1)');

    await userEvent.click(tab(/^active/));
    expect(screen.getByText('In flight')).toBeTruthy();
    expect(screen.queryByText('Not started')).toBeNull();
    expect(screen.queryByText('All done')).toBeNull();

    await userEvent.click(tab(/^completed/));
    expect(screen.getByText('All done')).toBeTruthy();
    expect(screen.queryByText('In flight')).toBeNull();

    await userEvent.click(tab(/^pending/));
    expect(screen.getByText('Not started')).toBeTruthy();
    expect(screen.getByText('Customer draft')).toBeTruthy();
    expect(screen.queryByText('All done')).toBeNull();

    await userEvent.click(tab(/^all/));
    expect(screen.getAllByText(/^#\d$/)).toHaveLength(4);
  });

  it('omits the count on an empty tab and explains an empty filter', async () => {
    open([PENDING]);
    await screen.findByText('Not started');

    expect(tab(/^active/).textContent).toBe('active');
    await userEvent.click(tab(/^active/));

    expect(screen.getByText('No active projects')).toBeTruthy();
    expect(screen.queryByText(/No projects yet/)).toBeNull();
  });
});

describe('ProjectsScreen - navigation', () => {
  it('opens the project when its card is clicked', async () => {
    open();
    await userEvent.click(await screen.findByText('In flight'));

    expect(where()).toBe('/projects/2');
  });

  it('Edit goes to the builder without also opening the detail page', async () => {
    open();
    await screen.findByText('In flight');

    await userEvent.click(card('In flight').getByRole('button', { name: 'Edit' }));

    expect(where()).toBe('/projects/2/edit');
  });
});

describe('ProjectsScreen - generate', () => {
  it('cannot generate a project that has no parts', async () => {
    open();
    await screen.findByText('Customer draft');

    expect(card('Customer draft').getByRole('button', { name: 'Generate' }).hasAttribute('disabled')).toBe(true);
    expect(card('Not started').getByRole('button', { name: 'Generate' }).hasAttribute('disabled')).toBe(false);
  });

  it('generates jobs without dispatch straight from the card and refreshes the list', async () => {
    let listed: object[] = ALL;
    const api = open(ALL, {
      'GET /api/v1/projects': () => listed,
      'POST /api/v1/projects/1/generate': () => { listed = [{ ...PENDING, jobs_total: 1 }, ACTIVE, DONE, DRAFT]; return { jobs: [{ id: 9 }] }; },
    });
    await screen.findByText('Not started');

    await userEvent.click(card('Not started').getByRole('button', { name: 'Generate' }));

    expect(await card('Not started').findByText('0 / 1 jobs')).toBeTruthy();
    expect(api.to('POST', '/api/v1/projects/1/generate').map(c => c.body)).toEqual([
      { eligible_printer_ids: [], process_preset: null },
    ]);
    expect(where()).toBe('/projects');                                          // the click did not open the project
  });

  it('shows the failure on the card and lets the operator try again', async () => {
    // a draft with parts (the Generate button is only disabled when there are none)
    const api = open([project(5, 'Broken', { stage: 'draft' })], {
      'POST /api/v1/projects/5/generate': new Reply(409, 'Promote the project to planning first'),
    });
    await screen.findByText('Broken');

    await userEvent.click(card('Broken').getByRole('button', { name: 'Generate' }));

    expect(await card('Broken').findByText('409 Promote the project to planning first')).toBeTruthy();
    expect(card('Broken').getByRole('button', { name: 'Generate' }).hasAttribute('disabled')).toBe(false);
    expect(api.to('POST', '/api/v1/projects/5/generate')).toHaveLength(1);
  });
});

describe('ProjectsScreen - delete', () => {
  it('deletes after confirmation and drops the project from the list', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    let listed: object[] = ALL;
    const api = open(ALL, {
      'GET /api/v1/projects': () => listed,
      'DELETE /api/v1/projects/3': () => { listed = ALL.filter(p => p.id !== 3); return { deleted: 3 }; },
    });
    await screen.findByText('All done');

    await userEvent.click(card('All done').getByTitle('Delete'));

    await waitFor(() => expect(screen.queryByText('All done')).toBeNull());
    expect(confirm).toHaveBeenCalledWith('Delete this project?');
    expect(api.to('DELETE', '/api/v1/projects/3')).toHaveLength(1);
    expect(screen.getByText('In flight')).toBeTruthy();
  });

  it('keeps the project when the operator says no', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false));
    const api = open();
    await screen.findByText('All done');

    await userEvent.click(card('All done').getByTitle('Delete'));

    expect(api.calls.filter(c => c.method === 'DELETE')).toEqual([]);
    expect(screen.getByText('All done')).toBeTruthy();
    expect(where()).toBe('/projects');
  });
});
