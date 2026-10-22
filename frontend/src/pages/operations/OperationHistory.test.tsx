import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { OperationHistory, ServerOperations } from '@/pages/operations/OperationHistory';
import source from '@/pages/operations/OperationHistory.tsx?raw';

/**
 * §23 from the reader's side (PRD §4.12).
 *
 * What the page owes the reader: the result with its evidence, an unknown outcome said as unknown,
 * an empty list that does not claim nothing happened, filters that go to the backend, and cells
 * that are text whatever a request contained.
 */

function item(over: Record<string, unknown> = {}) {
  return {
    operation_id: 'op-1',
    execution_status: 'SUCCEEDED',
    approval_status: 'NOT_REQUIRED',
    action_type: 'restart',
    target: {
      server_id: 's1',
      server_display_name: 'Web 1',
      service_name: 'nginx.service',
      environment: 'staging',
    },
    requested_by: { id: 'u2', display_name: 'Riley Ops' },
    approval: null,
    created_at: '2026-10-22T09:00:00Z',
    started_at: '2026-10-22T09:00:05Z',
    completed_at: '2026-10-22T09:00:20Z',
    deadline_at: '2026-10-22T09:05:00Z',
    failure_reason: null,
    correlation_id: 'c1',
    external_execution_id: 'cmd-0001',
    observed_state_before: 'INACTIVE',
    observed_state_after: 'ACTIVE',
    ...over,
  };
}

function list(items: unknown[], page: Record<string, unknown> = {}) {
  return {
    items,
    page: { limit: 50, next_cursor: null, has_more: false, total: null, ...page },
  };
}

function state(): SessionState {
  const session: Session = {
    user: { id: 'me-id', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'APPROVER', scope: {} }],
    permissions: ['operation:read'],
    session: { expires_at: '2026-10-23T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: false,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

function mockApi(respond: (url: string, n: number) => Promise<unknown>) {
  let n = 0;
  const fetchMock = vi.fn().mockImplementation((url: string) => respond(url, n++));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
const urls = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.map((c) => String(c[0]));

function renderPage(path = '/operations') {
  return render(
    <SessionContext.Provider
      value={{ state: state(), refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/operations" element={<OperationHistory />} />
          <Route path="/servers/:serverId/operations" element={<ServerOperations />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('what a row says', () => {
  it('shows a success with the state read back, and the execution id', async () => {
    mockApi(() => reply(200, list([item()])));
    renderPage();
    const row = (await screen.findByText('Web 1')).closest('tr') as HTMLElement;
    expect(within(row).getByText(/Verified: ACTIVE/)).toBeInTheDocument();
    expect(within(row).getByText('cmd-0001')).toBeInTheDocument();
    expect(within(row).getByText('nginx.service', { exact: false })).toBeInTheDocument();
  });

  it('says a timeout is an unknown outcome, never a failure', async () => {
    mockApi(() =>
      reply(
        200,
        list([
          item({
            execution_status: 'TIMED_OUT',
            observed_state_after: null,
            external_execution_id: null,
          }),
        ]),
      ),
    );
    renderPage();
    expect(await screen.findByText(/Outcome unknown/)).toBeInTheDocument();
  });

  it('names the approver on an approved operation, and says "not needed" where none was', async () => {
    mockApi(() =>
      reply(
        200,
        list([
          item({
            operation_id: 'op-a',
            approval: {
              approval_id: 'ap',
              decision: 'APPROVED',
              approved_by: { id: 'u3', display_name: 'Sam Approver' },
              decided_at: '2026-10-22T09:00:02Z',
            },
          }),
          item({ operation_id: 'op-b' }),
        ]),
      ),
    );
    renderPage();
    expect(await screen.findByText(/approved by Sam Approver/)).toBeInTheDocument();
    expect(screen.getByText('Not needed')).toBeInTheDocument();
  });

  it('shows an unfinished row as not finished and a cancelled one as nothing run', async () => {
    mockApi(() =>
      reply(
        200,
        list([
          item({ operation_id: 'op-a', execution_status: 'RUNNING', completed_at: null }),
          item({ operation_id: 'op-b', execution_status: 'CANCELLED' }),
        ]),
      ),
    );
    renderPage();
    expect(await screen.findByText('Not finished')).toBeInTheDocument();
    expect(screen.getByText('Nothing was run')).toBeInTheDocument();
  });

  it('renders markup in a name as text', async () => {
    mockApi(() =>
      reply(
        200,
        list([
          item({
            requested_by: { id: 'u9', display_name: '<img src=x onerror=alert(1)>' },
          }),
        ]),
      ),
    );
    const { container } = renderPage();
    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(source).not.toContain('dangerouslySetInnerHTML');
  });
});

describe('states', () => {
  it('does not claim "nothing happened" when the list is empty', async () => {
    mockApi(() => reply(200, list([])));
    renderPage();
    expect(await screen.findByText(/none are within your access scope/)).toBeInTheDocument();
  });

  it('shows a transport failure as such, with a way to retry', async () => {
    mockApi(() => Promise.reject(new Error('network down')));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not reach the backend/);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows a refusal in words and not as an empty list', async () => {
    mockApi(() => reply(403, { error: { code: 'forbidden', message: 'no' } }));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/do not have permission/);
  });
});

describe('filters go to the backend', () => {
  it('sends what was chosen, only after Apply, and starts again from page one', async () => {
    const fetchMock = mockApi(() => reply(200, list([item()])));
    renderPage();
    await screen.findByText('Web 1');
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText('Status'), 'FAILED');
    await user.selectOptions(screen.getByLabelText('Environment'), 'production');
    expect(urls(fetchMock)).toHaveLength(1); // nothing was asked while the form was half filled

    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toContain('execution_status=FAILED');
    });
    expect(urls(fetchMock).at(-1)).toContain('environment=production');
  });

  it('"only my requests" narrows to the signed-in person, by id', async () => {
    const fetchMock = mockApi(() => reply(200, list([item()])));
    renderPage();
    await screen.findByText('Web 1');
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('Only my requests'));
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toContain('requested_by=me-id');
    });
  });

  it('reads a requester from the address, and only ever as a narrowing filter', async () => {
    const fetchMock = mockApi(() => reply(200, list([item()])));
    renderPage('/operations?requested_by=u2');
    await screen.findByText('Web 1');
    expect(urls(fetchMock)[0]).toContain('requested_by=u2');
  });

  it('sends a range as instants with an offset', async () => {
    const fetchMock = mockApi(() => reply(200, list([item()])));
    renderPage();
    await screen.findByText('Web 1');
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('From'), '2026-10-01T00:00');
    await user.type(screen.getByLabelText('To'), '2026-10-08T00:00');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toMatch(
        /from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}\.\d{3}Z/,
      );
    });
  });

  it("shows the backend's refusal of a range it will not serve, not an empty table", async () => {
    mockApi((url) =>
      url.includes('from=')
        ? reply(422, {
            error: {
              code: 'validation_failed',
              message: 'The range is limited to 90 days.',
              details: {
                fields: [{ loc: ['query', 'from'], code: 'range_too_large', message: 'x' }],
              },
            },
          })
        : reply(200, list([item()])),
    );
    renderPage();
    await screen.findByText('Web 1');
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('From'), '2025-01-01T00:00');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/90 days/);
  });
});

describe('paging', () => {
  it('follows the cursor the backend gave and can go back', async () => {
    const fetchMock = mockApi((url) =>
      url.includes('cursor=next-1')
        ? reply(200, list([item({ operation_id: 'op-2', external_execution_id: 'cmd-0002' })]))
        : reply(200, list([item()], { next_cursor: 'next-1', has_more: true })),
    );
    renderPage();
    await screen.findByText('cmd-0001');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('cmd-0002')).toBeInTheDocument();
    expect(screen.getByText('Page 2')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('cmd-0001')).toBeInTheDocument();
    expect(urls(fetchMock).some((u) => u.includes('cursor=next-1'))).toBe(true);
  });
});

describe('the per-server view (§23.2)', () => {
  it('asks the per-server endpoint and says whose operations these are', async () => {
    const fetchMock = mockApi(() => reply(200, list([item()])));
    renderPage('/servers/s1/operations');
    expect(await screen.findByRole('heading', { name: 'Operations on this server' })).toBeVisible();
    expect(urls(fetchMock)[0]).toMatch(/\/servers\/s1\/operations/);
  });
});
