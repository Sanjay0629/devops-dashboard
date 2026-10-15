/**
 * Application list (§18.1).
 *
 * Reads need `server:read`, which every role holds; writing needs
 * `admin:integrations`, which only an Administrator holds — so there is no
 * "Register application" control here for most people. There is no
 * registration *form* at all yet either: §18.2's body carries
 * `health_check_config`, whose URLs are SSRF-allowlisted at write time
 * (docs/SECURITY.md §19), and a form that offers a URL box against an empty
 * allowlist would only ever produce a rejection. Applications are registered
 * through the API until the allowlist is configurable in the UI.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { listApplications } from '@/api/inventory';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { Pager } from '@/components/Pager';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';
import { ErrorState } from '@/components/ErrorState';
import { StatusBadge } from '@/components/StatusBadge';

export function ApplicationList(): React.JSX.Element {
  const [q, setQ] = useState('');
  const paging = useCursorPaging();
  const { cursor, reset } = paging;

  // See `useLatestResponse`: the search box issues a request per change, and
  // the answers do not necessarily arrive in the order they were asked for.
  const {
    value: result,
    loading,
    reload: load,
  } = useLatestResponse(
    () => listApplications({ q: q.trim() || undefined, cursor: cursor ?? undefined }),
    [q, cursor],
  );

  // A search change is a different collection, so page 1.
  useEffect(() => {
    reset();
  }, [q, reset]);

  return (
    <section aria-labelledby="applications-heading">
      <h1 id="applications-heading" className="mb-4 text-xl font-semibold">
        Applications
      </h1>

      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void load();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Search
          </span>
          <input
            type="search"
            value={q}
            placeholder="name or slug"
            onChange={(event) => {
              setQ(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          />
        </label>
        <button
          type="submit"
          className="rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Apply
        </button>
      </form>

      {loading && <LoadingState what="applications" />}
      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <EmptyState message="No applications are registered." />
      )}
      {result !== null && result.kind !== 'ok' && <ErrorState result={result} />}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead>
            <tr style={{ color: 'var(--c-text-muted)' }}>
              <th scope="col" className="py-2">
                Name
              </th>
              <th scope="col">Slug</th>
              <th scope="col">Owner</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {result.value.items.map((application) => (
              <tr
                key={application.id}
                className="border-t"
                style={{ borderColor: 'var(--c-border)' }}
              >
                <td className="py-2">
                  <Link to={`/applications/${application.id}`} className="underline">
                    {application.name}
                  </Link>
                </td>
                <td>{application.slug}</td>
                <td
                  style={
                    application.owner_team === null ? { color: 'var(--c-text-muted)' } : undefined
                  }
                >
                  {application.owner_team ?? 'Not recorded'}
                </td>
                <td>
                  <StatusBadge value={application.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {result?.kind === 'ok' && (
        <Pager
          paging={paging}
          nextCursor={result.value.page.next_cursor}
          hasMore={result.value.page.has_more}
          label="Applications"
        />
      )}
    </section>
  );
}
