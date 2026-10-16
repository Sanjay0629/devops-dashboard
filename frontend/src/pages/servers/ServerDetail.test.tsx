import { render, screen, waitFor } from '@testing-library/react';
import axe from 'axe-core';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { removeAssociation } from '@/api/inventory';
import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ServerDetail } from '@/pages/servers/ServerDetail';

/**
 * Association management on the server detail page (§14.6, §14.7).
 *
 * The assertions are about the **requests**, not only the rendering: an
 * association is a row in `application_servers` and an audited
 * `SERVER_UPDATED`, so what matters is that the right call went out with the
 * right body — and, for the duplicate case, that nothing was sent twice.
 *
 * The candidates deliberately include an application already associated with
 * this server. §14.6 answers `409` with a message that says so, and the page
 * shows it rather than carrying a second copy of the rule.
 */

const APPLICATION = { id: 'app1', name: 'Checkout', role_on_server: 'web' };

const SERVER = {
  id: 's1',
  cloud_account_id: 'a1',
  provider_resource_id: 'i-0abc',
  hostname: 'web-01.internal.example',
  display_name: 'Web 01',
  operating_system: 'LINUX',
  os_version: null,
  environment: 'staging',
  region: null,
  private_ip: null,
  owner_team: null,
  management_method: 'RESTRICTED_AGENT',
  monitoring_enabled: true,
  metrics_instance: null,
  agent_status: 'UNKNOWN',
  status: 'MANAGED',
  last_seen_at: null,
  registered_at: '2026-10-14T00:00:00Z',
  created_at: '2026-10-14T00:00:00Z',
  updated_at: '2026-10-14T09:15:07Z',
  provider_metadata: {},
  stale: null,
  health: { data_state: 'not_configured', state: null, collected_at: null },
  applications: [APPLICATION],
  registered_service_count: 0,
  open_alerts: { data_state: 'not_configured', counts: null, collected_at: null },
  management_capabilities: {
    data_state: 'not_configured',
    capabilities: null,
    collected_at: null,
  },
};

const CANDIDATES = {
  items: [
    { id: 'app1', name: 'Checkout', slug: 'checkout', owner_team: null, status: 'ACTIVE' },
    { id: 'app2', name: 'Billing', slug: 'billing', owner_team: 'fin', status: 'ACTIVE' },
  ],
  page: { limit: 50, next_cursor: null, has_more: false, total: null },
  demo_mode: false,
};

function session(permissions: string[], readOnly = false): Session {
  return {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'A', status: 'ACTIVE' },
    roles: [{ name: 'OPERATOR', scope: {} }],
    permissions,
    session: { expires_at: '2026-10-16T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
}

const authenticated = (permissions: string[], readOnly = false): SessionState => ({
  kind: 'authenticated',
  session: session(permissions, readOnly),
});

/** Reads answer the row or the candidate page; writes answer `onWrite`. */
function mockApi(onWrite: { status: number; body?: unknown } = { status: 200 }) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === undefined) {
      const body =
        url.includes('/applications') && !url.includes('/servers/') ? CANDIDATES : SERVER;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    }
    return Promise.resolve({
      ok: onWrite.status < 400,
      status: onWrite.status,
      json: () => Promise.resolve(onWrite.body ?? SERVER),
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderDetail(state: SessionState = authenticated(['server:read', 'server:write'])) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter initialEntries={['/servers/s1']}>
        <Routes>
          <Route path="/servers/:serverId" element={<ServerDetail />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

/**
 * Choose a candidate, waiting for the candidates to arrive first.
 *
 * The `<select>` is rendered by the detail payload's first response and the
 * options by §18.1's, a second request — so waiting on the select alone is a
 * race, and one that passes on its own and fails in a full run.
 */
async function choose(id: string, label: RegExp) {
  await screen.findByRole('option', { name: label });
  await userEvent.selectOptions(screen.getByLabelText('Application'), id);
}

function writeCall(fetchMock: ReturnType<typeof vi.fn>, method: string) {
  return fetchMock.mock.calls.find(
    (entry) => (entry[1] as RequestInit | undefined)?.method === method,
  );
}

describe('ServerDetail associations', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('offers an edit route and a removal control with server:write', async () => {
    mockApi();
    renderDetail();
    expect(await screen.findByRole('link', { name: 'Edit' })).toHaveAttribute(
      'href',
      '/servers/s1/edit',
    );
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Associate an application' })).toBeInTheDocument();
  });

  it('hides the write controls without server:write', async () => {
    mockApi();
    renderDetail(authenticated(['server:read']));
    expect(await screen.findByText('web-01.internal.example')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Edit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: 'Associate an application' }),
    ).not.toBeInTheDocument();
    // The association is still listed — reading it needs `server:read`.
    expect(screen.getByRole('link', { name: 'Checkout' })).toBeInTheDocument();
  });

  it('places no gate of its own in front of the API', async () => {
    // Hiding the button is usability (docs/SECURITY.md §6). The client sends
    // the request whatever the session's advisory list said, so the backend
    // is the only thing that can refuse it — which is the point.
    const fetchMock = mockApi({ status: 403 });
    renderDetail(authenticated(['server:read']));
    await screen.findByText('web-01.internal.example');
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument();

    const outcome = await removeAssociation('s1', 'app1');
    expect(writeCall(fetchMock, 'DELETE')?.[0]).toBe('/api/v1/servers/s1/applications/app1');
    expect(outcome.kind).toBe('forbidden');
  });

  it('hides the association controls in read-only mode', async () => {
    mockApi();
    renderDetail(authenticated(['server:read', 'server:write'], true));
    await screen.findByText('web-01.internal.example');
    expect(
      screen.queryByRole('heading', { name: 'Associate an application' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove' })).toBeDisabled();
  });

  it('associates the chosen application, with its role', async () => {
    const fetchMock = mockApi();
    renderDetail();
    await choose('app2', /Billing/);
    await userEvent.type(screen.getByLabelText('Role on server'), 'worker');
    await userEvent.click(screen.getByRole('button', { name: 'Associate' }));

    await waitFor(() => {
      expect(writeCall(fetchMock, 'POST')).toBeDefined();
    });
    const init = writeCall(fetchMock, 'POST')?.[1] as { body: string };
    expect(JSON.parse(init.body)).toEqual({ application_id: 'app2', role_on_server: 'worker' });
  });

  it('omits an empty role rather than sending a blank one', async () => {
    const fetchMock = mockApi();
    renderDetail();
    await choose('app2', /Billing/);
    await userEvent.click(screen.getByRole('button', { name: 'Associate' }));

    await waitFor(() => {
      expect(writeCall(fetchMock, 'POST')).toBeDefined();
    });
    const init = writeCall(fetchMock, 'POST')?.[1] as { body: string };
    expect(JSON.parse(init.body)).toEqual({ application_id: 'app2' });
  });

  it('will not associate until an application is chosen', async () => {
    mockApi();
    renderDetail();
    await screen.findByRole('heading', { name: 'Associate an application' });
    expect(screen.getByRole('button', { name: 'Associate' })).toBeDisabled();
  });

  it('shows the backend’s duplicate message instead of filtering candidates', async () => {
    const fetchMock = mockApi({
      status: 409,
      body: {
        error: {
          code: 'duplicate_resource',
          message: 'That application is already associated with this server.',
        },
      },
    });
    renderDetail();
    // `app1` is already associated and is deliberately still on offer.
    await choose('app1', /Checkout/);
    await userEvent.click(screen.getByRole('button', { name: 'Associate' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('already associated');
    expect(
      fetchMock.mock.calls.filter(
        (entry) => (entry[1] as RequestInit | undefined)?.method === 'POST',
      ).length,
    ).toBe(1);
  });

  it('removes an association and re-reads the server', async () => {
    const fetchMock = mockApi({ status: 204 });
    renderDetail();
    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }));

    await waitFor(() => {
      expect(writeCall(fetchMock, 'DELETE')).toBeDefined();
    });
    expect(writeCall(fetchMock, 'DELETE')?.[0]).toBe('/api/v1/servers/s1/applications/app1');
    // Re-read rather than patched in place: the detail payload carries the
    // association list, and guessing the new one would be a second source.
    await waitFor(() => {
      const reads = fetchMock.mock.calls.filter(
        (entry) =>
          (entry[1] as RequestInit | undefined)?.method === undefined &&
          String(entry[0]).endsWith('/servers/s1'),
      );
      expect(reads.length).toBeGreaterThan(1);
    });
  });

  it('reports a refused removal rather than dropping the row from view', async () => {
    mockApi({ status: 404, body: {} });
    renderDetail();
    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Not found');
    expect(screen.getByRole('link', { name: 'Checkout' })).toBeInTheDocument();
  });
  // --- accessibility ----------------------------------------------------------

  it('has no axe violations', async () => {
    mockApi();
    const { container } = renderDetail();
    // The loaded DOM, not the one with the candidate list still in flight.
    await screen.findByRole('option', { name: /Billing/ });

    const results = await axe.run(container, {
      // Landmark and page-level rules need the whole document; this renders
      // one section into a bare div, so they would report on the harness
      // rather than on the component.
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
      // jsdom has no layout and no canvas, so the contrast rule can only
      // return "incomplete" here. Disabled explicitly, so this test does not
      // look like it checks contrast when it cannot.
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });
});

describe('ServerDetail metrics link', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('is offered with metrics:read and points at the metrics page', async () => {
    mockApi();
    renderDetail(authenticated(['server:read', 'metrics:read']));

    const link = await screen.findByRole('link', { name: 'Metrics' });
    expect(link).toHaveAttribute('href', '/servers/s1/metrics');
  });

  it('is hidden without it (advisory only; the backend decides)', async () => {
    mockApi();
    renderDetail(authenticated(['server:read']));

    await screen.findByRole('heading', { name: 'Web 01' });
    expect(screen.queryByRole('link', { name: 'Metrics' })).not.toBeInTheDocument();
  });
});
