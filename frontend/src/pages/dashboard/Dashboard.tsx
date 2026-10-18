/**
 * The global dashboard (PRD §4.2, API_CONTRACTS.md §13, M1.6).
 *
 * **What this page is about is what it does not know.** Four sections have a
 * source: the registration-status counts and the failed-unit count (both out
 * of PostgreSQL), the host-health rollup (from `last_seen_at`, M2.5) and the
 * recent health changes (from the audit trail). Utilisation, alerts and the
 * other two recent-activity lists have none, and render as "Not configured"
 * with the milestone that will fill them. A value that is absent is never
 * rendered as `0`: PRD §4.2 makes "must not silently replace unavailable data
 * with zero values" an acceptance criterion, and a zero on a dashboard is a
 * number an operator acts on. That includes the health rollup when Prometheus
 * is silent, and `critical`, which nothing evaluates yet.
 *
 * Two pairs of numbers look alike and are not: **registration status**
 * (`servers.status`, which administrators and the sweep write) and **health**
 * (computed from the last scrape). They get separate, labelled groups.
 *
 * Auto-refresh is configurable (PRD §4.2) via `VITE_DASHBOARD_REFRESH_SECONDS`,
 * default 30s, independent of the backend's own cache TTL — polling faster
 * than the TTL just re-reads the same cached payload, which the displayed
 * `collected_at` makes visible rather than hiding.
 */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  readHealthList,
  readSummary,
  type DashboardFilters,
  type DashboardHealthList,
  type DashboardSummary,
  type HealthChange,
  type HealthStatus,
  HEALTH_STATUSES,
} from '@/api/dashboard';
import type { DataSection, Result } from '@/api/inventory';
import { CollectedAt, DataStateBanner } from '@/components/DataStateBanner';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { HealthBadge, LastSeen } from '@/components/HealthCell';
import { Pager } from '@/components/Pager';
import { useCursorPaging } from '@/components/useCursorPaging';
import { StatusBadge } from '@/components/StatusBadge';

const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];
const PROVIDERS = ['AWS', 'AZURE', 'ON_PREM', 'OTHER'];

/**
 * How often to re-poll, in seconds. `0` turns auto-refresh off.
 *
 * Read through Vite's own env mechanism rather than a new settings endpoint:
 * it is a deployment-time choice, it is not a secret, and `VITE_` variables
 * are already how this frontend is configured (`VITE_DEV_PROXY_TARGET`).
 */
function refreshSeconds(): number {
  const raw = Number(import.meta.env.VITE_DASHBOARD_REFRESH_SECONDS ?? 30);
  return Number.isFinite(raw) && raw >= 0 ? raw : 30;
}

/**
 * The alerts card's section, with a reason an operator can read. The API's reason is a code
 * (`alert_read_not_permitted`); the banner prints the reason it is given.
 */
function alertSection(section: DashboardSummary['active_alerts']): DataSection {
  if (section.unavailable_reason === 'alert_read_not_permitted') {
    return { ...section, unavailable_reason: 'You do not have permission to read alerts.' };
  }
  return section;
}

/**
 * What qualifies the number. **A `0` is only good news if something would have told us
 * otherwise**, so the card says when the alerting pipeline has not been confirmed by the
 * always-firing watchdog (API `pipeline`): "0" beside that reads "we cannot tell".
 */
function alertNote(section: DashboardSummary['active_alerts']): string | undefined {
  if (section.data_state !== 'ok') return undefined;
  const parts = [`${String(section.warning ?? 0)} warning`, `${String(section.info ?? 0)} info`];
  if (section.pipeline === 'not_confirmed') {
    parts.push('alerting not confirmed: a 0 here means we cannot tell');
  } else if (section.pipeline === 'unknown') {
    parts.push('could not check whether alerting is working');
  }
  return parts.join(' · ');
}

/**
 * One summary card.
 *
 * `value` is `number | null` and the `null` branch is the whole reason this
 * component exists: it renders the section's data state, never a dash and
 * never a zero.
 */
function Card({
  label,
  value,
  section,
  to,
  note,
  absent,
}: {
  label: string;
  value: number | null | undefined;
  section: DataSection;
  /** Summary cards link to the relevant detail view (PRD §4.2). */
  to?: string;
  /** Qualifies the number, e.g. how much of it could not be read. */
  note?: string | undefined;
  /** What to say for a null in an `ok` section. */
  absent?: string | undefined;
}): React.JSX.Element {
  // An `ok` section whose value is still null is a value nobody evaluates
  // (`critical`), not a collected one; the banner would say "Collected".
  const body =
    value == null ? (
      section.data_state === 'ok' ? (
        <span style={{ color: 'var(--c-text-muted)' }}>{absent ?? 'Not evaluated'}</span>
      ) : (
        <DataStateBanner section={section} inline />
      )
    ) : (
      <span className="text-2xl font-semibold">{value}</span>
    );

  return (
    <div
      className="rounded-lg border px-4 py-3"
      style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
    >
      <h3 className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
        {to === undefined ? label : <Link to={to}>{label}</Link>}
      </h3>
      <p className="mt-1">{body}</p>
      {note === undefined ? null : (
        <p className="mt-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
          {note}
        </p>
      )}
    </div>
  );
}

/**
 * The sweep's latest status moves. Unlike the other two recent-activity lists
 * this one has a table behind it, so an empty list is a fact ("none recorded")
 * and is said as one; a `null` list is still "not available".
 */
function HealthChanges({
  section,
}: {
  section: DashboardSummary['recent_health_changes'];
}): React.JSX.Element {
  const items: HealthChange[] | null | undefined = section.items;
  if (items == null) return <DataStateBanner section={section} />;
  if (items.length === 0) {
    return (
      <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
        No health changes recorded in the last 30 days.
      </p>
    );
  }
  return (
    <ul className="space-y-1 text-sm">
      {items.map((change) => (
        <li key={`${change.server_id}|${change.occurred_at}`}>
          <Link to={`/servers/${change.server_id}`} className="underline">
            {change.display_name}
          </Link>{' '}
          <span className="inline-flex items-center gap-1">
            <StatusBadge value={change.previous_status} />
            <span aria-label="became">→</span>
            <StatusBadge value={change.new_status} />
          </span>{' '}
          <time
            dateTime={change.occurred_at}
            className="text-xs"
            style={{ color: 'var(--c-text-muted)' }}
          >
            {new Date(change.occurred_at).toLocaleString()}
          </time>
        </li>
      ))}
    </ul>
  );
}

export function Dashboard(): React.JSX.Element {
  const [environment, setEnvironment] = useState('');
  const [provider, setProvider] = useState('');
  const [healthStatus, setHealthStatus] = useState<HealthStatus | ''>('');
  const [summary, setSummary] = useState<Result<DashboardSummary> | null>(null);
  const [health, setHealth] = useState<Result<DashboardHealthList> | null>(null);
  const paging = useCursorPaging();
  const { cursor, reset } = paging;

  const filterKey = `${environment}|${provider}|${healthStatus}`;

  const load = useCallback(async () => {
    const query: DashboardFilters = {
      environment: environment || undefined,
      provider: provider || undefined,
    };
    const [nextSummary, nextHealth] = await Promise.all([
      readSummary(query),
      readHealthList({
        ...query,
        health_status: healthStatus || undefined,
        cursor: cursor ?? undefined,
        limit: '25',
      }),
    ]);
    setSummary(nextSummary);
    setHealth(nextHealth);
  }, [environment, provider, healthStatus, cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  // A filter change means a different collection, so page 1. Keyed on the
  // filter values rather than on `load`, which also changes when the cursor
  // does — resetting there would make "Next" bounce straight back.
  useEffect(() => {
    reset();
  }, [filterKey, reset]);

  useEffect(() => {
    const seconds = refreshSeconds();
    if (seconds === 0) return;
    const timer = setInterval(() => void load(), seconds * 1000);
    return () => {
      clearInterval(timer);
    };
  }, [load]);

  return (
    <section aria-labelledby="dashboard-heading">
      <header className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <h1 id="dashboard-heading" className="text-xl font-semibold">
          Dashboard
        </h1>
        {summary?.kind === 'ok' && <CollectedAt section={summary.value.servers} />}
      </header>

      <form
        className="mb-6 flex flex-wrap items-end gap-3"
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
            Provider
          </span>
          <select
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">All</option>
            {PROVIDERS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Health status
          </span>
          <select
            value={healthStatus}
            onChange={(event) => {
              setHealthStatus(event.target.value as HealthStatus | '');
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">All</option>
            {HEALTH_STATUSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <button
          type="submit"
          className="rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Refresh
        </button>
      </form>

      {summary === null && <LoadingState what="the dashboard" />}
      {summary !== null && summary.kind !== 'ok' && (
        <ErrorState result={summary} onRetry={() => void load()} />
      )}

      {summary?.kind === 'ok' && (
        <>
          <section aria-labelledby="registration-heading" className="mb-6">
            <h2
              id="registration-heading"
              className="mb-2 text-sm font-medium"
              style={{ color: 'var(--c-text-muted)' }}
            >
              Registration status
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Card
                label="Total servers"
                value={summary.value.servers.total}
                section={summary.value.servers}
                to="/servers"
              />
              <Card
                label="Managed"
                value={summary.value.servers.managed}
                section={summary.value.servers}
                to="/servers?status=MANAGED"
              />
              <Card
                label="Stale"
                value={summary.value.servers.stale}
                section={summary.value.servers}
                to="/servers?status=STALE"
              />
              <Card
                label="Unreachable"
                value={summary.value.servers.unreachable}
                section={summary.value.servers}
                to="/servers?status=UNREACHABLE"
              />
            </div>
          </section>

          <section aria-labelledby="health-heading" className="mb-6">
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-3">
              <h2
                id="health-heading"
                className="text-sm font-medium"
                style={{ color: 'var(--c-text-muted)' }}
              >
                Health
              </h2>
              {/* When Prometheus last answered: the age of the evidence, not of the page. */}
              {summary.value.server_health.collected_at != null && (
                <CollectedAt section={summary.value.server_health} />
              )}
            </div>
            {/* Each card carries the section's reason itself rather than one
                banner above them: an operator reads one card at a time, and an
                absent number must say why wherever it is missing. */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Card
                label="Healthy"
                value={summary.value.server_health.healthy}
                section={summary.value.server_health}
              />
              <Card
                label="Warning"
                value={summary.value.server_health.warning}
                section={summary.value.server_health}
              />
              <Card
                label="Stale"
                value={summary.value.server_health.stale}
                section={summary.value.server_health}
              />
              <Card
                label="Unreachable"
                value={summary.value.server_health.unreachable}
                section={summary.value.server_health}
              />
              <Card
                label="Unknown"
                value={summary.value.server_health.unknown}
                section={summary.value.server_health}
              />
              <Card
                label="Critical"
                value={summary.value.server_health.critical}
                section={summary.value.server_health}
                absent="Not evaluated — utilisation is not assessed"
              />
            </div>
          </section>

          <section aria-labelledby="services-heading" className="mb-6">
            <h2
              id="services-heading"
              className="mb-2 text-sm font-medium"
              style={{ color: 'var(--c-text-muted)' }}
            >
              Services
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Card
                label="Failed services"
                value={summary.value.failed_services.count}
                section={summary.value.failed_services}
                note={
                  summary.value.failed_services.unobserved
                    ? `${String(summary.value.failed_services.unobserved)} not observed`
                    : undefined
                }
              />
            </div>
          </section>

          <h2 className="mb-2 text-sm font-medium" style={{ color: 'var(--c-text-muted)' }}>
            Utilisation
          </h2>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Card
              label="CPU average"
              value={summary.value.utilisation.cpu_percent_avg}
              section={summary.value.utilisation}
            />
            <Card
              label="Memory average"
              value={summary.value.utilisation.memory_percent_avg}
              section={summary.value.utilisation}
            />
            <Card
              label="Disk maximum"
              value={summary.value.utilisation.disk_percent_max}
              section={summary.value.utilisation}
            />
            <Card
              label="Open critical alerts"
              value={summary.value.active_alerts.critical}
              section={alertSection(summary.value.active_alerts)}
              to="/alerts"
              note={alertNote(summary.value.active_alerts)}
            />
          </div>

          <h2 className="mb-2 text-sm font-medium" style={{ color: 'var(--c-text-muted)' }}>
            Recent activity
          </h2>
          <div className="mb-8 grid gap-3 sm:grid-cols-3">
            {(
              [
                ['Deployments', summary.value.recent_deployments],
                ['Operations', summary.value.recent_operations],
              ] as const
            ).map(([label, section]) => (
              <div key={label}>
                <h3 className="mb-1 text-sm" style={{ color: 'var(--c-text-muted)' }}>
                  {label}
                </h3>
                {/* `items` is null, never []. An empty list here would claim
                    nothing has happened; there is no table to ask. */}
                <DataStateBanner section={section} />
              </div>
            ))}
            <div>
              <h3 className="mb-1 text-sm" style={{ color: 'var(--c-text-muted)' }}>
                Health changes
              </h3>
              <HealthChanges section={summary.value.recent_health_changes} />
            </div>
          </div>
        </>
      )}

      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="status-table-heading" className="text-lg font-semibold">
          Server status
        </h2>
        {health?.kind === 'ok' && (
          <CollectedAt
            section={{
              data_state: 'ok',
              collected_at: health.value.collected_at,
              source: 'postgresql',
            }}
          />
        )}
      </div>

      {/* The rollup's reason, once, when it has no counts; with counts it is
          already on the cards above, and "Collected" here would be noise. */}
      {health?.kind === 'ok' && health.value.health.data_state !== 'ok' && (
        <DataStateBanner section={health.value.health} />
      )}

      {health === null && <LoadingState what="server status" />}
      {health !== null && health.kind !== 'ok' && (
        <ErrorState result={health} onRetry={() => void load()} />
      )}
      {health?.kind === 'ok' && health.value.items.length === 0 && (
        <EmptyState message="No servers match these filters, or none are within your access scope." />
      )}

      {health?.kind === 'ok' && health.value.items.length > 0 && (
        // Eight columns do not fit a phone; the table scrolls inside its own box
        // rather than widening the page (PRD §8: usable on a narrow screen).
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm" aria-labelledby="status-table-heading">
            <thead>
              <tr style={{ color: 'var(--c-text-muted)' }}>
                <th scope="col" className="py-2">
                  Name
                </th>
                <th scope="col">Environment</th>
                <th scope="col">Provider</th>
                <th scope="col">Registration</th>
                <th scope="col">Health</th>
                <th scope="col">Last seen</th>
                <th scope="col">Failed units</th>
                <th scope="col">Open alerts</th>
              </tr>
            </thead>
            <tbody>
              {health.value.items.map((row) => (
                <tr
                  key={row.server_id}
                  className="border-t"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <td className="py-2">
                    <Link to={`/servers/${row.server_id}`} className="underline">
                      {row.display_name}
                    </Link>
                  </td>
                  <td>{row.environment}</td>
                  <td>{row.provider}</td>
                  <td>
                    <StatusBadge value={row.status} />
                  </td>
                  <td>
                    {/* null is "not evaluated", `unknown` is "evaluated, nothing
                      known"; neither is ever a green tick. */}
                    <HealthBadge state={row.health_state} />
                  </td>
                  <td>
                    <LastSeen at={row.last_seen_at} />
                  </td>
                  <td>{row.failed_service_count}</td>
                  <td style={{ color: 'var(--c-text-muted)' }}>
                    {/* `null` is "you may not read alerts for this server", never a 0. */}
                    {row.active_alert_count ?? 'Not permitted'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {health?.kind === 'ok' && (
        <Pager
          paging={paging}
          nextCursor={health.value.page.next_cursor}
          hasMore={health.value.page.has_more}
          label="Server status"
        />
      )}
    </section>
  );
}
