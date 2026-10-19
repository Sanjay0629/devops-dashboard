import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { OperationDetailPage } from '@/pages/operations/OperationDetail';

/**
 * §17.2 / §17.3 from the page's side. What matters: a success is shown with its evidence and
 * nothing is called succeeded without it; a timeout and a reconciliation flag say "we do not
 * know"; polling follows the interval the backend names and stops when the operation is
 * terminal; cancel is offered only while cancellable and a 409 is shown, not retried.
 */

function op(over: Record<string, unknown> = {}) {
  return {
    operation_id: 'op-1',
    execution_status: 'PENDING',
    approval_status: 'NOT_REQUIRED',
    action_type: 'restart',
    target: {
      server_id: 's1',
      server_display_name: 'Web 1',
      service_name: 'nginx.service',
      environment: 'staging',
    },
    requested_by: { id: 'u1', display_name: 'Alex Kim' },
    requested_reason: 'CR-1',
    approval: null,
    deadline_at: '2026-10-19T09:35:00Z',
    created_at: '2026-10-19T09:30:00Z',
    correlation_id: 'c1',
    poll_after_seconds: 2,
    observed_state_before: null,
    observed_state_after: null,
    verified_at: null,
    sanitized_result: { adapter_id: 'scripted' },
    failure_reason: null,
    external_execution_id: null,
    parent_operation_id: null,
    started_at: null,
    completed_at: null,
    ...over,
  };
}

const SUCCESS = op({
  execution_status: 'SUCCEEDED',
  poll_after_seconds: null,
  observed_state_before: 'INACTIVE',
  observed_state_after: 'ACTIVE',
  verified_at: '2026-10-19T09:31:12Z',
  started_at: '2026-10-19T09:31:00Z',
  completed_at: '2026-10-19T09:31:12Z',
  external_execution_id: 'scripted-1',
  sanitized_result: { adapter_id: 'scripted', steps: ['claimed', 'validated', 'submitted'] },
});

function state(readOnly = false): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'DEVOPS_ENGINEER', scope: {} }],
    permissions: ['operation:read'],
    session: { expires_at: '2026-10-19T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

function renderPage(readOnly = false) {
  return render(
    <SessionContext.Provider
      value={{
        state: state(readOnly),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <MemoryRouter initialEntries={['/operations/op-1']}>
        <Routes>
          <Route path="/operations/:operationId" element={<OperationDetailPage />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

const getCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method !== 'POST');

describe('OperationDetailPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows a verified success with the state that was read back', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(200, SUCCESS)),
    );
    renderPage();
    expect(await screen.findByText('Succeeded — verified')).toBeInTheDocument();
    expect(screen.getByText(/verified: the service was read back as/i)).toHaveTextContent('ACTIVE');
    expect(screen.getByText('INACTIVE')).toBeInTheDocument();
    expect(screen.getByText('scripted-1')).toBeInTheDocument();
    expect(screen.getByText('claimed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
  });

  it('does not call anything succeeded that has no evidence', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(200, op({ execution_status: 'RUNNING' }))),
    );
    renderPage();
    expect(await screen.findByText('Running')).toBeInTheDocument();
    expect(screen.queryByText(/verified/i)).not.toBeInTheDocument();
    expect(screen.getAllByText('Not observed')).toHaveLength(2);
  });

  it('says a failed verification is a failure, with the closed reason in words', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        reply(
          200,
          op({
            execution_status: 'FAILED',
            poll_after_seconds: null,
            failure_reason: 'VERIFICATION_MISMATCH',
            observed_state_before: 'INACTIVE',
            observed_state_after: 'INACTIVE',
          }),
        ),
      ),
    );
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/not in the state requested/i);
    expect(screen.queryByText(/verified: the service/i)).not.toBeInTheDocument();
  });

  it('says a timeout means the outcome is unknown and tells the person to check before retrying', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        reply(
          200,
          op({
            execution_status: 'TIMED_OUT',
            poll_after_seconds: null,
            failure_reason: 'DEADLINE_EXCEEDED',
            sanitized_result: { reconciliation_required: true },
          }),
        ),
      ),
    );
    renderPage();
    expect(await screen.findByText(/outcome is unknown/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/may have acted/i);
    expect(screen.getByRole('alert')).toHaveTextContent(/check the service/i);
  });

  it('shows an unknown failure code as itself rather than hiding it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        reply(
          200,
          op({
            execution_status: 'FAILED',
            poll_after_seconds: null,
            failure_reason: 'NEW_CODE',
          }),
        ),
      ),
    );
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('NEW_CODE');
  });

  it('polls at the interval the backend names and stops when the operation is terminal', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => reply(200, op({ poll_after_seconds: 2 })))
      .mockImplementationOnce(() =>
        reply(200, op({ execution_status: 'RUNNING', poll_after_seconds: 2 })),
      )
      .mockImplementation(() => reply(200, SUCCESS));
    vi.stubGlobal('fetch', fetchMock);
    renderPage();

    expect(await screen.findByText('Waiting for a worker')).toBeInTheDocument();
    expect(screen.getByText(/checking again every 2 seconds/i)).toBeInTheDocument();
    expect(getCalls(fetchMock)).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100);
    });
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Running');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100);
    });
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Succeeded');
    });
    const settled = getCalls(fetchMock).length;
    expect(settled).toBe(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000); // nothing more is asked of a finished operation
    });
    expect(getCalls(fetchMock)).toHaveLength(settled);
  });

  it('offers cancel only while the operation is cancellable and posts to the cancel endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation((_url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? reply(200, op({ execution_status: 'CANCELLED', poll_after_seconds: null }))
          : reply(200, op()),
      );
    vi.stubGlobal('fetch', fetchMock);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /cancel this operation/i }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/operations/op-1/cancel')),
      ).toBe(true);
    });
  });

  it.each(['RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED'])(
    'offers no cancel for %s',
    async (status) => {
      vi.stubGlobal(
        'fetch',
        vi
          .fn()
          .mockImplementation(() =>
            reply(
              200,
              op({ execution_status: status, poll_after_seconds: status === 'RUNNING' ? 2 : null }),
            ),
          ),
      );
      renderPage();
      await screen.findByText(/waiting|running|succeeded|failed|timed out|cancelled/i);
      expect(
        screen.queryByRole('button', { name: /cancel this operation/i }),
      ).not.toBeInTheDocument();
    },
  );

  it('shows a 409 on cancel as "a worker got there first" and does not retry', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation((_url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? reply(409, { error: { code: 'invalid_state_transition', message: 'm' } })
          : reply(200, op()),
      );
    vi.stubGlobal('fetch', fetchMock);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /cancel this operation/i }));
    expect(await screen.findByText(/already picked this up/i)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === 'POST'),
    ).toHaveLength(1);
  });

  it('disables cancel in read-only mode', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(200, op())),
    );
    renderPage(true);
    expect(await screen.findByRole('button', { name: /cancel this operation/i })).toBeDisabled();
  });

  it('says plainly when the operation is not there or not visible', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(404, {})),
    );
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i);
  });

  it('has no axe violations', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(200, SUCCESS)),
    );
    const { container } = renderPage();
    await screen.findByText('Succeeded — verified');
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });
});
