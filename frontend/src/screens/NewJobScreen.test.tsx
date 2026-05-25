import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewJobScreen } from './NewJobScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('NewJobScreen', () => {
  it('renders dropzone', () => {
    render(<NewJobScreen />, { wrapper });
    expect(screen.getByText(/Drop a .3mf or .stl/i)).toBeTruthy();
  });
  it('shows "Add jobs" button disabled before file', () => {
    render(<NewJobScreen />, { wrapper });
    const btn = screen.getByRole('button', { name: /add.*job/i });
    expect(btn).toBeDisabled();
  });
});
