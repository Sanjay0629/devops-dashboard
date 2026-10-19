import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { AlertDetail } from '@/pages/alerts/AlertDetail';

/**
 * §20.2 - §20.4 from the page's side.
 *
 * The properties that matter: acknowledging is shown as *still open*, never as resolved;
 * an expired alert says it was never confirmed either way; **there is no way to resolve one
 * from here**; the controls follow permission and read-only mode while the backend decides
 * every request; a `409` is shown, not retried; and alert text is text.
 */

function detail(over: Record<string, unknown> = {}) {
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
    details: { labels: { instance: 'web-1:9100' }, annotations: { summary: 'x' } },
    history: [{ at: '2026-10-18T08:00:00Z', event: 'first_seen' }],
    previous_occurrences: [],
    ...over,
  };
}

const ACKED = detail({
  status: 'ACKNOWLEDGED',
  acknowledged_by: { id: 'u1', display_name: 'Alex Kim' },
  acknowledged_at: '2026-10-18T08:10:00Z',
  history: [
    { at: '2026-10-18T08:00:00Z', event: 'first_seen' },
    {
      at: '2026-10-18T08:10:00Z',
      event: 'acknowledged',
      actor: 'alex@example.invalid',
      note: 'on it',
    },
  ],
});

function session(permissions: string[], readOnly = false): Session {
  return {
    user: { id: 'u1', email: 'alex@example.invalid', display_name: 'Alex Kim', status: 'ACTIVE' },
    roles: [{ name: 'DEVOPS_ENGINEER', scope: {} }],
    permissions,
    session: { expires_at: '2026-10-19T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
}

const authenticated = (permissions: string[], readOnly = false): SessionState => ({
  kind: 'authenticated',
  session: session(permissions, readOnly),
});

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

interface Writes {
  status: number;
  body?: unknown;
}

function mockApi(read: unknown = detail(), write: Writes = { status: 200, body: ACKED }) {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return reply(write.status, write.body ?? ACKED);
    return reply(200, read);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const posts = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST')
    .map((call) => ({
      url: String(call[0]),
      body: JSON.parse((call[1] as { body: string }).body) as Record<string, unknown>,
    }));

function renderPage(state: SessionState = authenticated(['alert:read', 'alert:acknowledge'])) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter initialEntries={['/alerts/a1']}>
        <Routes>
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

describe('AlertDetail', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the alert, its timeline and what the source sent', async () => {
    mockApi(
      detail({
        previous_occurrences: [
          {
            alert_id: 'a0',
            status: 'RESOLVED',
            first_seen_at: '2026-10-17T08:00:00Z',
            last_seen_at: '2026-10-17T08:20:00Z',
            resolved_at: '2026-10-17T08:21:00Z',
            occurrence_count: 2,
          },
        ],
      }),
    );
    renderPage();
    expect(
      await screen.findByRole('heading', { name: 'CPU on web-1 is at 95.0%' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Web 1' })).toHaveAttribute('href', '/servers/s1');
    const timeline = screen.getByRole('heading', { name: 'Timeline' }).nextElementSibling;
    expect(within(timeline as HTMLElement).getByText(/First seen/)).toBeInTheDocument();
    const earlier = screen.getByRole('heading', { name: 'Earlier occurrences' }).nextElementSibling;
    expect(within(earlier as HTMLElement).getByRole('link')).toHaveAttribute('href', '/alerts/a0');
    expect(screen.getByText(/"instance": "web-1:9100"/)).toBeInTheDocument();
  });

  it('shows markup in the stored text as text and creates no element', async () => {
    const markup = '<img src=x onerror=alert(1)>';
    mockApi(detail({ summary: markup, details: { annotations: { description: markup } } }));
    const { container } = renderPage();
    expect(await screen.findByRole('heading', { name: markup })).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });

  it('says acknowledged is not resolved', async () => {
    mockApi(ACKED);
    renderPage();
    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent(/Acknowledged means someone has taken it/);
    expect(note).toHaveTextContent(/not resolved/);
    expect(screen.getByText('Acknowledged (still open)')).toBeInTheDocument();
    expect(screen.queryByText(/Resolved by its source/)).toBeNull();
  });

  it('says an expired alert was never confirmed either way, and not that it went away', async () => {
    mockApi(
      detail({
        status: 'EXPIRED',
        history: [
          { at: '2026-10-18T08:00:00Z', event: 'first_seen' },
          { at: '2026-10-18T11:30:00Z', event: 'expired' },
        ],
      }),
    );
    renderPage();
    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent('Not confirmed either way.');
    expect(note).toHaveTextContent(/may or may not still be happening/);
    const timeline = screen.getByRole('heading', { name: 'Timeline' }).nextElementSibling;
    expect(
      within(timeline as HTMLElement).getByText(/Expired: never confirmed either way/),
    ).toBeInTheDocument();
  });

  it('says a resolved alert was resolved by its source', async () => {
    mockApi(detail({ status: 'RESOLVED', resolved_at: '2026-10-18T09:00:00Z' }));
    renderPage();
    expect(await screen.findByRole('note')).toHaveTextContent(/Resolved by its source at/);
  });

  it('has no way to resolve an alert, whatever the status or the permissions', async () => {
    for (const status of ['FIRING', 'ACKNOWLEDGED', 'RESOLVED', 'EXPIRED']) {
      vi.unstubAllGlobals();
      mockApi(detail({ status }));
      const { unmount } = renderPage(
        authenticated(['alert:read', 'alert:acknowledge', 'admin:manage']),
      );
      await screen.findByRole('heading', { name: 'CPU on web-1 is at 95.0%' });
      expect(screen.queryByRole('button', { name: /resolve|close|dismiss/i })).toBeNull();
      unmount();
    }
  });

  it('offers acknowledge and assign on a firing alert to someone who may act', async () => {
    mockApi();
    renderPage();
    expect(await screen.findByRole('button', { name: 'Acknowledge' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Assign to me' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Unassign' })).toBeNull();
  });

  it('offers nothing to someone who may only read', async () => {
    mockApi();
    renderPage(authenticated(['alert:read']));
    await screen.findByRole('heading', { name: 'CPU on web-1 is at 95.0%' });
    expect(screen.queryByRole('group', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull();
  });

  it('disables the controls in read-only mode', async () => {
    mockApi();
    renderPage(authenticated(['alert:read', 'alert:acknowledge'], true));
    expect(await screen.findByRole('button', { name: 'Acknowledge' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Assign to me' })).toBeDisabled();
  });

  it('offers no acknowledge on an alert that is already acknowledged, only assignment', async () => {
    mockApi(ACKED);
    renderPage();
    await screen.findByRole('note');
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Assign to me' })).toBeInTheDocument();
  });

  it('offers nothing on a closed alert', async () => {
    mockApi(detail({ status: 'RESOLVED', resolved_at: '2026-10-18T09:00:00Z' }));
    renderPage();
    await screen.findByRole('note');
    expect(screen.queryByRole('group', { name: 'Actions' })).toBeNull();
  });

  it('acknowledges with a note and then shows it as acknowledged but still open', async () => {
    const fetchMock = mockApi();
    renderPage();
    await userEvent.type(await screen.findByLabelText('Note (optional)'), '  looking  ');
    await userEvent.click(screen.getByRole('button', { name: 'Acknowledge' }));

    expect(await screen.findByText('Acknowledged (still open)')).toBeInTheDocument();
    expect(posts(fetchMock)).toEqual([
      { url: '/api/v1/alerts/a1/acknowledge', body: { note: 'looking' } },
    ]);
    expect(screen.getByRole('note')).toHaveTextContent(/not.*resolved/s);
    // The acknowledger is shown, and the Acknowledge control is gone.
    expect(screen.getAllByText(/Alex Kim/).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull();
  });

  it('sends no note at all when none was typed', async () => {
    const fetchMock = mockApi();
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    await screen.findByText('Acknowledged (still open)');
    expect(posts(fetchMock)[0]?.body).toEqual({});
  });

  it('assigns to the signed-in user, then offers to unassign', async () => {
    const assigned = detail({ assigned_to: { id: 'u1', display_name: 'Alex Kim' } });
    const fetchMock = mockApi(detail(), { status: 200, body: assigned });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Assign to me' }));
    expect(await screen.findByRole('button', { name: 'Unassign' })).toBeInTheDocument();
    expect(posts(fetchMock)).toEqual([
      { url: '/api/v1/alerts/a1/assign', body: { assignee_id: 'u1' } },
    ]);
    // Already mine: no second "assign to me".
    expect(screen.queryByRole('button', { name: 'Assign to me' })).toBeNull();
  });

  it('unassigns by sending null', async () => {
    const fetchMock = mockApi(detail({ assigned_to: { id: 'u9', display_name: 'Sam Rivera' } }), {
      status: 200,
      body: detail(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Unassign' }));
    await waitFor(() => {
      expect(posts(fetchMock)).toEqual([
        { url: '/api/v1/alerts/a1/assign', body: { assignee_id: null } },
      ]);
    });
  });

  it('shows a conflict as a conflict, does not retry it, and offers to reload', async () => {
    const fetchMock = mockApi(detail(), {
      status: 409,
      body: { error: { code: 'invalid_state_transition', message: 'no' } },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    const box = await screen.findByRole('alert');
    expect(box).toHaveTextContent(/changed while you were looking at it, so nothing was done/);
    expect(posts(fetchMock)).toHaveLength(1); // never retried
    const reads = fetchMock.mock.calls.length;
    await userEvent.click(within(box).getByRole('button', { name: 'Reload' }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(reads);
    });
  });

  it.each([
    [403, { error: { code: 'forbidden', message: 'x' } }, /do not have permission/],
    [423, { error: { code: 'read_only_mode', message: 'x' } }, /read-only mode/],
    [
      404,
      { error: { code: 'not_found', message: 'x' } },
      /does not exist, or is outside your access scope/,
    ],
    [
      422,
      { error: { code: 'validation_failed', message: 'That user cannot be assigned this alert.' } },
      /cannot be assigned/,
    ],
  ])('shows a %i for what it is', async (status, body, text) => {
    mockApi(detail(), { status, body });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
  });

  it('says an alert is missing or out of scope without saying which', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(404, { error: { code: 'not_found', message: 'x' } })),
    );
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /may not exist, or may be outside your access scope/,
    );
  });

  it('shows the timeline with who did what', async () => {
    mockApi(ACKED);
    renderPage();
    const timeline = (await screen.findByRole('heading', { name: 'Timeline' }))
      .nextElementSibling as HTMLElement;
    expect(within(timeline).getByText(/Acknowledged/)).toBeInTheDocument();
    expect(within(timeline).getByText(/by alex@example.invalid/)).toBeInTheDocument();
    expect(within(timeline).getByText(/on it/)).toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    mockApi(ACKED);
    const { container } = renderPage();
    await screen.findByRole('note');

    const results = await axe.run(container, {
      // Landmark and page-level rules need the whole document; this renders one section into a
      // bare div. Contrast cannot be computed without layout (jsdom), so it is disabled
      // explicitly and belongs to a real browser.
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });

  it('says when there are no earlier occurrences', async () => {
    mockApi();
    renderPage();
    expect(
      await screen.findByText(/None recorded for this condition on this target/),
    ).toBeInTheDocument();
  });
});

function delivery(over: Record<string, unknown> = {}) {
  return {
    delivery_id: 'd1',
    channel_id: 'c1',
    channel_name: 'ops-slack',
    channel_type: 'SLACK',
    status: 'FAILED',
    attempt_count: 1,
    last_error: 'http_404',
    next_attempt_at: null,
    sent_at: null,
    created_at: '2026-10-18T08:00:05Z',
    retryable: true,
    ...over,
  };
}

const postedUrls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST')
    .map((call) => String(call[0]));

describe('AlertDetail notifications (M3.4)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('says nobody was notified when there are no deliveries', async () => {
    mockApi(detail({ notifications: [] }));
    renderPage();
    expect(await screen.findByText(/Nobody was notified about this alert/)).toBeInTheDocument();
  });

  it('tolerates a response from before notifications existed', async () => {
    mockApi(detail());
    renderPage();
    expect(await screen.findByText(/Nobody was notified about this alert/)).toBeInTheDocument();
  });

  it('shows each delivery, its status, attempts and the reason in words', async () => {
    mockApi(
      detail({
        notifications: [
          delivery(),
          delivery({
            delivery_id: 'd2',
            channel_name: 'mail',
            channel_type: 'EMAIL',
            status: 'SENT',
            attempt_count: 1,
            last_error: null,
            sent_at: '2026-10-18T08:01:00Z',
            retryable: false,
          }),
          delivery({
            delivery_id: 'd3',
            channel_name: 'pager',
            channel_type: 'WEBHOOK',
            status: 'ABANDONED',
            attempt_count: 5,
            last_error: 'timeout',
          }),
          delivery({
            delivery_id: 'd4',
            channel_name: 'later',
            status: 'PENDING',
            attempt_count: 2,
            last_error: 'http_503',
            next_attempt_at: '2026-10-18T09:00:00Z',
            retryable: false,
          }),
        ],
      }),
    );
    renderPage();
    const table = await screen.findByRole('table', {
      name: 'Notification deliveries for this alert',
    });
    expect(within(table).getByText('Failed')).toBeInTheDocument();
    expect(within(table).getAllByText('Sent').length).toBeGreaterThan(0);
    expect(within(table).getByText('Gave up (retries used)')).toBeInTheDocument();
    expect(within(table).getByText(/the destination answered HTTP 404/)).toBeInTheDocument();
    expect(within(table).getByText(/the destination did not answer in time/)).toBeInTheDocument();
    expect(within(table).getByText(/Retrying/)).toBeInTheDocument();
    // A delivered message offers no retry; a failed or abandoned one does; a waiting one does not.
    expect(within(table).getAllByRole('button', { name: /^Retry sending to/ })).toHaveLength(2);
    // Raw reason codes are not shown as codes.
    expect(within(table).queryByText('http_404')).toBeNull();
  });

  it('shows a channel name containing markup as text', async () => {
    const markup = '<img src=x onerror=alert(1)>';
    mockApi(detail({ notifications: [delivery({ channel_name: markup })] }));
    const { container } = renderPage();
    expect(
      await screen.findByText(new RegExp(markup.replace(/[()]/g, '\\$&'))),
    ).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });

  it('retries a failed delivery by its ids, then reads the alert again', async () => {
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') return reply(202, delivery({ status: 'PENDING' }));
      return reply(200, detail({ notifications: [delivery()] }));
    });
    vi.stubGlobal('fetch', fetchMock);
    renderPage();
    await userEvent.click(
      await screen.findByRole('button', { name: 'Retry sending to ops-slack' }),
    );
    await waitFor(() => {
      expect(postedUrls(fetchMock)).toEqual(['/api/v1/alerts/a1/notifications/d1/retry']);
    });
    // Read once on load and again after the retry.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter((c) => !(c[1] as RequestInit | undefined)?.method),
      ).toHaveLength(2);
    });
  });

  it('offers no retry to someone who may only read, or in read-only mode', async () => {
    mockApi(detail({ notifications: [delivery()] }));
    const { unmount } = renderPage(authenticated(['alert:read']));
    await screen.findByRole('table', { name: 'Notification deliveries for this alert' });
    expect(screen.queryByRole('button', { name: /^Retry sending to/ })).toBeNull();
    unmount();

    vi.unstubAllGlobals();
    mockApi(detail({ notifications: [delivery()] }));
    renderPage(authenticated(['alert:read', 'alert:acknowledge'], true));
    expect(
      await screen.findByRole('button', { name: 'Retry sending to ops-slack' }),
    ).toBeDisabled();
  });

  it('shows a conflict as one and does not retry it by itself', async () => {
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return reply(409, { error: { code: 'invalid_state_transition', message: 'x' } });
      }
      return reply(200, detail({ notifications: [delivery()] }));
    });
    vi.stubGlobal('fetch', fetchMock);
    renderPage();
    await userEvent.click(
      await screen.findByRole('button', { name: 'Retry sending to ops-slack' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /cannot be retried in its current state/,
    );
    expect(postedUrls(fetchMock)).toHaveLength(1);
  });
});
