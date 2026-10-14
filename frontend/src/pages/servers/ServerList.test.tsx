import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ServerList } from '@/pages/servers/ServerList';

/**
 * List rendering, filtering, and permission-based control hiding
 * (IMPLEMENTATION_PLAN.md M1.5 frontend tests).
 *
 * The filter assertions check the **request**, not the rendered rows: filters
 * are applied by the backend inside a scope-filtered query, so a test that
 * only looked at the output would pass just as happily against a page that
 * filtered client-side — which is the implementation this one is written to
 * rule out.
 */

function session(permissions: string[], readOnly = false): Session {
  return {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'A', status: 'ACTIVE' },
    roles: [{ name: 'VIEWER', scope: {} }],
    permissions,
    session: { expires_at: '2026-10-15T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
}

function renderList(state: SessionState) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter>
        <ServerList />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

const SERVER = {
  id: 's1',
  cloud_account_id: 'a1',
  provider_resource_id: 'host-1',
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
  status: 'PENDING',
  last_seen_at: null,
  registered_at: '2026-10-14T00:00:00Z',
  created_at: '2026-10-14T00:00:00Z',
  updated_at: '2026-10-14T00:00:00Z',
};

function mockList(items: unknown[] = [SERVER]) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        items,
        page: { limit: 50, next_cursor: null, has_more: false, total: null },
        demo_mode: false,
      }),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('ServerList', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the servers it was given', async () => {
    mockList();
    renderList({ kind: 'authenticated', session: session(['server:read']) });
    expect(await screen.findByRole('link', { name: 'Web 01' })).toBeInTheDocument();
    expect(screen.getByText('web-01.internal.example')).toBeInTheDocument();
    // getAllBy: "PENDING" is also one of the status filter's options.
    expect(screen.getAllByText('PENDING').length).toBeGreaterThan(1);
  });

  it('says an owner was never recorded rather than showing a dash', async () => {
    mockList();
    renderList({ kind: 'authenticated', session: session(['server:read']) });
    expect(await screen.findByText('Not recorded')).toBeInTheDocument();
  });

  it('sends the chosen filters to the backend', async () => {
    const fetchMock = mockList();
    renderList({ kind: 'authenticated', session: session(['server:read']) });
    await screen.findByRole('link', { name: 'Web 01' });

    await userEvent.selectOptions(screen.getByLabelText('Environment'), 'production');
    await userEvent.type(screen.getByLabelText('Search'), 'web');

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('environment=production'))).toBe(true);
      expect(urls.some((url) => url.includes('q=web'))).toBe(true);
    });
  });

  it('distinguishes an empty result from a failure', async () => {
    mockList([]);
    renderList({ kind: 'authenticated', session: session(['server:read']) });
    expect(await screen.findByText(/No servers match these filters/)).toBeInTheDocument();
  });

  it('reports a permission failure as such, not as an empty inventory', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 403, json: () => Promise.resolve({}) }),
    );
    renderList({ kind: 'authenticated', session: session(['server:read']) });
    expect(await screen.findByRole('alert')).toHaveTextContent('do not have permission');
  });

  it('hides the register control without server:write', async () => {
    mockList();
    renderList({ kind: 'authenticated', session: session(['server:read']) });
    await screen.findByRole('link', { name: 'Web 01' });
    expect(screen.queryByRole('link', { name: 'Register server' })).not.toBeInTheDocument();
  });

  it('shows the register control with server:write', async () => {
    mockList();
    renderList({ kind: 'authenticated', session: session(['server:read', 'server:write']) });
    expect(await screen.findByRole('link', { name: 'Register server' })).toBeInTheDocument();
  });

  it('disables the register control in read-only mode', async () => {
    mockList();
    renderList({
      kind: 'authenticated',
      session: session(['server:read', 'server:write'], true),
    });
    const control = await screen.findByRole('link', { name: 'Register server' });
    expect(control).toHaveAttribute('aria-disabled', 'true');
  });
});
