import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ApprovalPolicies } from '@/pages/approvals/ApprovalPolicies';

/**
 * §25.11 – §25.14 from the Administrator's side: what a policy says is shown plainly, the two
 * edits that weaken a gate are called out, what the workflow does not implement (more than one
 * approval, a change of subject) cannot be entered, a policy in use cannot be deleted and the
 * backend's refusal is shown in words.
 */

function policy(over: Record<string, unknown> = {}) {
  return {
    policy_id: 'p1',
    name: 'production-service-operations',
    environment: 'production',
    applies_to: 'SERVICE_OPERATION',
    required_approvals: 1,
    allow_self_approval: false,
    required_role: 'APPROVER',
    approval_window_seconds: 3600,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  };
}

function state(readOnly = false): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'ADMINISTRATOR', scope: {} }],
    permissions: ['admin:policies'],
    session: { expires_at: '2026-10-21T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({
    ok: status < 400,
    status,
    json: () => (body === undefined ? Promise.reject(new Error('no body')) : Promise.resolve(body)),
  });

function mockApi(write: { status: number; body?: unknown } = { status: 200, body: policy() }) {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') return reply(200, { items: [policy()] });
    return reply(write.status, write.body);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const writes = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter((call) => ((call[1] as RequestInit | undefined)?.method ?? 'GET') !== 'GET')
    .map((call) => {
      const init = call[1] as RequestInit;
      return {
        method: init.method,
        url: String(call[0]),
        body: init.body === undefined ? undefined : (JSON.parse(init.body as string) as unknown),
      };
    });

function renderPage(readOnly = false) {
  return render(
    <SessionContext.Provider
      value={{
        state: state(readOnly),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <ApprovalPolicies />
    </SessionContext.Provider>,
  );
}

describe('ApprovalPolicies', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows what each policy says in words', async () => {
    mockApi();
    renderPage();
    const row = within(await screen.findByRole('table')).getAllByRole('row')[1];
    expect(row).toHaveTextContent('production-service-operations');
    expect(row).toHaveTextContent('Service operations');
    expect(row).toHaveTextContent('production');
    expect(row).toHaveTextContent('APPROVER');
    expect(row).toHaveTextContent('60 min');
    expect(row).toHaveTextContent('Not allowed');
  });

  it('creates a policy with one approval and the values entered', async () => {
    const fetchMock = mockApi({ status: 201, body: policy({ name: 'staging-restarts' }) });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New policy' }));
    const form = screen.getByRole('form', { name: 'New policy' });
    await userEvent.type(within(form).getByLabelText('Name'), 'staging-restarts');
    await userEvent.selectOptions(
      within(form).getByLabelText(/applies in environment/i),
      'staging',
    );
    const minutes = within(form).getByLabelText(/minutes a request may wait/i);
    await userEvent.clear(minutes);
    await userEvent.type(minutes, '15');
    await userEvent.click(within(form).getByRole('button', { name: 'Create policy' }));

    await waitFor(() => {
      expect(writes(fetchMock)).toHaveLength(1);
    });
    expect(writes(fetchMock)[0]).toEqual({
      method: 'POST',
      url: expect.stringContaining('/admin/approval-policies') as string,
      body: {
        name: 'staging-restarts',
        environment: 'staging',
        applies_to: 'SERVICE_OPERATION',
        required_approvals: 1,
        allow_self_approval: false,
        required_role: 'APPROVER',
        approval_window_seconds: 900,
      },
    });
  });

  it('says plainly what turning on self-approval does, before it is saved', async () => {
    mockApi();
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const form = screen.getByRole('form', { name: 'Edit policy' });
    expect(form).not.toHaveTextContent(/removes the separation/i);
    await userEvent.click(within(form).getByRole('checkbox'));
    expect(form).toHaveTextContent(/removes the separation between the person who asks/i);
    expect(form).toHaveTextContent(/recorded in the audit trail/i);
  });

  it('edits only what the editor allows: the subject is fixed and there is no approval count', async () => {
    const fetchMock = mockApi();
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const form = screen.getByRole('form', { name: 'Edit policy' });
    expect(within(form).getByLabelText(/applies to/i)).toBeDisabled();
    expect(within(form).queryByLabelText(/approvals required/i)).not.toBeInTheDocument();
    expect(form).toHaveTextContent(/one approval is required/i);
    await userEvent.click(within(form).getByRole('checkbox'));
    await userEvent.click(within(form).getByRole('button', { name: 'Save changes' }));

    await waitFor(() => {
      expect(writes(fetchMock)).toHaveLength(1);
    });
    const [sent] = writes(fetchMock);
    expect(sent?.method).toBe('PATCH');
    expect(sent?.url).toContain('/admin/approval-policies/p1');
    expect(Object.keys(sent?.body as object).sort()).toEqual([
      'allow_self_approval',
      'approval_window_seconds',
      'environment',
      'name',
      'required_role',
    ]);
    expect((sent?.body as { allow_self_approval: boolean }).allow_self_approval).toBe(true);
  });

  it('refuses a window outside 1 – 1440 minutes before sending anything', async () => {
    const fetchMock = mockApi();
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const form = screen.getByRole('form', { name: 'Edit policy' });
    const minutes = within(form).getByLabelText(/minutes a request may wait/i);
    await userEvent.clear(minutes);
    await userEvent.type(minutes, '5000');
    await userEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/between 1 and 1440 minutes/i);
    expect(writes(fetchMock)).toEqual([]);
  });

  it('shows the backend refusing a duplicate name in its own words', async () => {
    mockApi({
      status: 409,
      body: { error: { code: 'duplicate_resource', message: 'A policy with that name exists.' } },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/policy with that name exists/i);
  });

  it('says a policy that is still in use cannot be deleted', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const fetchMock = mockApi({
      status: 409,
      body: { error: { code: 'conflict', message: 'x', details: { reason: 'policy_in_use' } } },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/still in use.*cannot be deleted/i);
    expect(writes(fetchMock)[0]?.method).toBe('DELETE');
  });

  it('deletes nothing when the confirmation is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const fetchMock = mockApi();
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(writes(fetchMock)).toEqual([]);
  });

  it('disables every write control in read-only mode', async () => {
    mockApi();
    renderPage(true);
    await screen.findByRole('table');
    expect(screen.getByRole('button', { name: 'New policy' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
  });

  it('shows a 403 as what it is', async () => {
    mockApi({ status: 403 });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/do not have permission/i);
  });

  it('has no axe violations with the form open', async () => {
    mockApi();
    const { container } = renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New policy' }));
    await screen.findByRole('form', { name: 'New policy' });
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });
});
