/**
 * The selectable time ranges, and the step each one asks the backend for.
 *
 * Steps are chosen so every range lands between 240 and 360 points: enough to
 * show shape, well under §15.2's 11 000-point cap, and cheap to draw several
 * of on one page (M2.2 criterion: a 24 h range without jank). The step is the
 * UI's choice rather than a user control because the only thing a finer one
 * buys is more SVG.
 *
 * A rate-based metric is queried over a window at least as wide as the step
 * (the backend widens it), so a coarse step averages rather than samples.
 */

import type { RangeWindow } from '@/api/metrics';

export interface RangeChoice {
  id: string;
  label: string;
  seconds: number;
  stepSeconds: number;
}

export const RANGES: readonly RangeChoice[] = [
  { id: '1h', label: 'Last hour', seconds: 3_600, stepSeconds: 15 },
  { id: '6h', label: 'Last 6 hours', seconds: 21_600, stepSeconds: 60 },
  { id: '24h', label: 'Last 24 hours', seconds: 86_400, stepSeconds: 300 },
  { id: '7d', label: 'Last 7 days', seconds: 604_800, stepSeconds: 1_800 },
  { id: '30d', label: 'Last 30 days', seconds: 2_592_000, stepSeconds: 7_200 },
];

export const DEFAULT_RANGE = '1h';

export function rangeById(id: string): RangeChoice {
  return RANGES.find((choice) => choice.id === id) ?? (RANGES[0] as RangeChoice);
}

/** The concrete window for a choice, ending at `now`. */
export function windowFor(choice: RangeChoice, now: Date = new Date()): RangeWindow {
  // Snapped down to a whole step, so two charts on one page (and a reload a
  // second later) ask for the same grid instead of drifting by a sub-step.
  const end = Math.floor(now.getTime() / 1000 / choice.stepSeconds) * choice.stepSeconds * 1000;
  return {
    from: new Date(end - choice.seconds * 1000),
    to: new Date(end),
    stepSeconds: choice.stepSeconds,
  };
}
