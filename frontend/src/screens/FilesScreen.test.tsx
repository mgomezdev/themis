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

  it('selects multiple files and bulk-deletes them', async () => {
    const TWO = [
      { id: 1, original_filename: 'a.3mf', relative_path: 'a.3mf', folder: '/', size_bytes: 100,
        plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null },
      { id: 2, original_filename: 'b.3mf', relative_path: 'b.3mf', folder: '/', size_bytes: 200,
        plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null },
    ];
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.startsWith('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.startsWith('/api/v1/files/tree')) return new Response(JSON.stringify(
        { name: 'All files', path: '', count: 2, children: {} }), { status: 200 });
      if (url.startsWith('/api/v1/files')) return new Response(JSON.stringify(TWO), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('confirm', vi.fn(() => true));

    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('a.3mf')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Select a.3mf'));
    fireEvent.click(screen.getByLabelText('Select b.3mf'));
    expect(screen.getByText('2 selected')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Delete$/ }));

    await waitFor(() => {
      const deletes = fetchMock.mock.calls.filter(
        c => (c[1] as RequestInit | undefined)?.method === 'DELETE');
      expect(deletes.length).toBe(2);
    });
  });

  it('opens the folder picker (with real dirs + New folder) for a bulk move', async () => {
    const ONE = [
      { id: 1, original_filename: 'a.3mf', relative_path: 'a.3mf', folder: '/', size_bytes: 100,
        plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null },
    ];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.startsWith('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.startsWith('/api/v1/files/dirs')) return new Response(JSON.stringify(
        { name: 'All files', path: '', count: 1,
          children: { Archive: { name: 'Archive', path: '/Archive', count: 0, children: {} } } }),
        { status: 200 });
      if (url.startsWith('/api/v1/files/tree')) return new Response(JSON.stringify(
        { name: 'All files', path: '', count: 1, children: {} }), { status: 200 });
      if (url.startsWith('/api/v1/files')) return new Response(JSON.stringify(ONE), { status: 200 });
      return new Response('[]', { status: 200 });
    }));

    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('a.3mf')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Select a.3mf'));
    fireEvent.click(screen.getByRole('button', { name: /^Move$/ }));

    await waitFor(() => expect(screen.getByText(/Move 1 file to/i)).toBeInTheDocument());
    // the empty on-disk folder is selectable in the picker
    expect(screen.getByText('Archive')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Move here/i })).toBeInTheDocument();
  });
});
