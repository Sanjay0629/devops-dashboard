/**
 * The wording under a service's state badge: when it was collected, or why it
 * was not (PRD §4.5). Separate from the component so its sentences can be
 * tested directly and so the component file exports only components.
 */

import type { ServiceItem } from '@/api/services';
import { describeState } from '@/components/chart/format';
import { formatInstant } from '@/components/chart/ticks';

export type Evidence = Pick<
  ServiceItem,
  'state' | 'data_state' | 'unavailable_reason' | 'collected_at' | 'server_reachable'
>;

/** What the badge's evidence line says. Exported so a test can pin the wording. */
export function evidenceLine(item: Evidence): string {
  const collected =
    item.collected_at == null ? null : formatInstant(new Date(item.collected_at).getTime());
  const parts: string[] = [];
  if (item.server_reachable === false) parts.push('Host unreachable');
  if (item.data_state === 'ok') {
    parts.push(collected === null ? 'Never collected' : `Collected ${collected}`);
  } else {
    parts.push(describeState(item.data_state, item.unavailable_reason));
    // A stale or failed reading still has a last good time, and saying so is
    // the difference between "unknown since a minute ago" and "since Tuesday".
    parts.push(collected === null ? 'Never collected' : `Last collected ${collected}`);
  }
  return parts.join(' · ');
}
