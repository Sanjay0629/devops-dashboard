/**
 * The states an inventory page can be in, rendered once rather than five times.
 *
 * Loading, empty, error and not-configured are different answers and each
 * page has to show all four, so they live here instead of being written out
 * per page with small inconsistencies. `StatusBadge`, `DataStateBanner`,
 * `EmptyState` and `ErrorState` as *shared components* are M1.6's task
 * (IMPLEMENTATION_PLAN.md) — these are deliberately the minimum this
 * milestone needs, in one file, for that milestone to replace.
 */

import type { DataSection, Result } from '@/api/inventory';

export function Loading({ what }: { what: string }): React.JSX.Element {
  return (
    <p role="status" style={{ color: 'var(--c-text-muted)' }}>
      Loading {what}…
    </p>
  );
}

export function Empty({ message }: { message: string }): React.JSX.Element {
  return (
    <p
      className="rounded border px-4 py-6 text-center text-sm"
      style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
    >
      {message}
    </p>
  );
}

/**
 * A failed call, said plainly.
 *
 * `forbidden` and `not_found` are not merged: on a scoped resource the
 * backend answers `404` for a server outside the caller's scope (§2), and
 * telling them "you do not have permission" would be a guess about which of
 * the two it was.
 */
export function Failure({ result }: { result: Exclude<Result<unknown>, { kind: 'ok' }> }) {
  const message =
    result.kind === 'forbidden'
      ? 'You do not have permission to view this.'
      : result.kind === 'not_found'
        ? 'Not found. It may not exist, or may be outside your access scope.'
        : result.kind === 'read_only'
          ? 'The portal is in read-only mode. State-changing actions are disabled.'
          : result.kind === 'conflict'
            ? result.message
            : result.kind === 'invalid'
              ? result.message
              : `Could not reach the backend: ${result.reason}`;
  return (
    <p
      role="alert"
      className="rounded border px-4 py-3 text-sm"
      style={{ borderColor: 'var(--c-critical)', color: 'var(--c-critical)' }}
    >
      {message}
    </p>
  );
}

/**
 * A section with no data, saying which kind of "no data" it is.
 *
 * Never renders a zero or a dash in place of a missing value: PRD §4.2/§4.4
 * and CLAUDE.md §5 treat unknown, unavailable, unsupported and not-configured
 * as four different facts, and a dash reads as "nothing to report".
 */
export function NotCollected({ section }: { section: DataSection }): React.JSX.Element {
  const label: Record<DataSection['data_state'], string> = {
    ok: 'Collected',
    stale: 'Stale',
    unavailable: 'Unavailable',
    unsupported: 'Not supported for this host',
    not_configured: 'Not configured',
  };
  return (
    <span style={{ color: 'var(--c-text-muted)' }}>
      {label[section.data_state]}
      {section.unavailable_reason !== null && ` — ${section.unavailable_reason}`}
    </span>
  );
}

const STATUS_COLOUR: Record<string, string> = {
  MANAGED: 'var(--c-healthy)',
  ACTIVE: 'var(--c-healthy)',
  PENDING: 'var(--c-unknown)',
  STALE: 'var(--c-stale)',
  UNREACHABLE: 'var(--c-critical)',
  RETIRED: 'var(--c-unknown)',
  ARCHIVED: 'var(--c-unknown)',
};

/**
 * Status as text plus colour, never colour alone — colour is not a label, and
 * a red dot means nothing to a screen reader or to anyone who cannot
 * distinguish it. M1.6's `StatusBadge` takes this over.
 */
export function Status({ value }: { value: string }): React.JSX.Element {
  return (
    <span
      className="rounded border px-2 py-0.5 text-xs font-medium"
      style={{
        borderColor: STATUS_COLOUR[value] ?? 'var(--c-border)',
        color: STATUS_COLOUR[value] ?? 'var(--c-text)',
      }}
    >
      {value}
    </span>
  );
}
