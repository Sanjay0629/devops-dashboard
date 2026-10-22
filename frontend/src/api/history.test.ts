import { describe, expect, it } from 'vitest';

import { EMPTY_FILTERS, historyQuery, toInstant } from '@/api/history';
import { ACTION_TOKEN, EMPTY_AUDIT_FILTERS, auditQuery, exportFailureText } from '@/api/audit';

describe('historyQuery', () => {
  it('repeats a parameter for each value, because the API takes lists that way', () => {
    expect(
      historyQuery({
        ...EMPTY_FILTERS,
        statuses: ['FAILED', 'TIMED_OUT'],
        actions: ['restart'],
        environments: ['production'],
        requestedBy: '00000000-0000-0000-0000-000000000001',
        from: '2026-10-01T00:00:00.000Z',
        to: '2026-10-08T00:00:00.000Z',
        sort: 'completed_at',
        cursor: 'c1',
      }),
    ).toBe(
      '?execution_status=FAILED&execution_status=TIMED_OUT&action_type=restart&environment=production' +
        '&requested_by=00000000-0000-0000-0000-000000000001&from=2026-10-01T00%3A00%3A00.000Z' +
        '&to=2026-10-08T00%3A00%3A00.000Z&sort=completed_at&cursor=c1',
    );
  });

  it('is empty when nothing is set, so the backend applies its own default window', () => {
    expect(historyQuery(EMPTY_FILTERS)).toBe('');
  });

  it('sends the default sort implicitly', () => {
    expect(historyQuery({ ...EMPTY_FILTERS, sort: 'created_at' })).not.toContain('sort');
  });
});

describe('toInstant', () => {
  it('gives an instant with an offset, which is what the backend requires', () => {
    expect(toInstant('2026-10-22T10:30')).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  });

  it('gives nothing for an empty or unreadable value rather than a wrong date', () => {
    expect(toInstant('')).toBe('');
    expect(toInstant('not a date')).toBe('');
  });
});

describe('auditQuery', () => {
  it('sends only what was given, and trims ids', () => {
    expect(
      auditQuery({
        ...EMPTY_AUDIT_FILTERS,
        actionType: 'ROLE_GRANTED',
        outcome: 'DENIED',
        correlationId: '  abc  ',
      }),
    ).toBe('?action_type=ROLE_GRANTED&outcome=DENIED&correlation_id=abc');
    expect(auditQuery(EMPTY_AUDIT_FILTERS)).toBe('');
  });
});

describe('the action token', () => {
  it('accepts what the backend accepts and nothing a pattern could be built from', () => {
    for (const good of ['ROLE_GRANTED', 'A', 'AUDIT_EXPORT_REQUESTED']) {
      expect(ACTION_TOKEN.test(good)).toBe(true);
    }
    for (const bad of ['role_granted', 'ROLE%', 'ROLE GRANTED', 'ROLE*', '', 'A'.repeat(65)]) {
      expect(ACTION_TOKEN.test(bad)).toBe(false);
    }
  });
});

describe('exportFailureText', () => {
  it('says what each closed code means, and what to do', () => {
    expect(exportFailureText('export_too_large')).toMatch(/never truncated|Nothing was truncated/);
    expect(exportFailureText('requester_not_authorised')).toMatch(/no longer allows/);
    expect(exportFailureText('internal_error')).toMatch(/Try again/);
    expect(exportFailureText(null)).toBe('The export failed.');
  });
});
