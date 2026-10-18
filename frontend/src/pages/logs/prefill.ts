/**
 * What another page may pre-fill the log search with.
 *
 * Carried in the router's **history state**, never in the URL (`OPEN-20`,
 * `OPEN-38`): a filter in a query string lands in browser history, referrers and
 * every proxy's access log, and the log search deliberately does not allow that.
 * The cost, accepted: a reload loses the pre-fill and shows an empty form.
 */

export interface LogsPrefill {
  servers: { id: string; label: string }[];
  service?: string;
  rangeId?: string;
}

export function isLogsPrefill(value: unknown): value is LogsPrefill {
  if (typeof value !== 'object' || value === null) return false;
  const { servers, service, rangeId } = value as Record<string, unknown>;
  return (
    Array.isArray(servers) &&
    servers.every(
      (s) =>
        typeof s === 'object' &&
        s !== null &&
        typeof (s as Record<string, unknown>).id === 'string' &&
        typeof (s as Record<string, unknown>).label === 'string',
    ) &&
    (service === undefined || typeof service === 'string') &&
    (rangeId === undefined || typeof rangeId === 'string')
  );
}

export interface LogRange {
  id: string;
  label: string;
  seconds: number;
}

/** The time ranges offered. 7 days is the contract's maximum (§19.1). */
export const LOG_RANGES: readonly LogRange[] = [
  { id: '15m', label: 'Last 15 minutes', seconds: 900 },
  { id: '1h', label: 'Last hour', seconds: 3_600 },
  { id: '6h', label: 'Last 6 hours', seconds: 21_600 },
  { id: '24h', label: 'Last 24 hours', seconds: 86_400 },
  { id: '7d', label: 'Last 7 days', seconds: 604_800 },
];

export const DEFAULT_LOG_RANGE = '1h';

export function logRangeById(id: string): LogRange {
  return LOG_RANGES.find((range) => range.id === id) ?? (LOG_RANGES[1] as LogRange);
}
