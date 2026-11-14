import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

// Operator journey: a printer raises an alarm -> it shows under "Needs attention" and on the sidebar -> the operator
// acknowledges it -> it leaves the attention list but stays in History, and the request that did it is the one the
// backend expects. The alarm list is stateful, so what renders after the click is what the server would now return.

const RAISED = {
  id: 7, printer_id: 1, printer_name: 'Mono', code: 'HMS_0300_0100', severity: 'error', message: 'Heatbed temperature is abnormal',
  source: 'printer', help_url: 'https://example.test/help/0300-0100', first_seen: '2026-11-09T08:15:00+00:00',
  last_seen: '2026-11-09T08:16:00+00:00', resolved_at: null, acknowledged_at: null, active: true,
};

test('an alarm is shown, acknowledged, and kept in history', async ({ page }) => {
  let alarm: typeof RAISED & { acknowledged_at: string | null } = { ...RAISED };
  const asked: string[] = [];
  const mocks = await mockApi(page, {
    respond: (method, path) => {
      if (method === 'POST' && path === '/alarms/7/acknowledge') {
        alarm = { ...alarm, acknowledged_at: '2026-11-09T08:20:00+00:00' };
        return alarm;
      }
      return undefined;
    },
  });
  // Registered after mockApi, so these win for the alarm reads; writes fall through to mockApi and are captured.
  await page.route('**/api/v1/alarms**', async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') return route.fallback();
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname.endsWith('/alarms/summary')) {
      const open = alarm.acknowledged_at ? 0 : 1;
      return json({ count: open, worst: open ? 'error' : null, printers: open ? [{ printer_id: 1, count: 1, worst: 'error' }] : [] });
    }
    if (url.pathname.endsWith('/alarms/settings')) return json({ min_severity: 'warning', severities: ['info', 'warning', 'error', 'fatal'] });
    const status = url.searchParams.get('status') ?? 'active';
    asked.push(status);
    return json(status === 'unacknowledged' && alarm.acknowledged_at ? [] : [alarm]);
  });

  await page.goto('/alarms');

  // 1. It opens on "Needs attention" and shows what is wrong, on which printer.
  const row = page.getByTestId('alarm-7');
  await expect(row).toContainText('Heatbed temperature is abnormal');
  await expect(row).toContainText('Mono');
  await expect(row).toContainText('HMS_0300_0100');
  await expect(row).toContainText('active');
  await expect(row.getByRole('link', { name: 'Help' })).toHaveAttribute('href', RAISED.help_url);   // vendor-neutral label
  expect(asked[0]).toBe('unacknowledged');

  // 2. Acknowledge it: one POST for this alarm, and it leaves the attention list.
  await row.getByRole('button', { name: 'Acknowledge HMS_0300_0100' }).click();
  await expect(page.getByTestId('alarms-empty')).toContainText('every printer reports healthy');
  const posts = mocks.captured.filter(c => c.method === 'POST');
  expect(posts.map(c => c.url)).toEqual(['/alarms/7/acknowledge']);
  await expect(page.getByRole('button', { name: 'Acknowledge all' })).toBeDisabled();

  // 3. History still has it, marked acknowledged, with nothing left to press.
  await page.getByRole('button', { name: 'History' }).click();
  await expect(page.getByTestId('alarm-7')).toContainText('acknowledged');
  await expect(page.getByTestId('alarm-7').getByRole('button')).toHaveCount(0);
  expect(asked).toContain('all');
});
