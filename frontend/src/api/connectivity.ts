/**
 * Server connectivity check client (API_CONTRACTS.md §14.8; PRD §4.11).
 *
 * `reachable: false` is an **answer** (the host is not registered with its provider, or its
 * agent is offline). A provider that could not answer, or a deployment with no provider
 * integration, is a **refusal** with a closed `details.reason`; folding either into the other
 * would tell an operator the host is down when the portal merely cannot see it.
 *
 * Nothing here authorises anything, and nothing here can send a command: the request has no body.
 */

import type { Result } from '@/api/inventory';
import type { components } from '@/api/schema';
import { request } from '@/api/session';

export type ConnectivityResult = components['schemas']['ConnectivityOut'];

/** A deliberate answer from the backend that it could not tell, with its closed reason. */
export interface ConnectivityRefusal {
  kind: 'refused';
  status: number;
  code: string;
  reason: string | null;
  message: string;
}

export type Checked =
  | { kind: 'ok'; result: ConnectivityResult }
  | ConnectivityRefusal
  | Exclude<Result<never>, { kind: 'ok' }>;

interface ErrorBody {
  error?: { code?: string; message?: string; details?: { reason?: unknown } };
}

function isResult(value: unknown): value is ConnectivityResult {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { reachable?: unknown }).reachable === 'boolean' &&
    typeof (value as { provider_status?: unknown }).provider_status === 'string'
  );
}

export async function checkConnectivity(serverId: string): Promise<Checked> {
  try {
    const response = await request(`/servers/${encodeURIComponent(serverId)}/connectivity-check`, {
      method: 'POST',
    });
    if (response.ok) {
      const body: unknown = await response.json();
      return isResult(body)
        ? { kind: 'ok', result: body }
        : { kind: 'unreachable', reason: 'Unexpected response shape' };
    }
    if (response.status === 403) return { kind: 'forbidden' };
    if (response.status === 404) return { kind: 'not_found' };
    if (response.status === 423) return { kind: 'read_only' };
    let body: ErrorBody = {};
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      return { kind: 'unreachable', reason: `Unexpected status ${String(response.status)}` };
    }
    const reason = body.error?.details?.reason;
    return {
      kind: 'refused',
      status: response.status,
      code: body.error?.code ?? '',
      reason: typeof reason === 'string' ? reason : null,
      message: body.error?.message ?? `Unexpected status ${String(response.status)}`,
    };
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  }
}

/** What a refusal means, in words. The backend's message is the fallback, never hidden. */
export function refusalText(refusal: ConnectivityRefusal): string {
  if (refusal.code === 'rate_limited') {
    return 'Too many connectivity checks for this server in the last minute. Try again shortly.';
  }
  if (refusal.code === 'unsupported_operation') {
    return 'Connectivity checks are not supported by this server’s provider.';
  }
  switch (refusal.reason) {
    case 'demonstration_mode':
      return 'This portal is in demonstration mode. No connectivity check can run.';
    case 'no_adapter_for_provider':
      return 'No provider integration is configured for this server’s cloud account, so its connectivity cannot be checked.';
    case 'account_disabled':
      return 'This server’s cloud account is disabled.';
    case 'static_credentials_present':
      return 'The AWS integration is switched off because a static access key is present in the portal’s environment. Remove it.';
    case 'document_version_not_set':
      return 'The AWS integration is not configured: the SSM document version is not set.';
    case 'assume_role_denied':
      return 'The provider refused to let the portal assume the account’s execution role. Check the role’s trust policy.';
    case 'access_denied':
      return 'The execution role may not read this instance’s management state. Check its permissions.';
    case 'credentials_rejected':
    case 'no_credentials':
      return 'The provider rejected the portal’s credentials.';
    default:
      return refusal.message;
  }
}
