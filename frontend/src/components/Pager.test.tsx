import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { Pager } from '@/components/Pager';
import { useCursorPaging } from '@/components/useCursorPaging';

/**
 * The cursor stack (PROGRESS issue 46).
 *
 * Worth a test of its own because "Previous" is the part a keyset API does
 * not give you: it is reconstructed here from the cursors already visited,
 * and getting it wrong sends the operator to the wrong page rather than
 * failing visibly.
 */

/** A list whose pages are driven by the hook, as the real pages drive it. */
function Harness({ pages }: { pages: string[] }): React.JSX.Element {
  const paging = useCursorPaging();
  const [, force] = useState(0);
  // Index the cursor into the fake page set: `null` is page 1.
  const index = paging.cursor === null ? 0 : pages.indexOf(paging.cursor);
  const hasMore = index < pages.length - 1;

  return (
    <div>
      <p>Showing {pages[index]}</p>
      <Pager
        paging={{
          ...paging,
          goNext: (cursor) => {
            paging.goNext(cursor);
            force((n) => n + 1);
          },
        }}
        nextCursor={hasMore ? (pages[index + 1] ?? null) : null}
        hasMore={hasMore}
        label="Things"
      />
    </div>
  );
}

const PAGES = ['page-1', 'page-2', 'page-3'];

describe('Pager', () => {
  it('is absent when there is only one page', () => {
    render(<Harness pages={['page-1']} />);
    expect(screen.queryByRole('navigation')).toBeNull();
  });

  it('walks forward and back to the page it came from', async () => {
    render(<Harness pages={PAGES} />);
    expect(screen.getByText('Showing page-1')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Showing page-2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Showing page-3')).toBeInTheDocument();

    // The assertion that matters: back to 2, not back to 1. A stack that
    // only remembered the first cursor would pass a "went backwards" check
    // and still land in the wrong place.
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(screen.getByText('Showing page-2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(screen.getByText('Showing page-1')).toBeInTheDocument();
  });

  it('cannot go back from the first page', async () => {
    render(<Harness pages={PAGES} />);
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled();
  });

  it('cannot go forward from the last page', async () => {
    render(<Harness pages={PAGES} />);
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  it('counts the page but never claims a total', () => {
    // §5 returns `total: null` on a cursor page on purpose, so there is no
    // denominator to show and inventing one would be inventing the number
    // the contract declines to give.
    render(<Harness pages={PAGES} />);
    expect(screen.getByText('Page 1')).toBeInTheDocument();
    expect(screen.queryByText(/of \d/)).toBeNull();
  });
});
