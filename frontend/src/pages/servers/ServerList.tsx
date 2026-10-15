/**
 * Server inventory list with filters (§14.1).
 *
 * Filters are sent to the backend rather than applied to a fetched page:
 * the collection is scope-filtered and cursor-paged server-side, so a
 * client-side filter would be filtering one page of someone else's answer.
 *
 * "Register server" is hidden without `server:write` and disabled in
 * read-only mode. Both are **usability**, not control — the backend answers
 * `403` and `423` on its own account (docs/SECURITY.md §6).
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { listServers } from '@/api/inventory';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly } from '@/auth/useSession';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { Pager } from '@/components/Pager';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';
import { ErrorState } from '@/components/ErrorState';
import { StatusBadge } from '@/components/StatusBadge';

const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];
const STATUSES = ['PENDING', 'MANAGED', 'STALE', 'UNREACHABLE', 'RETIRED'];

export function ServerList(): React.JSX.Element {
  const [environment, setEnvironment] = useState('');
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const mayWrite = useCanRender(PERMISSIONS.serverWrite);
  const readOnly = useIsReadOnly();
  const paging = useCursorPaging();
  const { cursor, reset } = paging;

  // Through `useLatestResponse`, so a slower unfiltered request cannot land
  // after a faster filtered one and leave the list showing the wrong answer.
  const {
    value: result,
    loading,
    reload: load,
  } = useLatestResponse(
    () =>
      listServers({
        environment,
        status,
        q: q.trim() || undefined,
        cursor: cursor ?? undefined,
      }),
    [environment, status, q, cursor],
  );

  // A filter change is a different collection, so page 1. Keyed on the filter
  // values and not on `load`, which also changes with the cursor — resetting
  // there would send "Next" straight back to the first page.
  useEffect(() => {
    reset();
  }, [environment, status, q, reset]);

  return (
    <section aria-labelledby="servers-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 id="servers-heading" className="text-xl font-semibold">
          Servers
        </h1>
        {mayWrite && (
          <Link
            to="/servers/new"
            aria-disabled={readOnly}
            className="rounded border px-3 py-1 text-sm"
            style={{
              borderColor: 'var(--c-border)',
              opacity: readOnly ? 0.5 : 1,
              pointerEvents: readOnly ? 'none' : undefined,
            }}
          >
            Register server
          </Link>
        )}
      </header>

      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void load();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Environment
          </span>
          <select
            value={environment}
            onChange={(event) => {
              setEnvironment(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">All</option>
            {ENVIRONMENTS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Status
          </span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">All</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Search
          </span>
          <input
            type="search"
            value={q}
            placeholder="hostname, name or resource id"
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

      {loading && <LoadingState what="servers" />}
      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <EmptyState message="No servers match these filters, or none are within your access scope." />
      )}
      {result !== null && result.kind !== 'ok' && <ErrorState result={result} />}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead>
            <tr style={{ color: 'var(--c-text-muted)' }}>
              <th scope="col" className="py-2">
                Name
              </th>
              <th scope="col">Hostname</th>
              <th scope="col">Environment</th>
              <th scope="col">Status</th>
              <th scope="col">Owner</th>
            </tr>
          </thead>
          <tbody>
            {result.value.items.map((server) => (
              <tr key={server.id} className="border-t" style={{ borderColor: 'var(--c-border)' }}>
                <td className="py-2">
                  <Link to={`/servers/${server.id}`} className="underline">
                    {server.display_name}
                  </Link>
                </td>
                <td>{server.hostname}</td>
                <td>{server.environment}</td>
                <td>
                  <StatusBadge value={server.status} />
                </td>
                {/* An owner that was never recorded is unknown, not "—". */}
                <td
                  style={server.owner_team === null ? { color: 'var(--c-text-muted)' } : undefined}
                >
                  {server.owner_team ?? 'Not recorded'}
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
          label="Servers"
        />
      )}
    </section>
  );
}
