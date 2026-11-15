import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProjectLinksEditor } from './ProjectLinksEditor';

const LINKS = [{ localId: 'a', url: 'https://x.test', label: 'Spec' }, { localId: 'b', url: '', label: '' }];

describe('ProjectLinksEditor', () => {
  it('lists the links with a count', () => {
    render(<ProjectLinksEditor links={LINKS} dispatch={() => {}} />);
    expect(screen.getByText('Links (2)')).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText('https://...').map(i => (i as HTMLInputElement).value)).toEqual(['https://x.test', '']);
    expect(screen.getByDisplayValue('Spec')).toBeInTheDocument();
  });

  it('dispatches add, edit and remove for the right row', async () => {
    const dispatch = vi.fn();
    render(<ProjectLinksEditor links={LINKS} dispatch={dispatch} />);
    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    fireEvent.change(screen.getAllByPlaceholderText('https://...')[1], { target: { value: 'https://y.test' } });
    fireEvent.change(screen.getByDisplayValue('Spec'), { target: { value: 'Docs' } });
    await userEvent.click(screen.getAllByTitle('Remove link')[1]);
    expect(dispatch.mock.calls.map(c => c[0])).toEqual([
      { type: 'addLink' },
      { type: 'updateLink', localId: 'b', patch: { url: 'https://y.test' } },
      { type: 'updateLink', localId: 'a', patch: { label: 'Docs' } },
      { type: 'remove', kind: 'link', localId: 'b' },
    ]);
  });
});
