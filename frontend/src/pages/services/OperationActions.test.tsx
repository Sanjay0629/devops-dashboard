import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ServiceDetail } from '@/api/services';
import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { OperationActions } from '@/pages/services/OperationActions';

/**
 * §17.1 from the dialog's side (PRD §4.6): the four facts restated, production made visibly
 * different, one idempotency key per dialog, and every refusal said in words with the dialog left
 * open. **No request can carry anything but the closed action and a reason**, and the buttons are
 * a usability hint -- the backend decides.
 */

function service(over: Partial<ServiceDetail> = {}): ServiceDetail {
  return {
    server_id: 's1',
    server_display_name: 'Web 1',
    service_name: 'nginx.service',
    environment: 'staging',
    allowed_actions: ['status', 'logs_tail', 'start', 'stop', 'restart'],
    approval_required: false,
    approval_window_seconds: null,
    ...over,
  } as ServiceDetail;
}

function sessionState(readOnly = false): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'DEVOPS_ENGINEER', scope: {} }],
    permissions: ['service:read'],
    session: { expires_at: '2026-10-19T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

const ACCEPTED = { operation_id: 'op-1', execution_status: 'PENDING', idempotent_replay: false };

function mockApi(status = 202, body: unknown = ACCEPTED) {
  const fetchMock = vi.fn().mockImplementation(() => reply(status, body));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const posts = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.map((call) => {
    const init = call[1] as RequestInit;
    return {
      url: String(call[0]),
      body: JSON.parse(init.body as string) as Record<string, unknown>,
      key: new Headers(init.headers).get('Idempotency-Key'),
    };
  });

function renderPanel(detail: ServiceDetail = service(), readOnly = false) {
  return render(
    <SessionContext.Provider
      value={{
        state: sessionState(readOnly),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <MemoryRouter initialEntries={['/servers/s1/services/nginx.service']}>
        <Routes>
          <Route
            path="/servers/s1/services/nginx.service"
            element={<OperationActions service={detail} />}
          />
          <Route path="/operations/:id" element={<p>operation page</p>} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

describe('OperationActions', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('offers only the requestable actions the backend said are allowed, and nothing else', () => {
    renderPanel(service({ allowed_actions: ['status', 'logs_tail', 'restart'] }));
    expect(screen.getByRole('button', { name: 'Restart' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /status|logs/i })).not.toBeInTheDocument();
  });

  it('renders nothing when no action is offered', () => {
    const { container } = renderPanel(service({ allowed_actions: ['status', 'logs_tail'] }));
    expect(container).toBeEmptyDOMElement();
  });

  it('disables the buttons in read-only mode (the backend answers 423 regardless)', () => {
    renderPanel(service(), true);
    for (const name of ['Start', 'Stop', 'Restart']) {
      expect(screen.getByRole('button', { name })).toBeDisabled();
    }
  });

  it('restates server, service, environment and action before anything is sent', async () => {
    const fetchMock = mockApi();
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));

    const dialog = screen.getByRole('dialog', { name: /confirm: restart nginx\.service/i });
    expect(dialog).toHaveTextContent('Web 1');
    expect(dialog).toHaveTextContent('nginx.service');
    expect(dialog).toHaveTextContent('staging');
    expect(dialog).toHaveTextContent('Restart');
    expect(screen.queryByTestId('production-warning')).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled(); // opening the dialog sends nothing
  });

  it('makes a production target visibly different and requires a reason', async () => {
    const fetchMock = mockApi();
    renderPanel(service({ environment: 'production' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));

    expect(screen.getByTestId('production-warning')).toHaveTextContent(/production/i);
    expect(screen.getByText(/reason \(required\)/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/reason is required/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends exactly the closed action and the reason, with an idempotency key, then follows the operation', async () => {
    const fetchMock = mockApi();
    renderPanel(service({ environment: 'production' }));
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await userEvent.type(screen.getByRole('textbox'), 'CR-1234');
    await userEvent.click(screen.getByRole('button', { name: 'Stop nginx.service' }));

    expect(await screen.findByText('operation page')).toBeInTheDocument();
    const [sent] = posts(fetchMock);
    expect(sent?.url).toContain('/servers/s1/services/nginx.service/operations');
    expect(sent?.body).toEqual({ action: 'stop', reason: 'CR-1234' });
    expect(Object.keys(sent?.body ?? {}).sort()).toEqual(['action', 'reason']);
    expect(sent?.key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('omits the reason when none was given where it is optional', async () => {
    const fetchMock = mockApi();
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    await userEvent.click(screen.getByRole('button', { name: 'Start nginx.service' }));
    await screen.findByText('operation page');
    expect(posts(fetchMock)[0]?.body).toEqual({ action: 'start' });
  });

  it('re-sends the same key when the same dialog submits again, and a new key for a new dialog', async () => {
    const fetchMock = mockApi(503, {
      error: {
        code: 'not_configured',
        message: 'x',
        details: { reason: 'no_adapter_for_provider' },
      },
    });
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));
    await screen.findByRole('alert');
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    const [first, second] = posts(fetchMock);
    expect(first?.key).toBe(second?.key); // one intent, not two

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });
    expect(posts(fetchMock)[2]?.key).not.toBe(first?.key);
  });

  it.each([
    [
      503,
      {
        error: { code: 'not_configured', message: 'm', details: { reason: 'demonstration_mode' } },
      },
      /demonstration mode/i,
    ],
    [
      503,
      {
        error: {
          code: 'not_configured',
          message: 'm',
          details: { reason: 'no_adapter_for_provider' },
        },
      },
      /no provider integration is configured/i,
    ],
    [
      503,
      {
        error: {
          code: 'not_configured',
          message: 'm',
          details: { reason: 'approval_policy_unsupported' },
        },
      },
      /asks for more than one approval/i,
    ],
    [422, { error: { code: 'unsupported_operation', message: 'm' } }, /not supported by this/i],
    [
      409,
      { error: { code: 'operation_in_progress', message: 'm' } },
      /already pending or running/i,
    ],
    [429, { error: { code: 'rate_limited', message: 'm' } }, /too many operations/i],
  ])('says a %i refusal in words and keeps the dialog open', async (status, body, words) => {
    mockApi(status, body);
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(words);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText('operation page')).not.toBeInTheDocument();
  });

  it('does not call a deliberate 503 a network error', async () => {
    mockApi(503, {
      error: { code: 'not_configured', message: 'm', details: { reason: 'demonstration_mode' } },
    });
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));
    expect(await screen.findByRole('alert')).not.toHaveTextContent(/could not reach/i);
  });

  it.each([
    [403, undefined, /do not have permission/i],
    [404, undefined, /outside your access scope/i],
    [423, undefined, /read-only mode/i],
  ])('shows a %i as what it is', async (status, body, words) => {
    mockApi(status, body);
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(words);
  });

  it('says an approval is needed, from an Approver, and how long the request will wait', async () => {
    renderPanel(service({ approval_required: true, approval_window_seconds: 3600 }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/needs an approval from an approver/i);
    expect(dialog).toHaveTextContent(/someone other than you approves it/i);
    expect(dialog).toHaveTextContent(/wait up to 60 minutes/i);
    expect(dialog).not.toHaveTextContent(/not available|refused/i);
    expect(screen.getByText(/reason \(required\)/i)).toBeInTheDocument();
  });

  it('says so without a number when the window is not known', async () => {
    renderPanel(service({ approval_required: true, approval_window_seconds: null }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/needs an approval from an approver/i);
    expect(dialog).not.toHaveTextContent(/wait up to/i);
  });

  it('follows a request that is now waiting for approval to its operation page', async () => {
    const fetchMock = mockApi(202, {
      operation_id: 'op-9',
      execution_status: 'AWAITING_APPROVAL',
      idempotent_replay: false,
    });
    renderPanel(service({ environment: 'production', approval_required: true }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.type(screen.getByRole('textbox'), 'CR-9');
    await userEvent.click(screen.getByRole('button', { name: 'Restart nginx.service' }));
    expect(await screen.findByText('operation page')).toBeInTheDocument();
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it('closes on Escape and on Cancel without sending anything', async () => {
    const fetchMock = mockApi();
    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('has no axe violations with the dialog open on a production target', async () => {
    const { container } = renderPanel(service({ environment: 'production' }));
    await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await screen.findByRole('dialog');
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });
});
