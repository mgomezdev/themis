import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectMetaFields } from './ProjectMetaFields';
import type { HeaderFields } from '../../lib/projectBuilderState';
import { stubFetch } from '../../test/fetchStub';

const FIELDS: HeaderFields = {
  name: 'Shelf', customer: '', customerId: null, orderType: 'internal', onHold: false,
  dueDate: '', notes: '', price: '',
};
afterEach(() => vi.unstubAllGlobals());

function setup(fields: Partial<HeaderFields> = {}, projectId: number | null = null) {
  stubFetch({ 'GET /api/v1/customers': [] });
  const onChange = vi.fn();
  render(<ProjectMetaFields fields={{ ...FIELDS, ...fields }} projectId={projectId} onChange={onChange} />);
  return onChange;
}

describe('ProjectMetaFields', () => {
  it('shows the current values', () => {
    setup({ notes: 'rush', price: '12', dueDate: '2026-12-01', onHold: true }, 7);
    expect(screen.getByLabelText(/Project name/)).toHaveValue('Shelf');
    expect(screen.getByText('#7')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Optional notes')).toHaveValue('rush');
    expect(screen.getByLabelText('Price')).toHaveValue(12);
    expect(screen.getByDisplayValue('2026-12-01')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /On hold/ })).toBeChecked();
    expect(screen.getByDisplayValue('Internal')).toBeInTheDocument();
  });

  it.each<[string, () => unknown, Partial<HeaderFields>]>([
    ['name', () => fireEvent.change(screen.getByLabelText(/Project name/), { target: { value: 'Tray' } }), { name: 'Tray' }],
    ['type', () => userEvent.selectOptions(screen.getByDisplayValue('Internal'), 'customer'), { orderType: 'customer' }],
    ['due date', () => fireEvent.change(document.querySelector('input[type="date"]')!, { target: { value: '2026-12-02' } }), { dueDate: '2026-12-02' }],
    ['notes', () => fireEvent.change(screen.getByPlaceholderText('Optional notes'), { target: { value: 'n' } }), { notes: 'n' }],
    ['on hold', () => userEvent.click(screen.getByRole('checkbox', { name: /On hold/ })), { onHold: true }],
    ['price', () => fireEvent.change(screen.getByLabelText('Price'), { target: { value: '9.5' } }), { price: '9.5' }],
  ])('reports a %s change', async (_f, change, patch) => {
    const onChange = setup({}, null);
    await change();
    expect(onChange).toHaveBeenLastCalledWith(patch);
  });

  it('offers clearing the due date only when one is set', async () => {
    setup();
    expect(screen.queryByTitle('Clear due date')).toBeNull();
    vi.unstubAllGlobals();
    const onChange = setup({ dueDate: '2026-12-01' });
    await userEvent.click(screen.getByTitle('Clear due date'));
    expect(onChange).toHaveBeenCalledWith({ dueDate: '' });
  });

  it('shows the customer picker only for a customer project, reporting account and name together', async () => {
    setup();
    expect(screen.queryByText('Customer', { selector: 'label' })).toBeNull();
    vi.unstubAllGlobals();
    const onChange = setup({ orderType: 'customer' });
    expect(screen.getByText('Customer', { selector: 'label' })).toBeInTheDocument();
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Customer' }), 'Other (no account)');
    fireEvent.change(screen.getByRole('textbox', { name: 'Customer name' }), { target: { value: 'Bob' } });
    expect(onChange).toHaveBeenLastCalledWith({ customerId: null, customer: 'Bob' });
  });

  it('words the payments hint for a new vs saved project', () => {
    setup({}, null);
    expect(screen.getByText('Record payments on the project page once it is saved.')).toBeInTheDocument();
    vi.unstubAllGlobals();
    setup({}, 7);
    expect(screen.getByText(/Payments received are recorded on the project page/)).toBeInTheDocument();
  });

  it('has no project number for a new project', () => {
    setup({}, null);
    expect(screen.queryByText(/^#/)).toBeNull();
  });
});
