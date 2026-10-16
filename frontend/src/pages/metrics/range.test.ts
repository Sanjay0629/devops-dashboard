import { describe, expect, it } from 'vitest';

import { formatValue } from '@/components/chart/format';
import { RANGES, rangeById, windowFor } from '@/pages/metrics/range';

/** Backend bounds from API_CONTRACTS.md §15.2, restated so a drift fails here. */
const MAX_POINTS = 11_000;
const MIN_STEP = 15;
const MAX_RANGE_SECONDS = 30 * 24 * 3_600;

describe('the selectable ranges', () => {
  it.each(RANGES)('$id stays inside every §15.2 bound', (choice) => {
    const window = windowFor(choice, new Date('2026-10-16T09:07:31Z'));
    const span = (window.to.getTime() - window.from.getTime()) / 1000;
    expect(span).toBe(choice.seconds);
    expect(span).toBeLessThanOrEqual(MAX_RANGE_SECONDS);
    expect(window.stepSeconds).toBeGreaterThanOrEqual(MIN_STEP);
    // The same off-by-one the backend guard covers: a point lands on `from`.
    expect(Math.floor(span / window.stepSeconds) + 1).toBeLessThanOrEqual(MAX_POINTS);
  });

  it('snaps the end to a whole step so reloads ask for the same grid', () => {
    const choice = rangeById('24h');
    const a = windowFor(choice, new Date('2026-10-16T09:07:31Z'));
    const b = windowFor(choice, new Date('2026-10-16T09:09:59Z'));
    expect(a.to.toISOString()).toBe('2026-10-16T09:05:00.000Z');
    expect(b.to.toISOString()).toBe(a.to.toISOString());
  });

  it('falls back to the first range for an unknown id', () => {
    expect(rangeById('nonsense').id).toBe('1h');
  });
});

describe('formatValue', () => {
  it('formats by unit', () => {
    expect(formatValue(12.345, 'percent')).toBe('12.3 %');
    expect(formatValue(1536, 'bytes')).toBe('1.5 KiB');
    expect(formatValue(2048, 'bytes_per_second')).toBe('2.0 KiB/s');
    expect(formatValue(90_000, 'seconds')).toBe('1d 1h');
    expect(formatValue(0.5, 'load')).toBe('0.50');
  });

  it('treats a zero agent reading as Down, not as nothing', () => {
    expect(formatValue(0, 'boolean')).toBe('Down');
    expect(formatValue(1, 'boolean')).toBe('Up');
  });

  it('formats a real zero as a zero', () => {
    expect(formatValue(0, 'percent')).toBe('0.0 %');
    expect(formatValue(0, 'bytes')).toBe('0 B');
  });
});
