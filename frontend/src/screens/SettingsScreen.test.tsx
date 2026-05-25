import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('SettingsScreen', () => {
  it('renders General page by default', () => {
    render(<SettingsScreen />, { wrapper });
    expect(screen.getByText('Workshop name')).toBeTruthy();
  });
  it('nav items are all visible', () => {
    render(<SettingsScreen />, { wrapper });
    expect(screen.getByText('Tags')).toBeTruthy();
    expect(screen.getByText('Notifications')).toBeTruthy();
    expect(screen.getByText('Spoolman')).toBeTruthy();
  });
  it('clicking Notifications nav item shows notifications page', async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /notifications/i }));
    expect(screen.getByText('Job completed')).toBeTruthy();
  });
});
