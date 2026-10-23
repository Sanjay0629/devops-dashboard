/**
 * Deployment targets client (API_CONTRACTS.md §21.8 – §21.12).
 *
 * A target is a **registered pipeline**. Nothing here sends, or has a place to send, a Jenkins URL,
 * a node label, a credential or a command: the only administrator-written values that reach
 * Jenkins are `pipeline_reference` and the parameter template, which the backend validates
 * against closed grammars and never concatenates into a URL as written. The pre-checks below
 * exist to say so earlier and in plainer words; the backend decides every request.
 */

import type { components } from '@/api/schema';
import { call, idempotencyKey, queryString, type Result } from '@/api/inventory';

type Schemas = components['schemas'];

export type TargetSummary = Schemas['TargetSummary'];
export type TargetDetail = Schemas['TargetDetail'];
export type TargetList = Schemas['TargetList'];
export type TargetCreate = Schemas['TargetCreate'];
export type TargetUpdate = Schemas['TargetUpdate'];
export type ParameterTemplate = Schemas['ParameterTemplate'];
export type SlotSource = 'ref' | 'deployment_id' | 'environment' | 'requested_by';
export type TargetStatus = TargetSummary['status'];
export type TargetEnvironment = TargetSummary['environment'];

/** What each slot is filled with, in words. The set is closed by the backend. */
export const SLOT_SOURCES: readonly { value: SlotSource; label: string }[] = [
  { value: 'ref', label: 'the branch, tag or commit requested' },
  { value: 'deployment_id', label: 'this deployment’s id' },
  { value: 'environment', label: 'the environment' },
  { value: 'requested_by', label: 'the requester’s user id' },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

const json = (method: string, body: object, extra: Record<string, string> = {}): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json', ...extra },
  body: JSON.stringify(body),
});

const isTarget = (value: unknown): boolean => isRecord(value) && typeof value.id === 'string';

export function listTargets(
  filters: Record<string, string | undefined> = {},
): Promise<Result<TargetList>> {
  return call<TargetList>(
    `/deployment-targets${queryString(filters)}`,
    {},
    (value) => isRecord(value) && Array.isArray(value.items) && isRecord(value.page),
  );
}

export function readTarget(id: string): Promise<Result<TargetDetail>> {
  return call<TargetDetail>(`/deployment-targets/${encodeURIComponent(id)}`, {}, isTarget);
}

export function createTarget(body: TargetCreate): Promise<Result<TargetDetail>> {
  return call<TargetDetail>(
    '/deployment-targets',
    json('POST', body, { 'Idempotency-Key': idempotencyKey() }),
    isTarget,
  );
}

/**
 * `unmodifiedSince` is the `updated_at` the caller's copy carries, sent as `If-Unmodified-Since`
 * so a target someone else edited meanwhile answers `409` rather than being overwritten. An
 * unparseable timestamp sends no header (a malformed one would be a `422` about the wrong thing).
 */
export function updateTarget(
  id: string,
  changes: TargetUpdate,
  unmodifiedSince?: string,
): Promise<Result<TargetDetail>> {
  const at = unmodifiedSince === undefined ? null : new Date(unmodifiedSince);
  const precondition =
    at !== null && !Number.isNaN(at.getTime()) ? { 'If-Unmodified-Since': at.toUTCString() } : {};
  return call<TargetDetail>(
    `/deployment-targets/${encodeURIComponent(id)}`,
    json('PATCH', changes, precondition),
    isTarget,
  );
}

export function retireTarget(id: string): Promise<Result<null>> {
  return call<null>(`/deployment-targets/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** A pasted URL is the likeliest mistake in a pipeline field, and is refused before sending. */
export function looksLikeAUrl(value: string): boolean {
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(value.trim());
}

/** One pattern per line; blanks dropped. */
export function parsePatterns(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');
}
