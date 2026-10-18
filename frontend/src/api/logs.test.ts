import { beforeEach, describe, expect, it, vi } from 'vitest';

import { readLogSources, searchLogs, type LogQueryRequest } from '@/api/logs';

const BODY: LogQueryRequest = {
  from: '2026-10-18T08:00:00Z',
  to: '2026-10-18T09:00:00Z',
  direction: 'backward',
  limit: 200,
  server_ids: ['s1'],
};

const reply = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => (body === undefined ? Promise.reject(new Error('no body')) : Promise.resolve(body)),
  });

describe('searchLogs', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('POSTs the filter as JSON to the one endpoint', async () => {
    const fetchMock = reply(200, { entries: [], page: {} });
    vi.stubGlobal('fetch', fetchMock);
    await searchLogs(BODY);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/logs/query');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual(BODY);
  });

  it.each([
    [503, 'provider_unavailable', 'provider_unavailable'],
    [504, 'provider_timeout', 'provider_timeout'],
    [503, 'not_configured', 'not_configured'],
    [503, 'something_new', 'provider_unavailable'],
  ])('%i %s is `unavailable` (%s), never `unreachable`', async (status, code, expected) => {
    vi.stubGlobal('fetch', reply(status, { error: { code, message: 'm' } }));
    expect(await searchLogs(BODY)).toEqual({ kind: 'unavailable', code: expected, message: 'm' });
  });

  it('an unreadable 503 body is still `unavailable`', async () => {
    vi.stubGlobal('fetch', reply(503, undefined));
    expect(await searchLogs(BODY)).toMatchObject({
      kind: 'unavailable',
      code: 'provider_unavailable',
    });
  });

  it('keeps the shared ladder for everything else', async () => {
    vi.stubGlobal('fetch', reply(403, {}));
    expect(await searchLogs(BODY)).toEqual({ kind: 'forbidden' });
    vi.stubGlobal('fetch', reply(422, { error: { code: 'validation_failed', message: 'bad' } }));
    expect(await searchLogs(BODY)).toMatchObject({ kind: 'invalid', message: 'bad' });
  });

  it('a 200 that is not a search result is not believed', async () => {
    vi.stubGlobal('fetch', reply(200, { surprise: true }));
    expect(await searchLogs(BODY)).toMatchObject({ kind: 'unreachable' });
  });

  it('a transport failure is `unreachable`, which is a different fact from `unavailable`', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    expect(await searchLogs(BODY)).toEqual({ kind: 'unreachable', reason: 'offline' });
  });
});

describe('readLogSources', () => {
  it('reads the catalogue', async () => {
    vi.stubGlobal('fetch', reply(200, { source_types: [], presets: [] }));
    expect((await readLogSources()).kind).toBe('ok');
  });
});
