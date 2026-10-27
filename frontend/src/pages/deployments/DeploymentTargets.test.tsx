import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { DeploymentTargets } from '@/pages/deployments/DeploymentTargets';
import source from '@/pages/deployments/DeploymentTargets.tsx?raw';

/**
 * §21.8 – §21.12 from the page's side.
 *
 * What the page owes the reader: a registered pipeline is not a deployable one (Jenkins not
 * configured is said above the list); "none" says it may hide what the caller cannot see; a target
 * that needs approval says so without naming the policy. What it owes an administrator: it never
 * asks for, and cannot send, a Jenkins URL, a node label, a credential or a command; a pasted URL
 * never leaves the form; and every refusal from the backend is shown as what it is.
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
    updated_at: '2026-10-23T08:00:00Z',
    ...over,
  };
}

function detail(over: Record<string, unknown> = {}) {
  return {
    ...target(),
    approval_policy_id: null,
    parameter_template: { fixed: { TARGET: 'staging' }, slots: { GIT_REF: 'ref' } },
    health_check_config: {},
    rollback_pipeline_reference: null,
    created_at: '2026-10-23T07:00:00Z',
    ...over,
  };
}

function listing(items: unknown[], over: Record<string, unknown> = {}) {
  return {
    items,
    page: { limit: 50, next_cursor: null, has_more: false, total: null },
    jenkins: { status: 'configured', demo_mode: false },
    ...over,
  };
}

const POLICIES = {
  items: [
    {
      policy_id: 'p-prod',
      name: 'production-deployments',
      applies_to: 'DEPLOYMENT',
      environment: 'production',
    },
    {
      policy_id: 'p-ops',
      name: 'production-operations',
      applies_to: 'SERVICE_OPERATION',
      environment: 'production',
    },
    {
      policy_id: 'p-stg',
      name: 'staging-deployments',
      applies_to: 'DEPLOYMENT',
      environment: 'staging',
    },
  ],
};
const APPLICATIONS = {
  items: [
    { id: 'a1', name: 'Payments' },
    { id: 'a2', name: 'Search' },
  ],
  page: { limit: 200, next_cursor: null, has_more: false, total: null },
};

const session = (permissions: string[], readOnly = false): Session => ({
  user: { id: 'u1', email: 'admin@example.invalid', display_name: 'Admin', status: 'ACTIVE' },
  roles: [{ name: 'ADMINISTRATOR', scope: {} }],
  permissions,
  session: { expires_at: '2026-10-24T00:00:00Z', idle_timeout_seconds: 1800 },
  demo_mode: false,
  read_only_mode: readOnly,
});

const authenticated = (permissions: string[], readOnly = false): SessionState => ({
  kind: 'authenticated',
  session: session(permissions, readOnly),
});

const READER = ['deployment:read'];
const ADMIN = ['deployment:read', 'admin:integrations', 'admin:policies'];

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

interface Write {
  status: number;
  body?: unknown;
}

function mockApi(read: unknown = listing([target()]), write: Write = { status: 200 }) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET')
      return write.status === 204 ? reply(204, null) : reply(write.status, write.body ?? detail());
    if (url.startsWith('/api/v1/applications')) return reply(200, APPLICATIONS);
    if (url.startsWith('/api/v1/admin/approval-policies')) return reply(200, POLICIES);
    if (/^\/api\/v1\/deployment-targets\/[^?]+/.test(url)) return reply(200, detail());
    return reply(200, read);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const writes = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter((call) => (call[1] as RequestInit | undefined)?.method !== undefined)
    .map((call) => {
      const init = call[1] as RequestInit & { body?: string; headers?: Record<string, string> };
      return {
        method: init.method,
        url: String(call[0]),
        headers: Object.fromEntries(new Headers(init.headers)),
        body: init.body === undefined ? null : (JSON.parse(init.body) as Record<string, unknown>),
      };
    });

function renderPage(state: SessionState = authenticated(ADMIN)) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter>
        <DeploymentTargets />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

describe('DeploymentTargets', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows a target by its pipeline, refs, approval, rollback and status', async () => {
    mockApi();
    renderPage(authenticated(READER));
    const row = (await screen.findByText('team/payments-deploy')).closest('tr') as HTMLElement;
    expect(within(row).getByRole('link', { name: 'Payments' })).toHaveAttribute(
      'href',
      '/applications/a1',
    );
    expect(within(row).getByText('staging')).toBeInTheDocument();
    expect(within(row).getByText('^main$')).toBeInTheDocument();
    expect(within(row).getByText('Not required')).toBeInTheDocument();
    expect(within(row).getByText('Not supported')).toBeInTheDocument();
    expect(within(row).getByText('Active')).toBeInTheDocument();
  });

  it('says a target needs approval without naming the policy', async () => {
    mockApi(listing([target({ requires_approval: true, rollback_supported: true })]));
    renderPage(authenticated(READER));
    const row = (await screen.findByText('team/payments-deploy')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Required')).toBeInTheDocument();
    expect(within(row).getByText('Supported')).toBeInTheDocument();
  });

  it('says nothing can be deployed when no ref pattern is registered', async () => {
    mockApi(listing([target({ allowed_ref_patterns: [] })]));
    renderPage(authenticated(READER));
    expect(await screen.findByText(/nothing can be deployed/)).toBeInTheDocument();
  });

  it('says Jenkins is not configured, above the list, instead of leaving targets to read as ready', async () => {
    mockApi(listing([target()], { jenkins: { status: 'not_configured', demo_mode: false } }));
    renderPage(authenticated(READER));
    expect(await screen.findByText(/Jenkins is not configured on this deployment/)).toBeVisible();
    expect(screen.getByRole('note')).toHaveTextContent(/does not deploy anything/);
  });

  it('says Jenkins is a mock in demonstration mode, and says nothing when it is real', async () => {
    mockApi(listing([target()], { jenkins: { status: 'configured', demo_mode: true } }));
    const { unmount } = renderPage(authenticated(READER));
    expect(await screen.findByText(/Jenkins here is a mock/)).toBeVisible();
    unmount();
    mockApi();
    renderPage(authenticated(READER));
    await screen.findByText('team/payments-deploy');
    expect(screen.queryByText(/Jenkins here is a mock|Jenkins is not configured/)).toBeNull();
  });

  it('says an empty list may hide targets outside the reader’s scope', async () => {
    mockApi(listing([]));
    renderPage(authenticated(READER));
    expect(await screen.findByText(/or none that you may see/)).toBeInTheDocument();
  });

  it('shows a load failure as one', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(reply(503, {})));
    renderPage(authenticated(READER));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('shows a name containing markup as text and creates no element', async () => {
    const markup = '<img src=x onerror=alert(1)>';
    mockApi(listing([target({ application_name: markup, pipeline_reference: 'team/x' })]));
    const { container } = renderPage(authenticated(READER));
    expect(await screen.findByText(markup)).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(source).not.toMatch(/dangerouslySetInnerHTML/);
  });

  it('offers a reader no way to change anything', async () => {
    mockApi();
    renderPage(authenticated(READER));
    await screen.findByText('team/payments-deploy');
    expect(screen.queryByRole('button', { name: 'Register target' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Edit/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Retire/ })).toBeNull();
  });

  it('offers no way to deploy to someone who does not hold deployment:create', async () => {
    mockApi();
    renderPage(); // an administrator: register, edit, retire -- and no deployment:create
    await screen.findByText('team/payments-deploy');
    expect(screen.queryByRole('button', { name: /deploy/i })).toBeNull();
  });

  it('refuses a pasted URL before anything is sent', async () => {
    const fetchMock = mockApi(listing([]));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Register target' }));
    await userEvent.selectOptions(await screen.findByLabelText('Application'), 'a1');
    await userEvent.type(
      screen.getByLabelText(/Jenkins job path/),
      'https://jenkins.example.com/job/x',
    );
    await userEvent.type(screen.getByLabelText('Filled parameter 1 name'), 'GIT_REF');
    await userEvent.click(screen.getByRole('button', { name: 'Register target' }));
    expect(await screen.findByText(/That looks like a URL/)).toBeInTheDocument();
    expect(writes(fetchMock)).toEqual([]);
  });

  it('has no field for a Jenkins URL, a node, a credential or a command', async () => {
    mockApi(listing([]));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Register target' }));
    const form = await screen.findByRole('form', { name: 'New deployment target' });
    const labels = Array.from(form.querySelectorAll('input,select,textarea')).map(
      (element) =>
        `${element.getAttribute('aria-label') ?? ''} ${element.closest('label')?.textContent ?? ''}`,
    );
    for (const label of labels) {
      expect(label).not.toMatch(/\b(url|node|credential|token|password|command|script)\b/i);
    }
  });

  it('registers a target from a job path, patterns and a template, and sends nothing else', async () => {
    const fetchMock = mockApi(listing([]), { status: 201, body: detail() });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Register target' }));
    await userEvent.selectOptions(await screen.findByLabelText('Application'), 'a1');
    await userEvent.type(screen.getByLabelText(/Jenkins job path/), '  team/payments-deploy ');
    await userEvent.type(screen.getByLabelText(/Allowed branches/), '^main$\n\n  ^release/.+$ ');
    await userEvent.type(screen.getByLabelText('Filled parameter 1 name'), 'GIT_REF');
    await userEvent.click(screen.getByRole('button', { name: 'Add fixed parameter' }));
    await userEvent.type(screen.getByLabelText('Fixed parameter 1 name'), 'TARGET');
    await userEvent.type(screen.getByLabelText('Fixed parameter 1 value'), 'staging');
    await userEvent.click(screen.getByRole('button', { name: 'Register target' }));

    await waitFor(() => {
      expect(writes(fetchMock)).toHaveLength(1);
    });
    const [sent] = writes(fetchMock);
    expect(sent?.method).toBe('POST');
    expect(sent?.url).toBe('/api/v1/deployment-targets');
    expect(sent?.headers).toHaveProperty('idempotency-key');
    expect(sent?.body).toEqual({
      application_id: 'a1',
      environment: 'staging',
      pipeline_reference: 'team/payments-deploy',
      parameter_template: { fixed: { TARGET: 'staging' }, slots: { GIT_REF: 'ref' } },
      allowed_ref_patterns: ['^main$', '^release/.+$'],
      approval_policy_id: null,
      rollback_supported: false,
      rollback_pipeline_reference: null,
    });
    await waitFor(() => {
      expect(screen.queryByRole('form', { name: 'New deployment target' })).toBeNull();
    });
  });

  it('offers only the approval policies that fit the environment, and says production needs one', async () => {
    mockApi(listing([]));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Register target' }));
    const policy = await screen.findByLabelText('Approval policy');
    await waitFor(() => {
      expect(
        within(policy).getByRole('option', { name: 'staging-deployments' }),
      ).toBeInTheDocument();
    });
    expect(within(policy).queryByRole('option', { name: 'production-deployments' })).toBeNull();
    expect(within(policy).queryByRole('option', { name: 'production-operations' })).toBeNull();

    await userEvent.selectOptions(screen.getByLabelText('Environment'), 'production');
    expect(
      within(policy).getByRole('option', { name: 'production-deployments' }),
    ).toBeInTheDocument();
    expect(within(policy).queryByRole('option', { name: 'production-operations' })).toBeNull();
    expect(
      screen.getByText(/A production target must have an approval policy/),
    ).toBeInTheDocument();
  });

  it('shows a field problem from the backend next to the field', async () => {
    mockApi(listing([]), {
      status: 422,
      body: {
        error: {
          code: 'validation_failed',
          message: 'x',
          details: {
            fields: [
              {
                loc: ['body', 'pipeline_reference'],
                code: 'value_error',
                message: 'must be a job path',
              },
            ],
          },
        },
      },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Register target' }));
    await userEvent.selectOptions(await screen.findByLabelText('Application'), 'a1');
    await userEvent.type(screen.getByLabelText(/Jenkins job path/), 'a b');
    await userEvent.click(screen.getByRole('button', { name: 'Register target' }));
    expect(await screen.findByText('must be a job path')).toBeInTheDocument();
  });

  it('edits through the full detail, sends If-Unmodified-Since, and never sends the fixed identity', async () => {
    const fetchMock = mockApi(listing([target()]), { status: 200, body: detail() });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Payments staging' }));
    const form = await screen.findByRole('form', { name: 'Edit deployment target' });
    expect(within(form).getByLabelText(/^Application/)).toBeDisabled();
    expect(within(form).getByLabelText(/^Environment/)).toBeDisabled();
    expect(within(form).getByLabelText('Fixed parameter 1 name')).toHaveValue('TARGET');
    await userEvent.clear(within(form).getByLabelText(/Allowed branches/));
    await userEvent.type(within(form).getByLabelText(/Allowed branches/), '^main$');
    await userEvent.click(within(form).getByRole('button', { name: 'Save changes' }));

    await waitFor(() => {
      expect(writes(fetchMock)).toHaveLength(1);
    });
    const [sent] = writes(fetchMock);
    expect(sent?.method).toBe('PATCH');
    expect(sent?.url).toBe('/api/v1/deployment-targets/t1');
    expect(sent?.headers).toHaveProperty('if-unmodified-since', 'Fri, 23 Oct 2026 08:00:00 GMT');
    expect(sent?.body).not.toHaveProperty('application_id');
    expect(sent?.body).not.toHaveProperty('environment');
    expect(sent?.body).not.toHaveProperty('health_check_config');
    expect(sent?.body?.allowed_ref_patterns).toEqual(['^main$']);
  });

  it('disables and enables a target by status alone', async () => {
    const fetchMock = mockApi(listing([target()]));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Disable Payments staging' }));
    await waitFor(() => {
      expect(writes(fetchMock)).toHaveLength(1);
    });
    expect(writes(fetchMock)[0]?.body).toEqual({ status: 'DISABLED' });
  });

  it('retires only after a confirmation', async () => {
    const fetchMock = mockApi(listing([target()]), { status: 204 });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Retire Payments staging' }));
    expect(writes(fetchMock)).toEqual([]);
    await userEvent.click(screen.getByRole('button', { name: 'Keep' }));
    expect(writes(fetchMock)).toEqual([]);
    await userEvent.click(screen.getByRole('button', { name: 'Retire Payments staging' }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm retire' }));
    await waitFor(() => {
      expect(writes(fetchMock)).toHaveLength(1);
    });
    expect(writes(fetchMock)[0]).toMatchObject({
      method: 'DELETE',
      url: '/api/v1/deployment-targets/t1',
    });
  });

  it.each([
    [409, /conflicts with the current state/],
    [403, /do not have permission/],
    [423, /read-only mode/],
    [404, /no longer exists/],
  ])('shows a %i from the backend as what it is', async (status, words) => {
    mockApi(listing([target()]), { status, body: { error: { code: 'x', message: 'x' } } });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Disable Payments staging' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(words);
  });

  it('disables every control in read-only mode', async () => {
    mockApi();
    renderPage(authenticated(ADMIN, true));
    await screen.findByText('team/payments-deploy');
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Edit Payments staging' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Disable Payments staging' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Retire Payments staging' })).toBeDisabled();
  });

  it('pages without a total', async () => {
    mockApi(
      listing([target()], {
        page: { limit: 1, next_cursor: 'cursor-2', has_more: true, total: null },
      }),
    );
    renderPage(authenticated(READER));
    await screen.findByText('team/payments-deploy');
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
    expect(screen.getByText('Page 1')).toBeInTheDocument();
  });

  it('has no accessibility violations', async () => {
    mockApi();
    const { container } = renderPage();
    await screen.findByText('team/payments-deploy');
    const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
