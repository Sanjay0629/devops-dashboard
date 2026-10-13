import { useContext } from 'react';

import { canRender, isReadOnly, type Permission } from '@/auth/permissions';
import { SessionContext, type SessionContextValue } from '@/auth/sessionContext';

/**
 * Read the session context.
 *
 * Throws outside a provider rather than returning a default. A hook that
 * quietly reported "anonymous" when it was simply mounted in the wrong place
 * would turn a wiring mistake into a login loop that looks like a backend
 * problem.
 */
export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error('useSession must be used inside a <SessionProvider>');
  }
  return value;
}

/**
 * Does the session's advisory permission list contain this permission?
 *
 * **For rendering only** — the reasoning, and the permission names, live in
 * `@/auth/permissions`. This hook is the React-flavoured way to ask it, so
 * there is one answer rather than two.
 */
export function useCanRender(permission: Permission): boolean {
  const { state } = useSession();
  return canRender(state, permission);
}

/** Is the portal in incident read-only mode? For disabling controls only. */
export function useIsReadOnly(): boolean {
  const { state } = useSession();
  return isReadOnly(state);
}
