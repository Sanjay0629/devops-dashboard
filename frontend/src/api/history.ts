/**
 * Operations history client (API_CONTRACTS.md §23).
 *
 * Shapes are the generated ones. The query is built here because it repeats parameters
 * (`execution_status=A&execution_status=B`), which `queryString` cannot express. Nothing here
 * widens anything: a filter only narrows what the backend already decided the caller may see.
 */

import { call, type Result } from '@/api/inventory';
import type { components } from '@/api/schema';

type Schemas = components['schemas'];

export type HistoryItem = Schemas['HistoryItem'];
export type HistoryList = Schemas['HistoryList'];
export type HistoryStatus = HistoryItem['execution_status'];
export type HistorySort = 'created_at' | 'completed_at';

export interface HistoryFilters {
  statuses: HistoryStatus[];
  actions: string[];
  environments: string[];
  /** A UUID. `requested_by` narrows to one person's requests and cannot widen scope. */
  requestedBy: string;
  /** RFC 3339 with an offset, or empty for the backend's default (the last 7 days). */
  from: string;
  to: string;
  sort: HistorySort;
  cursor?: string | undefined;
}

export const EMPTY_FILTERS: HistoryFilters = {
  statuses: [],
  actions: [],
  environments: [],
  requestedBy: '',
  from: '',
  to: '',
  sort: 'created_at',
};

/** A `datetime-local` value as an RFC 3339 instant, or `''` when it is empty or not a date. */
export function toInstant(local: string): string {
  if (local === '') return '';
  const parsed = new Date(local);
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function historyQuery(filters: HistoryFilters): string {
  const params = new URLSearchParams();
  for (const status of filters.statuses) params.append('execution_status', status);
  for (const action of filters.actions) params.append('action_type', action);
  for (const environment of filters.environments) params.append('environment', environment);
  if (filters.requestedBy !== '') params.set('requested_by', filters.requestedBy);
  if (filters.from !== '') params.set('from', filters.from);
  if (filters.to !== '') params.set('to', filters.to);
  if (filters.sort !== 'created_at') params.set('sort', filters.sort);
  if (filters.cursor !== undefined) params.set('cursor', filters.cursor);
  const text = params.toString();
  return text === '' ? '' : `?${text}`;
}

const isList = (value: unknown): boolean =>
  isRecord(value) && Array.isArray(value.items) && isRecord(value.page);

/** §23.1, or §23.2 when a server is given. */
export function listOperations(
  filters: HistoryFilters,
  serverId?: string,
): Promise<Result<HistoryList>> {
  const base =
    serverId === undefined ? '/operations' : `/servers/${encodeURIComponent(serverId)}/operations`;
  return call<HistoryList>(`${base}${historyQuery(filters)}`, {}, isList);
}
