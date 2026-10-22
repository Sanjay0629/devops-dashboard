/**
 * The audit trail (§24.1, §24.2; PRD §4.12).
 *
 * Three things this page is careful about, because they are the ways it could do harm:
 *
 * 1. **Reading the trail is itself an audited act** (`AUDIT_QUERIED`), so the page asks exactly
 *    once per search and once per page turned. It never polls, never refetches on focus, and the
 *    detail panel shows what the list already returned instead of asking again.
 * 2. **Event content is untrusted text.** Metadata came from requests; it is rendered as a text
 *    node inside `<pre>`, never as markup, and this file has no raw-HTML escape hatch.
 * 3. **An empty answer is not "nothing happened".** The trail is scope-filtered by environment on
 *    the server; an event with no environment (a login, a role change) is only visible to a grant
 *    that covers every environment. The page says so rather than implying completeness.
 *
 * Nothing here can write an event, and nothing here authorises anything.
 */

import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import {
  ACTION_TOKEN,
  EMPTY_AUDIT_FILTERS,
  listAuditEvents,
  type AuditEvent,
  type AuditFilters,
} from '@/api/audit';
import { toInstant } from '@/api/history';
import { can, PERMISSIONS } from '@/auth/permissions';
import { useSession } from '@/auth/useSession';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { Pager } from '@/components/Pager';
import { StatusBadge } from '@/components/StatusBadge';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';
import { AuditExport } from '@/pages/audit/AuditExport';

const OUTCOMES = ['SUCCESS', 'FAILURE', 'DENIED'];
const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];
const ACTOR_TYPES = ['USER', 'SYSTEM', 'INTEGRATION'];

function Detail({
  event,
  onSameCorrelation,
  onClose,
}: {
  event: AuditEvent;
  onSameCorrelation: (id: string) => void;
  onClose: () => void;
}): React.JSX.Element {
  const rows: [string, React.ReactNode][] = [
    ['Event id', <code key="id">{event.id}</code>],
    [
      'When',
      <time key="t" dateTime={event.occurred_at}>
        {new Date(event.occurred_at).toISOString()}
      </time>,
    ],
    ['Actor', `${event.actor_label} (${event.actor_type.toLowerCase()})`],
    ['Action', event.action_type],
    ['Target', event.target_type === null ? '—' : `${event.target_type} ${event.target_id ?? ''}`],
    ['Environment', event.environment ?? 'none'],
    ['Outcome', event.outcome],
    ['Approval', event.approval_id ?? '—'],
    ['Correlation id', <code key="c">{event.correlation_id}</code>],
    ['Source address', event.source_ip ?? '—'],
    ['User agent', event.user_agent ?? '—'],
  ];
  return (
    <aside
      aria-label="Event detail"
      className="mt-4 rounded border px-4 py-3 text-sm"
      style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
    >
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-medium">Event detail</h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded border px-2 py-0.5 text-xs"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Close
        </button>
      </div>
      <dl>
        {rows.map(([label, value]) => (
          <div
            key={label}
            className="flex justify-between gap-4 border-t py-1"
            style={{ borderColor: 'var(--c-border)' }}
          >
            <dt style={{ color: 'var(--c-text-muted)' }}>{label}</dt>
            <dd className="text-right break-all">{value}</dd>
          </div>
        ))}
      </dl>
      <h3 className="mt-3 mb-1" style={{ color: 'var(--c-text-muted)' }}>
        Metadata (redacted; shown as text)
      </h3>
      {/* A text node: a metadata value containing markup is displayed, not interpreted. */}
      <pre
        className="overflow-x-auto rounded border p-2 text-xs"
        style={{ borderColor: 'var(--c-border)' }}
      >
        {JSON.stringify(event.sanitized_metadata, null, 2)}
      </pre>
      <button
        type="button"
        onClick={() => {
          onSameCorrelation(event.correlation_id);
        }}
        className="mt-3 rounded border px-3 py-1 text-xs"
        style={{ borderColor: 'var(--c-border)' }}
      >
        Show everything with this correlation id
      </button>
    </aside>
  );
}

export function AuditLog(): React.JSX.Element {
  const { state } = useSession();
  const mayExport =
    state.kind === 'authenticated' && can(state.session, PERMISSIONS.adminIntegrations);
  const [search] = useSearchParams();

  const [actionType, setActionType] = useState('');
  const [outcome, setOutcome] = useState('');
  const [environment, setEnvironment] = useState('');
  const [actorType, setActorType] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  // Ids come from the address bar or from "same correlation id": no user directory to pick from.
  const [applied, setApplied] = useState<AuditFilters>({
    ...EMPTY_AUDIT_FILTERS,
    actorId: search.get('actor_id') ?? '',
    targetId: search.get('target_id') ?? '',
    correlationId: search.get('correlation_id') ?? '',
  });
  const [selected, setSelected] = useState<AuditEvent | null>(null);

  const actionProblem = actionType !== '' && !ACTION_TOKEN.test(actionType);

  const paging = useCursorPaging();
  const { cursor, reset } = paging;
  const key = JSON.stringify(applied);

  const {
    value: result,
    loading,
    reload,
  } = useLatestResponse(
    () => listAuditEvents({ ...applied, cursor: cursor ?? undefined }),
    [key, cursor],
  );
  useEffect(() => {
    reset();
    setSelected(null);
  }, [key, reset]);

  const select = (
    label: string,
    value: string,
    set: (next: string) => void,
    values: string[],
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
        <option value="">Any</option>
        {values.map((option) => (
          <option key={option} value={option}>
            {option.toLowerCase()}
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
    <section aria-labelledby="audit-heading">
      <header className="mb-4">
        <h1 id="audit-heading" className="text-xl font-semibold">
          Audit log
        </h1>
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          The record of who did what. Events cannot be changed or deleted from here. Reading this
          page is itself recorded, so it searches when you ask and does not refresh on its own. The
          last 7 days unless you choose a range; a range beyond 7 days needs both a start and an end
          (90 days at most).
        </p>
      </header>

      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (actionProblem) return;
          setApplied((current) => ({
            ...current,
            actionType,
            outcome,
            environment,
            actorType,
            from: toInstant(from),
            to: toInstant(to),
          }));
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Action
          </span>
          <input
            type="text"
            value={actionType}
            maxLength={64}
            placeholder="e.g. ROLE_GRANTED"
            aria-invalid={actionProblem}
            onChange={(event) => {
              setActionType(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          />
        </label>
        {select('Outcome', outcome, setOutcome, OUTCOMES)}
        {select('Environment', environment, setEnvironment, ENVIRONMENTS)}
        {select('Actor type', actorType, setActorType, ACTOR_TYPES)}
        {date('From', from, setFrom)}
        {date('To', to, setTo)}
        <button
          type="submit"
          className="rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Search
        </button>
      </form>
      {actionProblem && (
        <p role="alert" className="mb-3 text-sm" style={{ color: 'var(--c-critical)' }}>
          An action is capital letters and underscores only, exactly as recorded. It is a match, not
          a pattern.
        </p>
      )}

      {loading && <LoadingState what="audit events" />}
      {result !== null && result.kind !== 'ok' && (
        <ErrorState
          result={result}
          onRetry={() => {
            void reload();
          }}
        />
      )}

      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <EmptyState message="No events match these filters in this range, or none are within your access scope. Events with no environment, such as sign-ins, are only shown to an access scope that covers every environment." />
      )}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr style={{ color: 'var(--c-text-muted)' }}>
                <th scope="col" className="py-2">
                  When
                </th>
                <th scope="col">Actor</th>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
                <th scope="col">Environment</th>
                <th scope="col">Outcome</th>
                <th scope="col">
                  <span className="sr-only">Detail</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {result.value.items.map((event) => (
                <tr
                  key={event.id}
                  className="border-t align-top"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <td className="py-2">
                    <time dateTime={event.occurred_at} title={event.occurred_at}>
                      {new Date(event.occurred_at).toLocaleString()}
                    </time>
                  </td>
                  <td>{event.actor_label}</td>
                  <td>{event.action_type}</td>
                  <td>{event.target_type ?? '—'}</td>
                  <td>{event.environment ?? '—'}</td>
                  <td>
                    <StatusBadge value={event.outcome} />
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => {
                        setSelected(event);
                      }}
                      className="underline"
                      aria-label={`Details of ${event.action_type} at ${event.occurred_at}`}
                    >
                      Details
                    </button>
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
          label="Audit events"
        />
      )}

      {selected !== null && (
        <Detail
          event={selected}
          onClose={() => {
            setSelected(null);
          }}
          onSameCorrelation={(id) => {
            setApplied({ ...EMPTY_AUDIT_FILTERS, correlationId: id });
          }}
        />
      )}

      {mayExport && <AuditExport />}
    </section>
  );
}
