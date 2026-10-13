import { useCallback, useEffect, useMemo, useState } from 'react';

import { fetchSession, logout as postLogout, type SessionState } from '@/api/session';
import { SessionContext, type SessionContextValue } from '@/auth/sessionContext';

/**
 * Session context.
 *
 * One fetch of `/auth/session` on mount, re-run on demand. Deliberately not
 * polled: permissions are re-resolved server-side on every request (AD-03), so
 * a stale copy here can only ever make the UI render a control that the
 * backend then refuses — never the other way round.
 *
 * `refresh` exists so a `401` from any later API call can be turned into a
 * re-check rather than a guess: the component that got the `401` asks the
 * provider to find out what the real state is now, instead of assuming it was
 * logged out (which would be wrong during a backend outage).
 */

export function SessionProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const [state, setState] = useState<SessionState>({ kind: 'loading' });

  const refresh = useCallback(async () => {
    setState(await fetchSession());
  }, []);

  const signOut = useCallback(async () => {
    const confirmed = await postLogout();
    if (confirmed) {
      // Trust the server's confirmation, not a local guess.
      setState({ kind: 'anonymous' });
      return;
    }
    // The logout was not confirmed, so the session may well still be live.
    // Re-read rather than showing a signed-out UI over a working session.
    await refresh();
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<SessionContextValue>(
    () => ({ state, refresh, signOut }),
    [state, refresh, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
