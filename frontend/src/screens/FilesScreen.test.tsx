import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FilesScreen } from './FilesScreen';

const FILES = [
  { id: 1, original_filename: 'arm.3mf', relative_path: 'Customers/Vela/arm.3mf',
    folder: '/Customers/Vela', size_bytes: 4200000, plate_count: 1, uploaded_at: '2026-06-01',
    missing: false, tags: [{ id: 1, name: 'PLA', color: '#fff', category: 'Material' }], thumbnail_url: null, plate_thumbnails: [] },
];
const TAGS = [{ id: 1, name: 'PLA', color: '#fff', category: 'Material', usage_count: 1 }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/v1/library/sources')) return new Response(JSON.stringify({ sources: [] }), { status: 200 });
    if (url.startsWith('/api/v1/library/entries')) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    if (url.startsWith('/api/v1/tags')) return new Response(JSON.stringify(TAGS), { status: 200 });
    if (url.startsWith('/api/v1/files/dirs')) return new Response(JSON.stringify(
      { name: 'All files', path: '', count: 1, children: {} }), { status: 200 });
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

  it('shows generic source setup when the Sources tab is selected', async () => {
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /Sources/i }));
    await waitFor(() => expect(screen.getByText(/No library sources are installed/i)).toBeInTheDocument());
  });

  it('selects multiple files and bulk-deletes them', async () => {
    const TWO = [
      { id: 1, original_filename: 'a.3mf', relative_path: 'a.3mf', folder: '/', size_bytes: 100,
        plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
      { id: 2, original_filename: 'b.3mf', relative_path: 'b.3mf', folder: '/', size_bytes: 200,
        plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
    ];
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.startsWith('/api/v1/library/sources')) return new Response(JSON.stringify({ sources: [] }), { status: 200 });
    if (url.startsWith('/api/v1/library/entries')) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    if (url.startsWith('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.startsWith('/api/v1/files/dirs')) return new Response(JSON.stringify(
        { name: 'All files', path: '', count: 2, children: {} }), { status: 200 });
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
        plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
    ];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.startsWith('/api/v1/library/sources')) return new Response(JSON.stringify({ sources: [] }), { status: 200 });
    if (url.startsWith('/api/v1/library/entries')) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
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
    // the empty on-disk folder is selectable (appears in the sidebar and the picker)
    expect(screen.getAllByText('Archive').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: /Move here/i })).toBeInTheDocument();
  });

  it('shows empty folders in the bar and deletes a selected empty folder', async () => {
    const dirsTree = {
      name: 'All files', path: '', count: 0,
      children: { Empty: { name: 'Empty', path: '/Empty', count: 0, children: {} } },
    };
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.startsWith('/api/v1/library/sources')) return new Response(JSON.stringify({ sources: [] }), { status: 200 });
    if (url.startsWith('/api/v1/library/entries')) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    if (url.startsWith('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.startsWith('/api/v1/files/dirs')) return new Response(JSON.stringify(dirsTree), { status: 200 });
      if (url.startsWith('/api/v1/files/tree')) return new Response(JSON.stringify(dirsTree), { status: 200 });
      if (url.startsWith('/api/v1/files')) return new Response('[]', { status: 200 }); // no files anywhere
      return new Response('[]', { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('confirm', vi.fn(() => true));

    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    // empty folder is visible in the bar despite having zero files
    await waitFor(() => expect(screen.getByText('Empty')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Empty'));                       // select it
    fireEvent.click(screen.getByRole('button', { name: /Delete folder/i }));

    await waitFor(() => {
      const dels = fetchMock.mock.calls.filter(c =>
        typeof c[0] === 'string'
        && c[0].includes('/api/v1/files/folders')
        && (c[1] as RequestInit | undefined)?.method === 'DELETE');
      expect(dels.length).toBe(1);
      expect(dels[0][0] as string).toContain('path=%2FEmpty');
    });
  });

  it('creates a new folder as a child of the selected folder', async () => {
    const dirsTree = {
      name: 'All files', path: '', count: 0,
      children: { Customers: { name: 'Customers', path: '/Customers', count: 0, children: {} } },
    };
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.startsWith('/api/v1/library/sources')) return new Response(JSON.stringify({ sources: [] }), { status: 200 });
    if (url.startsWith('/api/v1/library/entries')) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    if (url.startsWith('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.startsWith('/api/v1/files/dirs')) return new Response(JSON.stringify(dirsTree), { status: 200 });
      if (url.startsWith('/api/v1/files/tree')) return new Response(JSON.stringify(dirsTree), { status: 200 });
      if (url.startsWith('/api/v1/files/folders')) return new Response(
        JSON.stringify({ path: '/Customers/Sub' }), { status: 201 });
      if (url.startsWith('/api/v1/files')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('prompt', vi.fn(() => 'Sub'));

    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Customers')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Customers'));                          // select parent
    fireEvent.click(screen.getAllByRole('button', { name: /New folder/i })[0]);

    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(c =>
        typeof c[0] === 'string'
        && c[0].includes('/api/v1/files/folders')
        && (c[1] as RequestInit | undefined)?.method === 'POST');
      expect(posts.length).toBe(1);
      expect(String((posts[0][1] as RequestInit).body)).toContain('/Customers/Sub');
    });
  });

  it('pins Job Uploads to the top and forbids deleting it', async () => {
    const dirsTree = {
      name: 'All files', path: '', count: 0,
      children: {
        Aaa: { name: 'Aaa', path: '/Aaa', count: 0, children: {} },
        'Job Uploads': { name: 'Job Uploads', path: '/Job Uploads', count: 0, children: {} },
      },
    };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.startsWith('/api/v1/library/sources')) return new Response(JSON.stringify({ sources: [] }), { status: 200 });
    if (url.startsWith('/api/v1/library/entries')) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    if (url.startsWith('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.startsWith('/api/v1/files/dirs')) return new Response(JSON.stringify(dirsTree), { status: 200 });
      if (url.startsWith('/api/v1/files/tree')) return new Response(JSON.stringify(dirsTree), { status: 200 });
      if (url.startsWith('/api/v1/files')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    }));

    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Job Uploads')).toBeInTheDocument());

    // Job Uploads is ordered before the alphabetically-earlier "Aaa"
    const ju = screen.getByText('Job Uploads');
    const aaa = screen.getByText('Aaa');
    expect(ju.compareDocumentPosition(aaa) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // selecting Job Uploads disables the delete-folder button
    fireEvent.click(ju);
    expect(screen.getByRole('button', { name: 'Delete folder' })).toBeDisabled();
  });
});


it('opens a local file from a deep link and closes it without reopening', async () => {
  render(<MemoryRouter initialEntries={['/files?file=1']}><FilesScreen /></MemoryRouter>);
  await screen.findByText('File details');
  fireEvent.click(screen.getByTitle('Close'));
  await waitFor(() => expect(screen.queryByText('File details')).not.toBeInTheDocument());
});

it('shows historical source attribution and keeps source failures separate from local file actions', async () => {
  const baseFetch = globalThis.fetch;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/library/files/1/origin') return new Response(JSON.stringify({ origin: {
      file_id: 1, entry_id: 'entry', provider_plugin_id: 'fixture', source_url: 'https://source.test/one', revision: 'v1',
      downloaded_at: '2026-11-20T00:00:00Z', metadata: { creator: { name: 'Original artist' }, license_code: 'CC-BY-4.0' },
      is_latest: false, source_missing: true,
    } }), { status: 200 });
    return baseFetch(url, init);
  }));
  render(<MemoryRouter initialEntries={['/files?file=1']}><FilesScreen /></MemoryRouter>);
  const origin = await screen.findByTestId('source-attribution');
  expect(origin).toHaveTextContent('Original artist');
  expect(origin).toHaveTextContent('CC-BY-4.0');
  expect(origin).toHaveTextContent('Earlier local revision');
  expect(origin).toHaveTextContent('Missing at source; local file retained.');
  expect(screen.getByRole('link', { name: 'Open in source' })).toHaveAttribute('href', 'https://source.test/one');
});

it('retries attribution errors while leaving local file actions available', async () => {
  const baseFetch = globalThis.fetch;
  let attempts = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/library/files/1/origin') {
      attempts++;
      return attempts === 1
        ? new Response(JSON.stringify({ detail: 'Temporary attribution failure' }), { status: 503 })
        : new Response(JSON.stringify({ origin: null }), { status: 200 });
    }
    return baseFetch(url, init);
  }));
  render(<MemoryRouter initialEntries={['/files?file=1']}><FilesScreen /></MemoryRouter>);
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: /Use in new job/i })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry source attribution' }));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(attempts).toBe(2);
});


it.each(['-1', '0', 'bad', '99999'])('ignores an invalid or absent local file deep link: %s', async id => {
  render(<MemoryRouter initialEntries={[`/files?file=${id}`]}><FilesScreen /></MemoryRouter>);
  await screen.findByText('arm.3mf');
  expect(screen.queryByText('File details')).not.toBeInTheDocument();
});
