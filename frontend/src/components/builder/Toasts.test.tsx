import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { SavedToast, UndoToast } from './Toasts';

describe('Toasts', () => {
  it('says Saved', () => {
    render(<SavedToast />);
    expect(screen.getByText('Saved')).toBeInTheDocument();
  });

  it('names what was removed and undoes it', async () => {
    const onUndo = vi.fn();
    render(<UndoToast label="Bracket.stl" onUndo={onUndo} />);
    expect(screen.getByText('Removed Bracket.stl')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onUndo).toHaveBeenCalledTimes(1);
  });
});
