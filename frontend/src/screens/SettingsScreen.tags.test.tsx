// frontend/src/screens/SettingsScreen.tags.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

const TAGS = [{ id: 1, name: 'PLA', color: '#22c55e', category: 'Material', usage_count: 3 }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/v1/tags')) return new Response(JSON.stringify(TAGS), { status: 200 });
    return new Response('{}', { status: 200 });
  }));
});

describe('Settings Tags tab', () => {
  it('lists tags from the API', async () => {
    render(<MemoryRouter initialEntries={['/settings/tags']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('PLA')).toBeInTheDocument());
  });
});
