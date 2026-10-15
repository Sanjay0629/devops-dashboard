/**
 * The visited-cursor stack behind `Pager` (API_CONTRACTS.md §5).
 *
 * Separate from the component for the same reason `useSession` is separate
 * from `SessionProvider`: a module that exports both a hook and a component
 * breaks fast refresh.
 *
 * The backend pages by **keyset**, not offset: it hands back a `next_cursor`
 * and nothing else. That gives "next" for free and makes "previous"
 * impossible on its own — there is no backwards cursor and no page number to
 * subtract from, which is the price of a page window that cannot shift under
 * a concurrent insert. So the stack lives here, remembering the cursor that
 * opened each page visited.
 */

import { useCallback, useState } from 'react';

export interface CursorPaging {
  /** The cursor for the page now shown; `null` on the first page. */
  cursor: string | null;
  /** 1-based, for display only. Never "of N" — §5 keeps `total` null. */
  pageNumber: number;
  onFirstPage: boolean;
  /** Move to the page `next_cursor` opens. */
  goNext: (nextCursor: string) => void;
  goPrevious: () => void;
  /** Back to page 1 — call whenever a filter changes. */
  reset: () => void;
}

/**
 * The visited-cursor stack.
 *
 * `[null]` is page 1: the first page is the one fetched with no cursor, and
 * holding it in the stack keeps "previous from page 2" the same operation as
 * every other previous.
 */
export function useCursorPaging(): CursorPaging {
  const [stack, setStack] = useState<(string | null)[]>([null]);

  const goNext = useCallback((nextCursor: string) => {
    setStack((current) => [...current, nextCursor]);
  }, []);
  // Guarded rather than trusting the button to be disabled: a page that
  // popped the last entry would fetch with `undefined` and silently show
  // page 1 again.
  const goPrevious = useCallback(() => {
    setStack((current) => (current.length > 1 ? current.slice(0, -1) : current));
  }, []);
  const reset = useCallback(() => {
    setStack([null]);
  }, []);

  return {
    cursor: stack[stack.length - 1] ?? null,
    pageNumber: stack.length,
    onFirstPage: stack.length === 1,
    goNext,
    goPrevious,
    reset,
  };
}
