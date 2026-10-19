import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createChannel,
  describeRules,
  listChannels,
  reasonText,
  retryDelivery,
  updateChannel,
} from '@/api/notifications';

/**
 * The client's two jobs: send exactly what the contract names (and no field the backend would
 * refuse), and turn closed reason codes into words without ever inventing one.
 */

const ok = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

describe('notifications client', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('lists channels from the contract path', async () => {
    const fetchMock = vi.fn().mockReturnValue(ok({ items: [], email_available: false }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await listChannels();
    expect(result.kind).toBe('ok');
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/api/v1/notification-channels');
  });

  it('treats a malformed list as unreachable rather than as an empty one', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(ok({ surprise: true })));
    expect((await listChannels()).kind).not.toBe('ok');
  });

  it('creates with POST and a JSON body', async () => {
    const fetchMock = vi.fn().mockReturnValue(ok({ id: 'c1' }, 201));
    vi.stubGlobal('fetch', fetchMock);
    await createChannel({
      name: 'a',
      channel_type: 'WEBHOOK',
      destination_reference: 'env://A',
      routing_rules: {},
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/notification-channels');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      name: 'a',
      channel_type: 'WEBHOOK',
      destination_reference: 'env://A',
      routing_rules: {},
    });
  });

  it('patches by id, encoded, and cannot be asked to change the type', async () => {
    const fetchMock = vi.fn().mockReturnValue(ok({ id: 'c/1' }));
    vi.stubGlobal('fetch', fetchMock);
    await updateChannel('c/1', { status: 'DISABLED' });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/notification-channels/c%2F1');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ status: 'DISABLED' });
  });

  it('retries a delivery through its alert', async () => {
    const fetchMock = vi.fn().mockReturnValue(ok({ delivery_id: 'd1' }, 202));
    vi.stubGlobal('fetch', fetchMock);
    const result = await retryDelivery('a1', 'd1');
    expect(result.kind).toBe('ok');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/alerts/a1/notifications/d1/retry');
    expect(init.method).toBe('POST');
  });
});

describe('reasonText', () => {
  it('turns the closed codes into words', () => {
    expect(reasonText('timeout')).toBe('the destination did not answer in time');
    expect(reasonText('http_503')).toBe('the destination answered HTTP 503');
    expect(reasonText('redirect_refused')).toMatch(/redirect/);
    expect(reasonText('destination_refused')).toMatch(/not an address DCP may connect to/);
  });

  it('shows an unknown code as itself rather than hiding it', () => {
    expect(reasonText('something_new')).toBe('something_new');
  });

  it('says nothing for no reason', () => {
    expect(reasonText(null)).toBe('');
    expect(reasonText(undefined)).toBe('');
    expect(reasonText('')).toBe('');
  });
});

describe('describeRules', () => {
  it('calls an empty rule set every alert', () => {
    expect(describeRules({})).toBe('Every alert');
    expect(describeRules({ severity: [], environment: [], target_type: [] })).toBe('Every alert');
  });

  it('lists only the groups that restrict something', () => {
    expect(describeRules({ severity: ['CRITICAL', 'WARNING'], environment: [] })).toBe(
      'severity: CRITICAL, WARNING',
    );
    expect(describeRules({ environment: ['production'], target_type: ['service'] })).toBe(
      'environment: production · target type: service',
    );
  });
});
