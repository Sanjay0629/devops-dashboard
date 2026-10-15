/**
 * A failed call, said plainly.
 *
 * `forbidden` and `not_found` are deliberately **not** merged: on a scoped
 * resource the backend answers `404` for a server outside the caller's scope
 * (API_CONTRACTS.md §2), because a `403` on a specific id confirms the id
 * exists. Telling the operator "you do not have permission" would therefore
 * be a guess about which of the two it was — and it would undo the reason the
 * backend chose `404` in the first place.
 *
 * `unreachable` is its own case for the same reason it is on the session: "we
 * could not ask" and "the answer was no" must not render the same way.
 *
 * Replaces `Failure` in `pages/inventory/states.tsx`.
 */

import type { Result } from '@/api/inventory';

/** Every `Result` variant except success. Exported for callers that hold one. */
export type Failed<T> = Exclude<Result<T>, { kind: 'ok' }>;

export function ErrorState<T>({
  result,
  onRetry,
}: {
  result: Failed<T>;
  /** Offered only where retrying is meaningful — a transport failure. */
  onRetry?: () => void;
}): React.JSX.Element {
  const message =
    result.kind === 'forbidden'
      ? 'You do not have permission to view this.'
      : result.kind === 'not_found'
        ? 'Not found. It may not exist, or may be outside your access scope.'
        : result.kind === 'read_only'
          ? 'The portal is in read-only mode. State-changing actions are disabled.'
          : result.kind === 'conflict' || result.kind === 'invalid'
            ? result.message
            : `Could not reach the backend: ${result.reason}`;

  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded border px-4 py-3 text-sm"
      style={{ borderColor: 'var(--c-critical)', color: 'var(--c-critical)' }}
    >
      <span>{message}</span>
      {onRetry !== undefined && result.kind === 'unreachable' && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded border px-2 py-0.5 text-xs"
          style={{ borderColor: 'var(--c-critical)' }}
        >
          Try again
        </button>
      )}
    </div>
  );
}
