import { test, expect } from '@playwright/test';

// Real browser -> FastAPI -> Manyfold adapter -> loopback HTTP fixture -> local artifacts/SQLite.
// No route interception; the source exposes only fixture bytes and public/read OAuth scope.
test('source catalog materializes revisioned local files and stays usable after disabling the source', async ({ page, request }) => {
  const key = 'isolated-browser-test-key';
  await page.addInitScript(k => localStorage.setItem('themis.apiKey', k), key);
  const api = async (method: string, path: string, data?: unknown) => {
    const response = await request.fetch(`/api/v1${path}`, { method, headers: { 'X-Api-Key': key }, data });
    expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  };
  await api('PUT', '/plugins/manyfold', { enabled: true, settings: { url: 'http://127.0.0.1:18072', client_id: 'browser-client' },
    secrets: { client_secret: 'browser-fixture-secret' } });
  await page.goto('/files');
  await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await page.getByRole('button', { name: 'Sync now' }).click();
  await expect(page.getByRole('status')).toHaveText('Synchronized 1 catalog entries.');
  const catalog = (await api('GET', '/library/entries')).items[0];
  const row = page.getByTestId(`catalog-${catalog.id}`);
  await expect(row).toContainText('Browser fixture artist');
  await expect(row).toContainText('CC-BY-4.0');
  expect((await (await request.get('http://127.0.0.1:18072/__fixture/stats')).json()).downloads).toBe(0);
  await row.getByRole('button', { name: 'Prepare locally' }).click();
  await expect(row).toContainText('Local file ready.');
  const first = await api('GET', `/library/entries/${catalog.id}`);
  expect(first.cached_file_id).toBeGreaterThan(0);
  const bytes = async (id: number) => {
    const response = await request.get(`/api/v1/files/${id}/download`, { headers: { 'X-Api-Key': key } });
    expect(response.ok()).toBeTruthy();
    return response.text();
  };
  expect(await bytes(first.cached_file_id)).toContain('version1');
  await row.getByRole('link', { name: 'Use in job' }).click();
  await expect(page).toHaveURL(/\/queue\/new$/);
  expect(await page.evaluate(() => window.history.state.usr.libraryFileId)).toBe(first.cached_file_id);

  await api('PUT', '/plugins/mock', { enabled: true });
  const printer = await api('POST', '/printers', { name: 'Source revision printer', printer_type: 'mock', connection_config: {} });
  await api('PATCH', `/printers/${printer.id}`, { queue_on: false });
  const job = await api('POST', '/jobs', { uploaded_file_id: first.cached_file_id, plate_number: 1,
    printer_configs: [{ printer_id: printer.id, print_profile: 'Fixture process', filament_type: 'PLA', filament_subtype: 'standard', filament_color: '#FFFFFF' }] });

  await request.post('http://127.0.0.1:18072/__fixture/version', { data: { version: 2 } });
  await page.goto('/files');
  await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await row.getByRole('button', { name: 'Prepare locally' }).click();
  await expect(row).toContainText('Local file ready.');
  const second = await api('GET', `/library/entries/${catalog.id}`);
  expect(second.cached_file_id).not.toBe(first.cached_file_id);
  expect(await bytes(first.cached_file_id)).toContain('version1');
  expect(await bytes(second.cached_file_id)).toContain('version2');
  expect((await api('GET', `/jobs/${job.id}`)).uploaded_file_id).toBe(first.cached_file_id);
  await api('PUT', '/plugins/manyfold', { enabled: false });
  await page.reload();
  await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sync now' })).toBeDisabled();
  await row.getByRole('button', { name: 'Prepare locally' }).click();
  await expect(row.getByRole('alert')).toContainText('Using the last-known local copy.');
  await expect(row.getByRole('link', { name: 'Use in job' })).toBeVisible();
  const retained = await api('GET', `/library/entries/${catalog.id}`);
  expect(retained.cached_file_id).toBe(second.cached_file_id);
});
