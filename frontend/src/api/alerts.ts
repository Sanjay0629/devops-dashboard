/**
 * Alert client (API_CONTRACTS.md §20).
 *
 * Shapes are the generated ones; the transport is `session.request` and the error ladder
 * is `inventory.ts`'s `resultOf`. Two things worth knowing:
 *
 * - **Everything in an alert is untrusted text** (SECURITY.md §21): the summary, the
 *   stored labels and annotations came from a payload. This module returns them as
 *   strings and the pages render them as text nodes, never as markup.
 * - **There is no resolve.** Only the source resolves an alert, so there is deliberately
 *   no function here that could ask for it; acknowledge and assign are the whole of what
 *   a person may do (OPEN-44).
 */

import type { components } from '@/api/schema';
import { call, type Result } from '@/api/inventory';

type Schemas = components['schemas'];

export type AlertSummary = Schemas['AlertSummary'];
export type AlertDetail = Schemas['AlertDetail'];
export type AlertList = Schemas['AlertList'];
export type AlertStatus = AlertSummary['status'];
export type AlertSeverity = AlertSummary['severity'];
export type HistoryEntry = Schemas['HistoryEntry'];
export type ConditionCoverage = Schemas['ConditionCoverage'];

export interface AlertFilters {
  /** Empty means the server's default: what is still open. */
  statuses: AlertStatus[];
  severities: AlertSeverity[];
  environment: string;
  q: string;
  cursor?: string | undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** Repeated parameters (`status=A&status=B`), which `queryString` cannot express. */
export function alertsQuery(filters: AlertFilters): string {
  const params = new URLSearchParams();
  for (const status of filters.statuses) params.append('status', status);
  for (const severity of filters.severities) params.append('severity', severity);
  if (filters.environment !== '') params.set('environment', filters.environment);
  if (filters.q.trim() !== '') params.set('q', filters.q.trim());
  if (filters.cursor !== undefined) params.set('cursor', filters.cursor);
  const text = params.toString();
  return text === '' ? '' : `?${text}`;
}

export function listAlerts(filters: AlertFilters): Promise<Result<AlertList>> {
  return call<AlertList>(
    `/alerts${alertsQuery(filters)}`,
    {},
    (value) => isRecord(value) && Array.isArray(value.items) && isRecord(value.page),
  );
}

export function getAlert(alertId: string): Promise<Result<AlertDetail>> {
  return call<AlertDetail>(
    `/alerts/${encodeURIComponent(alertId)}`,
    {},
    (value) =>
      isRecord(value) && typeof value.alert_id === 'string' && Array.isArray(value.history),
  );
}

function post(alertId: string, action: 'acknowledge' | 'assign', body: object) {
  return call<AlertDetail>(
    `/alerts/${encodeURIComponent(alertId)}/${action}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    (value) => isRecord(value) && typeof value.alert_id === 'string',
  );
}

export function acknowledgeAlert(alertId: string, note: string): Promise<Result<AlertDetail>> {
  return post(alertId, 'acknowledge', note.trim() === '' ? {} : { note: note.trim() });
}

/** `null` unassigns. */
export function assignAlert(
  alertId: string,
  assigneeId: string | null,
): Promise<Result<AlertDetail>> {
  return post(alertId, 'assign', { assignee_id: assigneeId });
}
