/**
 * The single owner of status visuals (IMPLEMENTATION_PLAN.md M1.6).
 *
 * **Status is never conveyed by colour alone** (PRD §8). Every badge carries
 * three signals: a shape, a word, and a colour. Drop any one of them and the
 * badge still reads — which is the point, because a red dot means nothing to
 * a screen reader, to a monochrome print-out, or to the ~8% of men with a
 * red/green deficiency looking at a healthy/critical pair.
 *
 * The glyph is `aria-hidden`: it is a second *visual* channel, and a screen
 * reader already has the word. The word itself is the accessible name.
 *
 * It also owns the one rule that matters most on this screen: **an unknown
 * status is not a healthy one.** `null` renders as "Unknown" in the neutral
 * colour, never as a green tick, because at M1.6 no collector has looked at
 * any host and a dashboard full of green ticks would be a lie
 * (PRD §4.2, CLAUDE.md §5).
 *
 * Replaces `pages/inventory/states.tsx`'s `Status`, which said in its own
 * docstring that it was a placeholder for this.
 */

/** Shape, label and colour for every value this product can show as a status. */
const APPEARANCE: Record<string, { glyph: string; label: string; colour: string }> = {
  // Server health (API_CONTRACTS.md §9 `HealthState`). No source until M2.1.
  healthy: { glyph: '●', label: 'Healthy', colour: 'var(--c-healthy)' },
  warning: { glyph: '▲', label: 'Warning', colour: 'var(--c-warning)' },
  critical: { glyph: '■', label: 'Critical', colour: 'var(--c-critical)' },
  stale: { glyph: '◐', label: 'Stale', colour: 'var(--c-stale)' },
  unreachable: { glyph: '✕', label: 'Unreachable', colour: 'var(--c-critical)' },
  unknown: { glyph: '?', label: 'Unknown', colour: 'var(--c-unknown)' },

  // Server registration status (`servers.status`).
  PENDING: { glyph: '◌', label: 'Pending', colour: 'var(--c-unknown)' },
  MANAGED: { glyph: '●', label: 'Managed', colour: 'var(--c-healthy)' },
  STALE: { glyph: '◐', label: 'Stale', colour: 'var(--c-stale)' },
  UNREACHABLE: { glyph: '✕', label: 'Unreachable', colour: 'var(--c-critical)' },
  RETIRED: { glyph: '—', label: 'Retired', colour: 'var(--c-unknown)' },

  // Application status.
  ACTIVE: { glyph: '●', label: 'Active', colour: 'var(--c-healthy)' },
  ARCHIVED: { glyph: '—', label: 'Archived', colour: 'var(--c-unknown)' },
};

const UNKNOWN = { glyph: '?', label: 'Unknown', colour: 'var(--c-unknown)' };

export function StatusBadge({ value }: { value: string | null }): React.JSX.Element {
  // `null` is unknown and falls through to the neutral appearance — never to
  // a default of healthy. An unrecognised value does the same rather than
  // being hidden: a status the UI has not been taught about is still a fact
  // about the host, and swallowing it would be the quiet kind of wrong.
  const { glyph, label, colour } =
    value === null ? UNKNOWN : (APPEARANCE[value] ?? { ...UNKNOWN, label: value });

  return (
    <span
      className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium"
      style={{ borderColor: colour, color: colour }}
    >
      <span aria-hidden="true">{glyph}</span>
      {label}
    </span>
  );
}
