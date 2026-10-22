/**
 * Operations history (§23.1, §23.2; PRD §4.12).
 *
 * What this page owes the person reading it, in order of how badly it can go wrong:
 *
 * 1. **A result is shown with its evidence, and an unknown outcome says so.** A succeeded row shows
 *    the state read back afterwards; `TIMED_OUT` is "outcome unknown", never "failed". The provider's
 *    execution id is on every row that has one (PRD §4.12: history "includes the result and
 *    associated execution id").
 * 2. **An empty list is not "nothing happened".** The collection is scope-filtered on the server,
 *    so the page says "none match, or none are within your access scope" and never a bare count.
 * 3. **Filters go to the backend.** A client-side filter would be filtering one page of someone
 *    else's answer, and the window (7 days by default, 90 at most) is the backend's to apply.
 * 4. **Every cell is a text node.** Nothing here is markup from a request.
 *
 * Nothing on this page authorises anything; it is a view of what the backend chose to return.
 */

import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { useSession } from '@/auth/useSession';
import {
  EMPTY_FILTERS,
  listOperations,
  toInstant,
  type HistoryFilters,
  type HistoryItem,
  type HistorySort,
  type HistoryStatus,
} from '@/api/history';
import { failureText } from '@/api/operations';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { Pager } from '@/components/Pager';
import { StatusBadge } from '@/components/StatusBadge';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';

const STATUSES: HistoryStatus[] = [
  'PENDING',
  'AWAITING_APPROVAL',
  'APPROVED',
  'RUNNING',
  'SUCCEEDED',
  'FAILED',
  'TIMED_OUT',
  'CANCELLED',
  'REJECTED',
];
const ACTIONS = ['start', 'stop', 'restart'];
const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];

function stamp(value: string | null): React.JSX.Element {
  if (value === null) return <span style={{ color: 'var(--c-text-muted)' }}>—</span>;
  return (
    <time dateTime={value} title={value}>
      {new Date(value).toLocaleString()}
    </time>
  );
}

/** The outcome, said carefully: evidence for a success, doubt for a timeout. */
function ResultCell({ item }: { item: HistoryItem }): React.JSX.Element {
  if (item.execution_status === 'SUCCEEDED') {
    return <>Verified: {item.observed_state_after ?? 'state read back'}</>;
  }
  if (item.execution_status === 'TIMED_OUT') {
    return <>Outcome unknown — check the service before trying again</>;
  }
  if (item.execution_status === 'FAILED') {
    return <>{failureText(item.failure_reason) || 'Failed'}</>;
  }
  if (item.execution_status === 'REJECTED' || item.execution_status === 'CANCELLED') {
    return <span style={{ color: 'var(--c-text-muted)' }}>Nothing was run</span>;
  }
  return <span style={{ color: 'var(--c-text-muted)' }}>Not finished</span>;
}

function Approval({ item }: { item: HistoryItem }): React.JSX.Element {
  if (item.approval == null)
    return <span style={{ color: 'var(--c-text-muted)' }}>Not needed</span>;
  const who = item.approval.approved_by?.display_name;
  return (
    <>
      {item.approval.decision.toLowerCase()}
      {who !== undefined && who !== '' ? ` by ${who}` : ''}
    </>
  );
}

export function OperationHistory({ serverId }: { serverId?: string }): React.JSX.Element {
  const { state } = useSession();
  const me = state.kind === 'authenticated' ? state.session.user.id : '';
  const [search] = useSearchParams();

  const [status, setStatus] = useState('');
  const [action, setAction] = useState('');
  const [environment, setEnvironment] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [sort, setSort] = useState<HistorySort>('created_at');
  const [mine, setMine] = useState(false);
  // Only ever a UUID typed into the address bar: there is no user directory to pick from.
  const linkedRequester = search.get('requested_by') ?? '';

  const build = (): HistoryFilters => ({
    ...EMPTY_FILTERS,
    statuses: status === '' ? [] : [status as HistoryStatus],
    actions: action === '' ? [] : [action],
    environments: environment === '' ? [] : [environment],
    requestedBy: mine ? me : linkedRequester,
    from: toInstant(from),
    to: toInstant(to),
    sort,
  });
  const [applied, setApplied] = useState<HistoryFilters>(build);

  const paging = useCursorPaging();
  const { cursor, reset } = paging;
  const key = JSON.stringify(applied);

  const {
    value: result,
    loading,
    reload,
  } = useLatestResponse(
    () => listOperations({ ...applied, cursor: cursor ?? undefined }, serverId),
    [key, cursor, serverId],
  );

  useEffect(() => {
    reset();
  }, [key, reset]);

  const field = (
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
    <section aria-labelledby="history-heading">
      <header className="mb-4">
        <h1 id="history-heading" className="text-xl font-semibold">
          {serverId === undefined ? 'Operations' : 'Operations on this server'}
        </h1>
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          What was requested, who asked, who approved, and what happened. The last 7 days unless you
          choose a range (at most 90 days). You see your own requests and those in your access
          scope.
        </p>
      </header>

      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(build());
        }}
      >
        {field('Status', status, setStatus, [
          { value: '', label: 'Any' },
          ...STATUSES.map((value) => ({ value, label: value.toLowerCase().replace('_', ' ') })),
        ])}
        {field('Action', action, setAction, [
          { value: '', label: 'Any' },
          ...ACTIONS.map((value) => ({ value, label: value })),
        ])}
        {field('Environment', environment, setEnvironment, [
          { value: '', label: 'Any' },
          ...ENVIRONMENTS.map((value) => ({ value, label: value })),
        ])}
        {date('From', from, setFrom)}
        {date('To', to, setTo)}
        {field(
          'Sort by',
          sort,
          (next) => {
            setSort(next as HistorySort);
          },
          [
            { value: 'created_at', label: 'When requested' },
            { value: 'completed_at', label: 'When finished (unfinished last)' },
          ],
        )}
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

      {loading && <LoadingState what="operations" />}
      {result !== null && result.kind !== 'ok' && (
        <ErrorState
          result={result}
          onRetry={() => {
            void reload();
          }}
        />
      )}

      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <EmptyState message="No operations match these filters in this range, or none are within your access scope." />
      )}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr style={{ color: 'var(--c-text-muted)' }}>
                <th scope="col" className="py-2">
                  Requested
                </th>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
                <th scope="col">Environment</th>
                <th scope="col">Status</th>
                <th scope="col">Requested by</th>
                <th scope="col">Approval</th>
                <th scope="col">Result</th>
                <th scope="col">Execution id</th>
              </tr>
            </thead>
            <tbody>
              {result.value.items.map((item) => (
                <tr
                  key={item.operation_id}
                  className="border-t align-top"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <td className="py-2">
                    <Link to={`/operations/${item.operation_id}`} className="underline">
                      {stamp(item.created_at)}
                    </Link>
                  </td>
                  <td>{item.action_type}</td>
                  <td>
                    <Link to={`/servers/${item.target.server_id}`} className="underline">
                      {item.target.server_display_name}
                    </Link>
                    {item.target.service_name !== '' && (
                      <span style={{ color: 'var(--c-text-muted)' }}>
                        {' '}
                        · {item.target.service_name}
                      </span>
                    )}
                  </td>
                  <td>{item.target.environment}</td>
                  <td>
                    <StatusBadge value={item.execution_status} />
                  </td>
                  <td>{item.requested_by.display_name}</td>
                  <td>
                    <Approval item={item} />
                  </td>
                  <td>
                    <ResultCell item={item} />
                  </td>
                  <td>
                    {item.external_execution_id === null ? (
                      <span style={{ color: 'var(--c-text-muted)' }}>—</span>
                    ) : (
                      <code>{item.external_execution_id}</code>
                    )}
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
          label="Operations"
        />
      )}
    </section>
  );
}

/** `/servers/:serverId/operations`. */
export function ServerOperations(): React.JSX.Element {
  const { serverId } = useParams();
  if (serverId === undefined) return <p role="alert">No such server.</p>;
  return <OperationHistory serverId={serverId} />;
}
