/**
 * "There is nothing to draw", and "we have not asked yet".
 *
 * Two components in one file because they are the two halves of the same
 * decision and a page has to pick one — which is exactly why they are
 * **separate components** rather than one with a flag. A spinner that
 * doubles as an empty list tells the operator their inventory is empty while
 * the request is still in flight, and that is the single most common way a
 * dashboard lies.
 *
 * Replaces `Empty` and `Loading` in `pages/inventory/states.tsx`.
 */

export function LoadingState({ what }: { what: string }): React.JSX.Element {
  return (
    // `role="status"` so a screen reader is told the view is working rather
    // than being handed a silently empty region.
    <p role="status" style={{ color: 'var(--c-text-muted)' }}>
      Loading {what}…
    </p>
  );
}

export function EmptyState({ message }: { message: string }): React.JSX.Element {
  return (
    <p
      className="rounded border px-4 py-6 text-center text-sm"
      style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
    >
      {message}
    </p>
  );
}
