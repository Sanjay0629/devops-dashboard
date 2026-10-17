/**
 * The global dashboard (PRD §4.2, API_CONTRACTS.md §13, M1.6).
 *
 * **What this page is about is what it does not know.** At this milestone
 * exactly two sections have a source — the server status counts and the
 * failed-unit count, both out of PostgreSQL. Utilisation, health, alerts and
 * all three recent-activity lists have no table and no collector behind
 * them, and every one of them renders as "Not configured" with the milestone
 * that will fill it. None of them renders `0`: PRD §4.2 makes
 * "must not silently replace unavailable data with zero values" an
 * acceptance criterion, and a zero on a dashboard is a number an operator
 * acts on.
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
} from '@/api/dashboard';
import type { DataSection, Result } from '@/api/inventory';
import { CollectedAt, DataStateBanner } from '@/components/DataStateBanner';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
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
}: {
  label: string;
  value: number | null | undefined;
  section: DataSection;
  /** Summary cards link to the relevant detail view (PRD §4.2). */
  to?: string;
  /** Qualifies the number, e.g. how much of it could not be read. */
  note?: string | undefined;
}): React.JSX.Element {
  const body =
    value == null ? (
      <DataStateBanner section={section} inline />
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

export function Dashboard(): React.JSX.Element {
  const [environment, setEnvironment] = useState('');
  const [provider, setProvider] = useState('');
  const [summary, setSummary] = useState<Result<DashboardSummary> | null>(null);
  const [health, setHealth] = useState<Result<DashboardHealthList> | null>(null);
  const paging = useCursorPaging();
  const { cursor, reset } = paging;

  const filterKey = `${environment}|${provider}`;

  const load = useCallback(async () => {
    const query: DashboardFilters = {
      environment: environment || undefined,
      provider: provider || undefined,
    };
    const [nextSummary, nextHealth] = await Promise.all([
      readSummary(query),
      readHealthList({ ...query, cursor: cursor ?? undefined, limit: '25' }),
    ]);
    setSummary(nextSummary);
    setHealth(nextHealth);
  }, [environment, provider, cursor]);

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
          <h2 className="mb-2 text-sm font-medium" style={{ color: 'var(--c-text-muted)' }}>
            Inventory
          </h2>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
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

          <h2 className="mb-2 text-sm font-medium" style={{ color: 'var(--c-text-muted)' }}>
            Health
          </h2>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {/* Every one of these is null at M1.6. The cards render the
                section's reason, which is the point of showing them at all:
                an absent card would read as "nothing to report". */}
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
              label="Critical"
              value={summary.value.server_health.critical}
              section={summary.value.server_health}
            />
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
              label="Active alerts"
              value={summary.value.active_alerts.critical}
              section={summary.value.active_alerts}
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
                ['Health changes', summary.value.recent_health_changes],
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

      {health?.kind === 'ok' && <DataStateBanner section={health.value.health} />}

      {health === null && <LoadingState what="server status" />}
      {health !== null && health.kind !== 'ok' && (
        <ErrorState result={health} onRetry={() => void load()} />
      )}
      {health?.kind === 'ok' && health.value.items.length === 0 && (
        <EmptyState message="No servers match these filters, or none are within your access scope." />
      )}

      {health?.kind === 'ok' && health.value.items.length > 0 && (
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
                  {/* `health_state` is null until M2.1. StatusBadge renders
                      that as "Unknown", never as a green tick. */}
                  <StatusBadge value={row.health_state ?? null} />
                </td>
                <td>{row.failed_service_count}</td>
                <td style={{ color: 'var(--c-text-muted)' }}>
                  {/* Not 0: there is no alerts table to have counted. */}
                  {row.active_alert_count ?? 'Not configured'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
