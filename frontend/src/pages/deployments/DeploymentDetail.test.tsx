import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { DeploymentDetailPage } from '@/pages/deployments/DeploymentDetail';

/**
 * §21.3 / §21.6 from the page's side. What matters: a success says exactly what backs it (with no
 * health checks, *only* that the pipeline succeeded); an outcome DCP could not establish says a
 * build may exist; a Jenkins outage is shown as one and the status as "last known"; polling follows
 * the interval the backend names and stops when the deployment is terminal; cancel is offered only
 * while cancellable and a 409 is shown, not retried; no Jenkins URL is ever on the page.
 */

const NONE_CONFIGURED = { policy: 'all_must_pass', configured: false, checks: [], passed: true };

function deployment(over: Record<string, unknown> = {}) {
  return {
    deployment_id: 'd-1',
    kind: 'DEPLOY',
    application_id: 'app-1',
    application_name: 'Billing API',
    environment: 'staging',
    requested_ref: 'release/1.4',
    commit_sha: null,
    execution_status: 'REQUESTED',
    approval_status: 'NOT_REQUIRED',
    requested_by: { id: 'u1', display_name: 'Alex Kim' },
    created_at: '2026-10-27T09:30:00Z',
    started_at: null,
    completed_at: null,
    deadline_at: '2026-10-27T10:30:00Z',
    failure_reason: null,
    correlation_id: 'c1',
    target: {
      application_id: 'app-1',
      application_name: 'Billing API',
      environment: 'staging',
      pipeline_reference: 'billing/deploy',
      requested_ref: 'release/1.4',
      rollback_supported: false,
    },
    requested_reason: 'CR-1',
    external_execution_id: null,
    pipeline_result: null,
    health_check_result: {},
    sanitized_result: {},
    last_poll_error: null,
    last_polled_at: null,
    approvals: [],
    rollback_of_deployment_id: null,
    poll_after_seconds: 5,
    ...over,
  };
}

const SUCCESS = deployment({
  execution_status: 'SUCCEEDED',
  poll_after_seconds: null,
  commit_sha: '0123456789abcdef0123456789abcdef01234567',
  external_execution_id: '42',
  pipeline_result: 'SUCCESS',
  health_check_result: NONE_CONFIGURED,
  started_at: '2026-10-27T09:31:00Z',
  completed_at: '2026-10-27T09:35:00Z',
  last_polled_at: '2026-10-27T09:35:00Z',
});

function failed(code: string, over: Record<string, unknown> = {}) {
  return deployment({
    execution_status: 'FAILED',
    poll_after_seconds: null,
    failure_reason: code,
    completed_at: '2026-10-27T09:35:00Z',
    ...over,
  });
}

function state(readOnly = false): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'DEVOPS_ENGINEER', scope: {} }],
    permissions: ['deployment:read'],
    session: { expires_at: '2026-10-27T00:00:00Z', idle_timeout_seconds: 1800 },
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
      <MemoryRouter initialEntries={['/deployments/d-1']}>
        <Routes>
          <Route path="/deployments/:deploymentId" element={<DeploymentDetailPage />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

const show = (body: unknown): void => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(() => reply(200, body)),
  );
};

const getCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method !== 'POST');

describe('DeploymentDetailPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  describe('a success', () => {
    it('says what backs it: with no health checks, only that the pipeline succeeded', async () => {
      show(SUCCESS);
      renderPage();
      await screen.findByRole('heading', { level: 1 });
      expect(screen.getByRole('status')).toHaveTextContent('Succeeded');
      expect(screen.getByText(/jenkins reported the pipeline/i)).toHaveTextContent('SUCCESS');
      expect(screen.getByTestId('health-statement')).toHaveTextContent(
        'No post-deployment health checks are configured for this target; success means only that the pipeline succeeded.',
      );
      expect(screen.getByText('42')).toBeInTheDocument();
      expect(screen.getByText('0123456789abcdef0123456789abcdef01234567')).toBeInTheDocument();
    });

    it('never says healthy or verified when nothing was checked', async () => {
      show(SUCCESS);
      const { container } = renderPage();
      await screen.findByTestId('health-statement');
      expect(container.textContent).not.toMatch(/verified|is healthy|all healthy/i);
    });

    it('lists the checks when some were configured', async () => {
      show({
        ...SUCCESS,
        health_check_result: {
          policy: 'all_must_pass',
          configured: true,
          passed: true,
          checks: [{ name: 'api-health', healthy: true }],
        },
      });
      renderPage();
      const statement = await screen.findByTestId('health-statement');
      expect(statement).toHaveTextContent('Post-deployment health checks passed.');
      expect(within(statement).getByText('api-health: healthy')).toBeInTheDocument();
      expect(statement).not.toHaveTextContent(/only that the pipeline succeeded/);
    });

    it('offers no cancel and stops asking', async () => {
      show(SUCCESS);
      renderPage();
      await screen.findByTestId('health-statement');
      expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
      expect(screen.queryByText(/checking again/i)).not.toBeInTheDocument();
    });
  });

  describe('a failure', () => {
    it('gives the closed reason in words and the pipeline result', async () => {
      show(failed('PIPELINE_FAILED', { pipeline_result: 'FAILURE', external_execution_id: '7' }));
      renderPage();
      const alert = await screen.findByTestId('failure');
      expect(alert).toHaveTextContent('The pipeline failed.');
      expect(alert).toHaveTextContent('Pipeline result: FAILURE');
      expect(alert).not.toHaveTextContent(/may be running/);
    });

    it.each(['TRIGGER_OUTCOME_UNKNOWN', 'QUEUE_ITEM_LOST', 'DEPLOYMENT_TIMED_OUT'])(
      'says a build may exist, and to check Jenkins first, for %s',
      async (code) => {
        show(failed(code));
        renderPage();
        const alert = await screen.findByTestId('failure');
        expect(alert).toHaveTextContent(/a build may be running, or may have run, in jenkins/i);
        expect(alert).toHaveTextContent(/check jenkins before requesting this again/i);
      },
    );

    it.each([
      'PERMISSION_REVOKED',
      'TARGET_CHANGED',
      'JENKINS_UNAVAILABLE',
      'EXPIRED_BEFORE_START',
    ])('says nothing was sent for %s, and does not invent a running build', async (code) => {
      show(failed(code));
      renderPage();
      const alert = await screen.findByTestId('failure');
      expect(alert).not.toHaveTextContent(/may be running|may have run/);
      expect(alert).toHaveTextContent(/nothing was (sent|queued)/i);
    });

    it('shows an unknown failure code as itself rather than hiding it', async () => {
      show(failed('NEW_CODE'));
      renderPage();
      expect(await screen.findByTestId('failure')).toHaveTextContent('NEW_CODE');
    });

    it('offers no cancel', async () => {
      show(failed('PIPELINE_FAILED'));
      renderPage();
      await screen.findByTestId('failure');
      expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
    });
  });

  describe('a Jenkins outage', () => {
    it('is shown as one, with the status as last known, and not as a result', async () => {
      show(
        deployment({
          execution_status: 'RUNNING',
          external_execution_id: '7',
          last_poll_error: 'provider_unreachable',
          last_polled_at: '2026-10-27T09:40:00Z',
        }),
      );
      renderPage();
      const notice = await screen.findByTestId('poll-error');
      expect(notice).toHaveTextContent('Jenkins could not be reached.');
      expect(notice).toHaveTextContent(/as last known, not current/i);
      expect(screen.getByRole('status')).toHaveTextContent('Running in Jenkins');
      expect(screen.queryByTestId('failure')).not.toBeInTheDocument();
    });

    it('is not shown once the deployment is finished', async () => {
      show({ ...SUCCESS, last_poll_error: 'provider_unreachable' });
      renderPage();
      await screen.findByTestId('health-statement');
      expect(screen.queryByTestId('poll-error')).not.toBeInTheDocument();
    });

    it('shows how long ago Jenkins last answered', async () => {
      show(deployment({ execution_status: 'QUEUED', last_polled_at: '2026-10-27T09:40:00Z' }));
      renderPage();
      await screen.findByRole('heading', { level: 1 });
      expect(screen.getByText('Last asked Jenkins')).toBeInTheDocument();
      expect(screen.getByText(/2026.*09:40/)).toBeInTheDocument();
    });
  });

  describe('progress', () => {
    it('says where each status stands', async () => {
      show(deployment({ execution_status: 'QUEUED' }));
      renderPage();
      await screen.findByRole('heading', { level: 1 });
      expect(screen.getByRole('status')).toHaveTextContent(
        'Sent to Jenkins — waiting for a build to start',
      );
    });

    it('polls at the interval the backend names and stops when the deployment is terminal', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const fetchMock = vi
        .fn()
        .mockImplementationOnce(() => reply(200, deployment({ poll_after_seconds: 5 })))
        .mockImplementationOnce(() =>
          reply(200, deployment({ execution_status: 'RUNNING', poll_after_seconds: 5 })),
        )
        .mockImplementation(() => reply(200, SUCCESS));
      vi.stubGlobal('fetch', fetchMock);
      renderPage();

      expect(await screen.findByText(/requested — waiting for a worker/i)).toBeInTheDocument();
      expect(screen.getByText(/checking again every 5 seconds/i)).toBeInTheDocument();
      expect(getCalls(fetchMock)).toHaveLength(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100);
      });
      await waitFor(() => {
        expect(screen.getByRole('status')).toHaveTextContent('Running in Jenkins');
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100);
      });
      await waitFor(() => {
        expect(screen.getByRole('status')).toHaveTextContent('Succeeded');
      });
      const settled = getCalls(fetchMock).length;
      expect(settled).toBe(3);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000); // nothing more is asked of a finished one
      });
      expect(getCalls(fetchMock)).toHaveLength(settled);
    });
  });

  describe('cancel', () => {
    it('is offered while requested and posts to the cancel endpoint', async () => {
      const fetchMock = vi
        .fn()
        .mockImplementation((_url: string, init?: RequestInit) =>
          init?.method === 'POST'
            ? reply(200, deployment({ execution_status: 'CANCELLED', poll_after_seconds: null }))
            : reply(200, deployment()),
        );
      vi.stubGlobal('fetch', fetchMock);
      renderPage();
      await userEvent.click(await screen.findByRole('button', { name: /cancel this deployment/i }));
      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/deployments/d-1/cancel')),
        ).toBe(true);
      });
    });

    it.each(['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'AWAITING_APPROVAL'])(
      'is not offered for %s (DCP does not abort a Jenkins build)',
      async (status) => {
        show(
          deployment({
            execution_status: status,
            poll_after_seconds: ['QUEUED', 'RUNNING', 'AWAITING_APPROVAL'].includes(status)
              ? 5
              : null,
            failure_reason: status === 'FAILED' ? 'PIPELINE_FAILED' : null,
          }),
        );
        renderPage();
        await screen.findByRole('heading', { level: 1 });
        expect(
          screen.queryByRole('button', { name: /cancel this deployment/i }),
        ).not.toBeInTheDocument();
      },
    );

    it('shows a 409 as "a worker got there first" and does not retry', async () => {
      const fetchMock = vi
        .fn()
        .mockImplementation((_url: string, init?: RequestInit) =>
          init?.method === 'POST'
            ? reply(409, { error: { code: 'invalid_state_transition', message: 'm' } })
            : reply(200, deployment()),
        );
      vi.stubGlobal('fetch', fetchMock);
      renderPage();
      await userEvent.click(await screen.findByRole('button', { name: /cancel this deployment/i }));
      expect(await screen.findByText(/already picked this up/i)).toBeInTheDocument();
      expect(
        fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === 'POST'),
      ).toHaveLength(1);
    });

    it('is disabled in read-only mode', async () => {
      show(deployment());
      renderPage(true);
      expect(await screen.findByRole('button', { name: /cancel this deployment/i })).toBeDisabled();
    });

    it('says a cancelled deployment ran no build to completion', async () => {
      show(deployment({ execution_status: 'CANCELLED', poll_after_seconds: null }));
      renderPage();
      expect(
        await screen.findByText(/no build of this deployment ran to completion/i),
      ).toBeVisible();
    });
  });

  describe('what is not there yet, and what must never be', () => {
    it('says logs and rollback are not available rather than leaving them blank', async () => {
      show(deployment());
      renderPage();
      await screen.findByRole('heading', { level: 1 });
      expect(screen.getByText('Build logs are not available yet.')).toBeInTheDocument();
      expect(screen.getByText('Rollback is not available yet.')).toBeInTheDocument();
    });

    it('shows no Jenkins URL, however the deployment looks', async () => {
      show({ ...SUCCESS, sanitized_result: { pipeline_result: 'SUCCESS' } });
      const { container } = renderPage();
      await screen.findByTestId('health-statement');
      expect(container.textContent).not.toMatch(/https?:\/\//);
      expect(container.querySelector('a[href^="http"]')).toBeNull();
    });

    it('shows the job path the target registered, as text', async () => {
      show(deployment());
      renderPage();
      expect(await screen.findByText('billing/deploy')).toBeInTheDocument();
    });
  });

  it('says plainly when the deployment is not there or not visible', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(404, {})),
    );
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i);
  });

  it('has no axe violations', async () => {
    show(SUCCESS);
    const { container } = renderPage();
    await screen.findByTestId('health-statement');
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });

  it('has no axe violations while failing, either', async () => {
    show(failed('TRIGGER_OUTCOME_UNKNOWN'));
    const { container } = renderPage();
    await screen.findByTestId('failure');
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });
});
