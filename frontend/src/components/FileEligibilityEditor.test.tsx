import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FileEligibilityEditor } from './FileEligibilityEditor';
import { stubFetch } from '../test/fetchStub';

const model = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id, plugin_id: 'acme', manufacturer_id: 'acme', manufacturer_name: 'Acme', model_id: id, display_name: name, bed_mm: [256, 256],
  toolheads: 1, enabled: true, dormant: false, dormant_reason: null, printer_count: 0, ...over,
});
const MODELS = [model('x1', 'X1'), model('x2', 'X2'), model('old', 'Old', { enabled: false })];

afterEach(() => vi.unstubAllGlobals());

describe('FileEligibilityEditor', () => {
  it('shows unknown eligibility as unknown — never as a match — and offers only enabled models', async () => {
    stubFetch({ 'GET /api/v1/printer-models': MODELS, 'GET /api/v1/files/5/eligibility': { known: false, models: [] } });
    render(<FileEligibilityEditor fileId={5} />);

    expect((await screen.findByRole('status')).textContent).toMatch(/Unknown/);
    expect(screen.getByLabelText('Acme X1')).not.toBeChecked();
    expect(screen.getByLabelText('Acme X2')).not.toBeChecked();
    expect(screen.queryByLabelText('Acme Old')).toBeNull();                              // disabled in the registry
  });

  it('PUTs exactly the ticked models, then reports the file as known', async () => {
    const api = stubFetch({
      'GET /api/v1/printer-models': MODELS, 'GET /api/v1/files/5/eligibility': { known: false, models: [] },
      'PUT /api/v1/files/5/eligibility': { known: true, models: [{ model_uuid: 'x2', source: 'manual', display_name: 'X2', manufacturer_name: 'Acme' }] },
    });
    const onSaved = vi.fn();
    render(<FileEligibilityEditor fileId={5} onSaved={onSaved} />);

    await userEvent.click(await screen.findByLabelText('Acme X2'));
    await userEvent.click(screen.getByRole('button', { name: 'Save eligibility' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(api.to('PUT', '/api/v1/files/5/eligibility')[0].body).toEqual({ model_uuids: ['x2'] });
    expect(screen.queryByRole('status')).toBeNull();                                      // no longer "unknown"
  });

  it('keeps a model the user later disabled visible (and removable) while the file still lists it', async () => {
    stubFetch({
      'GET /api/v1/printer-models': MODELS,
      'GET /api/v1/files/5/eligibility': { known: true, models: [{ model_uuid: 'old', source: 'target', display_name: 'Old', manufacturer_name: 'Acme' }] },
    });
    render(<FileEligibilityEditor fileId={5} />);

    expect(await screen.findByLabelText('Acme Old')).toBeChecked();
  });

  it('surfaces a save failure and stays editable', async () => {
    const { Reply } = await import('../test/fetchStub');
    stubFetch({
      'GET /api/v1/printer-models': MODELS, 'GET /api/v1/files/5/eligibility': { known: true, models: [] },
      'PUT /api/v1/files/5/eligibility': new Reply(422, 'Unknown printer model(s): nope'),
    });
    render(<FileEligibilityEditor fileId={5} />);

    await userEvent.click(await screen.findByLabelText('Acme X1'));
    await userEvent.click(screen.getByRole('button', { name: 'Save eligibility' }));

    expect((await screen.findByRole('alert')).textContent).toMatch(/422/);
    expect(screen.getByRole('button', { name: 'Save eligibility' })).not.toBeDisabled();
  });
});
