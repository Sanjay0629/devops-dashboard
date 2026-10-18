import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LogSearch } from '@/pages/logs/LogSearch';
import resultsSource from '@/pages/logs/LogResults.tsx?raw';
import searchSource from '@/pages/logs/LogSearch.tsx?raw';

/**
 * §19.1 from the page's side.
 *
 * What the page must get right is *honesty*: the request is a structured filter and
 * nothing like a query; a log store that cannot answer is not an empty table; an
 * empty table is not "no errors" unless every selected server is shipping; a
 * dropped server is announced; and a line is text, whatever it contains.
 */

const SERVERS = {
  items: [
    { id: 's1', display_name: 'Web 1', environment: 'staging' },
    { id: 's2', display_name: 'DB 1', environment: 'staging' },
  ],
  page: { limit: 50, next_cursor: null, has_more: false, total: null },
  demo_mode: false,
};
const APPLICATIONS = {
  items: [{ id: 'a1', name: 'Checkout API' }],
  page: { limit: 50, next_cursor: null, has_more: false, total: null },
  demo_mode: false,
};
const SOURCES = {
  source_types: ['application', 'nginx'],
  levels: ['debug', 'info', 'warn', 'error', 'critical', 'unknown'],
  environments: ['development', 'staging', 'production', 'other'],
  presets: [{ id: 'http_5xx', label: 'HTTP 5xx responses', description: 'Nginx 5xx' }],
  limits: {
    max_range_days: 7,
    retention_days: 14,
    max_limit: 1000,
    default_limit: 200,
    max_contains: 256,
    max_server_ids: 50,
  },
};

function entry(line: string, over: Record<string, unknown> = {}) {
  return {
    timestamp: '2026-10-18T08:59:59.123Z',
    server_id: 's1',
    hostname: 'web-1.internal',
    service: 'api',
    source_type: 'application',
    level: 'error',
    line,
    line_truncated: false,
    ...over,
  };
}

function answer(over: Record<string, unknown> = {}) {
  return {
    entries: [entry('first line')],
    page: { limit: 200, next_cursor: null, has_more: false, total: null },
    query_stats: { duration_ms: 3, bytes_scanned: null, truncated: false },
    excluded_by_scope_count: 0,
    servers_searched: 1,
    ingestion: [{ server_id: 's1', hostname: 'web-1.internal', state: 'receiving' }],
    data_state: 'ok',
    source: 'loki',
    ...over,
  };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });

function failure(status: number, code: string, message: string) {
  return reply(status, { error: { code, message } });
}

type Responder = (body: Record<string, unknown>, n: number) => Promise<unknown>;

function mockApi(onQuery: Responder = () => reply(200, answer())) {
  let n = 0;
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes('/logs/query')) {
      return onQuery(
        JSON.parse((init?.body as string | undefined) ?? '{}') as Record<string, unknown>,
        n++,
      );
    }
    if (url.includes('/logs/sources')) return reply(200, SOURCES);
    if (url.includes('/applications')) return reply(200, APPLICATIONS);
    return reply(200, SERVERS);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const queries = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter((call) => String(call[0]).includes('/logs/query'))
    .map((call) => JSON.parse((call[1] as { body: string }).body) as Record<string, unknown>);

function renderPage(state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/logs', state }]}>
      <LogSearch />
    </MemoryRouter>,
  );
}

async function pickAndSearch(...names: string[]) {
  for (const name of names) {
    await userEvent.click(await screen.findByRole('checkbox', { name: new RegExp(name) }));
  }
  await userEvent.click(screen.getByRole('button', { name: 'Search' }));
}

describe('LogSearch', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('cannot search with nothing chosen: logs are never searched estate-wide', async () => {
    mockApi();
    renderPage();
    await screen.findByRole('checkbox', { name: /Web 1/ });
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
    expect(screen.getByText(/never searched across the whole estate/)).toBeInTheDocument();
  });

  it('posts a structured filter and nothing like a query', async () => {
    const fetchMock = mockApi();
    renderPage();
    await userEvent.type(await screen.findByLabelText(/Service or unit/), 'nginx.service');
    await userEvent.type(screen.getByLabelText(/Contains/), 'upstream "x"');
    await userEvent.click(screen.getByRole('checkbox', { name: 'error' }));
    await userEvent.selectOptions(screen.getByLabelText('Common error pattern'), 'http_5xx');
    await pickAndSearch('Web 1', 'DB 1');

    await screen.findByText('first line');
    const [body] = queries(fetchMock);
    expect(body).toMatchObject({
      server_ids: ['s1', 's2'],
      service: 'nginx.service',
      contains: 'upstream "x"',
      level: ['error'],
      pattern_preset: 'http_5xx',
      direction: 'backward',
      limit: 200,
    });
    expect(Object.keys(body ?? {}).sort()).toEqual(
      [
        'contains',
        'direction',
        'from',
        'level',
        'limit',
        'pattern_preset',
        'server_ids',
        'service',
        'to',
      ].sort(),
    );
    for (const smuggled of ['query', 'logql', 'selector', 'regex', 'labels']) {
      expect(body).not.toHaveProperty(smuggled);
    }
  });

  it('leaves empty fields out instead of sending empty strings', async () => {
    const fetchMock = mockApi();
    renderPage();
    await pickAndSearch('Web 1');
    await screen.findByText('first line');
    expect(Object.keys(queries(fetchMock)[0] ?? {}).sort()).toEqual(
      ['direction', 'from', 'limit', 'server_ids', 'to'].sort(),
    );
  });

  it('a whole environment is a valid target on its own', async () => {
    const fetchMock = mockApi();
    renderPage();
    await userEvent.selectOptions(await screen.findByLabelText('Environment'), 'staging');
    await userEvent.click(screen.getByRole('button', { name: 'Search' }));
    await screen.findByText('first line');
    expect(queries(fetchMock)[0]).toMatchObject({ environment: 'staging' });
    expect(queries(fetchMock)[0]).not.toHaveProperty('server_ids');
  });

  it('shows each line with its timestamp, source, service, level and server', async () => {
    mockApi();
    renderPage();
    await pickAndSearch('Web 1');

    const row = (await screen.findByText('first line')).closest('tr') as HTMLElement;
    expect(within(row).getByText('web-1.internal')).toHaveAttribute('href', '/servers/s1');
    expect(within(row).getByText('application')).toBeInTheDocument();
    expect(within(row).getByText('api')).toBeInTheDocument();
    expect(within(row).getByText('error')).toBeInTheDocument(); // the word, not only a colour
    const time = row.querySelector('time');
    expect(time).toHaveAttribute('datetime', '2026-10-18T08:59:59.123Z');
    expect(time?.textContent).toMatch(/\.123$/);
    expect(screen.getByText(/1 line · 1 server searched/)).toBeInTheDocument();
  });

  it('renders a line containing markup as text, creating no element from it', async () => {
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script> <b>bold</b>';
    mockApi(() => reply(200, answer({ entries: [entry(hostile)] })));
    const { container } = renderPage();
    await pickAndSearch('Web 1');

    await screen.findByText(hostile);
    expect(container.querySelector('img, script, b')).toBeNull();
  });

  it('never uses dangerouslySetInnerHTML (the pin behind the test above)', () => {
    // Comments are stripped first: the source explains the rule in words.
    const code = (source: string) =>
      source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    expect(code(resultsSource)).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
    expect(code(searchSource)).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
  });

  it('says a line was cut', async () => {
    mockApi(() => reply(200, answer({ entries: [entry('cut', { line_truncated: true })] })));
    renderPage();
    await pickAndSearch('Web 1');
    await screen.findByText('cut');
    expect(screen.getByText('(line cut here)')).toBeInTheDocument();
  });

  // --- the log store cannot answer: never an empty table ----------------------------

  it.each([
    [503, 'provider_unavailable', /log store is unavailable/],
    [504, 'provider_timeout', /did not answer in time/],
    [503, 'not_configured', /not configured on this deployment/],
  ])('a %i %s is its own state, not an empty result', async (status, code, wording) => {
    mockApi(() => failure(status, code, 'whatever the server said'));
    renderPage();
    await pickAndSearch('Web 1');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(wording);
    expect(alert).toHaveTextContent('This is not an absence of logs');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByText(/No lines match/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not reach the backend/)).not.toBeInTheDocument();
  });

  it('a failed search clears the table: stale rows under an error would read as the answer', async () => {
    mockApi((_, n) => (n === 0 ? reply(200, answer()) : failure(503, 'provider_unavailable', 'x')));
    renderPage();
    await pickAndSearch('Web 1');
    await screen.findByText('first line');

    await userEvent.click(screen.getByRole('button', { name: 'Search' }));
    await screen.findByRole('alert');
    expect(screen.queryByText('first line')).not.toBeInTheDocument();
  });

  // --- empty is not "no errors" (issue 93) ---------------------------------------------

  it('an empty result may say "no matching lines" only when every server is shipping', async () => {
    mockApi(() => reply(200, answer({ entries: [] })));
    renderPage();
    await pickAndSearch('Web 1');
    expect(await screen.findByText(/No lines match these filters/)).toBeInTheDocument();
    expect(screen.queryByText(/does not mean/)).not.toBeInTheDocument();
  });

  it('an empty result from a silent server refuses to say "no errors"', async () => {
    mockApi(() =>
      reply(
        200,
        answer({
          entries: [],
          ingestion: [{ server_id: 's1', hostname: 'web-1.internal', state: 'silent' }],
        }),
      ),
    );
    renderPage();
    await pickAndSearch('Web 1');

    const notice = await screen.findByRole('status');
    expect(notice).toHaveTextContent('That does not mean there are no errors');
    expect(notice).toHaveTextContent('web-1.internal has sent no logs in the last 24 hours');
    expect(notice).toHaveTextContent('a shipper that cannot read its files');
    expect(screen.queryByText(/No lines match these filters/)).not.toBeInTheDocument();
  });

  it.each([
    ['quiet', /no logs in the last 15 minutes/],
    ['unknown', /could not be checked just now/],
  ])('a %s server is named, with what that means', async (state, wording) => {
    mockApi(() =>
      reply(
        200,
        answer({
          entries: [],
          ingestion: [{ server_id: 's1', hostname: 'web-1.internal', state }],
        }),
      ),
    );
    renderPage();
    await pickAndSearch('Web 1');
    expect(await screen.findByRole('status')).toHaveTextContent(wording);
  });

  it('lines from one server do not hide that another is silent', async () => {
    mockApi(() =>
      reply(
        200,
        answer({
          servers_searched: 2,
          ingestion: [
            { server_id: 's1', hostname: 'web-1.internal', state: 'receiving' },
            { server_id: 's2', hostname: 'db-1.internal', state: 'silent' },
          ],
        }),
      ),
    );
    renderPage();
    await pickAndSearch('Web 1', 'DB 1');

    await screen.findByText('first line');
    const notice = screen.getByRole('status');
    expect(notice).toHaveTextContent('Some selected servers may be missing from these results');
    expect(notice).toHaveTextContent('db-1.internal');
    expect(notice).not.toHaveTextContent('web-1.internal');
  });

  it('nothing in scope is said as such, not as an empty search', async () => {
    mockApi(() => reply(200, answer({ entries: [], servers_searched: 0, ingestion: [] })));
    renderPage();
    await pickAndSearch('Web 1');
    expect(
      await screen.findByText(/None of the selected servers can be searched/),
    ).toBeInTheDocument();
  });

  it('announces a requested server that was left out of scope', async () => {
    mockApi(() => reply(200, answer({ excluded_by_scope_count: 2 })));
    renderPage();
    await pickAndSearch('Web 1');
    expect(
      await screen.findByText(/2 requested servers are outside your access and were not searched/),
    ).toBeInTheDocument();
  });

  // --- the other failures -----------------------------------------------------------------

  it('403 reads as a permission problem', async () => {
    mockApi(() => failure(403, 'forbidden', 'no'));
    renderPage();
    await pickAndSearch('Web 1');
    expect(await screen.findByRole('alert')).toHaveTextContent(/do not have permission/);
  });

  it('a 422 limit is shown in the server’s words', async () => {
    mockApi(() =>
      failure(
        422,
        'query_limit_exceeded',
        'The requested query exceeds the retention_seconds limit.',
      ),
    );
    renderPage();
    await pickAndSearch('Web 1');
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /exceeds the retention_seconds limit/,
    );
  });

  // --- paging ---------------------------------------------------------------------------------

  it('loads older lines with the cursor, appends them, and stops when there is no more', async () => {
    const fetchMock = mockApi((_, n) =>
      reply(
        200,
        n === 0
          ? answer({
              entries: [entry('newer')],
              page: { limit: 1, next_cursor: 'CURSOR-1', has_more: true, total: null },
            })
          : answer({ entries: [entry('older')] }),
      ),
    );
    renderPage();
    await pickAndSearch('Web 1');
    await screen.findByText('newer');
    expect(screen.getByText(/more lines match than are shown/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Load older lines' }));
    await screen.findByText('older');
    expect(screen.getByText('newer')).toBeInTheDocument(); // appended, not replaced

    const [first, second] = queries(fetchMock);
    expect(first).not.toHaveProperty('cursor');
    expect(second).toMatchObject({ cursor: 'CURSOR-1', server_ids: ['s1'] });
    expect(screen.queryByRole('button', { name: 'Load older lines' })).not.toBeInTheDocument();
  });

  it('continues the query that produced the rows, not the form as it has since been edited', async () => {
    const fetchMock = mockApi((_, n) =>
      reply(
        200,
        n === 0
          ? answer({ page: { limit: 1, next_cursor: 'C', has_more: true, total: null } })
          : answer({ entries: [entry('older')] }),
      ),
    );
    renderPage();
    await pickAndSearch('Web 1');
    await screen.findByText('first line');
    await userEvent.type(screen.getByLabelText(/Contains/), 'edited after the search');
    await userEvent.click(screen.getByRole('button', { name: 'Load older lines' }));
    await screen.findByText('older');
    expect(queries(fetchMock)[1]).not.toHaveProperty('contains');
  });

  it('a failed "load older" keeps the lines already shown and says why', async () => {
    mockApi((_, n) =>
      n === 0
        ? reply(200, answer({ page: { limit: 1, next_cursor: 'C', has_more: true, total: null } }))
        : failure(503, 'provider_unavailable', 'x'),
    );
    renderPage();
    await pickAndSearch('Web 1');
    await screen.findByText('first line');
    await userEvent.click(screen.getByRole('button', { name: 'Load older lines' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/log store is unavailable/);
    expect(screen.getByText('first line')).toBeInTheDocument();
  });

  it('discards a slow "load older" that a newer search has overtaken', async () => {
    let releaseSlow: (value: unknown) => void = () => undefined;
    const first = answer({ page: { limit: 1, next_cursor: 'C', has_more: true, total: null } });
    mockApi((_, n) =>
      n === 0
        ? reply(200, first)
        : n === 1
          ? new Promise((resolve) => {
              releaseSlow = resolve;
            })
          : reply(200, answer({ entries: [entry('the newer search')] })),
    );
    renderPage();
    await pickAndSearch('Web 1');
    await screen.findByText('first line');
    await userEvent.click(screen.getByRole('button', { name: 'Load older lines' }));
    await userEvent.click(screen.getByRole('button', { name: 'Search' }));
    await screen.findByText('the newer search');

    releaseSlow({
      ok: true,
      status: 200,
      json: () => Promise.resolve(answer({ entries: [entry('stale answer')] })),
    });
    await waitFor(() => {
      expect(screen.queryByText('stale answer')).not.toBeInTheDocument();
    });
    expect(screen.getByText('the newer search')).toBeInTheDocument();
    expect(screen.queryByText('first line')).not.toBeInTheDocument();
  });

  // --- navigation in -------------------------------------------------------------------------------

  it('opens pre-filled from a service, searches once, and puts nothing in the URL', async () => {
    const fetchMock = mockApi();
    renderPage({ servers: [{ id: 's1', label: 'Web 1' }], service: 'nginx.service' });

    await screen.findByText('first line');
    expect(queries(fetchMock)).toHaveLength(1);
    expect(queries(fetchMock)[0]).toMatchObject({ server_ids: ['s1'], service: 'nginx.service' });
    expect(screen.getByLabelText(/Service or unit/)).toHaveValue('nginx.service');
    expect(window.location.search).toBe('');
  });

  it('ignores history state that is not a pre-fill', async () => {
    const fetchMock = mockApi();
    renderPage({ servers: 'nope', service: 5 });
    await screen.findByRole('checkbox', { name: /Web 1/ });
    expect(queries(fetchMock)).toHaveLength(0);
  });
});
