import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/** Choose a printer (or group) in PrinterTargetPicker: open "+ Printer" and pick the menu entry whose name starts with `name`
 * (a menu entry's accessible name is its label followed by its hint). */
export async function pickPrinter(name: string | RegExp): Promise<void> {
  const matches = (accessible: string) => (typeof name === 'string' ? accessible.startsWith(name) : name.test(accessible));
  await userEvent.click(await screen.findByRole('button', { name: '+ Printer' }));
  await userEvent.click(await screen.findByRole('menuitem', { name: matches }));
}
