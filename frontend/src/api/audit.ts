/**
 * Audit trail client (API_CONTRACTS.md §24).
 *
 * Reading the trail is itself audited (`AUDIT_QUERIED`), so this client is deliberately small and
 * the page deliberately never asks twice for what it already holds: one request per search, one
 * per page turned, and **no polling and no refetch on focus**. The export's status is a separate
 * endpoint that is not an audit read, and is the one thing that is polled.
 *
 * Nothing here can write an event. There is no function for it, and the backend has no route.
 */

import { call, queryString, type Result } from '@/api/inventory';
import type { components } from '@/api/schema';
import { request } from '@/api/session';

type Schemas = components['schemas'];

export type AuditEvent = Schemas['AuditEventView'];
export type AuditEventList = Schemas['AuditEventList'];
export type ExportAccepted = Schemas['ExportAccepted'];
export type ExportStatus = Schemas['ExportStatus'];

export interface AuditFilters {
  actionType: string;
  outcome: string;
  environment: string;
  actorType: string;
  actorId: string;
  targetId: string;
  correlationId: string;
  from: string;
  to: string;
  cursor?: string | undefined;
}

export const EMPTY_AUDIT_FILTERS: AuditFilters = {
  actionType: '',
  outcome: '',
  environment: '',
  actorType: '',
  actorId: '',
  targetId: '',
  correlationId: '',
  from: '',
  to: '',
};

/** The exact token the backend accepts for `action_type`: it is a match, never a pattern. */
export const ACTION_TOKEN = /^[A-Z_]{1,64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function auditQuery(filters: AuditFilters): string {
  return queryString({
    action_type: filters.actionType,
    outcome: filters.outcome,
    environment: filters.environment,
    actor_type: filters.actorType,
    actor_id: filters.actorId.trim(),
    target_id: filters.targetId.trim(),
    correlation_id: filters.correlationId.trim(),
    from: filters.from,
    to: filters.to,
    cursor: filters.cursor,
  });
}

export function listAuditEvents(filters: AuditFilters): Promise<Result<AuditEventList>> {
  return call<AuditEventList>(
    `/audit-events${auditQuery(filters)}`,
    {},
    (value) => isRecord(value) && Array.isArray(value.items) && isRecord(value.page),
  );
}

// --- the export (§24.3 – §24.5) -----------------------------------------------------------------

/** A refusal with its closed reason, for the two answers a person can act on. */
export type Requested =
  | { kind: 'ok'; accepted: ExportAccepted }
  | { kind: 'in_progress' }
  | Exclude<Result<never>, { kind: 'ok' | 'conflict' }>
  | { kind: 'conflict'; message: string };

/** §24.3. `key` is generated once per dialog so a second click re-sends the same intent. */
export async function requestExport(from: string, to: string, key: string): Promise<Requested> {
  try {
    const response = await request('/audit-events/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
      body: JSON.stringify({ from, to, format: 'jsonl' }),
    });
    if (response.ok) {
      const body: unknown = await response.json();
      if (!isRecord(body) || typeof body.export_id !== 'string') {
        return { kind: 'unreachable', reason: 'Unexpected response shape' };
      }
      return { kind: 'ok', accepted: body as ExportAccepted };
    }
    if (response.status === 403) return { kind: 'forbidden' };
    if (response.status === 423) return { kind: 'read_only' };
    const body = (await response.json().catch(() => ({}))) as {
      error?: { code?: string; message?: string; details?: { fields?: { message?: string }[] } };
    };
    if (response.status === 409 && body.error?.code === 'export_in_progress') {
      return { kind: 'in_progress' };
    }
    const message = body.error?.message ?? `Unexpected status ${String(response.status)}`;
    if (response.status === 409) return { kind: 'conflict', message };
    if (response.status === 422) {
      const field = body.error?.details?.fields?.[0]?.message;
      return { kind: 'invalid', problems: [], message: field ?? message };
    }
    return { kind: 'unreachable', reason: message };
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  }
}

/** §24.4. Not an audit read, so it may be polled. */
export function exportStatus(id: string): Promise<Result<ExportStatus>> {
  return call<ExportStatus>(
    `/audit-events/exports/${encodeURIComponent(id)}`,
    {},
    (value) => isRecord(value) && typeof value.status === 'string',
  );
}

export type Downloaded =
  | { kind: 'ok'; blob: Blob; filename: string }
  | { kind: 'spent' }
  | Exclude<Result<never>, { kind: 'ok' | 'conflict' }>;

/**
 * §24.5. **The file leaves exactly once**: a second call is a `409` and never a file. The caller
 * must save the blob it gets here immediately; there is no second chance, and the page says so
 * before the click rather than after.
 */
export async function downloadExport(id: string): Promise<Downloaded> {
  try {
    const response = await request(`/audit-events/exports/${encodeURIComponent(id)}/download`);
    if (response.ok) {
      return { kind: 'ok', blob: await response.blob(), filename: `audit-export-${id}.jsonl` };
    }
    if (response.status === 403) return { kind: 'forbidden' };
    if (response.status === 404) return { kind: 'not_found' };
    if (response.status === 409) return { kind: 'spent' };
    return { kind: 'unreachable', reason: `Unexpected status ${String(response.status)}` };
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  }
}

/** What a failed export means, in words. The code is closed (§24.3). */
export function exportFailureText(code: string | null): string {
  switch (code) {
    case 'export_too_large':
      return 'That window holds more than an export may (100 000 events or 64 MiB). Nothing was truncated; ask for a shorter window.';
    case 'requester_not_authorised':
      return 'Your access no longer allows this export, so none was produced.';
    case 'internal_error':
      return 'The export could not be built. Try again; if it keeps failing, tell an administrator.';
    default:
      return 'The export failed.';
  }
}
