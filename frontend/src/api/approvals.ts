/**
 * Approvals client (API_CONTRACTS.md §22) and the approval-policy editor's (§25.11 – §25.14).
 *
 * Shapes are the generated ones. What this file adds is the part `Result` cannot say: a **decision
 * that was refused** (`403` for self-approval or a missing role, `409` for a request that is no
 * longer open or whose window passed) carries a closed `details.reason` that the approver needs in
 * words — folding it into "forbidden" or "conflict" would tell them nothing about what to do next.
 *
 * Nothing here authorises anything. The queue's `self_requested` flag is advice for the UI; the
 * backend refuses a self-approval on its own account, and a test calls it directly.
 */

import { call, queryString, type Result } from '@/api/inventory';
import type { components } from '@/api/schema';
import { request } from '@/api/session';

type Schemas = components['schemas'];

export type ApprovalItem = Schemas['ApprovalItem'];
export type ApprovalDetail = Schemas['ApprovalDetail'];
export type ApprovalList = Schemas['ApprovalList'];
export type ApprovalDecision = ApprovalItem['decision'];
export type OperationDetail = Schemas['OperationDetail'];
export type Policy = Schemas['PolicyOut'];
export type PolicyList = Schemas['PolicyList'];
export type PolicyCreate = Schemas['PolicyCreate'];
export type PolicyUpdate = Schemas['PolicyUpdate'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function listApprovals(
  decision: ApprovalDecision,
  cursor?: string,
): Promise<Result<ApprovalList>> {
  return call<ApprovalList>(
    `/approvals${queryString({ decision, cursor })}`,
    {},
    (value) => isRecord(value) && Array.isArray(value.items) && isRecord(value.page),
  );
}

export function readApproval(id: string): Promise<Result<ApprovalDetail>> {
  return call<ApprovalDetail>(
    `/approvals/${encodeURIComponent(id)}`,
    {},
    (value) => isRecord(value) && typeof value.approval_id === 'string',
  );
}

/** A deliberate refusal of a decision, with the closed reason the backend gave. */
export interface Refused {
  kind: 'refused';
  status: 403 | 409 | 422;
  reason: string | null;
  message: string;
}

export type Decided =
  | { kind: 'ok'; operation: OperationDetail }
  | Refused
  | Exclude<Result<never>, { kind: 'ok' | 'invalid' | 'conflict' }>;

interface ErrorBody {
  error?: { code?: string; message?: string; details?: { reason?: unknown } };
}

/**
 * §22.3 / §22.4. One call per click: the dialog disables itself while this is in flight, so a
 * double click is one request, and a request that races another decision is a `409`, not a
 * second effect.
 */
export async function decide(
  operationId: string,
  verb: 'approve' | 'reject',
  reason: string | null,
): Promise<Decided> {
  try {
    const response = await request(`/operations/${encodeURIComponent(operationId)}/${verb}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reason === null ? {} : { reason }),
    });
    if (response.ok) {
      const body: unknown = await response.json();
      if (!isRecord(body) || typeof body.operation_id !== 'string') {
        return { kind: 'unreachable', reason: 'Unexpected response shape' };
      }
      return { kind: 'ok', operation: body as OperationDetail };
    }
    if (response.status === 404) return { kind: 'not_found' };
    if (response.status === 423) return { kind: 'read_only' };
    let body: ErrorBody = {};
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      return { kind: 'unreachable', reason: `Unexpected status ${String(response.status)}` };
    }
    const message = body.error?.message ?? `Unexpected status ${String(response.status)}`;
    if (response.status === 403 || response.status === 409 || response.status === 422) {
      const reason = body.error?.details?.reason;
      return {
        kind: 'refused',
        status: response.status,
        reason: typeof reason === 'string' ? reason : null,
        message,
      };
    }
    return { kind: 'unreachable', reason: message };
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  }
}

/** What a refused (or failed) decision means, in words. The backend's message is the fallback. */
export function decisionProblem(result: Exclude<Decided, { kind: 'ok' }>): string {
  switch (result.kind) {
    case 'refused':
      if (result.reason === 'self_approval_not_permitted') {
        return 'You made this request, and the policy does not allow you to approve your own. Another approver has to decide it.';
      }
      if (result.reason === 'approver_role_required') {
        return 'The policy for this request needs a role you do not hold, so you cannot decide it.';
      }
      if (result.reason === 'approval_expired') {
        return 'The approval window for this request has passed. It has been cancelled, and it has to be requested again.';
      }
      if (result.reason === 'not_awaiting_approval') {
        return 'This request is no longer waiting for a decision — someone else decided it, or it was cancelled. Refresh the queue.';
      }
      if (result.status === 403) return 'You do not have permission to decide this request.';
      return result.message;
    case 'not_found':
      return 'This request does not exist, or is outside the environments you can approve in.';
    case 'forbidden':
      return 'You do not have permission to decide this request.';
    case 'read_only':
      return 'The portal is in read-only mode. State-changing actions are disabled.';
    case 'unreachable':
      return `Could not reach the backend: ${result.reason}`;
  }
}

// --- the policy editor (§25.11 – §25.14) -------------------------------------------------------

const json = (method: string, body: object): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export function listPolicies(): Promise<Result<PolicyList>> {
  return call<PolicyList>(
    '/admin/approval-policies',
    {},
    (value) => isRecord(value) && Array.isArray(value.items),
  );
}

export function createPolicy(body: PolicyCreate): Promise<Result<Policy>> {
  return call<Policy>(
    '/admin/approval-policies',
    json('POST', body),
    (value) => isRecord(value) && typeof value.policy_id === 'string',
  );
}

export function updatePolicy(id: string, changes: PolicyUpdate): Promise<Result<Policy>> {
  return call<Policy>(
    `/admin/approval-policies/${encodeURIComponent(id)}`,
    json('PATCH', changes),
    (value) => isRecord(value) && typeof value.policy_id === 'string',
  );
}

/** `409` while a service or an approval still points at the policy. */
export function deletePolicy(id: string): Promise<Result<null>> {
  return call<null>(`/admin/approval-policies/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
