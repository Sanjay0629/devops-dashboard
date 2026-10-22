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
  // No Prometheus: every count null, exactly as the backend reports it.
  server_health: {
    healthy: null,
    warning: null,
    critical: null,
    stale: null,
    unreachable: null,
    unknown: null,
    data_state: 'not_configured',
    collected_at: null,
    source: 'prometheus',
    stale_after_seconds: null,
    unavailable_reason: 'Prometheus is not configured, so health cannot be evaluated.',
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
    critical: 2,
    warning: 3,
    info: 0,
    data_state: 'ok',
    collected_at: '2026-10-15T09:30:00Z',
    source: 'postgresql',
    stale_after_seconds: null,
    unavailable_reason: null,
    pipeline: 'confirmed',
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
    unavailable_reason: 'Not configured in this fixture.',
  },
  recent_health_changes: {
    items: [],
    data_state: 'ok',
    collected_at: '2026-10-15T09:30:00Z',
    source: 'postgresql',
    stale_after_seconds: null,
    unavailable_reason: null,
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
  active_alert_count: 0,
};

function healthList(items: unknown[] = [ROW], hasMore = false) {
  return {
    items,
    page: { limit: 25, next_cursor: hasMore ? 'cursor-2' : null, has_more: hasMore, total: null },
    health: SUMMARY.server_health,
    alerts: SUMMARY.active_alerts,
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

  it('never shows an unevaluated server as healthy', async () => {
    mockApi();
    renderDashboard();
    const row = (await screen.findByRole('link', { name: 'Web 01' })).closest('tr');
    expect(row).not.toBeNull();
    // `health_state` is null: nothing was evaluated. Not a badge at all, and
    // above all not "Healthy" - and not "Unknown", which is a verdict.
    expect(within(row as HTMLElement).getByText('Not evaluated')).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText('Healthy')).toBeNull();
    expect(within(row as HTMLElement).queryByText('Unknown')).toBeNull();
    expect(within(row as HTMLElement).getByText('Never seen')).toBeInTheDocument();
    // The alert count is a real count now (M3.3), and a real zero is shown as one.
    const cells = (row as HTMLElement).querySelectorAll('td');
    expect(cells[cells.length - 1]).toHaveTextContent('0');
  });

  // --- alerts (M3.3) -------------------------------------------------------------

  const withAlerts = (active_alerts: Record<string, unknown>) =>
    mockApi({ summary: { ...SUMMARY, active_alerts } });

  it('shows the open critical count, the rest beside it, and links to the alerts', async () => {
    mockApi();
    renderDashboard();
    const card = (await screen.findByRole('link', { name: 'Open critical alerts' })).closest(
      'div',
    ) as HTMLElement;
    expect(within(card).getByRole('link')).toHaveAttribute('href', '/alerts');
    expect(card).toHaveTextContent('2');
    expect(card).toHaveTextContent('3 warning · 0 info');
    // Confirmed pipeline: nothing to qualify.
    expect(card).not.toHaveTextContent(/cannot tell/);
  });

  it('says a zero is not good news when alerting has not been confirmed', async () => {
    withAlerts({ ...SUMMARY.active_alerts, critical: 0, warning: 0, pipeline: 'not_confirmed' });
    renderDashboard();
    const card = (await screen.findByRole('link', { name: 'Open critical alerts' })).closest(
      'div',
    ) as HTMLElement;
    expect(card).toHaveTextContent('0');
    expect(card).toHaveTextContent(/alerting not confirmed: a 0 here means we cannot tell/);
  });

  it('says so when it could not check whether alerting works', async () => {
    withAlerts({ ...SUMMARY.active_alerts, pipeline: 'unknown' });
    renderDashboard();
    expect(await screen.findByText(/could not check whether alerting is working/)).toBeVisible();
  });

  it('is silent about the pipeline where no Alertmanager is configured', async () => {
    withAlerts({ ...SUMMARY.active_alerts, pipeline: null });
    renderDashboard();
    const card = (await screen.findByRole('link', { name: 'Open critical alerts' })).closest(
      'div',
    ) as HTMLElement;
    expect(card).not.toHaveTextContent(/cannot tell|alerting not confirmed/);
  });

  it('tells a caller who may not read alerts, instead of showing them a zero', async () => {
    withAlerts({
      critical: null,
      warning: null,
      info: null,
      data_state: 'unavailable',
      collected_at: null,
      source: 'postgresql',
      stale_after_seconds: null,
      unavailable_reason: 'alert_read_not_permitted',
      pipeline: null,
    });
    renderDashboard();
    const card = (await screen.findByRole('link', { name: 'Open critical alerts' })).closest(
      'div',
    ) as HTMLElement;
    expect(card).toHaveTextContent('You do not have permission to read alerts.');
    expect(within(card).queryByText('0')).toBeNull();
  });

  it('shows "Not permitted" for a server whose alerts the caller may not read, never 0', async () => {
    mockApi({ health: healthList([{ ...ROW, active_alert_count: null }]) });
    renderDashboard();
    const row = (await screen.findByRole('link', { name: 'Web 01' })).closest('tr') as HTMLElement;
    expect(within(row).getByText('Not permitted')).toBeInTheDocument();
  });

  // --- health (M2.5) -----------------------------------------------------------

  const ROLLUP = {
    ...SUMMARY.server_health,
    healthy: 3,
    warning: 1,
    stale: 1,
    unreachable: 1,
    unknown: 0,
    critical: null,
    data_state: 'ok',
    collected_at: '2026-10-15T09:29:50Z',
    unavailable_reason: null,
  };

  function healthRegion(): HTMLElement {
    return screen.getByRole('region', { name: 'Health' });
  }

  it('shows real health counts, including a real zero, and labels them apart from status', async () => {
    mockApi({ summary: { ...SUMMARY, server_health: ROLLUP } });
    renderDashboard();
    await screen.findByText('6');

    const card = (label: string) =>
      within(healthRegion()).getByRole('heading', { name: label }).parentElement;
    expect(card('Healthy')).toHaveTextContent('3');
    expect(card('Warning')).toHaveTextContent('1');
    expect(card('Stale')).toHaveTextContent('1');
    expect(card('Unreachable')).toHaveTextContent('1');
    // `unknown` is 0 because the rollup was computed and nobody is unknown.
    expect(card('Unknown')).toHaveTextContent('0');

    // The registration-status counts are a different group with their own
    // heading, so two cards named "Stale" cannot be mistaken for one number.
    const registration = screen.getByRole('region', { name: 'Registration status' });
    expect(within(registration).getByRole('link', { name: 'Stale' })).toHaveAttribute(
      'href',
      '/servers?status=STALE',
    );
    expect(within(healthRegion()).queryByRole('link')).toBeNull();
  });

  it('dates the health rollup by when Prometheus last answered', async () => {
    mockApi({ summary: { ...SUMMARY, server_health: ROLLUP } });
    renderDashboard();
    await screen.findByText('6');
    expect(within(healthRegion()).getByText(/Collected/)).toBeInTheDocument();
  });

  it('does not render critical as 0 or as collected', async () => {
    // Nothing evaluates utilisation, so `critical` is null inside an `ok`
    // section. A banner would say "Collected"; a `?? 0` would say "none".
    mockApi({ summary: { ...SUMMARY, server_health: ROLLUP } });
    renderDashboard();
    await screen.findByText('6');
    const critical = within(healthRegion()).getByRole('heading', {
      name: 'Critical',
    }).parentElement;
    expect(critical).toHaveTextContent(/Not evaluated/);
    expect(critical?.textContent ?? '').not.toMatch(/\b0\b/);
    expect(critical).not.toHaveTextContent(/Collected/);
  });

  it('shows no count at all while Prometheus is silent', async () => {
    mockApi({
      summary: {
        ...SUMMARY,
        server_health: {
          ...SUMMARY.server_health,
          data_state: 'unavailable',
          unavailable_reason: 'Prometheus has not answered yet, so health cannot be evaluated.',
        },
      },
    });
    renderDashboard();
    await screen.findByText('6');
    for (const label of ['Healthy', 'Warning', 'Stale', 'Unreachable', 'Unknown', 'Critical']) {
      const card = within(healthRegion()).getByRole('heading', { name: label }).parentElement;
      expect(card).toHaveTextContent(/Prometheus has not answered yet/);
      expect(card?.textContent ?? '').not.toMatch(/\d/);
    }
  });

  it('dates a stale rollup and counts nothing', async () => {
    mockApi({
      summary: {
        ...SUMMARY,
        server_health: {
          ...SUMMARY.server_health,
          data_state: 'stale',
          collected_at: '2026-10-15T07:00:00Z',
          unavailable_reason:
            'Prometheus has not answered recently, so health is not judged against old data.',
        },
      },
    });
    renderDashboard();
    await screen.findByText('6');
    expect(within(healthRegion()).getByText(/Collected/)).toBeInTheDocument();
    const card = within(healthRegion()).getByRole('heading', { name: 'Healthy' }).parentElement;
    expect(card).toHaveTextContent(/Stale — Prometheus has not answered recently/);
    // No digit in the card text, so no count - not even a dated one.
    expect(card?.textContent ?? '').not.toMatch(/\d/);
  });

  it('renders each health state, and "Never seen" for a host never scraped', async () => {
    const seen = (name: string, state: string, at: string | null, stale: boolean | null) => ({
      ...ROW,
      server_id: name.toLowerCase(),
      display_name: name,
      health_state: state,
      last_seen_at: at,
      stale,
    });
    mockApi({
      health: healthList([
        seen('Aa', 'healthy', '2026-10-15T09:30:00Z', false),
        seen('Bb', 'stale', '2026-10-15T09:20:00Z', true),
        seen('Cc', 'unreachable', '2026-10-15T08:00:00Z', true),
        seen('Dd', 'unknown', null, null),
      ]),
    });
    renderDashboard();
    const rowOf = async (name: string) =>
      (await screen.findByRole('link', { name })).closest('tr') as HTMLElement;

    expect(within(await rowOf('Aa')).getByText('Healthy')).toBeInTheDocument();
    expect(within(await rowOf('Bb')).getByText('Stale')).toBeInTheDocument();
    expect(within(await rowOf('Cc')).getByText('Unreachable')).toBeInTheDocument();
    const unknown = await rowOf('Dd');
    // Evaluated, nothing known: a verdict badge, and a last-seen that is not a time.
    expect(within(unknown).getByText('Unknown')).toBeInTheDocument();
    expect(within(unknown).getByText('Never seen')).toBeInTheDocument();
    expect(within(unknown).queryByText('Not evaluated')).toBeNull();
  });

  it('puts the health reason above the table only when the rollup has none', async () => {
    mockApi({
      summary: { ...SUMMARY, server_health: ROLLUP },
      health: { ...healthList([ROW]), health: ROLLUP },
    });
    const first = renderDashboard();
    await screen.findByRole('link', { name: 'Web 01' });
    // `ok`: the cards already say it; a banner above the table would be noise.
    expect(
      screen.queryByText(/Prometheus is not configured, so health cannot be evaluated/),
    ).toBeNull();
    first.unmount();

    mockApi();
    renderDashboard();
    await screen.findByRole('link', { name: 'Web 01' });
    expect(
      screen.getAllByText(/Prometheus is not configured, so health cannot be evaluated/).length,
    ).toBeGreaterThan(0);
  });

  it('sends the chosen health filter to the backend, to the list only', async () => {
    const fetchMock = mockApi();
    renderDashboard();
    await screen.findByText('6');
    await userEvent.selectOptions(screen.getByLabelText('Health status'), 'stale');
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([input]) =>
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
      );
      expect(
        urls.some(
          (url) => url.includes('/dashboard/health') && url.includes('health_status=stale'),
        ),
      ).toBe(true);
      // The summary is not filtered by health: its counts *are* the breakdown.
      expect(
        urls.some((url) => url.includes('/dashboard/summary') && url.includes('health_status')),
      ).toBe(false);
    });
  });

  // --- recent health changes ----------------------------------------------------

  const CHANGES = {
    ...SUMMARY.recent_health_changes,
    items: [
      {
        server_id: 's9',
        display_name: 'Db 09',
        environment: 'production',
        previous_status: 'MANAGED',
        new_status: 'STALE',
        health_state: 'stale',
        occurred_at: '2026-10-15T09:20:00Z',
      },
    ],
  };

  it('lists the moves the sweep made, with a link to the server', async () => {
    mockApi({ summary: { ...SUMMARY, recent_health_changes: CHANGES } });
    renderDashboard();
    const link = await screen.findByRole('link', { name: 'Db 09' });
    expect(link).toHaveAttribute('href', '/servers/s9');
    const item = link.closest('li') as HTMLElement;
    expect(within(item).getByText('Managed')).toBeInTheDocument();
    expect(within(item).getByText('Stale')).toBeInTheDocument();
  });

  it('says "none recorded" for an empty list, which is a fact', async () => {
    mockApi();
    renderDashboard();
    expect(await screen.findByText(/No health changes recorded/)).toBeInTheDocument();
  });

  it('says "not available" for a null list rather than "none recorded"', async () => {
    mockApi({
      summary: {
        ...SUMMARY,
        recent_health_changes: {
          ...SUMMARY.recent_health_changes,
          items: null,
          data_state: 'unavailable',
          collected_at: null,
          unavailable_reason: 'The audit trail could not be read.',
        },
      },
    });
    renderDashboard();
    expect(await screen.findByText(/The audit trail could not be read/)).toBeInTheDocument();
    expect(screen.queryByText(/No health changes recorded/)).toBeNull();
  });

  // --- the other states ------------------------------------------------------

  it('lists the operations the caller may see, newest first, each linking to its page', async () => {
    mockApi({
      summary: {
        ...SUMMARY,
        recent_operations: {
          ...SUMMARY.recent_operations,
          data_state: 'ok',
          collected_at: '2026-10-22T09:30:00Z',
          source: 'postgresql',
          unavailable_reason: null,
          items: [
            {
              operation_id: 'op-1',
              action_type: 'restart',
              server_id: 's1',
              server_display_name: 'Web 01',
              service_name: 'nginx.service',
              environment: 'staging',
              execution_status: 'SUCCEEDED',
              requested_by: { id: 'u1', display_name: 'Riley' },
              created_at: '2026-10-22T09:00:00Z',
            },
          ],
        },
      },
    });
    renderDashboard();
    const link = await screen.findByRole('link', { name: /restart nginx.service on Web 01/ });
    expect(link).toHaveAttribute('href', '/operations/op-1');
    expect(screen.getByRole('link', { name: 'All operations' })).toHaveAttribute(
      'href',
      '/operations',
    );
  });

  it('says "none in your scope" for an empty operations list, which is a fact', async () => {
    mockApi({
      summary: {
        ...SUMMARY,
        recent_operations: {
          ...SUMMARY.recent_operations,
          data_state: 'ok',
          collected_at: '2026-10-22T09:30:00Z',
          source: 'postgresql',
          unavailable_reason: null,
          items: [],
        },
      },
    });
    renderDashboard();
    expect(await screen.findByText('No operations within your access scope.')).toBeInTheDocument();
  });

  it('says "not configured" for a null operations list rather than "none"', async () => {
    mockApi();
    renderDashboard();
    await screen.findByText('6');
    expect(screen.queryByText('No operations within your access scope.')).toBeNull();
  });

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

  it('has no axe violations with real health data on the page', async () => {
    mockApi({
      summary: {
        ...SUMMARY,
        server_health: {
          ...SUMMARY.server_health,
          healthy: 3,
          warning: 1,
          stale: 1,
          unreachable: 1,
          unknown: 0,
          data_state: 'ok',
          collected_at: '2026-10-15T09:29:50Z',
          unavailable_reason: null,
        },
        recent_health_changes: {
          ...SUMMARY.recent_health_changes,
          items: [
            {
              server_id: 's9',
              display_name: 'Db 09',
              environment: 'production',
              previous_status: 'MANAGED',
              new_status: 'STALE',
              health_state: 'stale',
              occurred_at: '2026-10-15T09:20:00Z',
            },
          ],
        },
      },
      health: healthList([
        { ...ROW, health_state: 'stale', last_seen_at: '2026-10-15T09:20:00Z', stale: true },
        { ...ROW, server_id: 's2', display_name: 'Web 02', health_state: 'unknown' },
      ]),
    });
    const { container } = renderDashboard();
    await screen.findByRole('link', { name: 'Db 09' });
    const results = await axe.run(container, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });

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
