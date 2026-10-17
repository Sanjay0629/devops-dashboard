/**
 * systemd services: the cross-server failed-service view (§16.1) and the list
 * for one server (§16.2).
 *
 * One component for both, because they are one table with one extra filter:
 * `serverId` fixes the server and hides the column that would repeat it.
 *
 * The state filter is the point of the page ("which services have failed?",
 * PRD §1) and it is **sent to the backend**. It selects on the state a row
 * *shows*, which is host-aware — a unit cached `FAILED` on a host that has since
 * gone quiet is not in `FAILED` and is in `UNKNOWN`, and only the backend knows
 * that. Filtering a fetched page here would filter one page of its answer.
 *
 * Every row carries its state **and** its evidence (`ServiceStatus`): when it
 * was collected, or why it was not. A host that is unreachable renders as
 * unknown, never as a column of inactive services.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { listServices, type ServiceState } from '@/api/services';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { Pager } from '@/components/Pager';
import { ServiceStatus } from '@/components/ServiceStatus';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';

const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];
const STATES: ServiceState[] = [
  'FAILED',
  'UNKNOWN',
  'UNCOLLECTED',
  'ACTIVE',
  'INACTIVE',
  'ACTIVATING',
  'DEACTIVATING',
  'UNSUPPORTED',
];

const STATE_LABEL: Record<ServiceState, string> = {
  FAILED: 'Failed',
  UNKNOWN: 'Unknown',
  UNCOLLECTED: 'Not collected',
  ACTIVE: 'Active',
  INACTIVE: 'Inactive',
  ACTIVATING: 'Activating',
  DEACTIVATING: 'Deactivating',
  UNSUPPORTED: 'Not supported',
};

export function ServiceList({
  serverId,
  serverName,
}: {
  /** Fixes the server (§16.2). Absent: every server the caller may read (§16.1). */
  serverId?: string;
  serverName?: string;
}): React.JSX.Element {
  const [state, setState] = useState('');
  const [environment, setEnvironment] = useState('');
  const [q, setQ] = useState('');
  const paging = useCursorPaging();
  const { cursor, reset } = paging;

  const {
    value: result,
    loading,
    reload: load,
  } = useLatestResponse(
    () =>
      listServices(
        {
          state,
          // §16.2 has no environment filter: the path already fixes the server.
          environment: serverId === undefined ? environment : undefined,
          q: q.trim() || undefined,
          cursor: cursor ?? undefined,
        },
        serverId,
      ),
    [state, environment, q, cursor, serverId],
  );

  useEffect(() => {
    reset();
  }, [state, environment, q, serverId, reset]);

  const heading = serverName === undefined ? 'Services' : `Services on ${serverName}`;

  return (
    <section aria-labelledby="services-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 id="services-heading" className="text-xl font-semibold">
          {heading}
        </h1>
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
            State
          </span>
          <select
            value={state}
            onChange={(event) => {
              setState(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">All</option>
            {STATES.map((value) => (
              <option key={value} value={value}>
                {STATE_LABEL[value]}
              </option>
            ))}
          </select>
        </label>

        {serverId === undefined && (
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
        )}

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Search
          </span>
          <input
            type="search"
            value={q}
            placeholder="unit or display name"
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

      {loading && <LoadingState what="services" />}
      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <EmptyState
          message={
            state === '' && q.trim() === '' && environment === ''
              ? 'No services are registered here, or none are within your access scope.'
              : 'No services match these filters.'
          }
        />
      )}
      {result !== null && result.kind !== 'ok' && (
        <ErrorState result={result} onRetry={() => void load()} />
      )}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead>
            <tr style={{ color: 'var(--c-text-muted)' }}>
              <th scope="col" className="py-2">
                Service
              </th>
              {serverId === undefined && <th scope="col">Server</th>}
              <th scope="col">Environment</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {result.value.items.map((item) => (
              <tr
                key={item.service_id}
                className="border-t align-top"
                style={{ borderColor: 'var(--c-border)' }}
              >
                <td className="py-2">
                  <Link
                    to={`/servers/${item.server_id}/services/${encodeURIComponent(item.service_name)}`}
                    className="underline"
                  >
                    {item.service_name}
                  </Link>
                  {item.display_name !== null && (
                    <p className="text-xs" style={{ color: 'var(--c-text-muted)' }}>
                      {item.display_name}
                    </p>
                  )}
                </td>
                {serverId === undefined && (
                  <td>
                    <Link to={`/servers/${item.server_id}/services`} className="underline">
                      {item.server_display_name}
                    </Link>
                  </td>
                )}
                <td>{item.environment}</td>
                <td className="py-2">
                  <ServiceStatus item={item} />
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
          label="Services"
        />
      )}
    </section>
  );
}
