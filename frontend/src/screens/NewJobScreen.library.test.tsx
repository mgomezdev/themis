import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { NewJobScreen } from './NewJobScreen';

const FILES = [{ id: 7, original_filename: 'lib_part.3mf', relative_path: 'Job Uploads/lib_part.3mf',
  folder: '/Job Uploads', size_bytes: 1000, plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/v1/files/7/plates')) return new Response(JSON.stringify(
      [{ plate_number: 1, thumbnail_path: null, estimated_time: 0, filament_g: 0 }]), { status: 200 });
    if (url.startsWith('/api/v1/files')) return new Response(JSON.stringify(FILES), { status: 200 });
    if (url.startsWith('/api/v1/printers')) return new Response('[]', { status: 200 });
    return new Response('[]', { status: 200 });
  }));
});

describe('NewJob library picker', () => {
  it('offers a Pick from library option that lists library files', async () => {
    render(<MemoryRouter><NewJobScreen /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /Pick from library/i }));
    await waitFor(() => expect(screen.getByText('lib_part.3mf')).toBeInTheDocument());
  });
});
