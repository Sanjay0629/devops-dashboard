/**
 * "Check connectivity" on the server page (API_CONTRACTS.md §14.8; PRD §4.11).
 *
 * It asks the provider, now, whether the host is reachable through it, and shows the answer with
 * the time it was given. It changes nothing about the server: the monitoring agent state and
 * `last seen` above stay the Prometheus probe's. Four outcomes are kept apart because they mean
 * different things to an operator: reachable; not reachable (an answer); the portal could not
 * ask (a refusal, said in words); and not asked yet.
 *
 * The button is hidden for usability only (docs/SECURITY.md §6): the backend checks
 * `server:write` against this server's scope on every request.
 */

import { useState } from 'react';

import { checkConnectivity, refusalText, type Checked } from '@/api/connectivity';
import { useIsReadOnly } from '@/auth/useSession';

const STATUS_WORDS: Record<string, string> = {
  online: 'online',
  connection_lost: 'connection lost',
  inactive: 'inactive',
  not_registered_with_ssm: 'not registered with the provider’s management service',
  unknown: 'unknown',
};

function problem(result: Exclude<Checked, { kind: 'ok' }>): string {
  switch (result.kind) {
    case 'refused':
      return refusalText(result);
    case 'forbidden':
      return 'You do not have permission to check this server.';
    case 'not_found':
      return 'This server does not exist, or is outside your access scope.';
    case 'read_only':
      return 'The portal is in read-only mode.';
    case 'invalid':
    case 'conflict':
      return result.message;
    case 'unreachable':
      return `Could not reach the backend: ${result.reason}`;
  }
}

export function ConnectivityCheck({
  serverId,
  disabled,
}: {
  serverId: string;
  disabled: boolean;
}): React.JSX.Element {
  const readOnly = useIsReadOnly();
  const [checking, setChecking] = useState(false);
  const [outcome, setOutcome] = useState<Checked | null>(null);

  const run = async () => {
    setChecking(true);
    try {
      setOutcome(await checkConnectivity(serverId));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div>
      <button
        type="button"
        disabled={checking || disabled || readOnly}
        onClick={() => void run()}
        className="rounded border px-3 py-1 text-sm disabled:opacity-50"
        style={{ borderColor: 'var(--c-border)' }}
      >
        {checking ? 'Checking…' : 'Check connectivity'}
      </button>
      <div aria-live="polite" className="mt-1 text-xs">
        {outcome === null && <span style={{ color: 'var(--c-text-muted)' }}>Not checked yet.</span>}
        {outcome?.kind === 'ok' && (
          <span>
            {outcome.result.reachable
              ? 'Reachable through the provider'
              : 'Not reachable through the provider'}
            {' — '}
            {STATUS_WORDS[outcome.result.provider_status] ?? outcome.result.provider_status}
            {', checked '}
            <time dateTime={outcome.result.checked_at}>{outcome.result.checked_at}</time>
          </span>
        )}
        {outcome !== null && outcome.kind !== 'ok' && (
          <span role="alert">The connectivity could not be checked. {problem(outcome)}</span>
        )}
      </div>
    </div>
  );
}
