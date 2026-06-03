import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FilesScreen } from './FilesScreen';

const FILES = [
  { id: 1, original_filename: 'arm.3mf', relative_path: 'Customers/Vela/arm.3mf',
    folder: '/Customers/Vela', size_bytes: 4200000, plate_count: 1, uploaded_at: '2026-06-01',
    missing: false, tags: [{ id: 1, name: 'PLA', color: '#fff', category: 'Material' }], thumbnail_url: null },
];
const TAGS = [{ id: 1, name: 'PLA', color: '#fff', category: 'Material', usage_count: 1 }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/v1/tags')) return new Response(JSON.stringify(TAGS), { status: 200 });
    if (url.startsWith('/api/v1/files/tree')) return new Response(JSON.stringify(
      { name: 'All files', path: '', count: 1, children: {} }), { status: 200 });
    if (url.startsWith('/api/v1/files')) return new Response(JSON.stringify(FILES), { status: 200 });
    return new Response('[]', { status: 200 });
  }));
});

describe('FilesScreen', () => {
  it('renders files from the API', async () => {
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('arm.3mf')).toBeInTheDocument());
  });

  it('shows the Manyfold placeholder when that tab is selected', async () => {
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /Manyfold/i }));
    await waitFor(() => expect(screen.getByText(/coming soon/i)).toBeInTheDocument());
  });
});
