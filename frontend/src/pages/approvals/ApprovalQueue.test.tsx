import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ApprovalQueue } from '@/pages/approvals/ApprovalQueue';
import { blockedAsOwn, timeLeft } from '@/pages/approvals/queueFormat';

/**
 * §22 from the approver's side (PRD §3.6, §4.6): the four facts restated at the decision, a
 * reject that needs a reason, **a self-requested row whose control is disabled — as advice, with
 * the backend refusing it again in words** — every refusal said as what it is, time left shown,
 * and one request per click however many clicks.
 */

const NOW = Date.parse('2026-10-20T09:00:00Z');

function item(over: Record<string, unknown> = {}) {
  return {
    approval_id: 'ap-1',
    operation_id: 'op-1',
    operation_type: 'SERVICE_OPERATION',
    requested_by: { id: 'u2', display_name: 'Riley Ops' },
    target: {
      server_id: 's1',
      server_display_name: 'Web 1',
      service_name: 'api.service',
      environment: 'production',
    },
    action_type: 'restart',
    reason: 'CR-4411',
    decision: 'PENDING',
    expires_at: '2026-10-20T09:42:00Z',
    created_at: '2026-10-20T08:42:00Z',
    decided_at: null,
    approved_by: null,
    self_requested: false,
    self_approval_allowed: false,
    ...over,
  };
}

const DETAIL = {
  ...item(),
  policy: {
    policy_id: 'p1',
    required_approvals: 1,
    allow_self_approval: false,
    required_role: 'APPROVER',
    approval_window_seconds: 3600,
  },
  decisions_so_far: [],
  target_state: { state: 'ACTIVE', observed_at: '2026-10-20T08:59:00Z', data_state: 'ok' },
};

function state(readOnly = false): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'APPROVER', scope: {} }],
    permissions: ['operation:approve'],
    session: { expires_at: '2026-10-21T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

interface Api {
  items?: Record<string, unknown>[];
  detail?: Record<string, unknown>;
  decision?: { status: number; body: unknown };
  list?: { status: number; body: unknown };
}

function mockApi(api: Api = {}) {
  const items = api.items ?? [item()];
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = url;
    if (init?.method === 'POST') {
      return reply(api.decision?.status ?? 200, api.decision?.body ?? { operation_id: 'op-1' });
    }
    if (/\/approvals\/ap-/.test(path)) return reply(200, api.detail ?? { ...DETAIL, ...items[0] });
    if (api.list) return reply(api.list.status, api.list.body);
    return reply(200, {
      items,
      page: { limit: 50, next_cursor: null, has_more: false, total: null },
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const decisions = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST')
    .map((call) => ({
      url: String(call[0]),
      body: JSON.parse((call[1] as RequestInit).body as string) as Record<string, unknown>,
    }));

function renderQueue(readOnly = false) {
  return render(
    <SessionContext.Provider
      value={{
        state: state(readOnly),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <MemoryRouter>
        <ApprovalQueue />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

async function review() {
  await userEvent.click(await screen.findByRole('button', { name: 'Review' }));
  return screen.findByRole('region', { name: /restart api\.service on web 1/i });
}

describe('time left', () => {
  it.each([
    ['2026-10-20T09:42:00Z', '42 min left'],
    ['2026-10-20T09:00:30Z', 'less than a minute left'],
    ['2026-10-20T08:59:59Z', 'expired'],
    ['2026-10-20T09:00:00Z', 'expired'],
    ['2026-10-20T13:00:00Z', '4 h left'],
  ])('%s is %s', (expiresAt, words) => {
    expect(timeLeft(expiresAt, NOW)).toBe(words);
  });
});

describe('blockedAsOwn', () => {
  it('is advice: only your own request under a policy that forbids it', () => {
    expect(blockedAsOwn(item({ self_requested: true }) as never)).toBe(true);
    expect(blockedAsOwn(item({ self_requested: true, self_approval_allowed: true }) as never)).toBe(
      false,
    );
    expect(blockedAsOwn(item() as never)).toBe(false);
  });
});

describe('ApprovalQueue', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
  });

  it('lists what is waiting with who, what, where, why and how long is left', async () => {
    mockApi();
    renderQueue();
    const table = await screen.findByRole('table', { name: /approval requests/i });
    const row = within(table).getAllByRole('row')[1];
    expect(row).toHaveTextContent('Riley Ops');
    expect(row).toHaveTextContent('api.service on Web 1');
    expect(row).toHaveTextContent('production');
    expect(row).toHaveTextContent('Restart');
    expect(row).toHaveTextContent('CR-4411');
    expect(row).toHaveTextContent('42 min left');
    expect(row).not.toHaveTextContent(/your request/i);
  });

  it('says so when nothing is waiting, and does not call an error an empty queue', async () => {
    mockApi({ items: [] });
    renderQueue();
    expect(await screen.findByText(/nothing is waiting for your decision/i)).toBeInTheDocument();
  });

  it('shows an unreachable backend as that, not as an empty queue', async () => {
    mockApi({ list: { status: 500, body: { error: { code: 'x', message: 'down' } } } });
    renderQueue();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/nothing is waiting/i)).not.toBeInTheDocument();
  });

  it('restates server, service, environment and action in the decision dialog, production marked', async () => {
    const fetchMock = mockApi();
    renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));

    const dialog = await screen.findByRole('dialog', { name: /approve: restart api\.service/i });
    expect(dialog).toHaveTextContent('Web 1');
    expect(dialog).toHaveTextContent('api.service');
    expect(dialog).toHaveTextContent('production');
    expect(dialog).toHaveTextContent('Restart');
    expect(dialog).toHaveTextContent('Riley Ops');
    expect(screen.getByTestId('production-warning')).toHaveTextContent(/production/i);
    expect(decisions(fetchMock)).toEqual([]); // opening the dialog decides nothing
  });

  it('labels the last observed state as an observation, not a live read', async () => {
    mockApi();
    renderQueue();
    const panel = await review();
    expect(await within(panel).findByText(/last observed state/i)).toBeInTheDocument();
    expect(panel).toHaveTextContent('ACTIVE');
    expect(panel).toHaveTextContent(/last observed by monitoring/i);
    expect(panel).toHaveTextContent(/reads the service again, from the provider/i);
  });

  it('approves with the reason optional and sends exactly that', async () => {
    const fetchMock = mockApi();
    renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await userEvent.type(screen.getByRole('textbox'), 'Checked CR-4411');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));

    expect(await screen.findByRole('status')).toHaveTextContent(/approved: restart api\.service/i);
    expect(decisions(fetchMock)).toEqual([
      {
        url: expect.stringContaining('/operations/op-1/approve') as string,
        body: { reason: 'Checked CR-4411' },
      },
    ]);
  });

  it('approves with no reason by sending an empty body, not an empty reason', async () => {
    const fetchMock = mockApi();
    renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));
    await screen.findByText(/approved: restart/i);
    expect(decisions(fetchMock)[0]?.body).toEqual({});
  });

  it('will not reject without a reason, and sends nothing', async () => {
    const fetchMock = mockApi();
    renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    expect(screen.getByText(/reason \(required\)/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm rejection' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/reason is required to reject/i);
    expect(decisions(fetchMock)).toEqual([]);
  });

  it('rejects with the reason it was given', async () => {
    const fetchMock = mockApi();
    renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    await userEvent.type(screen.getByRole('textbox'), 'Not during the release');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm rejection' }));
    expect(await screen.findByRole('status')).toHaveTextContent(/rejected: restart api\.service/i);
    expect(decisions(fetchMock)).toEqual([
      {
        url: expect.stringContaining('/operations/op-1/reject') as string,
        body: { reason: 'Not during the release' },
      },
    ]);
  });

  it('sends one request however many times the confirm button is clicked', async () => {
    const fetchMock = mockApi();
    renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    const confirm = screen.getByRole('button', { name: 'Confirm approval' });
    await userEvent.dblClick(confirm);
    await userEvent.click(confirm);
    await screen.findByText(/approved: restart/i);
    expect(decisions(fetchMock)).toHaveLength(1);
  });

  describe('a request you made yourself', () => {
    const own = item({ self_requested: true });

    it('is marked in the list, and its controls are disabled with the reason said', async () => {
      const fetchMock = mockApi({ items: [own] });
      renderQueue();
      const row = within(await screen.findByRole('table')).getAllByRole('row')[1];
      expect(row).toHaveTextContent(/your request/i);
      await userEvent.click(screen.getByRole('button', { name: 'Review' }));
      const panel = await screen.findByRole('region', { name: /restart api\.service/i });
      expect(within(panel).getByRole('button', { name: 'Approve' })).toBeDisabled();
      expect(within(panel).getByRole('button', { name: 'Reject' })).toBeDisabled();
      expect(within(panel).getByRole('status')).toHaveTextContent(
        /does not allow you to approve your own/i,
      );
      await userEvent.click(within(panel).getByRole('button', { name: 'Approve' }));
      expect(decisions(fetchMock)).toEqual([]);
    });

    it('is decidable where the policy explicitly allows it', async () => {
      mockApi({ items: [item({ self_requested: true, self_approval_allowed: true })] });
      renderQueue();
      const panel = await review();
      expect(within(panel).getByRole('button', { name: 'Approve' })).toBeEnabled();
      expect(within(panel).getByRole('status')).toHaveTextContent(/allows you to decide it/i);
    });

    it('still shows the backend refusal in words if it is sent anyway (the control is not the control)', async () => {
      // The UI disabled nothing here: the row was not flagged, but the server knows better.
      mockApi({
        decision: {
          status: 403,
          body: {
            error: {
              code: 'forbidden',
              message: 'm',
              details: { reason: 'self_approval_not_permitted' },
            },
          },
        },
      });
      renderQueue();
      await review();
      await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
      await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));
      expect(await screen.findByRole('alert')).toHaveTextContent(
        /you made this request, and the policy does not allow you to approve your own/i,
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument(); // left open
    });
  });

  it.each([
    [
      403,
      { error: { code: 'forbidden', message: 'm', details: { reason: 'approver_role_required' } } },
      /needs a role you do not hold/i,
    ],
    [403, { error: { code: 'forbidden', message: 'm', details: null } }, /do not have permission/i],
    [
      409,
      {
        error: {
          code: 'invalid_state_transition',
          message: 'm',
          details: { reason: 'approval_expired' },
        },
      },
      /approval window for this request has passed/i,
    ],
    [
      409,
      {
        error: {
          code: 'invalid_state_transition',
          message: 'm',
          details: { reason: 'not_awaiting_approval' },
        },
      },
      /someone else decided it, or it was cancelled/i,
    ],
    [404, undefined, /outside the environments you can approve in/i],
    [423, undefined, /read-only mode/i],
  ])('says a %i refusal in words and keeps the dialog open', async (status, body, words) => {
    mockApi({ decision: { status, body } });
    renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(words);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText(/approved: restart/i)).not.toBeInTheDocument();
  });

  it('offers no decision on a request whose window has passed', async () => {
    mockApi({ items: [item({ expires_at: '2026-10-20T08:59:00Z' })] });
    renderQueue();
    const panel = await review();
    expect(within(panel).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(panel).toHaveTextContent(/approval window has passed/i);
  });

  it('disables the controls in read-only mode', async () => {
    mockApi();
    renderQueue(true);
    const panel = await review();
    expect(within(panel).getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(within(panel).getByRole('button', { name: 'Reject' })).toBeDisabled();
  });

  it('asks for the other states the queue can show', async () => {
    const fetchMock = mockApi({
      items: [
        item({
          decision: 'APPROVED',
          decided_at: '2026-10-20T08:50:00Z',
          approved_by: { id: 'u3', display_name: 'Sam' },
        }),
      ],
    });
    renderQueue();
    await screen.findByRole('table');
    await userEvent.selectOptions(screen.getByRole('combobox'), 'APPROVED');
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => String(call[0]).includes('decision=APPROVED')),
      ).toBe(true);
    });
  });

  it('has no axe violations with the decision dialog open', async () => {
    mockApi();
    const { container } = renderQueue();
    await review();
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    await screen.findByRole('dialog');
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });
});
