/**
 * Dashboard client (API_CONTRACTS.md §13).
 *
 * Shapes come from the generated schema, like the rest of the API surface —
 * this file is the first client written *after* `npm run generate:api`
 * existed, so there is no hand-written interface here to delete later.
 *
 * It reuses `inventory.ts`'s `Result` and its `call`, rather than repeating
 * the status-code handling: a `403` and a `404` mean the same things on this
 * endpoint as on every other, and a second copy of that ladder is a second
 * place for them to start meaning something different.
 */

import type { components } from '@/api/schema';
import { call, queryString, type Result } from '@/api/inventory';

type Schemas = components['schemas'];

export type DashboardSummary = Schemas['DashboardSummary'];
export type DashboardHealthList = Schemas['DashboardHealthList'];
export type ServerHealthRow = Schemas['ServerHealthRow'];
export type ServerCounts = Schemas['ServerCounts'];
export type ServerHealthRollup = Schemas['ServerHealthRollup'];
export type HealthChange = Schemas['HealthChange'];

/**
 * The filters §13.1 and §13.2 share, as the UI holds them.
 *
 * A type alias rather than an interface on purpose: only an alias gets an
 * implicit index signature, which is what lets `queryString` take it.
 */
export type DashboardFilters = {
  environment?: string | undefined;
  provider?: string | undefined;
  owner_team?: string | undefined;
};

/** §13.2's `health_status`. `critical` is accepted by the API and never produced. */
export const HEALTH_STATUSES = ['healthy', 'warning', 'stale', 'unreachable', 'unknown'] as const;
export type HealthStatus = (typeof HEALTH_STATUSES)[number];

/**
 * Is this parsed body the shape we asked for?
 *
 * Checked rather than cast, exactly as `isPage` checks a list response: a
 * `200` carrying something unexpected is a backend that changed under us, and
 * casting it pushes the failure into a render, where it reads as a UI bug and
 * takes the whole page down instead of showing an error state. Only the
 * sections the dashboard reads are checked; a full validator would be the
 * generated types' job (TECH_STACK.md §11).
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isSummary(value: unknown): boolean {
  return isRecord(value) && isRecord(value.servers) && isRecord(value.utilisation);
}

function isHealthList(value: unknown): boolean {
  return isRecord(value) && Array.isArray(value.items) && isRecord(value.page);
}

export async function readSummary(
  filters: DashboardFilters = {},
): Promise<Result<DashboardSummary>> {
  return call(`/dashboard/summary${queryString(filters)}`, {}, isSummary);
}

export async function readHealthList(
  filters: DashboardFilters & {
    cursor?: string | undefined;
    limit?: string | undefined;
    health_status?: HealthStatus | undefined;
  } = {},
): Promise<Result<DashboardHealthList>> {
  return call(`/dashboard/health${queryString(filters)}`, {}, isHealthList);
}
