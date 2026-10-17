import { describe, expect, it } from 'vitest';

import { evidenceLine } from '@/components/serviceEvidence';

/**
 * The sentence under a service's badge (PRD §4.5): every state is shown with
 * its collection time, or with the reason it has none.
 */

const base = {
  state: 'ACTIVE',
  data_state: 'ok',
  unavailable_reason: null,
  collected_at: '2026-10-17T12:00:00Z',
  server_reachable: true,
} as const;

describe('evidenceLine', () => {
  it('shows the collection time in UTC for a collected state', () => {
    expect(evidenceLine(base)).toMatch(/^Collected .*2026.* UTC$/);
  });

  it('says never collected rather than inventing a time', () => {
    const line = evidenceLine({ ...base, data_state: 'unavailable', collected_at: null });
    expect(line).toContain('Never collected');
    expect(line).not.toMatch(/Collected \d/);
  });

  it('says the host is unreachable and keeps the last good time', () => {
    const line = evidenceLine({
      ...base,
      state: 'UNKNOWN',
      data_state: 'stale',
      unavailable_reason: 'no_data',
      server_reachable: false,
    });
    expect(line).toContain('Host unreachable');
    expect(line).toContain('Stale');
    expect(line).toContain('Last collected');
  });

  it('turns a reason code into a sentence, never the raw code', () => {
    const line = evidenceLine({
      ...base,
      state: 'UNKNOWN',
      data_state: 'not_configured',
      unavailable_reason: 'no_metrics_instance',
      collected_at: null,
    });
    expect(line).toContain('No metrics instance is registered');
    expect(line).not.toContain('no_metrics_instance');
  });

  it('does not call a host unreachable when that is not known', () => {
    expect(evidenceLine({ ...base, server_reachable: null })).not.toContain('unreachable');
  });
});
