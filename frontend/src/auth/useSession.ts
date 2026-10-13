import { useContext } from 'react';

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
 * **For rendering only.** This is the frontend's copy of a list the backend
 * calls advisory (docs/API_CONTRACTS.md §12.4), and it is scope-blind — it can
 * say true for an action the caller cannot perform on a particular target.
 * Hiding a control the user cannot use is a usability choice; the control that
 * matters is the server-side check on every request (docs/SECURITY.md §6).
 */
export function useCanRender(permission: string): boolean {
  const { state } = useSession();
  return state.kind === 'authenticated' && state.session.permissions.includes(permission);
}
