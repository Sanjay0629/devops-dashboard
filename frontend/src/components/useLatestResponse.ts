/**
 * Run a loader and keep **only the newest answer**.
 *
 * Added by M1.7, for a defect the first E2E run found rather than one anybody
 * reasoned about. Five views shared this shape:
 *
 * ```ts
 * const load = useCallback(async () => {
 *   setResult(await listServers({ q, cursor }));
 * }, [q, cursor]);
 * ```
 *
 * Nothing there ties a response to the request that asked for it. Change a
 * filter and two requests are in flight; whichever *resolves* last wins, and
 * the unfiltered one is the slower of the two precisely because it returns
 * more rows. So the list could settle on the answer to a question the
 * operator had already changed, and stay there.
 *
 * It was not theoretical. `§12.2` of the E2E suite failed roughly two runs in
 * four — typing a hostname into the server list's search box and getting the
 * whole inventory back — and it grew *more* likely as the table grew, because
 * the unfiltered query got slower. The dashboard is worse: it refreshes on a
 * timer, so overlapping requests there are certain rather than possible.
 *
 * The fix is a ticket per request: a response whose ticket is no longer the
 * current one is discarded. Deliberately **not** `AbortController` —
 * cancelling the HTTP request would be a second mechanism to get wrong, and
 * the bug is not that the old request happens, it is that its answer is
 * believed.
 *
 * `value` is never cleared, so a caller keeps showing the previous answer
 * while the next one is in flight; `loading` is there for callers that would
 * rather show a loading state than a stale one. Having both is what let every
 * existing view keep its current behaviour exactly.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface LatestResponse<T> {
  /** The newest settled answer, or `null` before the first one arrives. */
  value: T | null;
  /** Is a request in flight? */
  loading: boolean;
  /** Run the loader again. Supersedes anything already in flight. */
  reload: () => Promise<void>;
}

/**
 * `deps` are the loader's dependencies, exactly as for `useEffect`: a change
 * re-issues the request, and the loader's own identity is ignored.
 *
 * Taking `deps` rather than a pre-memoised callback is deliberate, and the
 * hook's own test is why. An earlier version took just the loader and relied
 * on the caller having wrapped it in `useCallback` — which every page did, so
 * it worked. The first test written against it passed an inline arrow,
 * creating a new identity per render, and the hook re-issued on every render
 * for ever: the test timed out instead of failing. An API that is correct
 * only when the caller remembers something is the wrong API, so the
 * dependency list moved inside.
 */
export function useLatestResponse<T>(
  load: () => Promise<T>,
  deps: readonly unknown[],
): LatestResponse<T> {
  const [value, setValue] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  //: Monotonic, and read through a ref so a stale closure cannot compare
  //: against an old value.
  const latest = useRef(0);
  //: The newest loader, so `reload` can depend on `deps` alone without
  //: closing over a stale one.
  const loader = useRef(load);
  loader.current = load;

  const reload = useCallback(async () => {
    const ticket = latest.current + 1;
    latest.current = ticket;
    setLoading(true);
    const next = await loader.current();
    // Superseded: a newer request has already been issued, so this answer is
    // about a question nobody is asking any more. Dropped, including its
    // loading state — the newer request owns that now.
    if (latest.current !== ticket) return;
    setValue(next);
    setLoading(false);
    // `deps` is the caller's own dependency list, which is the whole point of
    // the signature, so the linter cannot verify it and should not try.
    // `loader` is a ref and needs no entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { value, loading, reload };
}
