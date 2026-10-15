/**
 * Paging controls for a cursor-paginated list (API_CONTRACTS.md §5,
 * PROGRESS issue 46).
 *
 * Honest about what the API offers: you can go back to a page you have
 * actually been to, and the count reads "page 3", never "page 3 of 9" —
 * `page.total` is `null` by §5, and inventing a denominator would be
 * inventing the number §5 declines to give.
 *
 * Used by the server list, the application list and the dashboard status
 * table. Built once, because three hand-rolled cursor stacks is three places
 * for "Previous" to land on the wrong page.
 */

import type { CursorPaging } from '@/components/useCursorPaging';

export function Pager({
  paging,
  nextCursor,
  hasMore,
  label,
}: {
  paging: CursorPaging;
  nextCursor: string | null;
  hasMore: boolean;
  /** What is being paged, for the navigation's accessible name. */
  label: string;
}): React.JSX.Element | null {
  // Nothing to page: one page, and the operator has never left it.
  if (paging.onFirstPage && !hasMore) return null;

  return (
    <nav
      aria-label={`${label} pagination`}
      className="mt-3 flex items-center gap-3 text-sm"
      style={{ color: 'var(--c-text-muted)' }}
    >
      <button
        type="button"
        onClick={paging.goPrevious}
        disabled={paging.onFirstPage}
        className="rounded border px-3 py-1 disabled:opacity-50"
        style={{ borderColor: 'var(--c-border)' }}
      >
        Previous
      </button>
      {/* No "of N": §5 returns `total: null` on a cursor page, on purpose. */}
      <span aria-live="polite">Page {paging.pageNumber}</span>
      <button
        type="button"
        onClick={() => {
          if (nextCursor !== null) paging.goNext(nextCursor);
        }}
        disabled={!hasMore || nextCursor === null}
        className="rounded border px-3 py-1 disabled:opacity-50"
        style={{ borderColor: 'var(--c-border)' }}
      >
        Next
      </button>
    </nav>
  );
}
