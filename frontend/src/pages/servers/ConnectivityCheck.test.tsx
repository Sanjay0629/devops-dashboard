import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ConnectivityCheck } from '@/pages/servers/ConnectivityCheck';

/**
 * §14.8 from the button's side (PRD §4.11). `reachable: false` is an answer and is said as one;
 * everything the portal could not ask is a refusal said in words, never "down"; the request has
 * no body, so nothing can ride along; and the button is a usability hint, not the control.
 */

function sessionState(readOnly = false): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'ADMINISTRATOR', scope: {} }],
    permissions: ['server:write'],
    session: { expires_at: '2026-10-20T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

const ANSWER = {
  reachable: true,
  checked_at: '2026-10-20T12:00:00Z',
  provider_status: 'online',
  data_state: 'ok',
  message: 'reachable',
};

function mockApi(status = 200, body: unknown = ANSWER) {
  const fetchMock = vi.fn().mockImplementation(() => reply(status, body));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderButton(readOnly = false, disabled = false) {
  return render(
    <SessionContext.Provider
      value={{
        state: sessionState(readOnly),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <ConnectivityCheck serverId="s1" disabled={disabled} />
    </SessionContext.Provider>,
  );
}

const refusal = (code: string, reason: string | null) => ({
  error: { code, message: 'backend words', details: reason === null ? null : { reason } },
});

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('ConnectivityCheck', () => {
  it('says it has not asked yet, before anything is clicked', () => {
    mockApi();
    renderButton();
    expect(screen.getByText('Not checked yet.')).toBeInTheDocument();
  });

  it('sends one POST with no body and shows a reachable answer with its time', async () => {
    const fetchMock = mockApi();
    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    await screen.findByText(/Reachable through the provider/);
    expect(screen.getByText(/online/)).toBeInTheDocument();
    expect(screen.getByText('2026-10-20T12:00:00Z')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/servers/s1/connectivity-check');
    expect(init.method).toBe('POST');
    expect(init.body).toBeUndefined();
  });

  it('says "not reachable" for reachable: false, as an answer and not as an error', async () => {
    mockApi(200, { ...ANSWER, reachable: false, provider_status: 'not_registered_with_ssm' });
    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    await screen.findByText(/Not reachable through the provider/);
    expect(screen.getByText(/not registered with the provider/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it.each([
    [503, 'not_configured', 'no_adapter_for_provider', /No provider integration is configured/],
    [503, 'not_configured', 'demonstration_mode', /demonstration mode/],
    [503, 'not_configured', 'account_disabled', /cloud account is disabled/],
    [503, 'not_configured', 'static_credentials_present', /static access key/],
    [502, 'provider_error', 'assume_role_denied', /assume the account’s execution role/],
    [422, 'unsupported_operation', null, /not supported by this server’s provider/],
    [429, 'rate_limited', null, /Too many connectivity checks/],
  ])('says a %s %s (%s) in words and never as "down"', async (status, code, reason, words) => {
    mockApi(status, refusal(code, reason));
    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('could not be checked');
    expect(alert).toHaveTextContent(words);
    expect(screen.queryByText(/Reachable|Not reachable/)).toBeNull();
  });

  it('falls back to the backend message for a reason it has no words for', async () => {
    mockApi(502, refusal('provider_error', 'something_new'));
    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('backend words');
  });

  it.each([
    [403, /do not have permission/],
    [404, /outside your access scope/],
    [423, /read-only mode/],
  ])('says a %s plainly', async (status, words) => {
    mockApi(status, {});
    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(words);
  });

  it('says so when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('network down');
  });

  it('does not let a second click start a second check while one is running', async () => {
    let release: (value: unknown) => void = () => undefined;
    const fetchMock = vi
      .fn()
      .mockImplementation(() => new Promise((resolve) => (release = resolve)));
    vi.stubGlobal('fetch', fetchMock);
    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    const busy = screen.getByRole('button', { name: 'Checking…' });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release({ ok: true, status: 200, json: () => Promise.resolve(ANSWER) });
    await screen.findByText(/Reachable through the provider/);
  });

  it('is disabled in read-only mode, for a retired server, and sends nothing', () => {
    const fetchMock = mockApi();
    const { unmount } = renderButton(true);
    expect(screen.getByRole('button', { name: 'Check connectivity' })).toBeDisabled();
    unmount();
    renderButton(false, true);
    expect(screen.getByRole('button', { name: 'Check connectivity' })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('has no accessibility violations in any state', async () => {
    mockApi(503, refusal('not_configured', 'no_adapter_for_provider'));
    const { container } = renderButton();
    expect((await axe.run(container)).violations).toEqual([]);
    await userEvent.click(screen.getByRole('button', { name: 'Check connectivity' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
