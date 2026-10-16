/**
 * Value formatting for a metric's `unit` (API_CONTRACTS.md §15.4).
 *
 * `null` never reaches a number formatter: the callers branch first, because
 * the thing to show for "no value" is a reason, not a formatted zero.
 */

const BYTE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];

function bytes(value: number): string {
  let scaled = Math.abs(value);
  let unit = 0;
  while (scaled >= 1024 && unit < BYTE_UNITS.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  const sign = value < 0 ? '-' : '';
  return `${sign}${scaled.toFixed(unit === 0 ? 0 : 1)} ${BYTE_UNITS[unit] ?? 'B'}`;
}

function duration(seconds: number): string {
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return `${String(days)}d ${String(hours)}h`;
  if (hours > 0) return `${String(hours)}h ${String(minutes)}m`;
  return `${String(minutes)}m`;
}

export function formatValue(value: number, unit: string): string {
  switch (unit) {
    case 'percent':
      return `${value.toFixed(1)} %`;
    case 'bytes':
      return bytes(value);
    case 'bytes_per_second':
      return `${bytes(value)}/s`;
    case 'seconds':
      return duration(value);
    case 'load':
      return value.toFixed(2);
    case 'boolean':
      // `0` is a real answer here: the exporter was not reachable.
      return value >= 1 ? 'Up' : 'Down';
    default:
      return String(value);
  }
}

/** Plain-language names for the templates the UI draws. */
export const TEMPLATE_LABEL: Record<string, string> = {
  cpu_utilisation: 'CPU utilisation',
  memory_utilisation: 'Memory utilisation',
  memory_available_bytes: 'Memory available',
  disk_utilisation: 'Disk utilisation',
  disk_available_bytes: 'Disk space available',
  disk_io: 'Disk I/O',
  network_throughput: 'Network throughput',
  load_average: 'Load average (1 min)',
  uptime_seconds: 'Uptime',
  agent_up: 'Monitoring agent',
};

export const labelOf = (template: string): string => TEMPLATE_LABEL[template] ?? template;

/** Human text for an `unavailable_reason`, so a code never reaches the screen. */
const REASON_TEXT: Record<string, string> = {
  provider_timeout: 'Prometheus timed out',
  provider_unreachable: 'Prometheus is unreachable',
  provider_error: 'Prometheus returned an error',
  circuit_open: 'Prometheus is failing; queries are paused',
  no_data: 'No data was returned for this host',
  monitoring_disabled: 'Monitoring is disabled for this server',
  no_metrics_instance: 'No metrics instance is registered for this server',
  prometheus_not_configured: 'Prometheus is not configured',
  unsupported_os: 'Not supported for this operating system',
};

export const reasonText = (reason: string | null | undefined): string =>
  reason == null ? '' : (REASON_TEXT[reason] ?? reason);

const STATE_TEXT: Record<string, string> = {
  ok: 'Collected',
  stale: 'Stale',
  unavailable: 'Unavailable',
  unsupported: 'Not supported',
  not_configured: 'Not configured',
};

/** "Unavailable — No data was returned for this host". */
export function describeState(state: string, reason?: string | null): string {
  const base = STATE_TEXT[state] ?? state;
  const why = reasonText(reason);
  return why === '' ? base : `${base} — ${why}`;
}
