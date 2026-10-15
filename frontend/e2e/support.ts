/**
 * Shared E2E helpers: logging in through the mock IdP, talking to the API as
 * the browser does, and the accessibility assertion.
 *
 * One module rather than four, because these four things are used together by
 * every spec and splitting them would mean four imports to say "drive the
 * stack as a signed-in person".
 */

import { createRequire } from 'node:module';

import { expect, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';

/** The synthetic accounts the mock identity provider will impersonate. */
export const ACCOUNTS = {
  admin: { key: 'admin', email: 'admin@example.invalid' },
  viewer: { key: 'viewer', email: 'viewer@example.invalid' },
} as const;

export const STORAGE_STATE = {
  admin: 'e2e/.auth/admin.json',
  viewer: 'e2e/.auth/viewer.json',
} as const;

/** Every environment the scope validator accepts (`app/authz/schemas.py`). */
export const ALL_ENVIRONMENTS = ['development', 'other', 'production', 'staging'];

/**
 * Full reach, deliberately identical to the administrator's.
 *
 * The Viewer is granted the *same* scope as the administrator so that the
 * authorisation specs prove what they claim. If the Viewer's scope were
 * narrower, "a Viewer cannot register a server" would pass because they
 * cannot see the environment, not because `VIEWER` lacks `server:write` — and
 * the test would keep passing if the permission matrix were wrong.
 */
export const FULL_SCOPE = {
  environment: ALL_ENVIRONMENTS,
  cloud_account: ['*'],
  server: ['*'],
  application: ['*'],
};

export const CSRF_COOKIE = 'dcp_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';

/**
 * Log in by driving the real redirect chain.
 *
 * `/auth/login` → the provider's account picker → back to the callback →
 * `next`. Clicking the picker rather than appending `?user=` keeps the
 * browser's own cookie and redirect handling in the path, which is the part
 * a direct API call would skip.
 *
 * Logging in is **rate limited** (the `/auth/*` class, 10 per 300 s), so
 * specs reuse a saved storage state instead of calling this per test. Only
 * global setup and the one spec that is *about* logging in call it.
 */
export async function logIn(page: Page, account: keyof typeof ACCOUNTS): Promise<void> {
  const { email } = ACCOUNTS[account];
  await page.goto('/api/v1/auth/login?next=/dashboard');
  // The mock renders a picker when no account is named. Its presence is also
  // the evidence that the redirect to the provider actually happened.
  await expect(page.getByRole('heading', { name: 'Mock identity provider' })).toBeVisible();
  await page.getByRole('link', { name: new RegExp(email.replace('.', '\\.')) }).click();
  await page.waitForURL('**/dashboard');
  await expect(page.getByText(email)).toBeVisible();
}

/**
 * Headers a state-changing API call needs, which is **two** controls, not one.
 *
 * `app/core/csrf.py` runs three layers and the first two both apply here:
 *
 * - **Origin.** Checked *first*, and an absent `Origin`/`Referer` on an unsafe
 *   method is a rejection in itself. Playwright's `APIRequestContext` is not a
 *   page fetch and sends neither, so without this header every write answers
 *   `403 "CSRF validation failed."` — which matters enormously in
 *   `authorisation.spec.ts`: a test asserting `403` would pass on a CSRF
 *   rejection and prove nothing about permissions. Sending it is what a real
 *   browser does; the backend compares it to its own base URL.
 * - **Double submit.** The `dcp_csrf` cookie is readable by design and
 *   authenticates nothing on its own (docs/SECURITY.md §11); the session
 *   cookie is HttpOnly and travels separately.
 */
export async function writeHeaders(
  context: BrowserContext,
  origin = 'http://localhost:5173',
): Promise<Record<string, string>> {
  const cookies = await context.cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE)?.value;
  if (token === undefined) {
    throw new Error(`no ${CSRF_COOKIE} cookie — was the login completed?`);
  }
  return {
    Origin: origin,
    [CSRF_HEADER]: token,
    'Content-Type': 'application/json',
  };
}

export interface ServerRow {
  id: string;
  cloud_account_id: string;
  display_name: string;
}

/**
 * The oldest server, which is always one the seed created.
 *
 * Deliberately `sort=created_at&order=asc` rather than taking the first row of
 * the default listing. The default sort is `display_name` ascending under a
 * `C` collation, where `E2E Registered Host` sorts before `[DEMO] …` — so
 * `items[0]` became a row that **a previous run of §12.2 had registered**, and
 * the suite's behaviour then depended on how many times it had been run
 * before. Nothing had broken yet, but that is the shape of the order
 * dependence PROGRESS issue 51 cost a session to find, so it is removed here
 * rather than left to be discovered.
 *
 * Seeding happens in global setup, before any spec, so the oldest row is a
 * seeded one on a fresh volume and on a long-lived stack alike.
 */
export async function aSeededServer(request: APIRequestContext): Promise<ServerRow> {
  const response = await request.get('/api/v1/servers?limit=1&sort=created_at&order=asc');
  expect(response.status(), 'the caller may read inventory').toBe(200);
  const { items } = (await response.json()) as { items: ServerRow[] };
  const row = items[0];
  if (row === undefined) {
    // Thrown rather than asserted-then-asserted-again: `expect(...)` would
    // not narrow the type, and a non-null assertion is banned by the lint
    // config for exactly the reason it would be wrong here — the empty case
    // is reachable, on a stack whose inventory was never seeded.
    throw new Error('no servers are registered — did global setup seed the inventory?');
  }
  return row;
}

/**
 * Assert the rendered page has no axe violations.
 *
 * `axe-core` is injected from `node_modules` rather than adding
 * `@axe-core/playwright`: the library is already a dependency for the
 * component tests (docs/TECH_STACK.md §8.2 names `axe` assertions as the
 * gate, not a particular wrapper), and `addScriptTag` plus `axe.run` is the
 * whole of what the wrapper would provide.
 *
 * Unlike the jsdom component tests, **colour contrast is checked here**. This
 * is a real browser with real layout, which is the only place that rule can
 * return anything other than "incomplete" — the component tests disable it
 * and say so, and this is where that debt is paid.
 */
export interface AxeFinding {
  id: string;
  impact: string | null | undefined;
  nodes: number;
}

/**
 * The shape `axe.run` returns, declared rather than suppressed.
 *
 * A `@ts-expect-error` on `window.axe` would type the whole expression as
 * `error` and every rule in `strictTypeChecked` downstream of it would fire —
 * so the lint config would have had to be loosened to accommodate one unsafe
 * access. Declaring the two fields actually read is smaller than that.
 */
interface AxeWindow extends Window {
  axe: {
    run: (
      context: Document,
      options: { runOnly: { type: string; values: string[] } },
    ) => Promise<{ violations: { id: string; impact?: string | null; nodes: unknown[] }[] }>;
  };
}

export async function expectNoAxeViolations(page: Page, label: string): Promise<void> {
  const resolve = createRequire(import.meta.url);
  await page.addScriptTag({ path: resolve.resolve('axe-core') });
  const violations: AxeFinding[] = await page.evaluate(async () => {
    const { axe } = window as unknown as AxeWindow;
    const results = await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
    });
    return results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.length,
    }));
  });
  expect(violations, `axe violations on ${label}: ${JSON.stringify(violations)}`).toEqual([]);
}
