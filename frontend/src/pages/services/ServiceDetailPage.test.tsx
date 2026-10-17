import { render, screen, within } from '@testing-library/react';
import axe from 'axe-core';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ServiceDetailPage } from '@/pages/services/ServiceDetailPage';

/**
 * One service, live (§16.4) — IMPLEMENTATION_PLAN.md M2.4 frontend tests.
 *
 * Two rules, both about not filling a gap with a plausible value:
 * the four fields this source cannot supply say so (OPEN-29), and a `null`
 * count or time is "unknown", never `0` or an epoch.
 */

function detail(overrides: Record<string, unknown> = {}) {
  return {
    service_id: 'svc-1',
    server_id: 'srv-1',
    server_display_name: 'Web 01',
    environment: 'staging',
    service_name: 'nginx.service',
    display_name: 'Front door',
    application_id: null,
    monitoring_enabled: true,
    state: 'FAILED',
    sub_state: null,
    load_state: null,
    enabled: null,
    main_pid: null,
    restart_count: 3,
    last_state_change_at: null,
    data_state: 'ok',
    unavailable_reason: null,
    collected_at: '2026-10-17T12:00:00Z',
    server_reachable: true,
    allowed_actions: [],
    approval_required: false,
    started_at: null,
    ...overrides,
  };
}

function mockApi(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: () => Promise.resolve(status < 400 ? body : {}),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/servers/srv-1/services/php8.3-fpm.service']}>
      <Routes>
        <Route path="/servers/:serverId/services/:serviceName" element={<ServiceDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function row(label: string): HTMLElement {
  const found = screen.getByText(label).closest('div');
  if (found === null) throw new Error(`no row labelled ${label}`);
  return found;
}

describe('ServiceDetailPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks for the unit named in the path, encoded', async () => {
    const fetchMock = mockApi(detail());
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });
    expect((fetchMock.mock.calls[0] as [string])[0]).toContain(
      '/servers/srv-1/services/php8.3-fpm.service',
    );
  });

  it('says the four fields this source cannot supply are not collected', async () => {
    mockApi(detail());
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });

    for (const label of ['Sub state', 'Load state', 'Enabled at boot', 'Main process ID']) {
      expect(row(label)).toHaveTextContent('Not collected by this source');
    }
    expect(row('Last state change')).toHaveTextContent('Not collected by this source');
  });

  it('never renders a missing field as 0, false or an empty value', async () => {
    mockApi(detail({ restart_count: null }));
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });

    expect(row('Restart count')).toHaveTextContent('Unknown');
    expect(row('Restart count')).not.toHaveTextContent(/\b0\b/);
    expect(row('Enabled at boot')).not.toHaveTextContent(/\b(Yes|No)\b/);
    expect(row('Main process ID')).not.toHaveTextContent(/\b0\b/);
  });

  it('shows a real zero restart count as a zero', async () => {
    mockApi(detail({ restart_count: 0 }));
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });
    expect(row('Restart count')).toHaveTextContent('0');
  });

  it('shows the activation time only when there is one', async () => {
    mockApi(detail({ started_at: '2026-10-17T07:30:59Z', state: 'ACTIVE' }));
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });
    expect(row('Active since')).toHaveTextContent(/2026.* UTC/);
  });

  it('says a unit with no activation time is not running or not reported, not 1970', async () => {
    mockApi(detail());
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });
    expect(row('Active since')).toHaveTextContent('Not running, or not reported');
    expect(row('Active since')).not.toHaveTextContent(/1970/);
  });

  it('renders an unreachable host as unknown with the host named unreachable', async () => {
    mockApi(
      detail({
        state: 'UNKNOWN',
        data_state: 'stale',
        unavailable_reason: 'no_data',
        server_reachable: false,
        restart_count: null,
      }),
    );
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });

    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0);
    expect(screen.queryByText('Inactive')).toBeNull();
    expect(row('Host reachable')).toHaveTextContent('No');
  });

  it('says a host whose reachability is not known is unknown, not no', async () => {
    mockApi(detail({ server_reachable: null, state: 'UNKNOWN', data_state: 'unavailable' }));
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });
    expect(row('Host reachable')).toHaveTextContent('Unknown');
  });

  it('reports a unit outside the caller scope as not found', async () => {
    mockApi({}, 404);
    renderDetail();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Not found/);
  });

  it('links back to the server and its services', async () => {
    mockApi(detail());
    renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });
    expect(screen.getByRole('link', { name: /Services on Web 01/ })).toHaveAttribute(
      'href',
      '/servers/srv-1/services',
    );
    expect(within(row('Server')).getByRole('link', { name: 'Web 01' })).toHaveAttribute(
      'href',
      '/servers/srv-1',
    );
  });

  it('has no axe violations', async () => {
    mockApi(detail());
    const { container } = renderDetail();
    await screen.findByRole('heading', { name: 'nginx.service' });
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });
});
