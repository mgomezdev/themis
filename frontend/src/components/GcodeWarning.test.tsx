import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { GcodeWarning } from './GcodeWarning';

const KEY = 'themis.gcodeWarningDismissed';

describe('GcodeWarning', () => {
  beforeEach(() => localStorage.clear());

  it('warns that matching gcode to the printer is the operator’s job', () => {
    render(<GcodeWarning />);
    expect(screen.getByTestId('gcode-warning').textContent).toMatch(/up to you/);
  });

  it('dismisses for now without remembering it', async () => {
    const { unmount } = render(<GcodeWarning />);

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

    expect(screen.queryByTestId('gcode-warning')).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
    unmount();
    render(<GcodeWarning />);   // next time it is back
    expect(screen.getByTestId('gcode-warning')).toBeTruthy();
  });

  it('stays dismissed once "Don\'t show this again" was ticked', async () => {
    const { unmount } = render(<GcodeWarning />);

    await userEvent.click(screen.getByLabelText("Don't show this again"));
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

    expect(localStorage.getItem(KEY)).toBe('1');
    unmount();
    render(<GcodeWarning />);
    expect(screen.queryByTestId('gcode-warning')).toBeNull();
  });
});
