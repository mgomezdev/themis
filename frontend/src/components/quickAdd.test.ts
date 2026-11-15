import { describe, expect, it } from 'vitest';
import type { ConnectionField, DiscoveredPrinter } from '../api/printers';
import { IP_FIELD, printerType } from '../test/printerTypes';
import { defaultNickname, missingQuickAdd, quickAddFields, runPool, statusLabel, stripBlank } from './quickAdd';

const field = (name: string, label: string, over: Partial<ConnectionField> = {}): ConnectionField =>
  ({ ...IP_FIELD, name, label, quick_add: true, ...over });

const BAMBU = printerType({
  plugin_id: 'bambu', display_name: 'P1S',
  connection_fields: [IP_FIELD, field('serial_number', 'Serial Number'), field('access_code', 'Access Code', { field_type: 'password' }),
    field('use_ams', 'Use AMS', { quick_add: false, required: false })],
});

const found = (over: Partial<DiscoveredPrinter> = {}): DiscoveredPrinter => ({
  model_uuid: null, printer_type: 'bambu', plugin_id: 'bambu', display_name: 'Bambu Lab', ip: '192.168.7.30', model: 'P1S',
  name: null, serial: null, connection_config: {}, note: null, already_added: false, added_printer: null, ip_in_use_by: null, ...over,
});

describe('quick-add helpers', () => {
  it('takes the quick-add inputs from the plugin metadata only', () => {
    expect(quickAddFields(BAMBU).map(f => f.name)).toEqual(['serial_number', 'access_code']);
    expect(quickAddFields(printerType({ plugin_id: 'elegoo_centauri', connection_fields: [IP_FIELD] }))).toEqual([]);
  });

  it('drops blank values instead of sending ""', () => {
    expect(stripBlank({ a: '', b: '  ', c: null, d: 0, e: 'x', f: undefined })).toEqual({ d: 0, e: 'x' });
  });

  it('names the required quick-add fields left blank', () => {
    expect(missingQuickAdd(BAMBU, { serial_number: ' ', access_code: '' })).toEqual(['Serial Number', 'Access Code']);
    expect(missingQuickAdd(BAMBU, { serial_number: 'S', access_code: 'A' })).toEqual([]);
  });

  it('nicknames a row from the scan, else model + last octet', () => {
    expect(defaultNickname(found({ name: 'Bay 3' }), BAMBU)).toBe('Bay 3');
    expect(defaultNickname(found(), BAMBU)).toBe('P1S 30');
    expect(defaultNickname(found(), null)).toBe('P1S 30');
  });

  it('labels each row status with the chip copy', () => {
    expect(statusLabel('ready')).toBe('Ready to add');
    expect(statusLabel('missing', 'Access Code')).toBe('Missing Access Code');
    expect(statusLabel('testing')).toBe('Testing…');
    expect(statusLabel('test_ok')).toBe('Connection OK');
    expect(statusLabel('test_failed')).toBe('Test failed');
    expect(statusLabel('adding')).toBe('Adding…');
    expect(statusLabel('added_configured')).toBe('Added · configured');
    expect(statusLabel('added_unconfigured', 'Missing Access Code')).toBe('Added · not configured: Missing Access Code');
    expect(statusLabel('exists')).toBe('Already added');
  });

  it('runPool never runs more than the limit and settles a failure without stopping the rest', async () => {
    let running = 0;
    let peak = 0;
    const out = await runPool([1, 2, 3, 4, 5, 6, 7], 4, async (n) => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise(r => setTimeout(r, 5));
      running -= 1;
      if (n === 2) throw new Error('boom');
      return n * 10;
    });
    expect(peak).toBe(4);
    expect(out.map(r => r.status)).toEqual(['fulfilled', 'rejected', 'fulfilled', 'fulfilled', 'fulfilled', 'fulfilled', 'fulfilled']);
    expect(out[6]).toEqual({ status: 'fulfilled', value: 70 });
  });
});
