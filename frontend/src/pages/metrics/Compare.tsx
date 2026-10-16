/**
 * Server and environment comparison (§15.3).
 *
 * **Nothing here widens what the caller may see.** The backend drops servers
 * outside the caller's scope and reports a count; this page says so plainly
 * rather than showing fewer lines without comment, so a comparison is never
 * silently about fewer servers than were asked for.
 *
 * Every server that was *included* stays in the output. One with no data
 * appears under the chart with its state and reason — an absent line must not
 * be readable as a quiet host.
 *
 * Colour follows the entity: a server keeps the categorical slot it was first
 * shown with, so removing another from the selection does not repaint it. At
 * most eight lines are plotted (the palette is validated to eight, and a ninth
 * hue is never invented); the rest are listed as not plotted.
 */

import { useRef, useState } from 'react';

import { listServers } from '@/api/inventory';
import {
  compareMetrics,
  readMetricTemplates,
  type ComparisonSeries,
  type Environment,
} from '@/api/metrics';
import { LineChart } from '@/components/chart/LineChart';
import { describeState, labelOf } from '@/components/chart/format';
import { formatInstant } from '@/components/chart/ticks';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useLatestResponse } from '@/components/useLatestResponse';
import { DEFAULT_RANGE, rangeById, windowFor } from '@/pages/metrics/range';
import { RangePicker } from '@/pages/metrics/RangePicker';

const ENVIRONMENTS: readonly Environment[] = ['development', 'staging', 'production', 'other'];
const MAX_SERVERS = 20;
const MAX_PLOTTED = 8;

type Mode = 'servers' | 'environments';
type Submitted = { metric: string; rangeId: string; mode: Mode; ids: string[]; at: number };

export function Compare(): React.JSX.Element {
  const [mode, setMode] = useState<Mode>('servers');
  const [metric, setMetric] = useState('cpu_utilisation');
  const [rangeId, setRangeId] = useState(DEFAULT_RANGE);
  const [q, setQ] = useState('');
  const [chosenServers, setChosenServers] = useState<Record<string, string>>({});
  const [chosenEnvironments, setChosenEnvironments] = useState<Environment[]>([]);
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  //: Entity → slot, kept for the life of the page. Never reassigned.
  const slots = useRef(new Map<string, number>());

  const { value: templates } = useLatestResponse(readMetricTemplates, []);
  const { value: candidates, loading: searching } = useLatestResponse(
    () => listServers({ q: q.trim() || undefined }),
    [q],
  );
  const { value: result, loading } = useLatestResponse(
    () =>
      submitted === null
        ? Promise.resolve(null)
        : compareMetrics(
            submitted.metric,
            windowFor(rangeById(submitted.rangeId)),
            submitted.mode === 'servers'
              ? { serverIds: submitted.ids }
              : { environments: submitted.ids as Environment[] },
          ),
    [submitted],
  );

  const selected = mode === 'servers' ? Object.keys(chosenServers) : chosenEnvironments;
  const items = candidates?.kind === 'ok' ? candidates.value.items : [];

  const toggleServer = (id: string, label: string) => {
    setChosenServers((current) => {
      if (id in current)
        return Object.fromEntries(Object.entries(current).filter(([k]) => k !== id));
      if (Object.keys(current).length >= MAX_SERVERS) return current;
      return { ...current, [id]: label };
    });
  };

  const slotOf = (id: string): number => {
    const known = slots.current.get(id);
    if (known !== undefined) return known;
    const next = slots.current.size % MAX_PLOTTED;
    slots.current.set(id, next);
    return next;
  };

  const templateList = templates?.kind === 'ok' ? templates.value.templates : [];
  // Booleans and a byte count are not meaningfully comparable on one axis with
  // a utilisation, but each comparison is one metric — so any template is fine.
  const unit = templateList.find((entry) => entry.template === metric)?.unit ?? '';

  const body = result?.kind === 'ok' ? result.value : null;
  const plotted: ComparisonSeries[] =
    body?.series.filter((one) => one.data_state === 'ok' && one.points != null) ?? [];
  const drawn = plotted.slice(0, MAX_PLOTTED);
  const notDrawn = (body?.series ?? []).filter((one) => !drawn.includes(one));

  return (
    <section aria-labelledby="compare-heading">
      <h1 id="compare-heading" className="mb-4 text-xl font-semibold">
        Compare metrics
      </h1>

      <form
        className="mb-6 flex flex-col gap-4 rounded border p-3"
        style={{ borderColor: 'var(--c-border)' }}
        onSubmit={(event) => {
          event.preventDefault();
          if (selected.length === 0) return;
          setSubmitted({ metric, rangeId, mode, ids: [...selected], at: Date.now() });
        }}
      >
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm">
            <span className="mr-2" style={{ color: 'var(--c-text-muted)' }}>
              Metric
            </span>
            <select
              value={metric}
              onChange={(event) => {
                setMetric(event.target.value);
              }}
              className="rounded border px-2 py-1"
              style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
            >
              {(templateList.length > 0
                ? templateList.map((entry) => entry.template)
                : ['cpu_utilisation']
              ).map((id) => (
                <option key={id} value={id}>
                  {labelOf(id)}
                </option>
              ))}
            </select>
          </label>
          <RangePicker value={rangeId} onChange={setRangeId} />
          <fieldset className="text-sm">
            <legend className="sr-only">Compare by</legend>
            {(['servers', 'environments'] as const).map((option) => (
              <label key={option} className="mr-4">
                <input
                  type="radio"
                  name="mode"
                  checked={mode === option}
                  onChange={() => {
                    setMode(option);
                  }}
                  className="mr-1"
                />
                {option === 'servers' ? 'Servers' : 'Environments'}
              </label>
            ))}
          </fieldset>
        </div>

        {mode === 'servers' ? (
          <div>
            <label className="mb-2 block text-sm">
              <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
                Search servers
              </span>
              <input
                type="search"
                value={q}
                placeholder="name or hostname"
                onChange={(event) => {
                  setQ(event.target.value);
                }}
                className="rounded border px-2 py-1"
                style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
              />
            </label>
            {searching && candidates === null && <LoadingState what="servers" />}
            {candidates !== null && candidates.kind !== 'ok' && <ErrorState result={candidates} />}
            {candidates?.kind === 'ok' && items.length === 0 && (
              <EmptyState message="No servers match that search." />
            )}
            <ul className="m-0 grid max-h-48 list-none grid-cols-1 gap-1 overflow-auto p-0 text-sm sm:grid-cols-2">
              {items.map((server) => (
                <li key={server.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={server.id in chosenServers}
                      disabled={
                        !(server.id in chosenServers) &&
                        Object.keys(chosenServers).length >= MAX_SERVERS
                      }
                      onChange={() => {
                        toggleServer(server.id, server.display_name);
                      }}
                      className="mr-2"
                    />
                    {server.display_name}{' '}
                    <span style={{ color: 'var(--c-text-muted)' }}>({server.environment})</span>
                  </label>
                </li>
              ))}
            </ul>
            {candidates?.kind === 'ok' && candidates.value.page.has_more && (
              <p className="mt-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
                More servers match than are listed. Narrow the search to find the rest.
              </p>
            )}
            <p className="mt-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
              {Object.keys(chosenServers).length} of {MAX_SERVERS} selected
            </p>
          </div>
        ) : (
          <fieldset className="text-sm">
            <legend className="mb-1" style={{ color: 'var(--c-text-muted)' }}>
              Environments
            </legend>
            {ENVIRONMENTS.map((environment) => (
              <label key={environment} className="mr-4">
                <input
                  type="checkbox"
                  checked={chosenEnvironments.includes(environment)}
                  onChange={() => {
                    setChosenEnvironments((current) =>
                      current.includes(environment)
                        ? current.filter((entry) => entry !== environment)
                        : [...current, environment],
                    );
                  }}
                  className="mr-1"
                />
                {environment}
              </label>
            ))}
          </fieldset>
        )}

        <div>
          <button
            type="submit"
            disabled={selected.length === 0}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Compare
          </button>
        </div>
      </form>

      {submitted !== null && loading && result === null && <LoadingState what="comparison" />}
      {result !== null && result.kind !== 'ok' && <ErrorState result={result} />}

      {body !== null && (
        <div aria-live="polite">
          <p className="mb-2 text-xs" style={{ color: 'var(--c-text-muted)' }}>
            {labelOf(body.metric)} · {formatInstant(Date.parse(body.start))} to{' '}
            {formatInstant(Date.parse(body.end))} · one point every {body.step_seconds} s · gaps
            mean no data was collected
          </p>
          {body.excluded_count > 0 && (
            <p
              role="note"
              className="mb-3 rounded border px-3 py-2 text-sm"
              style={{ borderColor: 'var(--c-border)' }}
            >
              {body.excluded_count} requested{' '}
              {body.excluded_count === 1 ? 'server was' : 'servers were'} not included: not found,
              or outside your access.
            </p>
          )}
          {body.series.length === 0 ? (
            <EmptyState message="No servers matched this selection." />
          ) : (
            <>
              {drawn.length > 0 ? (
                <LineChart
                  title={labelOf(body.metric)}
                  unit={body.unit || unit}
                  range={[Date.parse(body.start), Date.parse(body.end)]}
                  thresholds={
                    body.thresholds?.warning !== undefined && body.thresholds.critical !== undefined
                      ? { warning: body.thresholds.warning, critical: body.thresholds.critical }
                      : null
                  }
                  series={drawn.map((one) => ({
                    id: one.server_id,
                    label: `${one.display_name} (${one.environment})`,
                    slot: slotOf(one.server_id),
                    samples: (one.points ?? []).map(([t, value]) => ({
                      t: Date.parse(t),
                      value,
                    })),
                  }))}
                />
              ) : (
                <EmptyState message="None of the included servers has data to plot." />
              )}
              {notDrawn.length > 0 && (
                <>
                  <h2 className="mt-4 mb-1 text-sm font-medium">Included but not plotted</h2>
                  <ul className="m-0 list-none p-0 text-sm">
                    {notDrawn.map((one) => (
                      <li
                        key={one.server_id}
                        className="border-t py-1"
                        style={{ borderColor: 'var(--c-border)' }}
                      >
                        {one.display_name} ({one.environment}) —{' '}
                        {one.data_state === 'ok'
                          ? `more than ${String(MAX_PLOTTED)} series; not plotted`
                          : describeState(one.data_state, one.unavailable_reason)}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
