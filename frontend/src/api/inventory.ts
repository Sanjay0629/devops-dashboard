/**
 * Inventory client (API_CONTRACTS.md §14, §18).
 *
 * **The response shapes are generated, not written here.** `schema.ts` is
 * produced from the backend's OpenAPI schema by `npm run generate:api`
 * (docs/TECH_STACK.md §11, CLAUDE.md §5) and the `contracts` CI job fails if
 * it drifts, so a backend model change breaks the build rather than the
 * dashboard. The aliases below exist only to give the generated
 * `components['schemas'][...]` entries the short names the pages already use;
 * never widen or re-declare one by hand.
 *
 * What stays hand-written is the part the schema has nothing to say about:
 * `Result`, `FieldProblem` and the fetch wrappers are this client's error
 * handling, not API shapes.
 *
 * Nothing here authorises anything. The pages hide controls using the
 * advisory `permissions` array (docs/SECURITY.md §6); the backend answers
 * `403`, `404` or `423` on its own account whatever the UI renders.
 */

import type { components } from '@/api/schema';
import { request } from '@/api/session';

type Schemas = components['schemas'];

export type CursorPage = Schemas['CursorPage'];
/** The §4 partial-data envelope. `null` is unknown and never a substitute. */
export type DataSection = Pick<
  // The base class has no schema of its own once no endpoint returns it bare; every section
  // that extends it carries these five fields, so any one of them names the envelope.
  Schemas['DeploymentTargetsSection'],
  'data_state' | 'collected_at' | 'source' | 'stale_after_seconds' | 'unavailable_reason'
>;
export type ServerSummary = Schemas['ServerSummary'];
export type ServerDetail = Schemas['ServerDetail'];
export type AssociatedApplication = Schemas['AssociatedApplication'];
export type ApplicationSummary = Schemas['ApplicationSummary'];
export type ApplicationDetail = Schemas['ApplicationDetail'];
export type AssociatedServer = Schemas['AssociatedServer'];
/** §14.4's mutable fields. The immutable ones are not in it to be sent. */
export type ServerUpdate = Schemas['ServerUpdateRequest'];
export type AssociateApplication = Schemas['AssociateApplicationRequest'];

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

export async function resultOf<T>(
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

export async function call<T>(
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

export function queryString(filters: Record<string, string | undefined>): string {
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

/**
 * §14.4. `unmodifiedSince` is the `updated_at` the caller's copy carries.
 *
 * Sent as `If-Unmodified-Since`, so a row someone else edited in the meantime
 * answers `409` instead of being silently overwritten. The conversion to an
 * HTTP-date happens here rather than in the page: an HTTP-date has no
 * sub-second part, and the backend compares at whole-second resolution to
 * match (`router_servers._unmodified_since`).
 *
 * An unparseable timestamp sends **no** header rather than a malformed one —
 * a malformed date is a `422`, which would read to the operator as a problem
 * with the fields they just edited.
 */
export async function updateServer(
  id: string,
  body: ServerUpdate,
  unmodifiedSince?: string,
): Promise<Result<ServerDetail>> {
  const at = unmodifiedSince === undefined ? null : new Date(unmodifiedSince);
  const precondition =
    at !== null && !Number.isNaN(at.getTime()) ? { 'If-Unmodified-Since': at.toUTCString() } : {};
  return call(`/servers/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...precondition },
    body: JSON.stringify(body),
  });
}

export async function retireServer(id: string): Promise<Result<null>> {
  return call(`/servers/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function associateApplication(
  serverId: string,
  applicationId: string,
  roleOnServer?: string,
): Promise<Result<ServerDetail>> {
  const body: AssociateApplication = { application_id: applicationId };
  // Omitted rather than sent as `null`: `role_on_server` has a `min_length`,
  // so an empty box is "not supplied", not "the empty string".
  if (roleOnServer !== undefined && roleOnServer !== '') body.role_on_server = roleOnServer;
  return call(`/servers/${encodeURIComponent(serverId)}/applications`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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
