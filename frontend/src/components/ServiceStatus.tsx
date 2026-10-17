/**
 * A unit's state with the evidence for it (PRD §4.5, API_CONTRACTS.md §16).
 *
 * The badge says *what*; the line under it says *how much to believe it*: when
 * it was collected, or — when it was not — why not. Both are always shown,
 * because the acceptance criterion is that state is never presented without its
 * collection time, and that a silent host is never presented as stopped
 * services.
 *
 * That second rule is a property of the **data** (the backend answers
 * `UNKNOWN` for a host it cannot reach), and of this component's refusal to
 * improve on it: it renders the state it was given, says the host is
 * unreachable when `server_reachable` is `false`, and has no code path that
 * turns a missing reading into `Inactive`.
 */

import { evidenceLine, type Evidence } from '@/components/serviceEvidence';
import { StatusBadge } from '@/components/StatusBadge';

export function ServiceStatus({ item }: { item: Evidence }): React.JSX.Element {
  return (
    <div>
      <StatusBadge value={item.state} />
      <p className="mt-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
        {evidenceLine(item)}
      </p>
    </div>
  );
}

/** A field this source cannot supply (`OPEN-29`): unknown, never a default. */
export function NotCollected(): React.JSX.Element {
  return <span style={{ color: 'var(--c-text-muted)' }}>Not collected by this source</span>;
}
