import { describe, expect, it } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { PERMISSIONS, can, canRender, isReadOnly } from '@/auth/permissions';

/**
 * The advisory permission read.
 *
 * The important case is the last one: this module answers `true` for a
 * permission the backend will still refuse on a specific target, because the
 * array it reads is scope-blind. The test states it so nobody later "fixes"
 * the frontend into believing it is an authorisation layer — the backend
 * counterpart is
 * `tests/integration/test_admin_roles.py::test_the_permissions_array_is_advisory`.
 */

function session(overrides: Partial<Session> = {}): Session {
  return {
    user: {
      id: '00000000-0000-0000-0000-000000000001',
      email: 'operator@example.invalid',
      display_name: 'Example Operator',
      status: 'ACTIVE',
    },
    roles: [{ name: 'ADMINISTRATOR', scope: { environment: ['staging'] } }],
    permissions: ['dashboard:read', 'admin:users'],
    session: { expires_at: '2026-10-13T20:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: false,
    ...overrides,
  };
}

describe('can', () => {
  it('is true for a permission in the advisory list', () => {
    expect(can(session(), PERMISSIONS.adminUsers)).toBe(true);
  });

  it('is false for one that is absent', () => {
    expect(can(session(), PERMISSIONS.adminRoles)).toBe(false);
  });
});

describe('canRender', () => {
  it('is false while loading, so a control never flashes into view', () => {
    expect(canRender({ kind: 'loading' }, PERMISSIONS.adminUsers)).toBe(false);
  });

  it('is false when anonymous', () => {
    expect(canRender({ kind: 'anonymous' }, PERMISSIONS.adminUsers)).toBe(false);
  });

  it('is false when the backend is unreachable — "could not ask" is not "yes"', () => {
    const state: SessionState = { kind: 'unreachable', reason: 'offline' };
    expect(canRender(state, PERMISSIONS.adminUsers)).toBe(false);
  });

  it('is true for an authenticated session holding the permission', () => {
    expect(canRender({ kind: 'authenticated', session: session() }, PERMISSIONS.adminUsers)).toBe(
      true,
    );
  });

  it('is scope-blind, which is why it carries no authority', () => {
    // The grant is scoped to staging only. The array still lists the
    // permission, so this returns true — and a production request would be
    // refused server-side. Rendering a control the server refuses is the
    // documented trade-off (docs/API_CONTRACTS.md §12.4); the reverse would be
    // a security hole.
    const staging = session({
      roles: [{ name: 'ADMINISTRATOR', scope: { environment: ['staging'] } }],
    });
    expect(can(staging, PERMISSIONS.adminUsers)).toBe(true);
  });
});

describe('isReadOnly', () => {
  it('reflects the flag from the session response', () => {
    expect(isReadOnly({ kind: 'authenticated', session: session() })).toBe(false);
    expect(isReadOnly({ kind: 'authenticated', session: session({ read_only_mode: true }) })).toBe(
      true,
    );
  });

  it('is false when there is no session to read it from', () => {
    expect(isReadOnly({ kind: 'loading' })).toBe(false);
  });
});
