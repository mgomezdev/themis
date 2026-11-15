import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useState } from 'react';
import { SliceOverridesEditor, resetOverrideSettingsCache } from './SliceOverridesEditor';
import { stubFetch, Reply } from '../test/fetchStub';

const SETTINGS = [
  { key: 'enable_support', label: 'Enable supports', kind: 'bool', options: ['0', '1'] },
  { key: 'brim_type', label: 'Brim type', kind: 'enum', options: ['auto_brim', 'outer_only', 'no_brim'] },
  { key: 'wall_loops', label: 'Wall loops', kind: 'text', options: [] },
];

function Harness({ initial = {}, onValue }: { initial?: Record<string, string>; onValue: (v: Record<string, string>) => void }) {
  const [v, setV] = useState(initial);
  return <SliceOverridesEditor title="Job setting overrides" value={v} onChange={n => { setV(n); onValue(n); }} />;
}

beforeEach(() => {
  vi.restoreAllMocks();
  resetOverrideSettingsCache();
});

describe('SliceOverridesEditor', () => {
  it('adds "enable supports" as Yes and a brim type, edits and removes them', async () => {
    stubFetch({ 'GET /api/v1/jobs/override-settings': SETTINGS });
    const seen: Record<string, string>[] = [];
    render(<Harness onValue={v => seen.push(v)} />);

    fireEvent.change(await screen.findByLabelText('Add override'), { target: { value: 'enable_support' } });
    expect(seen[seen.length - 1]).toEqual({ enable_support: '1' });
    expect((screen.getByLabelText('Enable supports') as HTMLSelectElement).selectedOptions[0].textContent).toBe('Yes');

    fireEvent.change(screen.getByLabelText('Add override'), { target: { value: 'brim_type' } });
    fireEvent.change(screen.getByLabelText('Brim type'), { target: { value: 'outer_only' } });
    expect(seen[seen.length - 1]).toEqual({ enable_support: '1', brim_type: 'outer_only' });

    fireEvent.click(screen.getByRole('button', { name: 'Remove Enable supports' }));
    expect(seen[seen.length - 1]).toEqual({ brim_type: 'outer_only' });
    // a removed key is offered again; a chosen one isn't
    const addable = [...(screen.getByLabelText('Add override') as HTMLSelectElement).options].map(o => o.value);
    expect(addable).toEqual(['', 'enable_support', 'wall_loops']);
  });

  it('shows saved values, including one outside the known options, and edits free text', async () => {
    stubFetch({ 'GET /api/v1/jobs/override-settings': SETTINGS });
    const seen: Record<string, string>[] = [];
    render(<Harness initial={{ brim_type: 'brim_ears', wall_loops: '3' }} onValue={v => seen.push(v)} />);
    expect((await screen.findByLabelText('Brim type') as HTMLSelectElement).value).toBe('brim_ears');
    fireEvent.change(screen.getByLabelText('Wall loops'), { target: { value: '5' } });
    expect(seen[seen.length - 1]).toEqual({ brim_type: 'brim_ears', wall_loops: '5' });
  });

  it('renders nothing when the settings cannot be loaded, and loads them on the next mount', async () => {
    stubFetch({ 'GET /api/v1/jobs/override-settings': new Reply(500, 'down') });
    const first = render(<Harness onValue={() => {}} />);
    await act(async () => {});
    expect(first.container.textContent).toBe('');
    first.unmount();

    stubFetch({ 'GET /api/v1/jobs/override-settings': SETTINGS });     // a failed load is not cached
    render(<Harness onValue={() => {}} />);
    expect(await screen.findByLabelText('Add override')).toBeTruthy();
  });
});
