import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { FilesScreen } from './FilesScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('FilesScreen', () => {
  it('renders file grid', () => {
    render(<FilesScreen />, { wrapper });
    expect(screen.getByText('vr_arm_bracket_L.3mf')).toBeTruthy();
  });
  it('renders folder tree', () => {
    render(<FilesScreen />, { wrapper });
    expect(screen.getByText('All files')).toBeTruthy();
  });
  it('tag filter reduces file count', async () => {
    const user = userEvent.setup();
    render(<FilesScreen />, { wrapper });
    const paButton = screen.getByRole('button', { name: /PA-CF/i });
    await user.click(paButton);
    // PA-CF filter shows only PA-CF files, northbeam_cradle.3mf should not appear
    expect(screen.queryByText('northbeam_cradle.3mf')).toBeNull();
  });
});
