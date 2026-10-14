/**
 * Session client.
 *
 * Hand-written like `health.ts`, and for the same reason: from M1.5 onward API
 * types are *generated* from the backend's OpenAPI schema and committed
 * (docs/TECH_STACK.md §11). These two modules predate having a schema to
 * generate from, and the shapes here are checked against the real response by
 * the backend contract test rather than trusted.
 *
 * The SPA holds **no token** (AD-02). The session is an `HttpOnly` cookie this
 * code cannot read, so there is nothing here to store, refresh or leak. The
 * only thing it does read is the CSRF cookie, which is deliberately readable
 * and authenticates nothing on its own.
 *
 * And nothing here decides what the user may do. `permissions` is used to
 * *render*, never to authorise — every action is independently authorised
 * server-side (docs/SECURITY.md §6), and a UI that hides a button is a
 * convenience, not a control.
 */

const API_BASE = '/api/v1';
const REQUEST_TIMEOUT_MS = 10000;

export const CSRF_COOKIE_NAME = 'dcp_csrf';
export const CSRF_HEADER_NAME = 'X-CSRF-Token';

export interface SessionUser {
  id: string;
  email: string;
  display_name: string;
  status: string;
}

export interface SessionRole {
  name: string;
  scope: Record<string, unknown>;
}

export interface Session {
  user: SessionUser;
  roles: SessionRole[];
  /** For rendering only. Carries no authority (docs/API_CONTRACTS.md §12.4). */
  permissions: string[];
  session: { expires_at: string; idle_timeout_seconds: number };
  demo_mode: boolean;
  read_only_mode: boolean;
}

/**
 * What the UI knows about the session, as opposed to what it assumes.
 *
 * `anonymous` and `unreachable` are deliberately different states: "the server
 * says you are not signed in" and "we could not ask" must not render the same
 * way, or a backend outage looks like a logout and sends the user into a login
 * loop against a server that cannot log them in.
 */
export type SessionState =
  | { kind: 'loading' }
  | { kind: 'authenticated'; session: Session }
  | { kind: 'anonymous' }
  | { kind: 'unreachable'; reason: string };

/** Read a cookie the server marked readable. Returns null when absent. */
export function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split('; ')) {
    if (part.startsWith(prefix)) return decodeURIComponent(part.slice(prefix.length));
  }
  return null;
}

function isSession(value: unknown): value is Session {
  if (typeof value !== 'object' || value === null) return false;
  // Narrowed from `unknown`, not from Partial<Session>: typing the candidate
  // as a partial of the target would let TypeScript assume the very fields
  // this function exists to check.
  const candidate = value as Record<string, unknown>;
  const user = candidate.user;
  return (
    typeof user === 'object' &&
    user !== null &&
    typeof (user as Record<string, unknown>).email === 'string' &&
    Array.isArray(candidate.permissions) &&
    Array.isArray(candidate.roles) &&
    typeof candidate.session === 'object' &&
    candidate.session !== null
  );
}

/**
 * The one fetch wrapper: base path, Accept, timeout, CSRF, credentials.
 *
 * Exported since M1.5 so `inventory.ts` uses it rather than growing a second
 * copy. Nothing about its behaviour changed — a second wrapper would be a
 * second place for the CSRF rule to be got wrong.
 */
export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, REQUEST_TIMEOUT_MS);
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');

  // Double-submit: echo the readable CSRF cookie on anything state-changing
  // (docs/SECURITY.md §11). GET never needs it, and sending it there would
  // only widen where the value appears.
  if (init.method !== undefined && init.method !== 'GET') {
    const token = readCookie(CSRF_COOKIE_NAME);
    if (token !== null) headers.set(CSRF_HEADER_NAME, token);
  }

  try {
    return await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      // The session cookie is same-origin and HttpOnly; this keeps it attached
      // when the dev server proxies rather than serving the SPA itself.
      credentials: 'same-origin',
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Fetch the current session.
 *
 * Never throws: an unreachable backend is a state to display. A `401` is a
 * real answer — the server is up and telling us there is no session — and is
 * reported as `anonymous`, distinct from no answer at all.
 */
export async function fetchSession(): Promise<SessionState> {
  try {
    const response = await request('/auth/session');
    if (response.status === 401) return { kind: 'anonymous' };
    if (!response.ok) {
      return { kind: 'unreachable', reason: `Unexpected status ${String(response.status)}` };
    }
    const body: unknown = await response.json();
    if (!isSession(body)) return { kind: 'unreachable', reason: 'Unexpected response shape' };
    return { kind: 'authenticated', session: body };
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  }
}

/**
 * End the session server-side.
 *
 * Returns true only when the server confirmed it. A client that merely forgot
 * the session locally would show "signed out" while the session stayed usable
 * to anyone holding the cookie, so a failure is reported rather than assumed
 * away.
 */
export async function logout(): Promise<boolean> {
  try {
    const response = await request('/auth/logout', { method: 'POST' });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Where to send the browser to sign in.
 *
 * A full navigation, not `fetch`: the backend answers `302` to the identity
 * provider and the browser has to follow it. `next` is the path to come back
 * to, and the backend validates it again — this is convenience, not the
 * control (an absolute URL here is rejected server-side with `422`).
 */
export function loginUrl(next: string = window.location.pathname): string {
  const safe = next.startsWith('/') && !next.startsWith('//') ? next : '/';
  return `${API_BASE}/auth/login?next=${encodeURIComponent(safe)}`;
}
