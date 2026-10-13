import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CSRF_COOKIE_NAME, loginUrl, readCookie } from '@/api/session';
import { App } from '@/App';

/**
 * Session gating, 401 handling and logout.
 *
 * The case worth the most attention is `unreachable`: a backend outage must
 * not be rendered as a logout. Conflating them sends the operator into a login
 * loop against a server that cannot log them in, during the exact incident
 * they opened this tool to investigate.
 */

const SESSION = {
  user: {
    id: '00000000-0000-0000-0000-000000000001',
    email: 'operator@example.invalid',
    display_name: 'Example Operator',
    status: 'ACTIVE',
  },
  roles: [{ name: 'DEVOPS_ENGINEER', scope: { environment: ['staging'] } }],
  permissions: ['dashboard:read', 'server:read'],
  session: { expires_at: '2026-10-13T20:00:00Z', idle_timeout_seconds: 1800 },
  demo_mode: false,
  read_only_mode: false,
};

const READYZ = { status: 'ok', checks: { database: true, broker: true, migrations: true } };

/** `fetch` accepts three input shapes; only two of them have a usable `.url`. */
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
}

/** Route by URL, because the app fetches both /auth/session and /readyz. */
function status(ok: boolean, code: number, body: unknown = {}): Response {
  return { ok, status: code, json: () => Promise.resolve(body) } as unknown as Response;
}

type Handler = (init?: RequestInit) => Response;

function mockApi(handlers: Partial<Record<'session' | 'logout' | 'readyz', Handler>>) {
  // Both parameters are declared so the recorded call tuples keep their
  // arity — the tests read `init` back to assert on the CSRF header.
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = urlOf(input);
    if (url.includes('/auth/session')) {
      return Promise.resolve(handlers.session?.(init) ?? status(true, 200, SESSION));
    }
    if (url.includes('/auth/logout')) {
      return Promise.resolve(handlers.logout?.(init) ?? status(true, 200, { logged_out: true }));
    }
    return Promise.resolve(handlers.readyz?.(init) ?? status(true, 200, READYZ));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('session gating', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    document.cookie = `${CSRF_COOKIE_NAME}=; Max-Age=0; path=/`;
  });

  it('does not claim a state before asking', () => {
    mockApi({ session: () => status(true, 200, SESSION) });
    render(<App />);
    // Specifically not the login page: "we have not asked" is not "signed out".
    expect(screen.getByRole('status')).toHaveTextContent(/checking your session/i);
    expect(screen.queryByRole('link', { name: /identity provider/i })).not.toBeInTheDocument();
  });

  it('renders the identity once the session resolves', async () => {
    mockApi({});
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    expect(screen.getByText('operator@example.invalid')).toBeInTheDocument();
    expect(screen.getByText(/DEVOPS_ENGINEER/)).toBeInTheDocument();
  });

  it('shows the login page on a 401', async () => {
    mockApi({ session: () => status(false, 401, { error: { code: 'unauthenticated' } }) });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /identity provider/i })).toBeInTheDocument();
    });
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Sign in');
  });

  it('distinguishes an unreachable backend from being signed out', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/could not be reached/i);
    });
    // The operator is told their state is unknown, not that they are logged out.
    expect(screen.getByText(/unknown/i)).toBeInTheDocument();
    expect(screen.queryByText('Example Operator')).not.toBeInTheDocument();
  });

  it('treats a malformed session response as unreachable, not as a session', async () => {
    mockApi({ session: () => status(true, 200, { unexpected: 'shape' }) });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/could not be reached/i);
    });
  });

  it('treats a 500 as unreachable rather than as a logout', async () => {
    mockApi({ session: () => status(false, 500, { error: { code: 'internal_error' } }) });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/could not be reached/i);
    });
  });
});

describe('logout', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    document.cookie = `${CSRF_COOKIE_NAME}=; Max-Age=0; path=/`;
  });

  it('signs out and shows the login page', async () => {
    mockApi({});
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /identity provider/i })).toBeInTheDocument();
    });
  });

  it('sends the CSRF token from the cookie as a header', async () => {
    document.cookie = `${CSRF_COOKIE_NAME}=the-csrf-token; path=/`;
    const fetchMock = mockApi({});
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => urlOf(url).includes('/auth/logout'));
      expect(call).toBeDefined();
      const headers = new Headers((call![1] as RequestInit).headers);
      expect(headers.get('X-CSRF-Token')).toBe('the-csrf-token');
    });
  });

  it('does not send a CSRF header on a GET', async () => {
    document.cookie = `${CSRF_COOKIE_NAME}=the-csrf-token; path=/`;
    const fetchMock = mockApi({});
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    const call = fetchMock.mock.calls.find(([url]) => urlOf(url).includes('/auth/session'));
    const headers = new Headers((call![1] as RequestInit).headers);
    expect(headers.get('X-CSRF-Token')).toBeNull();
  });

  it('re-checks rather than showing signed-out when logout is not confirmed', async () => {
    // A failed logout may well mean the session is still live. Rendering a
    // signed-out UI over a working session is the bug being prevented.
    mockApi({ logout: () => status(false, 503, { error: { code: 'provider_unavailable' } }) });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    expect(screen.queryByRole('link', { name: /identity provider/i })).not.toBeInTheDocument();
  });
});

describe('the SPA holds no token', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('stores nothing from the session in browser storage', async () => {
    mockApi({});
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    // AD-02: the session is an HttpOnly cookie. Nothing about it is written
    // anywhere script can read it back, including by the theme toggle's
    // localStorage use.
    const stored = JSON.stringify([
      ...Object.entries(localStorage),
      ...Object.entries(sessionStorage),
    ]);
    expect(stored).not.toContain('operator@example.invalid');
    expect(stored).not.toContain(SESSION.user.id);
    expect(stored).not.toContain('dcp_session');
  });

  it('cannot read the session cookie', () => {
    // Nothing sets it here; the assertion is that the client code never looks
    // for it. It is HttpOnly in the browser, so this is only reachable if
    // someone changed that flag.
    expect(readCookie('dcp_session')).toBeNull();
  });
});

describe('login URL', () => {
  it('returns to the current path after signing in', () => {
    expect(loginUrl('/servers/123')).toBe('/api/v1/auth/login?next=%2Fservers%2F123');
  });

  it('refuses to put an absolute URL in next', () => {
    // The backend rejects this with 422 regardless; this keeps the client from
    // generating a request it knows is invalid.
    expect(loginUrl('http://evil.example.invalid/')).toBe('/api/v1/auth/login?next=%2F');
    expect(loginUrl('//evil.example.invalid/x')).toBe('/api/v1/auth/login?next=%2F');
  });
});

describe('demonstration mode', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('is never silent', async () => {
    // AD-15 / CLAUDE.md §7: a mocked integration is never presented as
    // operational.
    mockApi({ session: () => status(true, 200, { ...SESSION, demo_mode: true }) });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/demonstration mode/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/not operational/i)).toBeInTheDocument();
  });

  it('shows no banner when it is off', async () => {
    mockApi({});
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Example Operator')).toBeInTheDocument();
    });
    expect(screen.queryByText(/demonstration mode/i)).not.toBeInTheDocument();
  });
});

describe('a user with no roles', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('is told so plainly rather than shown an empty list', async () => {
    mockApi({
      session: () => status(true, 200, { ...SESSION, roles: [], permissions: [] }),
    });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/no roles granted/i)).toBeInTheDocument();
    });
  });
});
