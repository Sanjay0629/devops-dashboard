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

  // Monitoring agent (`servers.agent_status`, M2.3). `UNKNOWN` is "nobody has
  // observed it", never "fine"; `DEGRADED` and `NOT_INSTALLED` are permitted by
  // the schema and not emitted by anything yet, but a badge must not render
  // them as their raw enum name if one ever arrives.
  HEALTHY: { glyph: '●', label: 'Healthy', colour: 'var(--c-healthy)' },
  DEGRADED: { glyph: '▲', label: 'Degraded', colour: 'var(--c-warning)' },
  UNKNOWN: { glyph: '?', label: 'Unknown', colour: 'var(--c-unknown)' },
  NOT_INSTALLED: { glyph: '—', label: 'Not installed', colour: 'var(--c-unknown)' },

  // systemd unit state (`ServiceState`, M2.4). `INACTIVE` is a *known* stopped
  // unit; `UNKNOWN` is a unit nobody could read (a silent host), and
  // `UNCOLLECTED` is a host that answers while the exporter says nothing about
  // the unit. Three different facts, three different shapes: a dead host must
  // never be drawn as stopped services (PRD §4.5).
  FAILED: { glyph: '■', label: 'Failed', colour: 'var(--c-critical)' },
  INACTIVE: { glyph: '○', label: 'Inactive', colour: 'var(--c-unknown)' },
  ACTIVATING: { glyph: '◔', label: 'Activating', colour: 'var(--c-warning)' },
  DEACTIVATING: { glyph: '◕', label: 'Deactivating', colour: 'var(--c-warning)' },
  UNCOLLECTED: { glyph: '∅', label: 'Not collected', colour: 'var(--c-stale)' },
  UNSUPPORTED: { glyph: '—', label: 'Not supported', colour: 'var(--c-unknown)' },

  // Alert severity and status (§20, M3.3). `ACKNOWLEDGED` is drawn as open-but-taken, never
  // as resolved (PRD §4.8); `EXPIRED` is *unconfirmed*, a different fact from `RESOLVED`.
  CRITICAL: { glyph: '■', label: 'Critical', colour: 'var(--c-critical)' },
  WARNING: { glyph: '▲', label: 'Warning', colour: 'var(--c-warning)' },
  INFO: { glyph: '●', label: 'Info', colour: 'var(--c-unknown)' },
  FIRING: { glyph: '■', label: 'Firing', colour: 'var(--c-critical)' },
  ACKNOWLEDGED: { glyph: '◆', label: 'Acknowledged (still open)', colour: 'var(--c-warning)' },
  RESOLVED: { glyph: '●', label: 'Resolved', colour: 'var(--c-healthy)' },
  EXPIRED: { glyph: '◌', label: 'Expired (not confirmed)', colour: 'var(--c-stale)' },

  // Notification delivery (§20.2, M3.4). `PENDING` is drawn by the server-registration entry
  // above (not yet sent, or waiting for a retry). `FAILED` is a permanent failure and is drawn
  // above; `ABANDONED` is "retries ran out" -- a different fact, so a different word.
  SENT: { glyph: '●', label: 'Sent', colour: 'var(--c-healthy)' },
  ABANDONED: { glyph: '✕', label: 'Gave up (retries used)', colour: 'var(--c-critical)' },

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
