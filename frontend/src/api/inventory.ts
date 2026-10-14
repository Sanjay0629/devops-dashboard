/**
 * Inventory client (API_CONTRACTS.md §14, §18).
 *
 * **Hand-written, and that is a gap rather than a choice.**
 * docs/TECH_STACK.md §11 says TypeScript types are generated from the
 * backend's OpenAPI schema into a committed file and never hand-edited, with
 * CI failing when the two drift. No generator is wired up yet — adding
 * `openapi-typescript` would be a second new frontend dependency in a
 * milestone that agreed to exactly one (React Router) — so these shapes are
 * written by hand and pinned by the backend's own response tests instead.
 * Recorded in PROGRESS.md; the generator is the next change to make here, and
 * it replaces this file wholesale.
 *
 * Nothing here authorises anything. The pages hide controls using the
 * advisory `permissions` array (docs/SECURITY.md §6); the backend answers
 * `403`, `404` or `423` on its own account whatever the UI renders.
 */

import { request } from '@/api/session';

export interface CursorPage {
  limit: number;
  next_cursor: string | null;
  has_more: boolean;
  /** Always null on a cursor page, by §5. Not a number waiting to arrive. */
  total: null;
}

/** The §4 partial-data envelope. `null` is unknown and never a substitute. */
export interface DataSection {
  data_state: 'ok' | 'stale' | 'unavailable' | 'unsupported' | 'not_configured';
  collected_at: string | null;
  source: string | null;
  stale_after_seconds: number | null;
  unavailable_reason: string | null;
}

export interface ServerSummary {
  id: string;
  cloud_account_id: string;
  provider_resource_id: string;
  hostname: string;
  display_name: string;
  operating_system: string;
  os_version: string | null;
  environment: string;
  region: string | null;
  private_ip: string | null;
  owner_team: string | null;
  management_method: string;
  monitoring_enabled: boolean;
  metrics_instance: string | null;
  agent_status: string;
  status: string;
  last_seen_at: string | null;
  registered_at: string;
  created_at: string;
  updated_at: string;
}

export interface AssociatedApplication {
  id: string;
  name: string;
  slug: string;
  role_on_server: string | null;
}

export interface ServerDetail extends ServerSummary {
  provider_metadata: Record<string, unknown>;
  /** null when the host has never been seen — that is not staleness. */
  stale: boolean | null;
  health: DataSection & { state: string | null };
  applications: AssociatedApplication[];
  registered_service_count: number;
  open_alerts: DataSection & { counts: { critical: number; warning: number } | null };
  management_capabilities: DataSection & { capabilities: Record<string, boolean> | null };
}

export interface ApplicationSummary {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  owner_team: string | null;
  repository_reference: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface AssociatedServer {
  id: string;
  hostname: string;
  display_name: string;
  environment: string;
  status: string;
  role_on_server: string | null;
}

export interface ApplicationDetail extends ApplicationSummary {
  health_check_config: { checks: unknown[] };
  servers: AssociatedServer[];
  registered_service_count: number;
  deployment_targets: DataSection;
}

export interface Page<T> {
  items: T[];
  page: CursorPage;
  demo_mode: boolean;
}

/** One field-level problem from a §8 `422`, keyed for the form to render. */
export interface FieldProblem {
  field: string;
  code: string;
  message: string;
  allowed?: string[] | undefined;
}

/**
 * What a call returned, as data rather than as an exception.
 *
 * A failed request is a state the page renders — `forbidden`, `not_found`,
 * `read_only` and `unreachable` each mean something different to the person
 * looking at it, and collapsing them into one "error" is how a UI ends up
 * telling someone the server does not exist when they simply lack the scope.
 */
export type Result<T> =
  | { kind: 'ok'; value: T }
  | { kind: 'invalid'; problems: FieldProblem[]; message: string }
  | { kind: 'conflict'; message: string }
  | { kind: 'forbidden' }
  | { kind: 'read_only' }
  | { kind: 'not_found' }
  | { kind: 'unreachable'; reason: string };

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: {
      fields?: { loc?: string[]; code?: string; message?: string; allowed?: string[] }[];
    };
  };
}

function problemsOf(body: ErrorEnvelope): FieldProblem[] {
  const fields = body.error?.details?.fields ?? [];
  return fields.map((entry) => ({
    // The last element of `loc` is the field name; the first is the section
    // ("body", "query", "header"), which the form does not need.
    field: entry.loc?.[entry.loc.length - 1] ?? '',
    code: entry.code ?? 'invalid',
    message: entry.message ?? 'Invalid value.',
    allowed: entry.allowed,
  }));
}

/**
 * Is this parsed body the shape we asked for?
 *
 * Checked rather than asserted, exactly as `session.ts` checks its own
 * response: a `200` carrying something unexpected is a backend that changed
 * under us, and casting it would push the failure into a render where it
 * reads as a UI bug. Only the fields the pages actually rely on are checked —
 * a full validator here would be the generated types' job (§11).
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isPage(value: unknown): boolean {
  return isRecord(value) && Array.isArray(value.items) && isRecord(value.page);
}

async function resultOf<T>(
  response: Response,
  isExpected: (value: unknown) => boolean,
): Promise<Result<T>> {
  // 204 has no body at all, so parsing one would throw on every successful
  // delete. The contract uses it for retire and for removing an association.
  if (response.status === 204) return { kind: 'ok', value: null as T };
  if (response.ok) {
    const body: unknown = await response.json();
    if (!isExpected(body)) return { kind: 'unreachable', reason: 'Unexpected response shape' };
    return { kind: 'ok', value: body as T };
  }
  if (response.status === 403) return { kind: 'forbidden' };
  if (response.status === 404) return { kind: 'not_found' };
  if (response.status === 423) return { kind: 'read_only' };

  let body: ErrorEnvelope = {};
  try {
    body = (await response.json()) as ErrorEnvelope;
  } catch {
    return { kind: 'unreachable', reason: `Unexpected status ${String(response.status)}` };
  }
  const message = body.error?.message ?? `Unexpected status ${String(response.status)}`;
  if (response.status === 422) {
    return { kind: 'invalid', problems: problemsOf(body), message };
  }
  if (response.status === 409) return { kind: 'conflict', message };
  return { kind: 'unreachable', reason: message };
}

async function call<T>(
  path: string,
  init: RequestInit = {},
  isExpected: (value: unknown) => boolean = isRecord,
): Promise<Result<T>> {
  try {
    return await resultOf<T>(await request(path, init), isExpected);
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  }
}

function queryString(filters: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') params.set(key, value);
  }
  const rendered = params.toString();
  return rendered ? `?${rendered}` : '';
}

/**
 * A client-generated key per logical intent (§7).
 *
 * `crypto.randomUUID` rather than a library: it is in every browser this
 * project supports, and docs/TECH_STACK.md §10 rung 2 applies.
 */
export function idempotencyKey(): string {
  return crypto.randomUUID();
}

export async function listServers(
  filters: Record<string, string | undefined> = {},
): Promise<Result<Page<ServerSummary>>> {
  return call(`/servers${queryString(filters)}`, {}, isPage);
}

export async function readServer(id: string): Promise<Result<ServerDetail>> {
  return call(`/servers/${encodeURIComponent(id)}`);
}

export async function registerServer(body: Record<string, unknown>): Promise<Result<ServerDetail>> {
  return call('/servers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey() },
    body: JSON.stringify(body),
  });
}

export async function updateServer(
  id: string,
  body: Record<string, unknown>,
): Promise<Result<ServerDetail>> {
  return call(`/servers/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function retireServer(id: string): Promise<Result<null>> {
  return call(`/servers/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function associateApplication(
  serverId: string,
  applicationId: string,
): Promise<Result<ServerDetail>> {
  return call(`/servers/${encodeURIComponent(serverId)}/applications`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ application_id: applicationId }),
  });
}

export async function removeAssociation(
  serverId: string,
  applicationId: string,
): Promise<Result<null>> {
  return call(
    `/servers/${encodeURIComponent(serverId)}/applications/${encodeURIComponent(applicationId)}`,
    { method: 'DELETE' },
  );
}

export async function listApplications(
  filters: Record<string, string | undefined> = {},
): Promise<Result<Page<ApplicationSummary>>> {
  return call(`/applications${queryString(filters)}`, {}, isPage);
}

export async function readApplication(id: string): Promise<Result<ApplicationDetail>> {
  return call(`/applications/${encodeURIComponent(id)}`);
}
