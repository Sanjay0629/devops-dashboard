/**
 * Deployment history (§21.1; PRD §4.10, §8).
 *
 * What this page owes the person reading it, in order of how badly it can go wrong:
 *
 * 1. **A result is shown as what backs it, and an unknown outcome says so.** A success reads
 *    "pipeline succeeded" and nothing grander; a failure whose build may still exist says to
 *    check Jenkins; nothing is called "healthy" or "verified".
 * 2. **An empty list is not "nothing happened".** The collection is scope-filtered on the server,
 *    so the page says "none match, or none are within your access scope" and never a bare count.
 * 3. **Filters go to the backend.** A client-side filter would be filtering one page of someone
 *    else's answer, and the window (7 days by default, 90 at most) is the backend's to apply.
 * 4. **Every cell is a text node.** Nothing here is markup from a request, and no Jenkins URL is
 *    ever shown: a deployment names a job path and a build number.
 *
 * Nothing on this page authorises anything; it is a view of what the backend chose to return.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  EMPTY_FILTERS,
  STATUSES,
  UNKNOWN_OUTCOME,
  failureText,
  listDeployments,
  type DeploymentFilters,
  type DeploymentItem,
  type DeploymentStatus,
} from '@/api/deploy';
import { toInstant } from '@/api/history';
import { useSession } from '@/auth/useSession';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { Pager } from '@/components/Pager';
import { StatusBadge } from '@/components/StatusBadge';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';

const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];

function stamp(value: string | null): React.JSX.Element {
  if (value === null) return <span style={{ color: 'var(--c-text-muted)' }}>—</span>;
  return (
    <time dateTime={value} title={value}>
      {new Date(value).toLocaleString()}
    </time>
  );
}

/** The outcome, said carefully: what a success rests on, doubt where a build may exist. */
function ResultCell({ item }: { item: DeploymentItem }): React.JSX.Element {
  switch (item.execution_status) {
    case 'SUCCEEDED':
      return <>Pipeline succeeded</>;
    case 'FAILED':
      if (item.failure_reason != null && UNKNOWN_OUTCOME.includes(item.failure_reason)) {
        return <>Outcome unknown — check Jenkins before requesting again</>;
      }
      return <>{failureText(item.failure_reason) || 'Failed'}</>;
    case 'CANCELLED':
      return <span style={{ color: 'var(--c-text-muted)' }}>Cancelled</span>;
    case 'ROLLED_BACK':
      return <span style={{ color: 'var(--c-text-muted)' }}>Rolled back</span>;
    default:
      return <span style={{ color: 'var(--c-text-muted)' }}>Not finished</span>;
  }
}

export function DeploymentList(): React.JSX.Element {
  const { state } = useSession();
  const me = state.kind === 'authenticated' ? state.session.user.id : '';

  const [status, setStatus] = useState('');
  const [environment, setEnvironment] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [mine, setMine] = useState(false);

  const build = (): DeploymentFilters => ({
    ...EMPTY_FILTERS,
    statuses: status === '' ? [] : [status as DeploymentStatus],
    environments: environment === '' ? [] : [environment],
    requestedBy: mine ? me : '',
    from: toInstant(from),
    to: toInstant(to),
  });
  const [applied, setApplied] = useState<DeploymentFilters>(build);

  const paging = useCursorPaging();
  const { cursor, reset } = paging;
  const key = JSON.stringify(applied);

  const {
    value: result,
    loading,
    reload,
  } = useLatestResponse(
    () => listDeployments({ ...applied, cursor: cursor ?? undefined }),
    [key, cursor],
  );

  useEffect(() => {
    reset();
  }, [key, reset]);

  const select = (
    label: string,
    value: string,
    set: (next: string) => void,
    options: { value: string; label: string }[],
  ): React.JSX.Element => (
    <label className="text-sm">
      <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => {
          set(event.target.value);
        }}
        className="rounded border px-2 py-1"
        style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );

  const date = (label: string, value: string, set: (next: string) => void): React.JSX.Element => (
    <label className="text-sm">
      <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
        {label}
      </span>
      <input
        type="datetime-local"
        value={value}
        onChange={(event) => {
          set(event.target.value);
        }}
        className="rounded border px-2 py-1"
        style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
      />
    </label>
  );

  return (
    <section aria-labelledby="deployments-heading">
      <header className="mb-4">
        <h1 id="deployments-heading" className="text-xl font-semibold">
          Deployments
        </h1>
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          What was requested, by whom, and what happened. The last 7 days unless you choose a range
          (at most 90 days). You see your own requests and those in your access scope. To deploy,
          start from{' '}
          <Link to="/deployment-targets" className="underline">
            Deployment targets
          </Link>
          .
        </p>
      </header>

      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(build());
        }}
      >
        {select('Status', status, setStatus, [
          { value: '', label: 'Any' },
          ...STATUSES.map((value) => ({ value, label: value.toLowerCase().replace('_', ' ') })),
        ])}
        {select('Environment', environment, setEnvironment, [
          { value: '', label: 'Any' },
          ...ENVIRONMENTS.map((value) => ({ value, label: value })),
        ])}
        {date('From', from, setFrom)}
        {date('To', to, setTo)}
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={mine}
            onChange={(event) => {
              setMine(event.target.checked);
            }}
          />
          Only my requests
        </label>
        <button
          type="submit"
          className="rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Apply
        </button>
      </form>

      {loading && <LoadingState what="deployments" />}
      {result !== null && result.kind !== 'ok' && (
        <ErrorState
          result={result}
          onRetry={() => {
            void reload();
          }}
        />
      )}

      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <EmptyState message="No deployments match these filters in this range, or none are within your access scope." />
      )}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Deployments</caption>
            <thead>
              <tr style={{ color: 'var(--c-text-muted)' }}>
                <th scope="col" className="py-2">
                  Requested
                </th>
                <th scope="col">Application</th>
                <th scope="col">Environment</th>
                <th scope="col">Ref</th>
                <th scope="col">Commit</th>
                <th scope="col">Status</th>
                <th scope="col">Requested by</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {result.value.items.map((item) => (
                <tr
                  key={item.deployment_id}
                  className="border-t align-top"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <td className="py-2">
                    <Link to={`/deployments/${item.deployment_id}`} className="underline">
                      {stamp(item.created_at)}
                    </Link>
                  </td>
                  <td>
                    <Link to={`/applications/${item.application_id}`} className="underline">
                      {item.application_name}
                    </Link>
                  </td>
                  <td>{item.environment}</td>
                  <td>
                    <code>{item.requested_ref}</code>
                  </td>
                  <td>
                    {item.commit_sha === null ? (
                      <span style={{ color: 'var(--c-text-muted)' }}>—</span>
                    ) : (
                      <code>{item.commit_sha.slice(0, 7)}</code>
                    )}
                  </td>
                  <td>
                    <StatusBadge value={item.execution_status} />
                  </td>
                  <td>{item.requested_by.display_name}</td>
                  <td>
                    <ResultCell item={item} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {result?.kind === 'ok' && (
        <Pager
          paging={paging}
          nextCursor={result.value.page.next_cursor}
          hasMore={result.value.page.has_more}
          label="Deployments"
        />
      )}
    </section>
  );
}
