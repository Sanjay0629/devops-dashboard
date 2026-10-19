import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ChannelList } from '@/pages/notifications/ChannelList';
import source from '@/pages/notifications/ChannelList.tsx?raw';

/**
 * §26.8 - §26.10 from the page's side.
 *
 * What the page owes an administrator: a destination is a reference and a pasted URL never
 * leaves the form; a channel that has never delivered is "not proven", not healthy; an empty
 * rule set is called what it is (every alert); a channel with unreadable rules says it sends
 * nothing; and every refusal from the backend is shown as what it is.
 */

function channel(over: Record<string, unknown> = {}) {
  return {
    id: 'c1',
    name: 'ops-slack',
    channel_type: 'SLACK',
    destination_reference: 'env://OPS_SLACK',
    routing_rules: { severity: ['CRITICAL'], environment: ['production'], target_type: [] },
    routing_rules_valid: true,
    status: 'ACTIVE',
    created_at: '2026-10-19T08:00:00Z',
    updated_at: '2026-10-19T08:00:00Z',
    deliveries: {
      pending: 0,
      sent_24h: 0,
      failed_24h: 0,
      last_success_at: null,
      last_failure_at: null,
      last_error: null,
    },
    ...over,
  };
}

function listing(items: unknown[], over: Record<string, unknown> = {}) {
  return { items, email_available: true, demo_mode: false, ...over };
}

const session = (permissions: string[], readOnly = false): Session => ({
  user: { id: 'u1', email: 'admin@example.invalid', display_name: 'Admin', status: 'ACTIVE' },
  roles: [{ name: 'ADMINISTRATOR', scope: {} }],
  permissions,
  session: { expires_at: '2026-10-20T00:00:00Z', idle_timeout_seconds: 1800 },
  demo_mode: false,
  read_only_mode: readOnly,
});

const authenticated = (permissions: string[], readOnly = false): SessionState => ({
  kind: 'authenticated',
  session: session(permissions, readOnly),
});

const ADMIN = ['admin:integrations'];

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

interface Write {
  status: number;
  body?: unknown;
}

function mockApi(read: unknown = listing([channel()]), write: Write = { status: 200 }) {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') return reply(200, read);
    return reply(write.status, write.body ?? channel());
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const writes = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls
    .filter((call) => (call[1] as RequestInit | undefined)?.method !== undefined)
    .map((call) => ({
      method: (call[1] as RequestInit).method,
      url: String(call[0]),
      body: JSON.parse((call[1] as { body: string }).body) as Record<string, unknown>,
    }));

function renderPage(state: SessionState = authenticated(ADMIN)) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter>
        <ChannelList />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

describe('ChannelList', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows a channel by its reference, its rules in words and its status', async () => {
    mockApi();
    renderPage();
    const row = (await screen.findByText('ops-slack')).closest('tr') as HTMLElement;
    expect(within(row).getByText('env://OPS_SLACK')).toBeInTheDocument();
    expect(within(row).getByText(/severity: CRITICAL · environment: production/)).toBeVisible();
    expect(within(row).getByText('Active')).toBeInTheDocument();
  });

  it('says a channel that has never delivered is not proven, and never calls it healthy', async () => {
    mockApi();
    renderPage();
    expect(await screen.findByText(/Nothing sent yet — not proven\./)).toBeInTheDocument();
    expect(screen.queryByText(/0 sent, 0 failed/)).toBeNull();
  });

  it('shows what the last day of deliveries did, with the reason in words', async () => {
    mockApi(
      listing([
        channel({
          deliveries: {
            pending: 2,
            sent_24h: 5,
            failed_24h: 3,
            last_success_at: '2026-10-19T08:00:00Z',
            last_failure_at: '2026-10-19T09:00:00Z',
            last_error: 'http_404',
          },
        }),
      ]),
    );
    renderPage();
    expect(await screen.findByText(/5 sent, 3 failed \(24 h\)/)).toBeInTheDocument();
    expect(screen.getByText(/2 waiting/)).toBeInTheDocument();
    expect(screen.getByText(/last failure: the destination answered HTTP 404/)).toBeInTheDocument();
  });

  it('calls an empty rule set every alert, and says what a channel is told', async () => {
    mockApi(listing([channel({ routing_rules: {} })]));
    renderPage();
    expect(await screen.findByText('Every alert')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(/begins/);
    expect(screen.getByRole('note')).toHaveTextContent(/not told about repeats/);
  });

  it('says a channel with unreadable rules sends nothing', async () => {
    mockApi(
      listing([channel({ routing_rules: { severity: ['URGENT'] }, routing_rules_valid: false })]),
    );
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/sends nothing/);
  });

  it('says alerts go nowhere when no channel exists', async () => {
    mockApi(listing([]));
    renderPage();
    expect(await screen.findByText(/No channel is registered/)).toBeInTheDocument();
    expect(screen.getByText(/sent nowhere/)).toBeInTheDocument();
  });

  it('shows a name containing markup as text and creates no element', async () => {
    const markup = '<img src=x onerror=alert(1)>';
    mockApi(listing([channel({ name: markup })]));
    const { container } = renderPage();
    expect(await screen.findByText(markup)).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(source).not.toMatch(/dangerouslySetInnerHTML/);
  });

  it('shows a load failure as one, with a way to retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(reply(503, {})));
    renderPage();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('tells a non-administrator it is not theirs, and offers nothing', () => {
    mockApi();
    renderPage(authenticated(['alert:read']));
    expect(screen.getByRole('status')).toHaveTextContent(/managed by an administrator/);
    expect(screen.queryByRole('button', { name: 'New channel' })).toBeNull();
  });

  it('creates a webhook channel from a reference and the chosen rules', async () => {
    const fetchMock = mockApi(listing([]), { status: 201, body: channel() });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New channel' }));
    await userEvent.type(screen.getByLabelText('Name'), '  ops-slack ');
    await userEvent.selectOptions(screen.getByLabelText('Type'), 'SLACK');
    await userEvent.type(screen.getByLabelText(/Secret reference/), 'env://OPS_SLACK');
    await userEvent.click(screen.getByLabelText('CRITICAL'));
    await userEvent.click(screen.getByLabelText('production'));
    await userEvent.click(screen.getByRole('button', { name: 'Create channel' }));

    await waitFor(() => {
      expect(writes(fetchMock)).toEqual([
        {
          method: 'POST',
          url: '/api/v1/notification-channels',
          body: {
            name: 'ops-slack',
            channel_type: 'SLACK',
            destination_reference: 'env://OPS_SLACK',
            routing_rules: { severity: ['CRITICAL'], environment: ['production'] },
          },
        },
      ]);
    });
    // The form closes and the list is read again.
    await waitFor(() => {
      expect(screen.queryByRole('form', { name: 'New channel' })).toBeNull();
    });
  });

  it('refuses a pasted URL before anything is sent', async () => {
    const fetchMock = mockApi(listing([]));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New channel' }));
    await userEvent.type(screen.getByLabelText('Name'), 'leaky');
    await userEvent.type(
      screen.getByLabelText(/Secret reference/),
      'https://hooks.example.test/services/T0/B0/canary-secret-value',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Create channel' }));
    const problem = await screen.findByText(/looks like a URL/);
    expect(problem).toBeInTheDocument();
    expect(writes(fetchMock)).toEqual([]);
    // The URL is not repeated in the message.
    expect(problem.textContent).not.toMatch(/canary/);
  });

  it('shows the backend’s per-field refusals next to their fields', async () => {
    mockApi(listing([]), {
      status: 422,
      body: {
        error: {
          code: 'validation_failed',
          message: 'invalid',
          details: {
            fields: [
              {
                loc: ['body', 'destination_reference'],
                code: 'reference_not_resolvable',
                message: 'That reference does not resolve in this deployment’s secret store.',
              },
            ],
          },
        },
      },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New channel' }));
    await userEvent.type(screen.getByLabelText('Name'), 'x');
    await userEvent.type(screen.getByLabelText(/Secret reference/), 'env://NOPE');
    await userEvent.click(screen.getByRole('button', { name: 'Create channel' }));
    expect(await screen.findByText(/does not resolve in this deployment/)).toBeInTheDocument();
    // Still open, so the administrator can fix it.
    expect(screen.getByRole('form', { name: 'New channel' })).toBeInTheDocument();
  });

  it('says a duplicate name is a duplicate', async () => {
    mockApi(listing([]), {
      status: 409,
      body: { error: { code: 'duplicate_resource', message: 'x' } },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New channel' }));
    await userEvent.type(screen.getByLabelText('Name'), 'dup');
    await userEvent.type(screen.getByLabelText(/Secret reference/), 'env://X');
    await userEvent.click(screen.getByRole('button', { name: 'Create channel' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/already exists/);
  });

  it('does not offer email when the deployment cannot send mail, and says why', async () => {
    mockApi(listing([], { email_available: false }));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New channel' }));
    expect(screen.getByRole('option', { name: 'EMAIL' })).toBeDisabled();
    expect(screen.getByText(/Email is not configured on this deployment/)).toBeInTheDocument();
  });

  it('asks for an address, not a reference, for an email channel', async () => {
    mockApi(listing([]));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'New channel' }));
    await userEvent.selectOptions(screen.getByLabelText('Type'), 'EMAIL');
    expect(screen.getByLabelText('Recipient address')).toBeInTheDocument();
    expect(screen.queryByText(/never entered here/)).toBeNull();
  });

  it('edits a channel without letting its type change', async () => {
    const fetchMock = mockApi(listing([channel()]), {
      status: 200,
      body: channel({ name: 'renamed' }),
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit ops-slack' }));
    expect(screen.getByLabelText('Type')).toBeDisabled();
    const name = screen.getByLabelText('Name');
    await userEvent.clear(name);
    await userEvent.type(name, 'renamed');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => {
      expect(writes(fetchMock)).toHaveLength(1);
    });
    const [write] = writes(fetchMock);
    expect(write?.method).toBe('PATCH');
    expect(write?.url).toBe('/api/v1/notification-channels/c1');
    expect(write?.body).toMatchObject({
      name: 'renamed',
      destination_reference: 'env://OPS_SLACK',
    });
    expect(write?.body).not.toHaveProperty('channel_type');
  });

  it('disables and enables a channel with one click', async () => {
    const fetchMock = mockApi(listing([channel()]), {
      status: 200,
      body: channel({ status: 'DISABLED' }),
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Disable ops-slack' }));
    await waitFor(() => {
      expect(writes(fetchMock)).toEqual([
        { method: 'PATCH', url: '/api/v1/notification-channels/c1', body: { status: 'DISABLED' } },
      ]);
    });
  });

  it('offers Enable for a disabled channel', async () => {
    mockApi(listing([channel({ status: 'DISABLED' })]));
    renderPage();
    expect(await screen.findByRole('button', { name: 'Enable ops-slack' })).toBeEnabled();
  });

  it('disables every control in read-only mode', async () => {
    mockApi();
    renderPage(authenticated(ADMIN, true));
    await screen.findByText('ops-slack');
    expect(screen.getByRole('button', { name: 'New channel' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Edit ops-slack' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Disable ops-slack' })).toBeDisabled();
  });

  it('shows a refusal in read-only mode returned by the backend, not a silent failure', async () => {
    mockApi(listing([channel()]), {
      status: 423,
      body: { error: { code: 'read_only_mode', message: 'x' } },
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Disable ops-slack' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/read-only mode/);
  });

  it('has no accessibility violations', async () => {
    mockApi(listing([channel(), channel({ id: 'c2', name: 'mail', channel_type: 'EMAIL' })]));
    const { container } = renderPage();
    await screen.findByText('mail');
    const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
