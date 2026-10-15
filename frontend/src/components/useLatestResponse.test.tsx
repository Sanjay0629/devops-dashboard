import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useLatestResponse } from '@/components/useLatestResponse';

/**
 * The one behaviour this hook exists for: an answer that is no longer the
 * newest is discarded.
 *
 * Written against the real failure. `§12.2` of the E2E suite failed about two
 * runs in four because the server list issued an unfiltered request and then
 * a filtered one, and the unfiltered answer — slower, because it returns more
 * rows — landed last and won. The second test below resolves the two requests
 * in exactly that order and fails against the previous implementation.
 */

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe('useLatestResponse', () => {
  it('reports loading until the first answer arrives', async () => {
    const pending = deferred<string>();
    const { result } = renderHook(() => useLatestResponse(() => pending.promise, []));

    expect(result.current.loading).toBe(true);
    // `null` is "we have not been told yet", never an empty answer — the
    // distinction the whole honest-states rule rests on (PRD §4.2).
    expect(result.current.value).toBeNull();

    await act(async () => {
      pending.resolve('first');
      await pending.promise;
    });
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.value).toBe('first');
  });

  it('discards a superseded answer that resolves last', async () => {
    const issued: Deferred<string>[] = [];
    const load = () => {
      const next = deferred<string>();
      issued.push(next);
      return next.promise;
    };
    const { result } = renderHook(() => useLatestResponse(load, []));
    await waitFor(() => {
      expect(issued).toHaveLength(1);
    });

    // A second request while the first is still in flight — a filter change.
    // Deliberately not awaited: the point is that it is still outstanding.
    // The microtask turn is what lets it get as far as issuing.
    await act(async () => {
      void result.current.reload();
      await Promise.resolve();
    });
    expect(issued).toHaveLength(2);

    // The newer request answers first.
    await act(async () => {
      issued[1]?.resolve('filtered');
      await issued[1]?.promise;
    });
    await waitFor(() => {
      expect(result.current.value).toBe('filtered');
    });

    // Then the older, slower one answers. This is the line that mattered:
    // before the hook, this assignment won and the view showed the answer to
    // a question the operator had already changed.
    await act(async () => {
      issued[0]?.resolve('unfiltered');
      await issued[0]?.promise;
    });
    expect(result.current.value).toBe('filtered');
    expect(result.current.loading).toBe(false);
  });

  it('runs again when a dependency changes', async () => {
    const calls: string[] = [];
    const { rerender } = renderHook(
      ({ term }: { term: string }) =>
        useLatestResponse(() => {
          calls.push(term);
          return Promise.resolve(term);
        }, [term]),
      { initialProps: { term: 'first' } },
    );
    await waitFor(() => {
      expect(calls).toContain('first');
    });

    // An inline closure per render is exactly what the pages pass, so the
    // dependency list — not the loader's identity — has to drive re-issuing.
    rerender({ term: 'second' });
    await waitFor(() => {
      expect(calls).toContain('second');
    });
  });
});
