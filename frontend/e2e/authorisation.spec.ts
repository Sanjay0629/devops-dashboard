/**
 * §12.8 — an unauthorised action is rejected, in the UI **and** in the API.
 *
 * This is the one spec in the suite that is about a security control rather
 * than a feature, and the second half is the half that matters. The UI hiding
 * a control is a usability choice and nothing more (docs/SECURITY.md §6,
 * mandatory rule 4): the backend decides every request on its own account.
 * A test that only checked for a missing button would pass just as happily
 * against a frontend-only "permission system", which is the implementation
 * this spec exists to rule out.
 *
 * The Viewer is granted the **same scope** as the administrator (see
 * `FULL_SCOPE` in support.ts), so every rejection below is caused by the role
 * lacking `server:write` and not by the row being out of reach. Without that,
 * these tests would pass for the wrong reason and keep passing if the
 * permission matrix broke.
 *
 * §12.9 (production approval enforcement) is deliberately absent: approvals
 * arrive with M4.3, and there is no privileged operation to gate yet.
 */

import { expect, test, type APIResponse } from '@playwright/test';

import { aSeededServer, ACCOUNTS, STORAGE_STATE, writeHeaders } from './support';

/**
 * Assert a refusal is an **authorisation** refusal.
 *
 * Without this the spec would be close to worthless. The CSRF middleware also
 * answers `403 {"code": "forbidden"}`, and it runs before the route's
 * permission dependency — so a request missing an `Origin` header is rejected
 * for the wrong reason and a bare `expect(status).toBe(403)` passes while
 * proving nothing about permissions. That exact mistake was made writing this
 * suite and caught by the 403 arriving with "CSRF validation failed."
 */
async function expectPermissionDenied(response: APIResponse, what: string): Promise<void> {
  expect(response.status(), what).toBe(403);
  const body = await response.text();
  expect(body, `${what} must be denied on permissions, not CSRF`).not.toContain('CSRF');
}

test.describe('as a Viewer', () => {
  test.use({ storageState: STORAGE_STATE.viewer });

  test('the session is the Viewer, and the advisory list carries no write', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page.getByText(ACCOUNTS.viewer.email)).toBeVisible();
    // Scoped to the identity panel *and* exact: "VIEWER" also appears in the
    // dashboard's filter controls, and a default `getByText` is a
    // case-insensitive substring, so it matched `viewer@example.invalid` as
    // well.
    await expect(
      page.getByRole('region', { name: 'Signed in' }).getByText('VIEWER', { exact: true }),
    ).toBeVisible();

    const session = await page.request.get('/api/v1/auth/session');
    const { permissions } = (await session.json()) as { permissions: string[] };
    // §12.4: this array renders the UI and carries no authority. Asserting it
    // here documents what the UI is reacting to in the next test.
    expect(permissions).toContain('server:read');
    expect(permissions).not.toContain('server:write');
  });

  test('can read inventory but is offered no write control', async ({ page }) => {
    await page.goto('/servers');
    // Reading works: this is not an empty page dressed up as a permission.
    await expect(page.getByRole('table')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Register server' })).toHaveCount(0);

    const { id: serverId, display_name: serverName } = await aSeededServer(page.request);
    await page.goto(`/servers/${serverId}`);
    // The server's own heading, by name. `getByRole('heading', { level: 1 })` alone is ambiguous:
    // the shell renders an <h1> too, so it matched one element while the page was still loading
    // (passing, having waited for nothing) and two once it had loaded (a strict-mode failure).
    // Waiting for *this* heading is what makes the "no write control" assertions below mean
    // something: they are only true of a page that has finished rendering.
    await expect(page.getByRole('heading', { level: 1, name: serverName })).toBeVisible();
    // Retire, Edit and the association controls are all `server:write`.
    await expect(page.getByRole('button', { name: 'Retire' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Edit' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Remove' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Associate an application' })).toHaveCount(0);
  });

  test('the direct API call fails too, with the UI out of the loop', async ({ page, context }) => {
    // The point of §12.8. Everything above is cosmetic; this is the control.
    await page.goto('/dashboard');
    const { id: serverId } = await aSeededServer(page.request);
    const headers = await writeHeaders(context);

    // Editing a server (§14.4) needs `server:write`.
    const edited = await page.request.patch(`/api/v1/servers/${serverId}`, {
      headers,
      data: { display_name: 'Viewer should not be able to do this' },
    });
    await expectPermissionDenied(edited, 'PATCH /servers/{id} as a Viewer');

    // Retiring one (§14.5), same permission, different verb.
    const retired = await page.request.delete(`/api/v1/servers/${serverId}`, { headers });
    await expectPermissionDenied(retired, 'DELETE /servers/{id} as a Viewer');

    // Registering one (§14.2).
    const registered = await page.request.post('/api/v1/servers', {
      headers: { ...headers, 'Idempotency-Key': `e2e-viewer-${String(Date.now())}` },
      data: {
        cloud_account_id: '00000000-0000-0000-0000-000000000000',
        provider_resource_id: 'viewer-should-not-create-this',
        hostname: 'viewer.e2e.invalid',
        display_name: 'Nope',
        operating_system: 'LINUX',
        environment: 'staging',
        management_method: 'NONE',
      },
    });
    await expectPermissionDenied(registered, 'POST /servers as a Viewer');

    // Administration is a different permission family again, and a Viewer
    // holds none of it. A GET, so CSRF never enters into it.
    const users = await page.request.get('/api/v1/admin/users');
    expect(users.status(), 'GET /admin/users as a Viewer').toBe(403);

    // The row is untouched: a 403 that had already written would be worse
    // than a 200.
    const after = await page.request.get(`/api/v1/servers/${serverId}`);
    expect(after.status()).toBe(200);
    const { display_name: displayName, status } = (await after.json()) as {
      display_name: string;
      status: string;
    };
    expect(displayName).not.toBe('Viewer should not be able to do this');
    expect(status).not.toBe('RETIRED');
  });
});

test.describe('unauthenticated', () => {
  // No storage state at all: the browser has never signed in.
  test('is shown the login page and the API refuses', async ({ page }) => {
    await page.goto('/servers');
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    // Not "pretend you are logged out": the server said so.
    const response = await page.request.get('/api/v1/servers');
    expect(response.status()).toBe(401);
  });
});
