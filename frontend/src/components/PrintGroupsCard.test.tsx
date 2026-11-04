import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrintGroupsCard } from './PrintGroupsCard';
import { stubFetch } from '../test/fetchStub';
import type { ProjectGroup, ProjectItem } from '../api/projects';

const group = (id: number, name: string, over: Partial<ProjectGroup> = {}): ProjectGroup => ({
  id, project_id: 7, name, sort_order: id, is_default: id === 1, strategy: 'fixed', envelope_x: null, envelope_y: null,
  mix_colours: false, targets: [], ...over,
});
const ITEM = { id: 50, project_id: 7, file_id: 1, file_name: 'Bracket.stl', quantity: 4, quantity_completed: 0, quantity_failed: 0,
  filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 0, group_id: 1 } as ProjectItem;
const PRINTERS = [{ id: 3, name: 'Kepler', enabled: true, model_uuid: null, model_label: null, printer_type: 'bambu', bed_x_mm: 256, bed_y_mm: 256 }];

function open(groups: ProjectGroup[]) {
  return stubFetch({
    'GET /api/v1/projects/7/groups': () => groups,
    'GET /api/v1/printers': PRINTERS,
    'GET /api/v1/printers/3/profiles': { print_profiles: ['0.12mm Fine', '0.20mm Standard'], filament_profiles: [] },
    'PATCH /api/v1/projects/7/groups/2': (c: { body: unknown }) => ({ ...groups[1], ...(c.body as object) }),
    'PATCH /api/v1/projects/7/groups/1': (c: { body: unknown }) => ({ ...groups[0], ...(c.body as object) }),
    'PUT /api/v1/projects/7/groups/order': [],
    'POST /api/v1/projects/7/groups': group(3, 'Draft'),
    'PUT /api/v1/projects/7/items/50': { ...ITEM, group_id: 2 },
  });
}

describe('PrintGroupsCard (BIZ-360)', () => {
  it('adds a printer target to a group with Printer default, then sets its process profile', async () => {
    const api = open([group(1, 'Default Group'), group(2, 'Fine')]);
    render(<PrintGroupsCard projectId={7} items={[ITEM]} />);
    await screen.findByDisplayValue('Fine');
    const fine = screen.getByTestId('group-2');
    await userEvent.click(fine.querySelector('button.pill.idle') as HTMLElement);         // its "+ Printer"
    await userEvent.click(await screen.findByRole('menuitem', { name: /^Kepler/ }));
    await waitFor(() => expect(api.to('PATCH', '/api/v1/projects/7/groups/2')).toHaveLength(1));
    expect(api.to('PATCH', '/api/v1/projects/7/groups/2')[0].body).toEqual({ targets: [{ target_key: '3', process_profile: '' }] });
  });

  it('saves a per-target process profile and keeps the other targets', async () => {
    const api = open([group(1, 'Default Group'),
      group(2, 'Fine', { targets: [{ target_key: '3', process_profile: '' }, { target_key: 'any', process_profile: 'X' }] })]);
    render(<PrintGroupsCard projectId={7} items={[]} />);
    const sel = await screen.findByLabelText('Process profile for Kepler');
    await waitFor(() => expect([...(sel as HTMLSelectElement).options].map(o => o.textContent)).toContain('0.12mm Fine'));
    await userEvent.selectOptions(sel, '0.12mm Fine');
    await waitFor(() => expect(api.to('PATCH', '/api/v1/projects/7/groups/2')).toHaveLength(1));
    expect(api.to('PATCH', '/api/v1/projects/7/groups/2')[0].body).toEqual({
      targets: [{ target_key: '3', process_profile: '0.12mm Fine' }, { target_key: 'any', process_profile: 'X' }] });
  });

  it('sets a custom envelope, reorders, adds a group and moves an item', async () => {
    const api = open([group(1, 'Default Group'), group(2, 'Fine')]);
    render(<PrintGroupsCard projectId={7} items={[ITEM]} />);
    await screen.findByDisplayValue('Fine');
    const fine = screen.getByTestId('group-2');
    fireEvent.click(fine.querySelector('input[type="checkbox"]') as HTMLElement);          // untick "smallest selected bed"
    await waitFor(() => expect(api.to('PATCH', '/api/v1/projects/7/groups/2')[0]?.body).toEqual({ envelope_x: 256, envelope_y: 256 }));

    await userEvent.click(screen.getByRole('button', { name: 'Move Fine up' }));
    await waitFor(() => expect(api.to('PUT', '/api/v1/projects/7/groups/order')[0]?.body).toEqual({ group_ids: [2, 1] }));
    expect((screen.getByRole('button', { name: 'Move Default Group up' }) as HTMLButtonElement).disabled).toBe(true);

    await userEvent.type(screen.getByPlaceholderText('New group name'), 'Draft');
    await userEvent.click(screen.getByRole('button', { name: '+ New group' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/projects/7/groups')[0]?.body).toEqual({ name: 'Draft' }));

    await userEvent.selectOptions(screen.getByLabelText('Group for Bracket.stl'), '2');
    await waitFor(() => expect(api.to('PUT', '/api/v1/projects/7/items/50')[0]?.body).toEqual({ group_id: 2 }));
  });

  it('never offers to delete the Default Group', async () => {
    open([group(1, 'Default Group'), group(2, 'Fine')]);
    render(<PrintGroupsCard projectId={7} items={[]} />);
    await screen.findByDisplayValue('Fine');
    expect(screen.getByTestId('group-1').textContent).not.toContain('Delete');
    expect(screen.getByTestId('group-2').textContent).toContain('Delete');
  });
});

