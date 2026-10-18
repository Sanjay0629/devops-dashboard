/**
 * Permission names, and the one place the UI asks "should I render this?".
 *
 * **None of this authorises anything.** It reads the advisory `permissions`
 * array from `/auth/session` (docs/API_CONTRACTS.md §12.4), which is
 * scope-blind: it can say `true` for an action the caller cannot perform on a
 * particular server, and the backend decides every request independently
 * (docs/SECURITY.md §6). Hiding a control the user cannot use is a usability
 * choice; the control that matters is the server-side check. A test asserts
 * exactly that by calling the API directly after the UI has hidden a button.
 *
 * The names are constants rather than string literals at the call sites so a
 * typo is a compile error instead of a silently hidden control — a mistyped
 * permission would make `can()` return false everywhere and the UI would look
 * like a permissions problem rather than a bug.
 */

import type { Session, SessionState } from '@/api/session';

/**
 * The permissions the frontend currently renders from. Kept to the ones in
 * use: adding an unused constant for every seeded permission would be a
 * catalogue the backend already owns.
 */
export const PERMISSIONS = {
  /** List, pre-create, edit and disable users; terminate their sessions. */
  adminUsers: 'admin:users',
  /** Grant and revoke role assignments; read the role catalogue. */
  adminRoles: 'admin:roles',
  /** Change system settings, including read-only mode. */
  adminSettings: 'admin:manage',
  /** Read host metrics (§15). Scope is checked by the backend per server. */
  metricsRead: 'metrics:read',
  /** Search application-source logs (§19.1). System sources need `logs:read:system`. */
  logsReadApplication: 'logs:read:application',
  /** Read alerts (§20.1, §20.2). Scope is checked by the backend, per alert. */
  alertRead: 'alert:read',
  /** Acknowledge and assign alerts (§20.3, §20.4). Never resolves one. */
  alertAcknowledge: 'alert:acknowledge',
  /** Read systemd service state (§16). Scope is checked per server by the backend. */
  serviceRead: 'service:read',
  /** Read server and application inventory. Held by every role. */
  serverRead: 'server:read',
  /** Register, edit and retire servers. Administrator only. */
  serverWrite: 'server:write',
  /** Register and edit applications and integrations. Administrator only. */
  adminIntegrations: 'admin:integrations',
} as const;

export type Permission = (typeof PERMISSIONS)[keyof typeof PERMISSIONS];

/** Does this session's advisory list contain the permission? */
export function can(session: Session, permission: Permission): boolean {
  return session.permissions.includes(permission);
}

/**
 * The same question against a `SessionState`, so callers do not have to narrow
 * first. Anything other than an authenticated session is `false` — including
 * `loading`, so a control never flashes into view before the answer arrives,
 * and `unreachable`, because "we could not ask" is not "yes".
 */
export function canRender(state: SessionState, permission: Permission): boolean {
  return state.kind === 'authenticated' && can(state.session, permission);
}

/**
 * Is the portal in incident read-only mode (docs/SECURITY.md §27)?
 *
 * Used to disable write controls and show the banner. The backend answers
 * `423 read_only_mode` regardless of what the UI does with this.
 */
export function isReadOnly(state: SessionState): boolean {
  return state.kind === 'authenticated' && state.session.read_only_mode;
}
