/**
 * systemd service client (API_CONTRACTS.md §16).
 *
 * Shapes are the generated ones; the transport and the error ladder are
 * `inventory.ts`'s `call`, so a `404` on a unit outside the caller's scope
 * means here what it means on the server pages.
 *
 * Reads only. Registration and editing (§16.3, §16.5, §16.6) exist on the
 * backend and have no screen yet, and a client function with no caller would
 * be an untested way to reach them.
 */

import { call, queryString, type Page, type Result } from '@/api/inventory';
import type { components } from '@/api/schema';

type Schemas = components['schemas'];

export type ServiceItem = Schemas['ServiceItem'];
export type ServiceDetail = Schemas['ServiceDetail'];
export type ServiceState = ServiceItem['state'];

function isPage(value: unknown): boolean {
  return (
    typeof value === 'object' &&
    value !== null &&
    Array.isArray((value as { items?: unknown }).items) &&
    typeof (value as { page?: unknown }).page === 'object'
  );
}

/**
 * §16.1, or §16.2 when `serverId` is given.
 *
 * The state filter is sent to the backend and not applied to a fetched page:
 * it selects on the state a row *shows* (host-aware), which only the backend
 * can compute, and a client-side filter would be filtering one page of it.
 */
export function listServices(
  filters: Record<string, string | undefined> = {},
  serverId?: string,
): Promise<Result<Page<ServiceItem>>> {
  const base =
    serverId === undefined ? '/services' : `/servers/${encodeURIComponent(serverId)}/services`;
  return call(`${base}${queryString(filters)}`, {}, isPage);
}

/** §16.4 — a live reading, the one read that asks Prometheus now. */
export function readService(serverId: string, serviceName: string): Promise<Result<ServiceDetail>> {
  return call(
    `/servers/${encodeURIComponent(serverId)}/services/${encodeURIComponent(serviceName)}`,
  );
}
