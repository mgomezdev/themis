import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { SourceCatalogPanel } from './SourceCatalogPanel';
import { fetchCatalog, fetchSources, materializeEntry, syncSource, type CatalogEntry, type LibrarySource } from '../../api/library';

vi.mock('../../api/library', () => ({ fetchCatalog: vi.fn(), fetchSources: vi.fn(), materializeEntry: vi.fn(), syncSource: vi.fn() }));
const source: LibrarySource = { plugin_id: 'fixture', name: 'Fixture source', enabled: true, active: true, error: null, last_sync_at: null };
const entry = (id: string): CatalogEntry => ({ id, name: `Part ${id}.stl`, provider_plugin_id: 'fixture', source_file_id: id,
  source_url: `https://source.test/${id}`, missing: false, cached_file_id: null, metadata: {} });
const catalog = vi.mocked(fetchCatalog), sources = vi.mocked(fetchSources), prepare = vi.mocked(materializeEntry), sync = vi.mocked(syncSource);
function Location() { const location = useLocation(); return <div data-testid="location">{location.pathname}:{JSON.stringify(location.state)}</div>; }
const mount = () => render(<MemoryRouter><SourceCatalogPanel /><Location /></MemoryRouter>);
const loaded = async () => { await waitFor(() => expect(screen.queryByText('Loading library sources…')).not.toBeInTheDocument()); };

beforeEach(() => {
  vi.resetAllMocks();
  sources.mockResolvedValue({ sources: [source] });
  catalog.mockResolvedValue({ items: [entry('one')], total: 1 });
  sync.mockResolvedValue({ entries: 1, full_scan: true });
  prepare.mockResolvedValue({ file_id: 42, fresh: true, warning: null });
});

describe('SourceCatalogPanel', () => {
  it('retries an initial read failure without claiming that no sources are installed', async () => {
    catalog.mockRejectedValueOnce(new Error('Catalog unavailable'));
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('Catalog unavailable');
    expect(screen.queryByText(/No library sources are installed/)).not.toBeInTheDocument();
    expect(screen.queryByText('No synchronized files yet.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry library sources' }));
    await screen.findByRole('link', { name: 'Part one.stl' });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(catalog).toHaveBeenCalledTimes(2);
  });

  it('shows setup guidance only after a successful empty read', async () => {
    sources.mockResolvedValue({ sources: [] });
    catalog.mockResolvedValue({ items: [], total: 0 });
    mount();
    await screen.findByText(/No library sources are installed/);
    expect(screen.getByRole('link', { name: 'Open plugin settings' })).toHaveAttribute('href', '/settings/plugins');
    expect(screen.getByText('No synchronized files yet.')).toBeVisible();
  });

  it('retries sync failures and refreshes the catalog only on success', async () => {
    sync.mockRejectedValueOnce(new Error('Read scope missing'));
    mount(); await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Read scope missing');
    expect(catalog).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Synchronized 1 catalog entries.');
    await loaded();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(sync).toHaveBeenNthCalledWith(2, 'fixture');
    expect(catalog).toHaveBeenCalledTimes(2);
  });

  it('retains disabled-source metadata and warns before using an offline local copy', async () => {
    sources.mockResolvedValue({ sources: [{ ...source, enabled: false, active: false }] });
    catalog.mockResolvedValue({ items: [{ ...entry('one'), missing: true, cached_file_id: 42 }], total: 1 });
    prepare.mockResolvedValue({ file_id: 42, fresh: false, warning: 'Using the last-known local copy.' });
    mount(); await loaded();
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeDisabled();
    const row = screen.getByTestId('catalog-one');
    expect(row).toHaveTextContent('Missing at source');
    fireEvent.click(within(row).getByRole('button', { name: 'Prepare locally' }));
    await loaded();
    expect(await screen.findByRole('alert')).toHaveTextContent('Using the last-known local copy.');
    expect(screen.getByRole('link', { name: 'Open local details' })).toHaveAttribute('href', '/files?file=42');
    fireEvent.click(screen.getByRole('link', { name: 'Use in job' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/queue/new:{"libraryFileId":42}');
    expect(sync).not.toHaveBeenCalled();
  });

  it('removes an earlier success link when a subsequent preparation fails', async () => {
    mount(); await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Prepare locally' }));
    await screen.findByRole('link', { name: 'Use in job' }); await loaded();
    prepare.mockRejectedValueOnce(new Error('No local bytes available'));
    fireEvent.click(screen.getByRole('button', { name: 'Prepare locally' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('No local bytes available');
    expect(screen.queryByRole('link', { name: 'Use in job' })).not.toBeInTheDocument();
  });

  it('invalidates prepared links when the catalog is synchronized again', async () => {
    mount(); await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Prepare locally' }));
    await screen.findByRole('link', { name: 'Use in job' }); await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));
    await screen.findByRole('status'); await loaded();
    expect(screen.queryByRole('link', { name: 'Use in job' })).not.toBeInTheDocument();
  });

  it('does not display first-page rows as a failed second page and retries that page', async () => {
    const pageOne = Array.from({ length: 100 }, (_, i) => entry(String(i)));
    catalog.mockResolvedValueOnce({ items: pageOne, total: 101 })
      .mockRejectedValueOnce(new Error('Second page unavailable'))
      .mockResolvedValueOnce({ items: [entry('last')], total: 101 })
      .mockResolvedValueOnce({ items: pageOne, total: 101 });
    mount(); await loaded();
    expect(screen.getByText('1–100 of 101')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('alert');
    expect(screen.queryByRole('link', { name: 'Part 0.stl' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry library sources' }));
    await screen.findByRole('link', { name: 'Part last.stl' });
    expect(screen.getByText('101–101 of 101')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    expect(catalog).toHaveBeenNthCalledWith(3, 100);
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await screen.findByRole('link', { name: 'Part 0.stl' });
    expect(screen.getByText('1–100 of 101')).toBeVisible();
  });
});

it('reports configuration and sync health while retaining known source attribution', async () => {
  sources.mockResolvedValue({ sources: [{ ...source, active: false, error: 'Configure OAuth credentials', last_sync_at: '2026-11-20T00:00:00Z' }] });
  catalog.mockResolvedValue({ items: [{ ...entry('one'), metadata: { creator: { name: 'Artist' }, license_code: 'CC0-1.0' } }], total: 1 });
  mount(); await loaded();
  expect(screen.getByText('Needs configuration')).toBeVisible();
  expect(screen.getByText('Configure OAuth credentials')).toBeVisible();
  expect(screen.getByText(/Last sync:/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/plugins/fixture');
  expect(screen.getByTestId('catalog-one')).toHaveTextContent('Artist');
  expect(screen.getByTestId('catalog-one')).toHaveTextContent('CC0-1.0');
});

it('handles an unstructured load rejection with a usable retry message', async () => {
  catalog.mockRejectedValueOnce(null);
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load library sources');
  expect(screen.getByRole('button', { name: 'Retry library sources' })).toBeEnabled();
});
