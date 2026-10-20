/**
 * Alert list (§20.1, PRD §4.8).
 *
 * What this page owes the operator, in order of how badly it can go wrong:
 *
 * 1. **An empty list is not "all clear".** The page says which conditions DCP can raise an
 *    alert for and which it cannot yet (`coverage` comes from the API, so the page hard-codes
 *    none of it): "no alerts" about a condition nothing watches would be a comfortable lie.
 * 2. **Status is a word, a shape and a colour** (`StatusBadge`), never colour alone.
 * 3. **Alert text is untrusted** (SECURITY.md §21). A summary came from a payload; it is
 *    rendered as a text node, and there is no `dangerouslySetInnerHTML` in this file.
 * 4. **Filters go to the backend.** The collection is scope-filtered and cursor-paged there,
 *    so a client-side filter would be filtering one page of someone else's answer.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  listAlerts,
  type AlertSeverity,
  type AlertStatus,
  type AlertSummary,
  type ConditionCoverage,
} from '@/api/alerts';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { Pager } from '@/components/Pager';
import { StatusBadge } from '@/components/StatusBadge';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';

const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];
const SEVERITIES: AlertSeverity[] = ['CRITICAL', 'WARNING', 'INFO'];

/** What each "view" asks the API for. `open` is the API's own default, sent explicitly. */
const VIEWS: Record<string, { label: string; statuses: AlertStatus[] }> = {
  open: { label: 'Open', statuses: ['FIRING', 'ACKNOWLEDGED'] },
  resolved: { label: 'Resolved', statuses: ['RESOLVED'] },
  expired: { label: 'Expired (not confirmed)', statuses: ['EXPIRED'] },
  all: { label: 'All', statuses: ['FIRING', 'ACKNOWLEDGED', 'RESOLVED', 'EXPIRED'] },
};

const OPEN_VIEW = VIEWS.open ?? {
  label: 'Open',
  statuses: ['FIRING', 'ACKNOWLEDGED'] as AlertStatus[],
};

const PRODUCED: Record<string, string> = {
  host_not_reporting: 'a host that stops reporting',
  cpu: 'sustained high CPU',
  memory: 'sustained high memory use',
  disk: 'a nearly full filesystem',
  unit_failed: 'a failed systemd service',
  operation_timed_out_or_failed: 'an operation that fails or times out',
};

const NOT_PRODUCED: Record<string, string> = {
  application_health_check_failed: 'a failed application health check',
  deployment_failed: 'a failed deployment',
};

/** Which conditions can raise an alert, and which cannot yet: never imply coverage. */
export function CoverageNote({ coverage }: { coverage: ConditionCoverage }): React.JSX.Element {
  const produced = coverage.produced.map((key) => PRODUCED[key] ?? key);
  const missing = coverage.not_produced_yet.map((item) => {
    const condition = item.condition ?? '';
    return `${NOT_PRODUCED[condition] ?? condition} (arrives in ${item.arrives_in ?? 'a later milestone'})`;
  });
  const silent = coverage.notification_channels_active === 0;
  return (
    <aside
      aria-label="What raises alerts"
      className="mt-4 rounded border px-4 py-3 text-sm"
      style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
    >
      <p>
        <strong>Alerts are raised for:</strong> {produced.join('; ')}.
      </p>
      {missing.length > 0 && (
        <p className="mt-1">
          <strong>Not raised yet:</strong> {missing.join('; ')}. Having no alert about one of those
          means nothing is watching it, not that it is fine.
        </p>
      )}
      <p className="mt-1" role={silent ? 'status' : undefined}>
        {silent ? (
          <>
            <strong>Alerts are not being sent anywhere:</strong> no notification channel is active,
            so nobody is told when one begins.
          </>
        ) : (
          <>
            <strong>Notifications:</strong> {coverage.notification_channels_active} channel
            {coverage.notification_channels_active === 1 ? ' is' : 's are'} told when an alert
            begins.
          </>
        )}
      </p>
    </aside>
  );
}

function stamp(value: string): React.JSX.Element {
  return (
    <time dateTime={value} title={value}>
      {new Date(value).toLocaleString()}
    </time>
  );
}

function Target({ alert }: { alert: AlertSummary }): React.JSX.Element {
  if (alert.target == null) {
    // Said, not left blank: an alert whose labels matched no server is its own fact.
    return <span style={{ color: 'var(--c-text-muted)' }}>Not linked to a server</span>;
  }
  const name = alert.target.display_name ?? alert.target.id;
  const serverPath = alert.links.server?.replace('/api/v1', '');
  if (serverPath === undefined) return <>{name}</>;
  return (
    <Link to={serverPath} className="underline">
      {name}
    </Link>
  );
}

export function AlertList(): React.JSX.Element {
  const [view, setView] = useState('open');
  const [severity, setSeverity] = useState('');
  const [environment, setEnvironment] = useState('');
  const [q, setQ] = useState('');
  const paging = useCursorPaging();
  const { cursor, reset } = paging;

  const {
    value: result,
    loading,
    reload,
  } = useLatestResponse(
    () =>
      listAlerts({
        statuses: (VIEWS[view] ?? OPEN_VIEW).statuses,
        severities: severity === '' ? [] : [severity as AlertSeverity],
        environment,
        q,
        cursor: cursor ?? undefined,
      }),
    [view, severity, environment, q, cursor],
  );

  // A filter change is a different collection, so page 1 (keyed on the filter values and not
  // on `reload`, which also changes with the cursor).
  useEffect(() => {
    reset();
  }, [view, severity, environment, q, reset]);

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

  return (
    <section aria-labelledby="alerts-heading">
      <header className="mb-4">
        <h1 id="alerts-heading" className="text-xl font-semibold">
          Alerts
        </h1>
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          Acknowledging an alert says someone has it. It does not resolve it: only the source of the
          alert can do that.
        </p>
      </header>

      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void reload();
        }}
      >
        {select(
          'Show',
          view,
          setView,
          Object.entries(VIEWS).map(([key, { label }]) => ({ value: key, label })),
        )}
        {select('Severity', severity, setSeverity, [
          { value: '', label: 'All' },
          ...SEVERITIES.map((value) => ({ value, label: value.toLowerCase() })),
        ])}
        {select('Environment', environment, setEnvironment, [
          { value: '', label: 'All' },
          ...ENVIRONMENTS.map((value) => ({ value, label: value })),
        ])}
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Search
          </span>
          <input
            type="search"
            value={q}
            maxLength={100}
            placeholder="summary or rule"
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

      {loading && <LoadingState what="alerts" />}
      {result !== null && result.kind !== 'ok' && (
        <ErrorState
          result={result}
          onRetry={() => {
            void reload();
          }}
        />
      )}

      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <EmptyState
          message={
            view === 'open'
              ? 'No open alerts match these filters, or none are within your access scope.'
              : 'No alerts match these filters, or none are within your access scope.'
          }
        />
      )}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead>
            <tr style={{ color: 'var(--c-text-muted)' }}>
              <th scope="col" className="py-2">
                Severity
              </th>
              <th scope="col">Status</th>
              <th scope="col">Alert</th>
              <th scope="col">About</th>
              <th scope="col">Environment</th>
              <th scope="col">Last seen</th>
              <th scope="col">Times</th>
              <th scope="col">Assigned to</th>
            </tr>
          </thead>
          <tbody>
            {result.value.items.map((alert) => (
              <tr
                key={alert.alert_id}
                className="border-t align-top"
                style={{ borderColor: 'var(--c-border)' }}
              >
                <td className="py-2">
                  <StatusBadge value={alert.severity} />
                </td>
                <td>
                  <StatusBadge value={alert.status} />
                </td>
                <td>
                  {/* Untrusted text: a text node inside a link, nothing else. */}
                  <Link to={`/alerts/${alert.alert_id}`} className="underline">
                    {alert.summary}
                  </Link>
                </td>
                <td>
                  <Target alert={alert} />
                </td>
                <td>{alert.environment ?? '—'}</td>
                <td>{stamp(alert.last_seen_at)}</td>
                <td>{alert.occurrence_count}</td>
                <td>{alert.assigned_to?.display_name ?? 'Nobody'}</td>
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
          label="Alerts"
        />
      )}
      {result?.kind === 'ok' && <CoverageNote coverage={result.value.coverage} />}
    </section>
  );
}
