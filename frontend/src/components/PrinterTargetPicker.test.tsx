import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';
import { PrinterTargetPicker } from './PrinterTargetPicker';
import { buildEligibility } from './ModelTargets';
import { defaultPerPrinterCfg } from './PerPrinterConfig';
import type { ApiPrinter } from '../api/printers';

const P = (id: number, name: string, model_uuid: string | null, model_label: string | null) =>
  ({ id, name, model_uuid, model_label, printer_type: 'bambu' } as unknown as ApiPrinter);
const PRINTERS = [P(1, 'Iris', 'm-p1s', 'Bambu Lab P1S'), P(2, 'Juno', 'm-p1s', 'Bambu Lab P1S'), P(3, 'Kepler', 'm-x1', 'Bambu Lab X1C')];

function Harness({ initial = [] as string[], onToggle = vi.fn() }) {
  const [sel, setSel] = useState<string[]>(initial);
  return <PrinterTargetPicker printers={PRINTERS} selected={sel}
    onToggle={k => { onToggle(k); setSel(s => (s.includes(k) ? s.filter(x => x !== k) : [...s, k])); }} />;
}

const item = (name: string | RegExp) => screen.getByRole('menuitem', { name });

describe('PrinterTargetPicker (BIZ-357)', () => {
  it('starts empty and flags that nothing will dispatch', () => {
    render(<Harness />);
    expect(screen.queryByTestId(/^target-badge-/)).toBeNull();
    expect(screen.getByText(/nothing will dispatch/)).toBeTruthy();
  });

  it('adds a group badge (yellow) and a printer badge (blue), and × removes one', () => {
    const onToggle = vi.fn();
    render(<Harness onToggle={onToggle} />);
    fireEvent.click(screen.getByRole('button', { name: '+ Printer' }));
    fireEvent.click(item(/^Any Bambu Lab X1C/));
    fireEvent.click(screen.getByRole('button', { name: '+ Printer' }));
    fireEvent.click(item(/^Iris/));
    expect(onToggle.mock.calls.map(c => c[0])).toEqual(['model:m-x1', '1']);
    expect(screen.getByTestId('target-badge-model:m-x1').className).toContain('warn');
    expect(screen.getByTestId('target-badge-1').className).toContain('info');
    fireEvent.click(screen.getByRole('button', { name: 'Remove Iris' }));
    expect(screen.queryByTestId('target-badge-1')).toBeNull();
    expect(screen.queryByText(/nothing will dispatch/)).toBeNull();
  });

  it('greys printers covered by a chosen model, and everything once Any printer is chosen', () => {
    render(<Harness initial={['model:m-p1s']} />);
    fireEvent.click(screen.getByRole('button', { name: '+ Printer' }));
    expect((item(/^Iris/) as HTMLButtonElement).disabled).toBe(true);
    expect((item(/^Kepler/) as HTMLButtonElement).disabled).toBe(false);
    expect((item(/^Any Bambu Lab P1S/) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(item(/^Any printer/));
    fireEvent.click(screen.getByRole('button', { name: '+ Printer' }));
    for (const name of [/^Kepler/, /^Any Bambu Lab X1C/, /^Any printer/]) expect((item(name) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId('target-badge-any').textContent).toContain('Any printer');
  });

  it('searches both sections', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: '+ Printer' }));
    fireEvent.change(screen.getByPlaceholderText('Search printers'), { target: { value: 'kep' } });
    expect(screen.queryByRole('menuitem', { name: /^Iris/ })).toBeNull();
    expect(item(/^Kepler/)).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: /^Any printer/ })).toBeNull();
  });

  it('turns an Any printer selection into a model target with a null model', () => {
    expect(buildEligibility(['any', '3'], { any: defaultPerPrinterCfg(), '3': defaultPerPrinterCfg() })).toMatchObject({
      model_targets: [{ model_uuid: null }], printer_configs: [{ printer_id: 3 }],
    });
  });
});
