/**
 * A section's `data_state`, said plainly (API_CONTRACTS.md §4).
 *
 * Five states, five different sentences. `ok`, `stale`, `unavailable`,
 * `unsupported` and `not_configured` are five different facts about why a
 * number is missing, and collapsing them into "—" or "N/A" throws away the
 * only information the operator can act on: a stale reading means the
 * collector is behind, an unsupported one means the question does not apply
 * to this host, and a not-configured one means somebody has to go and
 * configure something.
 *
 * **It never renders a value.** Its whole job is the absence of one. A caller
 * that has a number shows the number; a caller that does not shows this.
 *
 * Replaces `pages/inventory/states.tsx`'s `NotCollected`, and fixes one thing
 * that file got wrong: the generated types make a field with a default
 * *optional*, so `unavailable_reason` can be `undefined` as well as `null`,
 * and `!== null` printed "— undefined".
 */

import type { DataSection } from '@/api/inventory';

const LABEL: Record<DataSection['data_state'], string> = {
  ok: 'Collected',
  stale: 'Stale',
  unavailable: 'Unavailable',
  unsupported: 'Not supported for this host',
  not_configured: 'Not configured',
};

/** `collected_at` for a section, or an explicit statement that there is none. */
export function CollectedAt({ section }: { section: DataSection }): React.JSX.Element {
  return (
    <span className="text-xs" style={{ color: 'var(--c-text-muted)' }}>
      {section.collected_at == null
        ? // §4 rule 3: a time is present when data was collected. Nothing
          // collected this, so there is no time — not "just now".
          'Never collected'
        : `Collected ${new Date(section.collected_at).toLocaleString()}`}
      {section.source != null && ` · ${section.source}`}
    </span>
  );
}

export function DataStateBanner({
  section,
  inline = false,
}: {
  section: DataSection;
  /** Inline for a table cell or a definition list; block for a card. */
  inline?: boolean;
}): React.JSX.Element {
  const reason = section.unavailable_reason;
  const text = `${LABEL[section.data_state]}${reason ? ` — ${reason}` : ''}`;

  if (inline) {
    return <span style={{ color: 'var(--c-text-muted)' }}>{text}</span>;
  }
  return (
    <p
      className="rounded border px-3 py-2 text-sm"
      style={{
        borderColor: section.data_state === 'stale' ? 'var(--c-stale)' : 'var(--c-border)',
        color: 'var(--c-text-muted)',
      }}
    >
      {text}
    </p>
  );
}
