/**
 * Metrics for one server: current cards and range charts (§15.1, §15.2).
 *
 * **Every card is a value or a reason.** `null` is unknown (CLAUDE.md §5), so a
 * card with no number says which of `stale`, `unavailable`, `unsupported` or
 * `not_configured` it is and why, and shows when the host was last scraped — a
 * zero is never drawn for a missing reading, and a stale one carries no number
 * at all. The time range and the UTC timestamp of what is shown are always
 * visible (PRD §4.4 acceptance).
 *
 * The page asks the backend for template ids and nothing else. Which five
 * templates are charted is a UI choice; thresholds come back from the API.
 */

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { readServer } from '@/api/inventory';
import {
  readCurrentMetrics,
  readRangeMetrics,
  type MetricReading,
  type MetricSeries,
} from '@/api/metrics';
import { LineChart } from '@/components/chart/LineChart';
import { describeState, formatValue, labelOf } from '@/components/chart/format';
import { formatInstant } from '@/components/chart/ticks';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { StatusBadge } from '@/components/StatusBadge';
import { useLatestResponse } from '@/components/useLatestResponse';
import { DEFAULT_RANGE, rangeById, windowFor } from '@/pages/metrics/range';
import { RangePicker } from '@/pages/metrics/RangePicker';

/** The five charted templates — §15.2 allows at most five per request. */
const CHARTED = [
  'cpu_utilisation',
  'memory_utilisation',
  'disk_utilisation',
  'load_average',
  'network_throughput',
] as const;

function Card({ reading }: { reading: MetricReading }): React.JSX.Element {
  const ok = reading.data_state === 'ok' && reading.value != null;
  return (
    <li
      className="rounded border p-3"
      style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
    >
      <h3 className="text-xs" style={{ color: 'var(--c-text-muted)' }}>
        {labelOf(reading.template)}
      </h3>
      {ok ? (
        <p className="mt-1 text-xl font-semibold">
          {formatValue(reading.value ?? 0, reading.unit)}
        </p>
      ) : (
        // No number, so a reason. Never a dash standing in for a zero.
        <p className="mt-1 text-sm">
          {describeState(reading.data_state, reading.unavailable_reason)}
        </p>
      )}
      <p className="mt-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
        {reading.collected_at == null
          ? 'Never collected'
          : `Collected ${formatInstant(Date.parse(reading.collected_at))}`}
      </p>
      {reading.threshold_state === 'warning' || reading.threshold_state === 'critical' ? (
        <div className="mt-1">
          <StatusBadge value={reading.threshold_state} />
        </div>
      ) : reading.threshold_state === 'normal' ? (
        <p className="mt-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
          Within thresholds
        </p>
      ) : null}
    </li>
  );
}

function Chart({
  series,
  thresholds,
  range,
}: {
  series: MetricSeries;
  thresholds: Record<string, { warning?: number; critical?: number }>;
  range: readonly [number, number];
}): React.JSX.Element {
  if (series.data_state !== 'ok' || series.points == null) {
    return (
      <p
        className="rounded border px-3 py-6 text-center text-sm"
        style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
      >
        {labelOf(series.template)}: {describeState(series.data_state, series.unavailable_reason)}
      </p>
    );
  }
  const limits = thresholds[series.template];
  return (
    <LineChart
      title={labelOf(series.template)}
      unit={series.unit}
      range={range}
      series={[
        {
          id: series.template,
          label: labelOf(series.template),
          // `null` stays `null`: this is the whole of the gap rule's plumbing.
          samples: series.points.map(([t, value]) => ({ t: Date.parse(t), value })),
        },
      ]}
      thresholds={
        limits?.warning !== undefined && limits.critical !== undefined
          ? { warning: limits.warning, critical: limits.critical }
          : null
      }
    />
  );
}

export function ServerMetrics(): React.JSX.Element {
  const { serverId = '' } = useParams();
  const [rangeId, setRangeId] = useState(DEFAULT_RANGE);
  const choice = rangeById(rangeId);

  const { value: server } = useLatestResponse(() => readServer(serverId), [serverId]);
  const {
    value: current,
    loading: loadingCurrent,
    reload: reloadCurrent,
  } = useLatestResponse(() => readCurrentMetrics(serverId), [serverId]);
  const {
    value: history,
    loading: loadingHistory,
    reload: reloadHistory,
  } = useLatestResponse(
    () => readRangeMetrics(serverId, { metrics: CHARTED, ...windowFor(choice) }),
    [serverId, rangeId],
  );

  const name = server?.kind === 'ok' ? server.value.display_name : 'Server';
  const refresh = () => {
    void reloadCurrent();
    void reloadHistory();
  };

  return (
    <section aria-labelledby="metrics-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 id="metrics-heading" className="text-xl font-semibold">
            {name} — metrics
          </h1>
          <p className="text-sm">
            <Link to={`/servers/${serverId}`} className="underline">
              Back to server
            </Link>
          </p>
        </div>
        <div className="flex items-center gap-3">
          <RangePicker value={rangeId} onChange={setRangeId} />
          <button
            type="button"
            onClick={refresh}
            className="rounded border px-3 py-1 text-sm"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Refresh
          </button>
        </div>
      </header>

      <h2 className="mb-2 font-medium">Current</h2>
      {current === null ? (
        <LoadingState what="current metrics" />
      ) : current.kind !== 'ok' ? (
        <ErrorState result={current} onRetry={() => void reloadCurrent()} />
      ) : (
        <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:grid-cols-5">
          {current.value.metrics.map((reading) => (
            <Card key={reading.template} reading={reading} />
          ))}
        </ul>
      )}
      {loadingCurrent && current !== null && <LoadingState what="current metrics" />}

      <h2 className="mt-6 mb-1 font-medium">History</h2>
      {history?.kind === 'ok' && (
        <p className="mb-2 text-xs" style={{ color: 'var(--c-text-muted)' }}>
          {formatInstant(Date.parse(history.value.start))} to{' '}
          {formatInstant(Date.parse(history.value.end))} · one point every{' '}
          {history.value.step_seconds} s · gaps mean no data was collected
        </p>
      )}
      {history === null ? (
        <LoadingState what="history" />
      ) : history.kind !== 'ok' ? (
        <ErrorState result={history} onRetry={() => void reloadHistory()} />
      ) : history.value.series.length === 0 ? (
        <EmptyState message="No metrics were returned." />
      ) : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          {history.value.series.map((series) => (
            <Chart
              key={series.template}
              series={series}
              thresholds={history.value.thresholds}
              range={[Date.parse(history.value.start), Date.parse(history.value.end)]}
            />
          ))}
        </div>
      )}
      {loadingHistory && history !== null && <LoadingState what="history" />}
    </section>
  );
}
