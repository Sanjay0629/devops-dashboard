/**
 * Metrics client (API_CONTRACTS.md §15).
 *
 * Shapes are the generated ones, and the transport and error ladder are
 * `inventory.ts`'s `call` — a `404` on a server outside the caller's scope
 * means the same thing here as on the detail page.
 *
 * **No query ever leaves this file.** A caller names template ids; the backend
 * renders the PromQL (AD-04). There is deliberately no parameter here that
 * could carry one.
 */

import type { components } from '@/api/schema';
import { call, type Result } from '@/api/inventory';

type Schemas = components['schemas'];

export type CurrentMetrics = Schemas['CurrentMetrics'];
export type MetricReading = Schemas['MetricReading'];
export type RangeMetrics = Schemas['RangeMetrics'];
export type MetricSeries = Schemas['MetricSeries'];
export type MetricComparison = Schemas['MetricComparison'];
export type ComparisonSeries = Schemas['ComparisonSeries'];
export type MetricTemplateList = Schemas['MetricTemplateList'];
type CompareRequest = Schemas['CompareRequest'];
export type Environment = NonNullable<CompareRequest['environments']>[number];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

const hasList = (key: string) => (value: unknown) => isRecord(value) && Array.isArray(value[key]);

export function readCurrentMetrics(
  serverId: string,
  metrics?: readonly string[],
): Promise<Result<CurrentMetrics>> {
  const params = new URLSearchParams();
  for (const id of metrics ?? []) params.append('metrics', id);
  const rendered = params.toString();
  return call(
    `/servers/${encodeURIComponent(serverId)}/metrics${rendered ? `?${rendered}` : ''}`,
    {},
    hasList('metrics'),
  );
}

export interface RangeWindow {
  from: Date;
  to: Date;
  stepSeconds: number;
}

export function readRangeMetrics(
  serverId: string,
  window: RangeWindow & { metrics: readonly string[] },
): Promise<Result<RangeMetrics>> {
  const params = new URLSearchParams();
  for (const id of window.metrics) params.append('metrics', id);
  params.set('from', window.from.toISOString());
  params.set('to', window.to.toISOString());
  params.set('step_seconds', String(window.stepSeconds));
  return call(
    `/servers/${encodeURIComponent(serverId)}/metrics/range?${params.toString()}`,
    {},
    hasList('series'),
  );
}

export type Selection = { serverIds: readonly string[] } | { environments: readonly Environment[] };

export function compareMetrics(
  metric: string,
  window: RangeWindow,
  selection: Selection,
): Promise<Result<MetricComparison>> {
  const body: CompareRequest = {
    metric,
    from: window.from.toISOString(),
    to: window.to.toISOString(),
    step_seconds: window.stepSeconds,
    ...('serverIds' in selection
      ? { server_ids: [...selection.serverIds] }
      : { environments: [...selection.environments] }),
  };
  return call(
    '/metrics/compare',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    hasList('series'),
  );
}

export function readMetricTemplates(): Promise<Result<MetricTemplateList>> {
  return call('/metrics/templates', {}, hasList('templates'));
}
