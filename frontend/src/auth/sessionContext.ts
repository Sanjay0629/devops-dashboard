import { createContext } from 'react';

import type { SessionState } from '@/api/session';

/**
 * Session context, in its own module.
 *
 * Separate from `SessionProvider.tsx` because a module that exports both a
 * component and a non-component breaks Vite's fast refresh — the whole module
 * reloads instead of the component, so a context value is recreated on every
 * edit and every consumer remounts.
 */

export interface SessionContextValue {
  state: SessionState;
  /** Re-read `/auth/session`. Used to turn a 401 into a fresh answer. */
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const SessionContext = createContext<SessionContextValue | null>(null);
