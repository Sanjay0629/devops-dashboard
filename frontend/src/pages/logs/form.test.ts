import { describe, expect, it } from 'vitest';

import { BLANK, bodyOf, hasTarget } from '@/pages/logs/form';
import { LOG_RANGES, isLogsPrefill, logRangeById } from '@/pages/logs/prefill';

const NOW = new Date('2026-10-18T09:00:00.000Z');

describe('bodyOf', () => {
  it('turns a range into a window ending a minute ahead, for clock skew', () => {
    const body = bodyOf({ ...BLANK, rangeId: '15m', servers: { s1: 'Web' } }, NOW);
    expect(body.from).toBe('2026-10-18T08:45:00.000Z');
    expect(body.to).toBe('2026-10-18T09:01:00.000Z');
  });

  it('the longest range is exactly the contract maximum plus the headroom', () => {
    const longest = LOG_RANGES[LOG_RANGES.length - 1];
    expect(longest?.seconds).toBe(7 * 86_400);
    const body = bodyOf({ ...BLANK, rangeId: '7d', servers: { s1: 'Web' } }, NOW);
    // 7 d + 1 min: the headroom must not push a 7-day request past §19.1's cap
    // unless the backend counts it, which it does against `to - from`.
    expect(Date.parse(body.to) - Date.parse(body.from)).toBe(7 * 86_400_000 + 60_000);
  });

  it('an unknown range falls back to an hour rather than to nothing', () => {
    expect(logRangeById('nonsense').id).toBe('1h');
  });

  it('trims the service and leaves blanks out', () => {
    const body = bodyOf({ ...BLANK, environment: 'staging', service: '  api  ' }, NOW);
    expect(body.service).toBe('api');
    expect(body).not.toHaveProperty('contains');
    expect(body).not.toHaveProperty('level');
    expect(body).not.toHaveProperty('server_ids');
  });

  it('needs a target', () => {
    expect(hasTarget(BLANK)).toBe(false);
    expect(hasTarget({ ...BLANK, environment: 'staging' })).toBe(true);
    expect(hasTarget({ ...BLANK, applicationId: 'a1' })).toBe(true);
    expect(hasTarget({ ...BLANK, servers: { s1: 'Web' } })).toBe(true);
  });
});

describe('isLogsPrefill', () => {
  it('accepts the shape another page sends', () => {
    expect(isLogsPrefill({ servers: [{ id: 's', label: 'L' }], service: 'x' })).toBe(true);
    expect(isLogsPrefill({ servers: [] })).toBe(true);
  });

  it.each([
    null,
    undefined,
    'x',
    5,
    {},
    { servers: 'x' },
    { servers: [{ id: 1, label: 'L' }] },
    { servers: [], service: 3 },
    { servers: [], rangeId: {} },
  ])('rejects %j', (value) => {
    expect(isLogsPrefill(value)).toBe(false);
  });
});
