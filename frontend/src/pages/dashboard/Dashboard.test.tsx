import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { Dashboard } from '@/pages/dashboard/Dashboard';

/**
 * Dashboard rendering (IMPLEMENTATION_PLAN.md M1.6 frontend tests).
 *
 * The test that matters most is
 * `renders an unavailable metric as unavailable, never as 0` — PRD §4.2 makes
 * that an acceptance criterion in its own words ("must not silently replace
 * unavailable data with zero values"), and it is the single easiest thing to
 * regress: a `?? 0` added to silence a TypeScript complaint turns "we have no
 * metrics" into "your fleet is idle", and nothing else in the suite would
 * notice.
 */

function session(permissions: string[] = ['dashboard:read', 'server:read']): Session {
  return {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'A', status: 'ACTIVE' },
    roles: [{ name: 'VIEWER', scope: {} }],
    permissions,
    session: { expires_at: '2026-10-15T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: false,
  };
}

const SUMMARY = {
  servers: {
    total: 6,
    pending: 1,
    managed: 3,
    stale: 1,
    unreachable: 1,
    retired: 0,
    data_state: 'ok',
    collected_at: '2026-10-15T09:30:00Z',
    source: 'postgresql',
    stale_after_seconds: null,
    unavailable_reason: null,
  },
  // Every value null, exactly as the backend reports it at M1.6.
  server_health: {
    healthy: null,
    warning: null,
    critical: null,
    unknown: null,
    data_state: 'not_configured',
    collected_at: null,
    source: 'prometheus',
    stale_after_seconds: null,
    unavailable_reason: 'Server health evaluation is not implemented yet (M2.1).',
  },
  utilisation: {
    cpu_percent_avg: null,
    memory_percent_avg: null,
    disk_percent_max: null,
    network_bytes_per_second: null,
    data_state: 'not_configured',
    collected_at: null,
    source: 'prometheus',
    stale_after_seconds: null,
    unavailable_reason: 'Metrics collection is not implemented yet (M2.1).',
  },
  failed_services: {
    count: 0,
    unobserved: 0,
    data_state: 'ok',
    collected_at: '2026-10-15T09:30:00Z',
    source: 'postgresql',
    stale_after_seconds: null,
    unavailable_reason: null,
  },
  active_alerts: {
    critical: null,
    warning: null,
    info: null,
    data_state: 'not_configured',
    collected_at: null,
    source: null,
    stale_after_seconds: null,
    unavailable_reason: 'Alerting is not implemented yet (M3.3).',
  },
  recent_deployments: {
    items: null,
    data_state: 'not_configured',
    collected_at: null,
    source: null,
    stale_after_seconds: null,
    unavailable_reason: 'Deployments are not implemented yet (M5.1).',
  },
  recent_operations: {
    items: null,
    data_state: 'not_configured',
    collected_at: null,
    source: null,
    stale_after_seconds: null,
    unavailable_reason: 'Service operations are not implemented yet (M4.2).',
  },
  recent_health_changes: {
    items: null,
    data_state: 'not_configured',
    collected_at: null,
    source: null,
    stale_after_seconds: null,
    unavailable_reason: 'Health-change history is not implemented yet (M2.5).',
  },
  filters_applied: {},
  demo_mode: false,
  cache_ttl_seconds: 15,
};

const ROW = {
  server_id: 's1',
  display_name: 'Web 01',
  hostname: 'web-01.internal.example',
  environment: 'staging',
  provider: 'ON_PREM',
  status: 'MANAGED',
  health_state: null,
  last_seen_at: null,
  stale: null,
  failed_service_count: 0,
  active_alert_count: null,
};

function healthList(items: unknown[] = [ROW], hasMore = false) {
  return {
    items,
    page: { limit: 25, next_cursor: hasMore ? 'cursor-2' : null, has_more: hasMore, total: null },
    health: {
      data_state: 'not_configured',
      collected_at: null,
      source: 'prometheus',
      stale_after_seconds: null,
      unavailable_reason: 'Server health evaluation is not implemented yet (M2.1).',
    },
    alerts: {
      data_state: 'not_configured',
      collected_at: null,
      source: null,
      stale_after_seconds: null,
      unavailable_reason: 'Alerting is not implemented yet (M3.3).',
    },
    collected_at: '2026-10-15T09:30:00Z',
    filters_applied: {},
    demo_mode: false,
    cache_ttl_seconds: 15,
  };
}

function mockApi({
  summary = SUMMARY,
  health = healthList(),
  status = 200,
}: { summary?: unknown; health?: unknown; status?: number } = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const body = url.includes('/dashboard/summary') ? summary : health;
    return Promise.resolve({
      ok: status < 400,
      status,
      json: () => Promise.resolve(status < 400 ? body : {}),
    } as unknown as Response);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderDashboard(state: SessionState = { kind: 'authenticated', session: session() }) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

describe('Dashboard', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  // --- the PRD §4.2 acceptance criterion ------------------------------------

  it('renders an unavailable metric as unavailable, never as 0', async () => {
    mockApi();
    renderDashboard();
    await screen.findByText('6');

    // The four utilisation and health cards all carry `null`. Not one of them
    // may render a zero.
    for (const label of ['CPU average', 'Memory average', 'Healthy', 'Critical']) {
      const card = screen.getByRole('heading', { name: label }).parentElement;
      expect(card).not.toBeNull();
      expect(card).toHaveTextContent(/Not configured/);
      // The assertion with teeth: no digit anywhere in the card. A `?? 0`
      // would put one here.
      expect(card?.textContent ?? '').not.toMatch(/\b0\b/);
    }
  });

  it('names the milestone that will provide each missing section', async () => {
    mockApi();
    renderDashboard();
    // An actionable reason, not "N/A": the operator can tell "nobody has
    // built this yet" from "the collector is down".
    // getAllBy throughout: one reason appears on every card that shares a
    // section, which is the intended behaviour — each card has to stand on
    // its own, because an operator reads one card at a time.
    expect(
      (await screen.findAllByText(/Metrics collection is not implemented yet/)).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/Alerting is not implemented yet/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Deployments are not implemented yet/).length).toBeGreaterThan(0);
  });

  it('shows a real zero where there is a real source', async () => {
    mockApi();
    renderDashboard();
    await screen.findByText('6');
    // `services` exists and is empty, so this 0 is a fact. The distinction
    // the whole milestone turns on: a zero is allowed *because* it has a
    // source, and the card says which.
    const card = screen.getByRole('heading', { name: 'Failed services' }).parentElement;
    expect(card).toHaveTextContent('0');
    expect(card).not.toHaveTextContent(/Not configured/);
  });

  it('says how many services could not be read beside a zero failed', async () => {
    // 0 failed of 0 registered is good news; 0 failed of 12 nobody could read
    // is not, and the card must not let the first stand in for the second.
    mockApi({
      summary: {
        ...SUMMARY,
        failed_services: { ...SUMMARY.failed_services, count: 0, unobserved: 12 },
      },
    });
    renderDashboard();
    await screen.findByText('6');
    const card = screen.getByRole('heading', { name: 'Failed services' }).parentElement;
    expect(card).toHaveTextContent('12 not observed');
  });

  it('adds no qualifier when every service was read', async () => {
    mockApi();
    renderDashboard();
    await screen.findByText('6');
    const card = screen.getByRole('heading', { name: 'Failed services' }).parentElement;
    expect(card).not.toHaveTextContent(/not observed/);
  });

  it('never shows an unmonitored server as healthy', async () => {
    mockApi();
    renderDashboard();
    const row = (await screen.findByRole('link', { name: 'Web 01' })).closest('tr');
    expect(row).not.toBeNull();
    // `health_state` is null. "Unknown", never "Healthy".
    expect(within(row as HTMLElement).getByText('Unknown')).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText('Healthy')).toBeNull();
    // And the alert count is not a zero either — there is no alerts table.
    expect(within(row as HTMLElement).getByText('Not configured')).toBeInTheDocument();
  });

  // --- the other states ------------------------------------------------------

  it('distinguishes loading from empty', async () => {
    mockApi({ health: healthList([]) });
    renderDashboard();
    // Before the response lands, the page says it is working — it does not
    // show an empty inventory. Two regions load independently (the summary
    // and the status table), so both say so.
    const loading = screen.getAllByRole('status');
    expect(loading.length).toBeGreaterThan(0);
    expect(loading.every((node) => /Loading/.test(node.textContent))).toBe(true);
    expect(await screen.findByText(/No servers match these filters/)).toBeInTheDocument();
  });

  it('reports a permission failure as such, not as an empty dashboard', async () => {
    mockApi({ status: 403 });
    renderDashboard();
    const alerts = await screen.findAllByRole('alert');
    expect(alerts[0]).toHaveTextContent('do not have permission');
  });

  it('does not crash on a 200 carrying the wrong shape', async () => {
    // A backend that changed under us becomes an error state, not a blank
    // screen from a render-time TypeError.
    mockApi({ summary: { unexpected: true }, health: { unexpected: true } });
    renderDashboard();
    const alerts = await screen.findAllByRole('alert');
    expect(alerts[0]).toHaveTextContent(/Could not reach the backend/);
  });

  it('displays collected_at for the data sections', async () => {
    mockApi();
    renderDashboard();
    await screen.findByText('6');
    expect(screen.getAllByText(/Collected /).length).toBeGreaterThan(0);
  });

  // --- filters and drill-through ---------------------------------------------

  it('sends the chosen filter to the backend rather than filtering locally', async () => {
    const fetchMock = mockApi();
    renderDashboard();
    await screen.findByText('6');

    await userEvent.selectOptions(screen.getByLabelText('Environment'), 'production');

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([input]) =>
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
      );
      expect(urls.some((url) => url.includes('environment=production'))).toBe(true);
    });
  });

  it('links summary cards to the relevant detail view', async () => {
    mockApi();
    renderDashboard();
    expect(await screen.findByRole('link', { name: 'Total servers' })).toHaveAttribute(
      'href',
      '/servers',
    );
    expect(screen.getByRole('link', { name: 'Unreachable' })).toHaveAttribute(
      'href',
      '/servers?status=UNREACHABLE',
    );
  });

  it('offers paging only when there is another page', async () => {
    mockApi({ health: healthList([ROW], true) });
    renderDashboard();
    await screen.findByRole('link', { name: 'Web 01' });
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
  });

  // --- accessibility ----------------------------------------------------------

  it('has no axe violations', async () => {
    mockApi();
    const { container } = renderDashboard();
    await screen.findByText('6');

    const results = await axe.run(container, {
      // Landmark and page-level rules need the whole document; this renders
      // one section into a bare div, so they would report on the harness
      // rather than on the component.
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
      rules: {
        // jsdom has no layout and no canvas, so axe cannot compute a
        // contrast ratio here: the rule returns "incomplete", never a
        // violation. Disabled explicitly rather than left on, so this test
        // does not look like it checks contrast when it cannot. The palette's
        // contrast belongs to a real browser — M1.7's E2E suite.
        'color-contrast': { enabled: false },
      },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });
});
