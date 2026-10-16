/**
 * Axis tick selection (TS-01: this is what d3-scale is here for).
 *
 * Kept apart from `linePath.ts` so that file stays the two-function gate its
 * test pins. Ticks are chosen in UTC for the reason `scalesFor` gives, and
 * labelled in UTC so the axis and its own gridlines never disagree about
 * what hour it is.
 */

import { scaleLinear, scaleUtc } from 'd3-scale';

/** Millisecond positions of sensible time ticks across a range. */
export function timeTicks(range: readonly [number, number], count = 5): number[] {
  return scaleUtc()
    .domain([new Date(range[0]), new Date(range[1])])
    .ticks(count)
    .map((date) => date.getTime());
}

export function valueTicks(domain: readonly [number, number], count = 4): number[] {
  return scaleLinear()
    .domain([...domain])
    .ticks(count);
}

const SHORT = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  hour: '2-digit',
  minute: '2-digit',
});
const LONG = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
});
const FULL = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  dateStyle: 'medium',
  timeStyle: 'medium',
});

/** `HH:mm` within a day-ish window, `DD Mon HH:mm` beyond it. Always UTC. */
export function formatTick(t: number, spanMs: number): string {
  return (spanMs > 36 * 3_600_000 ? LONG : SHORT).format(new Date(t));
}

/** A full timestamp for a readout or a table. UTC, and says so. */
export function formatInstant(t: number): string {
  return `${FULL.format(new Date(t))} UTC`;
}
