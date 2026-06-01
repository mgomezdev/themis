import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  uploadFile, getFilePlates, getPrinterProfiles, createJob,
  getQueue, cancelJob, reorderQueue,
} from './queue';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function mockOk(body: unknown) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function mockErr(status: number, text: string) {
  mockFetch.mockResolvedValueOnce({
    ok: false,
    status,
    text: () => Promise.resolve(text),
    statusText: text,
  });
}

beforeEach(() => vi.clearAllMocks());

describe('uploadFile', () => {
  it('POSTs to /api/v1/files/upload and returns parsed file', async () => {
    const plate = { plate_number: 1, estimated_time: 3600, filament_g: 42, thumbnail_path: null };
    mockOk({ id: 1, original_filename: 'model.3mf', plates: [plate] });

    const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
    const result = await uploadFile(file);

    expect(mockFetch).toHaveBeenCalledWith('/api/v1/files/upload', expect.objectContaining({ method: 'POST' }));
    expect(result.id).toBe(1);
    expect(result.plates[0].plate_number).toBe(1);
  });

  it('throws on non-ok response', async () => {
    mockErr(422, 'Only .3mf files are accepted');
    const file = new File(['x'], 'model.stl');
    await expect(uploadFile(file)).rejects.toThrow('422');
  });
});

describe('getFilePlates', () => {
  it('fetches plates for a file', async () => {
    const plates = [{ plate_number: 1, estimated_time: 100, filament_g: 10, thumbnail_path: null }];
    mockOk(plates);
    const result = await getFilePlates(5);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/files/5/plates');
    expect(result).toEqual(plates);
  });
});

describe('getPrinterProfiles', () => {
  it('fetches print and filament profiles', async () => {
    mockOk({ print_profiles: ['0.20mm Standard'], filament_profiles: ['Bambu PLA'] });
    const result = await getPrinterProfiles(1);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/printers/1/profiles');
    expect(result.print_profiles).toContain('0.20mm Standard');
  });
});

describe('createJob', () => {
  it('POSTs to /api/v1/jobs', async () => {
    const job = { id: 1, uploaded_file_id: 1, plate_number: 1, order_id: null, assigned_printer_id: null, queue_position: 1, status: 'queued', created_at: '', updated_at: '' };
    mockOk(job);
    const result = await createJob({
      uploaded_file_id: 1,
      plate_number: 1,
      printer_configs: [{ printer_id: 2, print_profile: '0.20mm', filament_profile: 'PLA' }],
    });
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/jobs', expect.objectContaining({ method: 'POST' }));
    expect(result.id).toBe(1);
  });
});

describe('getQueue', () => {
  it('fetches active queue', async () => {
    mockOk([]);
    const result = await getQueue();
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/queue');
    expect(Array.isArray(result)).toBe(true);
  });
});

describe('cancelJob', () => {
  it('POSTs to cancel endpoint', async () => {
    const job = { id: 3, uploaded_file_id: 1, plate_number: 1, order_id: null, assigned_printer_id: null, queue_position: null, status: 'cancelled', created_at: '', updated_at: '' };
    mockOk(job);
    const result = await cancelJob(3);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/jobs/3/cancel', expect.objectContaining({ method: 'POST' }));
    expect(result.status).toBe('cancelled');
  });
});

describe('reorderQueue', () => {
  it('PATCHes to reorder endpoint', async () => {
    mockOk([]);
    await reorderQueue([{ job_id: 1, queue_position: 3.0 }]);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/queue/reorder', expect.objectContaining({ method: 'PATCH' }));
  });
});
