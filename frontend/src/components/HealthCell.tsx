/**
 * How host health is shown (API_CONTRACTS.md §13.2, §14.3, M2.5).
 *
 * Three different facts that used to share one grey "Unknown", kept apart:
 *
 * - `state: null` — **nothing was evaluated.** Prometheus is unconfigured,
 *   silent or stale, so no host was judged. Rendered as "Not evaluated", never
 *   as a badge: a badge is a verdict, and there is none.
 * - `state: 'unknown'` — it **was** evaluated and nothing is known about this
 *   host (retired, unmonitored, unsupported, or never scraped).
 * - a real state — a verdict from `last_seen_at`.
 *
 * And a host that was never scraped is "Never seen", not a time and not
 * "stale": no observation is not an old one.
 */

import { StatusBadge } from '@/components/StatusBadge';

export function HealthBadge({ state }: { state: string | null | undefined }): React.JSX.Element {
  if (state == null) {
    return <span style={{ color: 'var(--c-text-muted)' }}>Not evaluated</span>;
  }
  return <StatusBadge value={state} />;
}

export function LastSeen({ at }: { at: string | null | undefined }): React.JSX.Element {
  if (at == null) {
    return <span style={{ color: 'var(--c-text-muted)' }}>Never seen</span>;
  }
  return <time dateTime={at}>{new Date(at).toLocaleString()}</time>;
}
