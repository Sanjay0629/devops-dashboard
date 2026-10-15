/**
 * E2E preconditions: a reachable stack, an administrator, and a Viewer.
 *
 * Run once before the suite. Everything here is **idempotent**, because the
 * stack is long-lived locally and a second run must not need a wiped volume.
 *
 * The order matters and is not arbitrary:
 *
 * 1.  Sign in as the administrator. JIT provisioning creates the user row on
 *     first login with **no roles** (PRD §3), so this has to happen before
 *     anything can be granted to them — the bootstrap CLI grants only to an
 *     existing user, and exits 1 if asked for an unknown address.
 * 2.  Run the bootstrap CLI. Exit 2 means an administrator already exists,
 *     which is success on a re-run, not a failure.
 * 3.  Sign in as the Viewer, which creates *their* row, also with no roles.
 * 4.  As the administrator, grant `VIEWER`. A role change takes effect on the
 *     target's next request with no re-login, so the saved Viewer session is
 *     immediately a Viewer.
 *
 * Both signed-in states are saved to disk and reused by the specs. That is
 * not just speed: logging in is rate limited (the `/auth/*` class, 10 per
 * 300 s), so a suite that logged in per test would start failing as it grew.
 */

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';

import { chromium, request, type FullConfig } from '@playwright/test';

import { ACCOUNTS, FULL_SCOPE, logIn, STORAGE_STATE, writeHeaders } from './support';

const COMPOSE_FILE = process.env.E2E_COMPOSE_FILE ?? 'infra/compose/docker-compose.yml';
const BOOTSTRAP_ALREADY_DONE = 2;

interface UserSummary {
  id: string;
  email: string;
}

interface RoleGrant {
  role_name: string;
}

function compose(...args: string[]): void {
  // cwd is the repository root: Playwright runs from `frontend/`, and the
  // compose file path is repository-relative.
  execFileSync('docker', ['compose', '-f', COMPOSE_FILE, ...args], {
    cwd: '..',
    stdio: 'inherit',
  });
}

/** Fail with the command to run, rather than with a connection error. */
async function requireStack(baseURL: string): Promise<void> {
  const context = await request.newContext({ baseURL });
  try {
    const response = await context.get('/readyz', { timeout: 10_000 });
    if (response.status() !== 200) {
      const body = await response.text();
      throw new Error(
        `${baseURL}/readyz answered ${String(response.status())}: ${body}\n` +
          'The E2E suite needs a ready stack. Start it with:\n' +
          `  docker compose -f ${COMPOSE_FILE} --profile core --profile mocks up -d --wait`,
      );
    }
  } catch (error) {
    if (error instanceof Error && error.message.includes('/readyz answered')) throw error;
    throw new Error(
      `Could not reach ${baseURL}. The E2E suite runs against the Compose stack:\n` +
        `  docker compose -f ${COMPOSE_FILE} --profile core --profile mocks up -d --wait\n` +
        `(${String(error)})`,
    );
  } finally {
    await context.dispose();
  }
}

/**
 * Does a saved session still authenticate as the account it was saved for?
 *
 * This is what makes a re-run free, and it is not only about speed. Signing in
 * is rate limited (the `/auth/*` class, 10 per 300 s, and it fails **closed**),
 * so a setup that signed in unconditionally started answering `429` after
 * three or four local iterations — measured, while writing this suite: the
 * limiter had `dcp:ratelimit:auth:<ip>` well over the limit and `/auth/login`
 * returned 429 instead of redirecting, which surfaces as "the mock identity
 * provider heading was not found" and looks nothing like its cause.
 *
 * A stale state (wiped Redis, expired session) answers 401 and is discarded,
 * so this cannot make the suite run against a session that no longer exists.
 */
async function stillSignedIn(baseURL: string, path: string, email: string): Promise<boolean> {
  if (!existsSync(path)) return false;
  const context = await request.newContext({ baseURL, storageState: path });
  try {
    const response = await context.get('/api/v1/auth/session');
    if (response.status() !== 200) return false;
    const { user } = (await response.json()) as { user: { email: string } };
    return user.email === email;
  } catch {
    return false;
  } finally {
    await context.dispose();
  }
}

async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0]?.use.baseURL ?? 'http://localhost:5173';
  await requireStack(baseURL);
  mkdirSync('e2e/.auth', { recursive: true });

  // Synthetic inventory is a precondition, not a side effect of having run the
  // smoke test earlier: the specs need a cloud account to register against and
  // a server to attempt a write on. Idempotent, and keyed on
  // (cloud_account_id, provider_resource_id), so a second run creates nothing.
  compose('exec', '-T', 'backend', 'python', '-m', 'app.inventory.seed');

  if (
    (await stillSignedIn(baseURL, STORAGE_STATE.admin, ACCOUNTS.admin.email)) &&
    (await stillSignedIn(baseURL, STORAGE_STATE.viewer, ACCOUNTS.viewer.email))
  ) {
    console.log('e2e: reusing the saved sessions; no login needed');
    return;
  }

  const browser = await chromium.launch();
  try {
    // --- 1. the administrator, who starts with no roles at all ------------
    const adminContext = await browser.newContext({ baseURL });
    const adminPage = await adminContext.newPage();
    await logIn(adminPage, 'admin');

    // --- 2. grant the first role through the application ------------------
    try {
      compose(
        'exec',
        '-T',
        'backend',
        'python',
        '-m',
        'app.admin.bootstrap',
        '--email',
        ACCOUNTS.admin.email,
      );
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (status !== BOOTSTRAP_ALREADY_DONE) throw error;
    }

    // --- 3. the Viewer, created by their own first login ------------------
    const viewerContext = await browser.newContext({ baseURL });
    const viewerPage = await viewerContext.newPage();
    await logIn(viewerPage, 'viewer');

    // --- 4. grant VIEWER, if they do not hold it already ------------------
    const headers = await writeHeaders(adminContext, baseURL);
    const users = await adminPage.request.get('/api/v1/admin/users?limit=100');
    if (users.status() !== 200) {
      throw new Error(
        `listing users answered ${String(users.status())} — did the bootstrap grant land? ` +
          (await users.text()),
      );
    }
    const { items } = (await users.json()) as { items: UserSummary[] };
    const viewer = items.find((user) => user.email === ACCOUNTS.viewer.email);
    if (viewer === undefined) {
      throw new Error(`${ACCOUNTS.viewer.email} is not in the user list after logging in`);
    }

    const detail = await adminPage.request.get(`/api/v1/admin/users/${viewer.id}`);
    const { roles: grants } = (await detail.json()) as { roles: RoleGrant[] };
    if (!grants.some((grant) => grant.role_name === 'VIEWER')) {
      const granted = await adminPage.request.post(`/api/v1/admin/users/${viewer.id}/roles`, {
        headers,
        data: { role_name: 'VIEWER', scope_definition: FULL_SCOPE },
      });
      if (granted.status() !== 201) {
        throw new Error(
          `granting VIEWER answered ${String(granted.status())}: ${await granted.text()}`,
        );
      }
    }

    await adminContext.storageState({ path: STORAGE_STATE.admin });
    await viewerContext.storageState({ path: STORAGE_STATE.viewer });
    await adminContext.close();
    await viewerContext.close();
  } finally {
    await browser.close();
  }
}

export default globalSetup;
