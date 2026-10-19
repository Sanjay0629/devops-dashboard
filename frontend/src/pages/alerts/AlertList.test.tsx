import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { alertsQuery } from '@/api/alerts';
import { AlertList } from '@/pages/alerts/AlertList';
import listSource from '@/pages/alerts/AlertList.tsx?raw';
import detailSource from '@/pages/alerts/AlertDetail.tsx?raw';

/**
 * §20.1 from the page's side.
 *
 * What the page owes the operator is honesty: an empty list says what is *not* watched, the
 * words for a status never rely on colour, an alert about no server says so, and the text of
 * an alert -- which came from a payload -- is text, whatever it contains.
 */

const COVERAGE = {
  produced: ['cpu', 'disk', 'host_not_reporting', 'memory', 'unit_failed'],
  not_produced_yet: [
    { condition: 'application_health_check_failed', arrives_in: 'M5' },
    { condition: 'deployment_failed', arrives_in: 'M5' },
    { condition: 'operation_timed_out_or_failed', arrives_in: 'M4' },
  ],
  notification_channels_active: 2,
};

function alert(over: Record<string, unknown> = {}) {
  return {
    alert_id: 'a1',
    source: 'PROMETHEUS',
    rule_name: 'HostCpuCritical',
    severity: 'CRITICAL',
    status: 'FIRING',
    summary: 'CPU on web-1 is at 95.0%',
    target: { type: 'server', id: 's1', display_name: 'Web 1' },
    environment: 'production',
    first_seen_at: '2026-10-18T08:00:00Z',
    last_seen_at: '2026-10-18T08:30:00Z',
    occurrence_count: 3,
    acknowledged_by: null,
    acknowledged_at: null,
    assigned_to: null,
    resolved_at: null,
    links: { server: '/api/v1/servers/s1', service: null },
    ...over,
  };
}

function page(items: unknown[], over: Record<string, unknown> = {}) {
  return {
    items,
    page: { limit: 50, next_cursor: null, has_more: false, total: null },
    coverage: COVERAGE,
    demo_mode: false,
    ...over,
  };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

function mockApi(
  respond: (url: string, n: number) => Promise<unknown> = () => reply(200, page([alert()])),
) {
  let n = 0;
  const fetchMock = vi.fn().mockImplementation((url: string) => respond(url, n++));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const urls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.map((call) => String(call[0]));

function renderPage() {
  return render(
    <MemoryRouter>
      <AlertList />
    </MemoryRouter>,
  );
}

describe('alertsQuery', () => {
  it('repeats a parameter for each value, because the API takes lists that way', () => {
    expect(
      alertsQuery({
        statuses: ['FIRING', 'ACKNOWLEDGED'],
        severities: ['CRITICAL'],
        environment: 'production',
        q: '  disk 100%  ',
        cursor: 'c1',
      }),
    ).toBe(
      '?status=FIRING&status=ACKNOWLEDGED&severity=CRITICAL&environment=production&q=disk+100%25&cursor=c1',
    );
  });

  it('omits what is empty, and is empty when nothing is set', () => {
    expect(alertsQuery({ statuses: [], severities: [], environment: '', q: '   ' })).toBe('');
  });
});

describe('AlertList notification coverage (M3.4)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  function show(active: number) {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValue(
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              page([alert()], { coverage: { ...COVERAGE, notification_channels_active: active } }),
            ),
        }),
      ),
    );
    return render(
      <MemoryRouter>
        <AlertList />
      </MemoryRouter>,
    );
  }

  it('says alerts are being sent nowhere when no channel is active', async () => {
    show(0);
    const note = await screen.findByText(/Alerts are not being sent anywhere/);
    expect(note.closest('p')).toHaveAttribute('role', 'status');
    expect(note.closest('p')).toHaveTextContent(/nobody is told when one begins/);
  });

  it('says how many channels are told when some are active', async () => {
    show(2);
    expect(await screen.findByText(/2 channels are/)).toBeInTheDocument();
    expect(screen.queryByText(/not being sent anywhere/)).toBeNull();
  });

  it('uses the singular for one channel', async () => {
    show(1);
    expect(await screen.findByText(/1 channel is/)).toBeInTheDocument();
  });
});

describe('AlertList', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks for what is still open by default', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByRole('link', { name: 'CPU on web-1 is at 95.0%' });
    expect(urls(fetchMock)[0]).toBe('/api/v1/alerts?status=FIRING&status=ACKNOWLEDGED');
  });

  it('shows severity and status as words with a shape, not as colour alone', async () => {
    mockApi(() =>
      reply(
        200,
        page([alert(), alert({ alert_id: 'a2', status: 'ACKNOWLEDGED', severity: 'WARNING' })]),
      ),
    );
    renderPage();
    const rows = await screen.findAllByRole('row');
    const first = rows[1] as HTMLElement;
    expect(within(first).getByText('Critical')).toBeInTheDocument();
    expect(within(first).getByText('Firing')).toBeInTheDocument();
    // Acknowledged is drawn as still open, never as resolved.
    expect(screen.getByText('Acknowledged (still open)')).toBeInTheDocument();
    expect(within(screen.getByRole('table')).queryByText('Resolved')).toBeNull();
  });

  it('renders a summary containing markup as text, creating no element', async () => {
    const markup = '<img src=x onerror=alert(1)> restart nginx everywhere';
    mockApi(() => reply(200, page([alert({ summary: markup })])));
    const { container } = renderPage();
    expect(await screen.findByRole('link', { name: markup })).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
  });

  it('never uses dangerouslySetInnerHTML on alert text', () => {
    // Comments stripped first: the files explain the rule in words and would trip their own pin.
    for (const source of [listSource, detailSource]) {
      const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
      expect(code).not.toContain('dangerouslySetInnerHTML');
      expect(code).not.toContain('innerHTML');
    }
  });

  it('says an alert about no server is not linked to one', async () => {
    mockApi(() =>
      reply(
        200,
        page([alert({ target: null, links: { server: null, service: null }, environment: null })]),
      ),
    );
    renderPage();
    expect(await screen.findByText('Not linked to a server')).toBeInTheDocument();
  });

  it('links the server by its own page', async () => {
    mockApi();
    renderPage();
    expect(await screen.findByRole('link', { name: 'Web 1' })).toHaveAttribute(
      'href',
      '/servers/s1',
    );
  });

  it('an empty list names what is not watched, so nothing reads as all clear', async () => {
    mockApi(() => reply(200, page([])));
    renderPage();
    expect(await screen.findByText(/No open alerts match these filters/)).toBeInTheDocument();
    const note = screen.getByRole('complementary', { name: 'What raises alerts' });
    expect(note).toHaveTextContent('a host that stops reporting');
    expect(note).toHaveTextContent('Not raised yet');
    expect(note).toHaveTextContent('a failed deployment (arrives in M5)');
    expect(note).toHaveTextContent('an operation that times out or fails (arrives in M4)');
    expect(note).toHaveTextContent(/nothing is watching it, not that it is fine/);
  });

  it('shows the same note under a list that has rows', async () => {
    mockApi();
    renderPage();
    await screen.findByRole('link', { name: 'CPU on web-1 is at 95.0%' });
    expect(screen.getByRole('complementary', { name: 'What raises alerts' })).toBeInTheDocument();
  });

  it('sends a filter to the backend and starts again from page one', async () => {
    const fetchMock = mockApi((url) =>
      reply(
        200,
        page(
          [alert()],
          url.includes('cursor=')
            ? {}
            : { page: { limit: 50, next_cursor: 'c2', has_more: true, total: null } },
        ),
      ),
    );
    renderPage();
    await screen.findByRole('link', { name: 'CPU on web-1 is at 95.0%' });
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => {
      expect(urls(fetchMock).some((u) => u.includes('cursor=c2'))).toBe(true);
    });

    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'critical');
    await waitFor(() => {
      const last = urls(fetchMock).at(-1) ?? '';
      expect(last).toContain('severity=CRITICAL');
      expect(last).not.toContain('cursor=');
    });
  });

  it('asks for resolved and for expired alerts under their own names', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByRole('link', { name: 'CPU on web-1 is at 95.0%' });
    await userEvent.selectOptions(screen.getByLabelText('Show'), 'expired');
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toBe('/api/v1/alerts?status=EXPIRED');
    });
    await userEvent.selectOptions(screen.getByLabelText('Show'), 'resolved');
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toBe('/api/v1/alerts?status=RESOLVED');
    });
  });

  it('says plainly that the caller may not see alerts, instead of an empty list', async () => {
    mockApi(() => reply(403, { error: { code: 'forbidden', message: 'x' } }));
    renderPage();
    const alertBox = await screen.findByRole('alert');
    expect(alertBox).toHaveTextContent('You do not have permission to view this.');
    expect(screen.queryByText(/No open alerts/)).toBeNull();
  });

  it('reports a backend that cannot be reached, with a way to try again', async () => {
    let calls = 0;
    mockApi(() => {
      calls += 1;
      return calls === 1 ? Promise.reject(new Error('network down')) : reply(200, page([alert()]));
    });
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the backend');
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(
      await screen.findByRole('link', { name: 'CPU on web-1 is at 95.0%' }),
    ).toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    mockApi(() =>
      reply(
        200,
        page([alert(), alert({ alert_id: 'a2', status: 'ACKNOWLEDGED', severity: 'WARNING' })]),
      ),
    );
    const { container } = renderPage();
    await screen.findAllByRole('link', { name: 'CPU on web-1 is at 95.0%' });

    const results = await axe.run(container, {
      // Landmark and page-level rules need the whole document; this renders one section into a
      // bare div. Contrast cannot be computed without layout (jsdom), so it is disabled
      // explicitly and belongs to a real browser.
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });

  it('shows who an alert is assigned to, and "Nobody" when it is not', async () => {
    mockApi(() =>
      reply(
        200,
        page([
          alert(),
          alert({ alert_id: 'a2', assigned_to: { id: 'u9', display_name: 'Sam Rivera' } }),
        ]),
      ),
    );
    renderPage();
    expect(await screen.findByText('Sam Rivera')).toBeInTheDocument();
    expect(screen.getByText('Nobody')).toBeInTheDocument();
  });
});
