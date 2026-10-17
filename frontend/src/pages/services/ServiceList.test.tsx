import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ServerServices } from '@/pages/services/ServerServices';
import { ServiceList } from '@/pages/services/ServiceList';

/**
 * The service list (§16.1, §16.2) — IMPLEMENTATION_PLAN.md M2.4 frontend tests.
 *
 * The test that matters most is the unreachable-server one: PRD §4.5 makes "an
 * unreachable server does not render as all-services-inactive" an acceptance
 * criterion, and it is the easiest thing to regress — one `?? 'INACTIVE'` added
 * to silence a type error turns a dead host into a column of stopped services,
 * and nothing else in the suite would notice.
 *
 * Filters are asserted on the **request**, as in `ServerList.test`: the state
 * filter selects on what a row *shows*, which only the backend can compute, so
 * a page that filtered the fetched rows would pass an output-only test while
 * being wrong.
 */

const SESSION: Session = {
  user: { id: 'u1', email: 'a@example.invalid', display_name: 'A', status: 'ACTIVE' },
  roles: [{ name: 'VIEWER', scope: {} }],
  permissions: ['service:read', 'server:read'],
  session: { expires_at: '2026-10-15T00:00:00Z', idle_timeout_seconds: 1800 },
  demo_mode: false,
  read_only_mode: false,
};

function item(overrides: Record<string, unknown> = {}) {
  return {
    service_id: 'svc-1',
    server_id: 'srv-1',
    server_display_name: 'Web 01',
    environment: 'staging',
    service_name: 'nginx.service',
    display_name: null,
    application_id: null,
    monitoring_enabled: true,
    state: 'ACTIVE',
    sub_state: null,
    load_state: null,
    enabled: null,
    main_pid: null,
    restart_count: null,
    last_state_change_at: null,
    data_state: 'ok',
    unavailable_reason: null,
    collected_at: '2026-10-17T12:00:00Z',
    server_reachable: true,
    allowed_actions: [],
    approval_required: false,
    ...overrides,
  };
}

const UNREACHABLE = item({
  service_id: 'svc-dead',
  service_name: 'api.service',
  server_display_name: 'Web 02',
  state: 'UNKNOWN',
  data_state: 'stale',
  unavailable_reason: 'no_data',
  server_reachable: false,
  collected_at: '2026-10-17T11:50:00Z',
});

function page(items: unknown[], extra: Record<string, unknown> = {}) {
  return {
    items,
    page: { limit: 50, next_cursor: null, has_more: false, total: null, ...extra },
    demo_mode: false,
  };
}

function mockApi(body: unknown = page([item()]), status = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const isServer = /\/servers\/srv-1$/.test(url);
    return Promise.resolve({
      ok: status < 400,
      status,
      json: () =>
        Promise.resolve(
          isServer
            ? { id: 'srv-1', display_name: 'Web 01', hostname: 'web-01.internal' }
            : status < 400
              ? body
              : {},
        ),
    } as unknown as Response);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function urls(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls.map((call) => (call as [string])[0]);
}

function renderList() {
  return render(
    <SessionContext.Provider
      value={{
        state: { kind: 'authenticated', session: SESSION },
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <MemoryRouter>
        <ServiceList />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

describe('ServiceList', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  // --- the PRD §4.5 acceptance criterion ------------------------------------

  it('renders an unreachable server as unknown, never as inactive', async () => {
    mockApi(page([UNREACHABLE]));
    renderList();

    const row = (await screen.findByRole('link', { name: 'api.service' })).closest('tr');
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText('Unknown')).toBeInTheDocument();
    expect(cells.getByText(/Host unreachable/)).toBeInTheDocument();
    // The assertion with teeth: nothing in the row says the unit is stopped.
    expect(row).not.toHaveTextContent(/Inactive/);
    expect(row).not.toHaveTextContent(/Active/);
  });

  it('keeps unreachable, not-collected, failed and inactive visibly different', async () => {
    mockApi(
      page([
        UNREACHABLE,
        item({
          service_id: 'a',
          service_name: 'collector.service',
          state: 'UNCOLLECTED',
          data_state: 'unavailable',
          unavailable_reason: 'no_data',
          collected_at: null,
        }),
        item({ service_id: 'b', service_name: 'broken.service', state: 'FAILED' }),
        item({ service_id: 'c', service_name: 'stopped.service', state: 'INACTIVE' }),
        item({
          service_id: 'd',
          service_name: 'winsvc.service',
          state: 'UNSUPPORTED',
          data_state: 'unsupported',
          unavailable_reason: 'unsupported_os',
          server_reachable: null,
          collected_at: null,
        }),
      ]),
    );
    renderList();
    await screen.findByRole('link', { name: 'broken.service' });

    const stateOf = (name: string) =>
      (screen.getByRole('link', { name }).closest('tr') as HTMLElement).textContent;
    expect(stateOf('api.service')).toContain('Unknown');
    expect(stateOf('collector.service')).toContain('Not collected');
    expect(stateOf('broken.service')).toContain('Failed');
    expect(stateOf('stopped.service')).toContain('Inactive');
    expect(stateOf('winsvc.service')).toContain('Not supported');
  });

  it('shows a collection time with every state, and says when there is none', async () => {
    mockApi(
      page([
        item(),
        item({
          service_id: 'x',
          service_name: 'never.service',
          state: 'UNCOLLECTED',
          data_state: 'unavailable',
          collected_at: null,
        }),
      ]),
    );
    renderList();
    const ok = (await screen.findByRole('link', { name: 'nginx.service' })).closest('tr');
    expect(ok).toHaveTextContent(/Collected .*2026.* UTC/);
    const never = screen.getByRole('link', { name: 'never.service' }).closest('tr');
    expect(never).toHaveTextContent(/Never collected/);
  });

  // --- filtering is the backend's job ---------------------------------------

  it('sends the state filter to the backend rather than filtering locally', async () => {
    const fetchMock = mockApi(page([item({ state: 'FAILED' })]));
    renderList();
    await screen.findByRole('link', { name: 'nginx.service' });

    await userEvent.selectOptions(screen.getByLabelText('State'), 'FAILED');

    await waitFor(() => {
      expect(urls(fetchMock).some((url) => url.includes('state=FAILED'))).toBe(true);
    });
  });

  it('sends environment and search too, and leaves out the ones not chosen', async () => {
    const fetchMock = mockApi();
    renderList();
    await screen.findByRole('link', { name: 'nginx.service' });
    expect(urls(fetchMock)[0]).toMatch(/\/services$/);

    await userEvent.selectOptions(screen.getByLabelText('Environment'), 'production');
    await userEvent.type(screen.getByLabelText('Search'), 'ngi');

    await waitFor(() => {
      const last = urls(fetchMock).at(-1) ?? '';
      expect(last).toContain('environment=production');
      expect(last).toContain('q=ngi');
      expect(last).not.toContain('state=');
    });
  });

  it('links a row to its detail page and its server', async () => {
    mockApi(page([item({ service_name: 'php8.3-fpm.service' })]));
    renderList();
    const unit = await screen.findByRole('link', { name: 'php8.3-fpm.service' });
    expect(unit).toHaveAttribute('href', '/servers/srv-1/services/php8.3-fpm.service');
    expect(screen.getByRole('link', { name: 'Web 01' })).toHaveAttribute(
      'href',
      '/servers/srv-1/services',
    );
  });

  // --- the states around the table ------------------------------------------

  it('distinguishes loading from empty, and unfiltered empty from filtered empty', async () => {
    mockApi(page([]));
    renderList();
    expect(screen.getByRole('status')).toHaveTextContent(/Loading services/);
    expect(await screen.findByText(/No services are registered here/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('State'), 'FAILED');
    expect(await screen.findByText('No services match these filters.')).toBeInTheDocument();
  });

  it('reports a permission failure as such, not as an empty list', async () => {
    mockApi({}, 403);
    renderList();
    expect(await screen.findByRole('alert')).toHaveTextContent(/do not have permission/);
    expect(screen.queryByText(/No services/)).toBeNull();
  });

  it('does not crash on a 200 carrying the wrong shape', async () => {
    mockApi({ unexpected: true });
    renderList();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Unexpected response shape/);
  });

  it('offers paging only when there is another page', async () => {
    mockApi(page([item()], { next_cursor: 'abc', has_more: true }));
    renderList();
    await screen.findByRole('link', { name: 'nginx.service' });
    expect(screen.getByRole('navigation', { name: 'Services pagination' })).toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    mockApi(page([item(), UNREACHABLE]));
    const { container } = renderList();
    await screen.findByRole('link', { name: 'api.service' });
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });
});

describe('ServerServices', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  function renderPanel() {
    return render(
      <SessionContext.Provider
        value={{
          state: { kind: 'authenticated', session: SESSION },
          refresh: () => Promise.resolve(),
          signOut: () => Promise.resolve(),
        }}
      >
        <MemoryRouter initialEntries={['/servers/srv-1/services']}>
          <Routes>
            <Route path="/servers/:serverId/services" element={<ServerServices />} />
          </Routes>
        </MemoryRouter>
      </SessionContext.Provider>,
    );
  }

  it('lists one server through §16.2 and drops the columns the path already fixes', async () => {
    const fetchMock = mockApi();
    renderPanel();

    expect(await screen.findByRole('heading', { name: 'Services on Web 01' })).toBeInTheDocument();
    await screen.findByRole('link', { name: 'nginx.service' });
    expect(urls(fetchMock).some((url) => url.endsWith('/servers/srv-1/services'))).toBe(true);
    expect(screen.queryByRole('columnheader', { name: 'Server' })).toBeNull();
    expect(screen.queryByLabelText('Environment')).toBeNull();
  });

  it('says so when the server cannot be seen, and lists nothing', async () => {
    mockApi({}, 404);
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Not found/);
    expect(screen.queryByRole('table')).toBeNull();
  });
});
