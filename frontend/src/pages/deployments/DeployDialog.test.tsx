import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { DeploymentTargets } from '@/pages/deployments/DeploymentTargets';
import source from '@/pages/deployments/DeployDialog.tsx?raw';

/**
 * §21.2 from the dialog's side. What it owes the person at it: the exact target restated before
 * anything is sent, a production target visibly different, every refusal said in words and the
 * dialog kept open, one idempotency key per opening, and a request that is four values and nothing
 * a Jenkins could be pointed with. What the page owes everyone: the action is disabled, with the
 * reason, wherever a deployment cannot run.
 */

function target(over: Record<string, unknown> = {}) {
  return {
    id: 't1',
    application_id: 'a1',
    application_name: 'Payments',
    environment: 'staging',
    pipeline_reference: 'team/payments-deploy',
    allowed_ref_patterns: ['^main$', '^release/.+$'],
    requires_approval: false,
    rollback_supported: false,
    status: 'ACTIVE',
    updated_at: '2026-10-27T08:00:00Z',
    ...over,
  };
}

function listing(items: unknown[], jenkins = { status: 'configured', demo_mode: false }) {
  return {
    items,
    page: { limit: 50, next_cursor: null, has_more: false, total: null },
    jenkins,
  };
}

const ACCEPTED = {
  deployment_id: 'd-9',
  execution_status: 'REQUESTED',
  approval_status: 'NOT_REQUIRED',
  target: {},
  approval: null,
  deadline_at: '2026-10-27T10:00:00Z',
  correlation_id: 'c',
  poll_after_seconds: 5,
  idempotent_replay: false,
};

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

function authenticated(permissions: string[], readOnly = false): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex Kim', status: 'ACTIVE' },
    roles: [{ name: 'DEVOPS_ENGINEER', scope: {} }],
    permissions,
    session: { expires_at: '2026-10-28T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
  return { kind: 'authenticated', session };
}

const DEPLOYER = ['deployment:read', 'deployment:create'];

/** `POST /deployments` answers `post`; everything else is the list. */
function mockApi(read: unknown, post: () => Promise<unknown> = () => reply(202, ACCEPTED)) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if ((init?.method ?? 'GET') === 'POST' && url === '/api/v1/deployments') return post();
    return reply(200, read);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const posts = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter(
      (call) =>
        (call[1] as RequestInit | undefined)?.method === 'POST' &&
        call[0] === '/api/v1/deployments',
    )
    .map((call) => {
      const init = call[1] as RequestInit & { body: string };
      return {
        headers: Object.fromEntries(new Headers(init.headers)),
        body: JSON.parse(init.body) as Record<string, unknown>,
      };
    });

function renderPage(state: SessionState = authenticated(DEPLOYER)) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter initialEntries={['/deployment-targets']}>
        <Routes>
          <Route path="/deployment-targets" element={<DeploymentTargets />} />
          <Route path="/deployments/:id" element={<p>the deployment page</p>} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

async function openDialog(name = 'Deploy Payments staging') {
  await userEvent.click(await screen.findByRole('button', { name }));
  return screen.findByRole('dialog');
}

describe('the Deploy action', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('is offered to a holder of deployment:create, on active targets only', async () => {
    mockApi(
      listing([
        target(),
        target({ id: 't2', environment: 'development', status: 'DISABLED' }),
        target({ id: 't3', environment: 'other' }),
      ]),
    );
    renderPage();
    expect(await screen.findByRole('button', { name: 'Deploy Payments staging' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Deploy Payments other' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Deploy Payments development' })).toBeNull();
  });

  it('is not offered to a reader', async () => {
    mockApi(listing([target()]));
    renderPage(authenticated(['deployment:read']));
    await screen.findByText('team/payments-deploy');
    expect(screen.queryByRole('button', { name: /^Deploy/ })).toBeNull();
  });

  it('is disabled, with the reason, where Jenkins is not configured', async () => {
    mockApi(listing([target()], { status: 'not_configured', demo_mode: false }));
    renderPage();
    const button = await screen.findByRole('button', { name: 'Deploy Payments staging' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', expect.stringMatching(/Jenkins is not configured/));
  });

  it('is disabled in demonstration mode, where backing it with a mock would be a lie', async () => {
    mockApi(listing([target()], { status: 'configured', demo_mode: true }));
    renderPage();
    expect(await screen.findByRole('button', { name: 'Deploy Payments staging' })).toBeDisabled();
  });

  it('is disabled in read-only mode, with the reason', async () => {
    mockApi(listing([target()]));
    renderPage(authenticated(DEPLOYER, true));
    const button = await screen.findByRole('button', { name: 'Deploy Payments staging' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', expect.stringMatching(/read-only/));
  });
});

describe('the confirmation dialog', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('restates the exact target, who is asking, and what the target allows', async () => {
    mockApi(listing([target()]));
    renderPage();
    const dialog = await openDialog();
    expect(within(dialog).getByRole('heading')).toHaveTextContent(
      'Confirm: deploy Payments to staging',
    );
    for (const text of ['Payments', 'staging', 'team/payments-deploy', 'Alex Kim']) {
      expect(within(dialog).getAllByText(text).length).toBeGreaterThan(0);
    }
    expect(within(dialog).getByText('^main$')).toBeInTheDocument();
    expect(within(dialog).getByText('^release/.+$')).toBeInTheDocument();
    expect(within(dialog).queryByTestId('production-warning')).toBeNull();
  });

  it('is visibly different for production', async () => {
    mockApi(listing([target({ environment: 'production', requires_approval: true })]));
    renderPage();
    const dialog = await openDialog('Deploy Payments production');
    expect(within(dialog).getByTestId('production-warning')).toHaveTextContent(/PRODUCTION/);
  });

  it('says plainly that an approval is needed and cannot be given yet', async () => {
    mockApi(listing([target({ requires_approval: true })]));
    renderPage();
    const dialog = await openDialog();
    expect(within(dialog).getByTestId('approval-notice')).toHaveTextContent(
      /approval workflow is not available yet, so this request will be refused/,
    );
  });

  it('says nothing about approvals for a target that needs none', async () => {
    mockApi(listing([target()]));
    renderPage();
    const dialog = await openDialog();
    expect(within(dialog).queryByTestId('approval-notice')).toBeNull();
  });

  it('has two fields only: the ref and a reason, and none for a url, a job or a parameter', async () => {
    mockApi(listing([target()]));
    renderPage();
    const dialog = await openDialog();
    const fields = within(dialog).getAllByRole('textbox');
    expect(fields).toHaveLength(2);
    expect(within(dialog).getByLabelText('Branch, tag or commit to deploy')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Reason (optional)')).toBeInTheDocument();
    expect(source).not.toMatch(/jenkins_url|job_name|build_param|node_label|parameter_template/);
  });

  it('closes on Cancel and on Escape, and a fresh opening is a fresh dialog', async () => {
    mockApi(listing([target()]));
    renderPage();
    await openDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await openDialog();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('requesting', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends four values and a key, and goes to the deployment', async () => {
    const fetchMock = mockApi(listing([target()]));
    renderPage();
    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText('Branch, tag or commit to deploy'), 'main');
    await userEvent.type(within(dialog).getByLabelText('Reason (optional)'), 'CR-7');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Deploy Payments' }));

    expect(await screen.findByText('the deployment page')).toBeInTheDocument();
    const [sent] = posts(fetchMock);
    expect(sent?.body).toEqual({
      application_id: 'a1',
      environment: 'staging',
      ref: 'main',
      reason: 'CR-7',
    });
    expect(sent?.headers['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/);
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it('trims the ref and leaves an empty reason out', async () => {
    const fetchMock = mockApi(listing([target()]));
    renderPage();
    const dialog = await openDialog();
    await userEvent.type(
      within(dialog).getByLabelText('Branch, tag or commit to deploy'),
      '  release/1.4  ',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Deploy Payments' }));
    await screen.findByText('the deployment page');
    expect(posts(fetchMock)[0]?.body).toEqual({
      application_id: 'a1',
      environment: 'staging',
      ref: 'release/1.4',
    });
  });

  it('will not send an empty ref', async () => {
    const fetchMock = mockApi(listing([target()]));
    renderPage();
    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Deploy Payments' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      /Enter the branch, tag or commit/,
    );
    expect(posts(fetchMock)).toEqual([]);
  });

  it('uses one idempotency key for one opening, so a second click is the same intent', async () => {
    const fetchMock = mockApi(listing([target()]), () =>
      reply(503, {
        error: { code: 'not_configured', message: 'm', details: { reason: 'demonstration_mode' } },
      }),
    );
    renderPage();
    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText('Branch, tag or commit to deploy'), 'main');
    const button = within(dialog).getByRole('button', { name: 'Deploy Payments' });
    await userEvent.click(button);
    await within(dialog).findByRole('alert');
    await userEvent.click(button);
    await waitFor(() => {
      expect(posts(fetchMock)).toHaveLength(2);
    });
    const [first, second] = posts(fetchMock);
    expect(first?.headers['idempotency-key']).toBe(second?.headers['idempotency-key']);
  });

  it('uses a new key for a new opening', async () => {
    const fetchMock = mockApi(listing([target()]), () =>
      reply(503, { error: { code: 'not_configured', message: 'm' } }),
    );
    renderPage();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const dialog = await openDialog();
      await userEvent.type(
        within(dialog).getByLabelText('Branch, tag or commit to deploy'),
        'main',
      );
      await userEvent.click(within(dialog).getByRole('button', { name: 'Deploy Payments' }));
      await within(dialog).findByRole('alert');
      await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    }
    const [first, second] = posts(fetchMock);
    expect(first?.headers['idempotency-key']).not.toBe(second?.headers['idempotency-key']);
  });
});

describe('a refusal', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  async function refusedWith(status: number, error: Record<string, unknown>, ref = 'main') {
    mockApi(listing([target()]), () => reply(status, { error: { message: 'raw', ...error } }));
    renderPage();
    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText('Branch, tag or commit to deploy'), ref);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Deploy Payments' }));
    return within(dialog).findByRole('alert');
  }

  it.each([
    [
      503,
      { code: 'not_configured', details: { reason: 'approval_workflow_unavailable' } },
      /approval workflow is not available yet/,
    ],
    [
      503,
      { code: 'not_configured', details: { reason: 'jenkins_not_configured' } },
      /Jenkins is not configured/,
    ],
    [
      503,
      { code: 'not_configured', details: { reason: 'demonstration_mode' } },
      /demonstration mode/,
    ],
    [409, { code: 'operation_in_progress' }, /already pending or running/],
    [429, { code: 'rate_limited' }, /last hour/],
    [422, { code: 'not_registered' }, /No active deployment target is registered/],
  ])('is said in words, with the dialog kept open (%i)', async (status, error, words) => {
    const alert = await refusedWith(status, error);
    expect(alert).toHaveTextContent(words);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('names the refs a target allows when the ref is refused', async () => {
    const alert = await refusedWith(
      422,
      {
        code: 'not_permitted',
        details: { reason: 'ref_not_permitted', allowed_ref_patterns: ['^main$', '^release/.+$'] },
      },
      'develop',
    );
    expect(alert).toHaveTextContent('^main$, ^release/.+$');
  });

  it.each([
    [403, /do not have permission/],
    [404, /outside your access scope/],
    [423, /read-only mode/],
  ])('says what %i means', async (status, words) => {
    const alert = await refusedWith(status, {});
    expect(alert).toHaveTextContent(words);
  });

  it('shows the backend’s own message for a refusal it has no words for, rather than hiding it', async () => {
    const alert = await refusedWith(409, { code: 'something_new', message: 'a new rule applies' });
    expect(alert).toHaveTextContent('a new rule applies');
  });

  it('says it could not reach the backend when it could not', async () => {
    mockApi(listing([target()]), () => Promise.reject(new Error('network down')));
    renderPage();
    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText('Branch, tag or commit to deploy'), 'main');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Deploy Payments' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      /Could not reach the backend: network down/,
    );
  });
});

it('has no accessibility violations with the dialog open', async () => {
  vi.unstubAllGlobals();
  mockApi(listing([target({ requires_approval: true })]));
  const { container } = renderPage();
  await openDialog();
  const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  expect(results.violations).toEqual([]);
});
