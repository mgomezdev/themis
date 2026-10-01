import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from './Sidebar';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ enabled: false, url: null, has_api_key: false }), { status: 200 })));
});

const renderBar = (alarmCount?: number, alarmWorst?: 'info' | 'warning' | 'error' | 'fatal' | null) => render(
  <MemoryRouter initialEntries={['/fleet']}>
    <Sidebar queueCounts={{ active: 0, pending: 0, blocked: 0 }} operatorName={null} printerCount={1}
             alarmCount={alarmCount} alarmWorst={alarmWorst} />
  </MemoryRouter>);

describe('Sidebar alarms', () => {
  it('links to the alarms page and shows the count of unacknowledged alarms', () => {
    renderBar(3, 'error');
    expect(screen.getByRole('link', { name: /Alarms/ }).getAttribute('href')).toBe('/alarms');
    expect(screen.getByTestId('badge-alarms').textContent).toBe('3');
  });

  it('shows no badge without alarms', () => {
    renderBar(0, null);
    expect(screen.queryByTestId('badge-alarms')).toBeNull();
    renderBar();
    expect(screen.queryByTestId('badge-alarms')).toBeNull();
  });

  it('colours the badge by the worst severity', () => {
    const { unmount } = renderBar(1, 'warning');
    expect(screen.getByTestId('badge-alarms').style.color).toBe('var(--warn)');
    unmount();
    renderBar(1, 'fatal');
    expect(screen.getByTestId('badge-alarms').style.color).toBe('var(--err)');
  });
});
