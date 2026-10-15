/**
 * The Phase 1 critical paths (docs/TESTING.md §12.1 and §12.2).
 *
 * §12.3–§12.7 and §12.9 need metrics, services, logs, alerts, deployments and
 * approvals, none of which exist before Phase 2. They are **not** stubbed
 * here: a test named "server health inspection" that asserted an empty panel
 * would report a working feature (CLAUDE.md §7).
 *
 * These run against the real Compose stack, so they are the first evidence
 * for several things the component tests can only approximate: the dev
 * server's proxy, the session cookie surviving a redirect chain, the
 * demonstration banner, and colour contrast in a browser that has layout.
 */

import { expect, test } from '@playwright/test';

import { aSeededServer, expectNoAxeViolations, logIn, STORAGE_STATE } from './support';

/** A hostname nothing can resolve (RFC 2606), unique per run. */
function uniqueHost(): string {
  return `e2e-${String(Date.now())}-${String(Math.floor(Math.random() * 1000))}.e2e.invalid`;
}

test.describe('§12.1 login, dashboard, logout', () => {
  // This one signs in itself rather than reusing a saved session: it is the
  // test *about* signing in, and logging out would otherwise destroy the
  // session the rest of the suite shares.
  test('signs in through the identity provider, lands on the dashboard, signs out', async ({
    page,
  }) => {
    await logIn(page, 'admin');

    await expect(page.getByRole('heading', { name: 'DevOps Control Plane' })).toBeVisible();
    // The landing route is the dashboard as of M1.6, not a redirect to
    // /servers.
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole('heading', { name: /Dashboard/i }).first()).toBeVisible();

    await page.getByRole('button', { name: 'Sign out' }).click();
    // Signed out means the login page, not an empty dashboard. The page
    // offers a heading and a link to the provider; there is no password field
    // and no sign-in button, because this application has no password of its
    // own (AD-02).
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Continue to identity provider' })).toBeVisible();
  });

  // A saved session, not a fresh login: signing in is rate limited (10 per
  // 300s, failing closed) and this test does not need the redirect chain. With
  // a login here the suite needed four per run, and a couple of local re-runs
  // then answered 429 — which surfaces as "the mock identity provider heading
  // was not found" and looks nothing like its cause.
  test.describe('with a saved session', () => {
    test.use({ storageState: STORAGE_STATE.admin });

    test('the demonstration banner is rendered, not merely configured', async ({ page }) => {
      // AD-15 and CLAUDE.md §7: a mocked integration is never presented as
      // operational. The smoke test asserts `demo_mode: true` on the API; this
      // is the only place that can see whether the banner actually renders,
      // and the stack it runs against sets DCP_DEMO_MODE=true.
      await page.goto('/dashboard');
      await expect(page.getByText(/Demonstration mode/i)).toBeVisible();
      await expect(page.getByText(/integrations are not operational/i)).toBeVisible();
    });
  });
});

test.describe('§12.2 register a server, find it in inventory, open it', () => {
  test.use({ storageState: STORAGE_STATE.admin });

  test('a registered server appears in the list and its detail page loads', async ({ page }) => {
    // The seeded cloud account is what a server must reference; reading it
    // from the API rather than hardcoding a UUID keeps this working after a
    // volume wipe, when every id is new.
    const { cloud_account_id: cloudAccountId } = await aSeededServer(page.request);

    const hostname = uniqueHost();
    await page.goto('/servers/new');
    await expect(page.getByText(/grants no operational permission/)).toBeVisible();

    await page.getByLabel(/Cloud account id/).fill(cloudAccountId);
    // Deliberately not derived from the hostname: a provider resource id
    // containing it would make the hostname assertion below match two
    // elements and fail on strict mode rather than on the behaviour.
    await page.getByLabel(/Provider resource id/).fill(`i-e2e${String(Date.now())}`);
    await page.getByLabel(/Hostname/).fill(hostname);
    await page.getByLabel(/Display name/).fill('E2E Registered Host');
    await page.getByLabel(/Owner team/).fill('e2e');
    await page.getByRole('button', { name: 'Register' }).click();

    // Registration navigates to the new server's detail page.
    await expect(page).toHaveURL(/\/servers\/[0-9a-f-]{36}$/);
    await expect(page.getByRole('heading', { name: 'E2E Registered Host' })).toBeVisible();
    await expect(page.getByText(hostname)).toBeVisible();
    // PRD §4.3: registration grants no operational permission, so a new row
    // is PENDING. StatusBadge renders the human label.
    await expect(page.getByText('Pending').first()).toBeVisible();

    // And it is in the list, which is the half a unit test cannot show.
    await page.goto('/servers');
    await page.getByLabel('Search').fill(hostname);
    await page.getByRole('button', { name: 'Apply' }).click();
    await expect(page.getByRole('link', { name: 'E2E Registered Host' })).toBeVisible();
  });

  test('an unknown server id is reported as not found, not as an empty page', async ({ page }) => {
    await page.goto('/servers/00000000-0000-0000-0000-000000000000');
    await expect(page.getByRole('alert')).toContainText(/Not found/i);
  });
});

test.describe('accessibility of the gated flows (docs/TECH_STACK.md §8.2)', () => {
  test.use({ storageState: STORAGE_STATE.admin });

  // Contrast is checked here and nowhere else: jsdom has no layout, so the
  // component tests can only ever get "incomplete" from that rule and
  // disable it explicitly.
  for (const [label, path] of [
    ['the dashboard', '/dashboard'],
    ['the server list', '/servers'],
    ['the registration form', '/servers/new'],
  ] as const) {
    test(`${label} has no axe violations`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole('heading', { name: 'DevOps Control Plane' })).toBeVisible();
      await expectNoAxeViolations(page, label);
    });
  }
});
