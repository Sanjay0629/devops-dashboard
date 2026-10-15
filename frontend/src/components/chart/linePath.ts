/**
 * Gap-correct line geometry for metric charts (TS-01, docs/TECH_STACK.md §2.5).
 *
 * This is the whole of the charting "library" integration, and it is deliberately
 * two functions rather than a chart component: M2.2 builds the components
 * (axes, tooltips, comparison views) and this is the piece their correctness
 * rests on.
 *
 * **Why there is a module here at all.** PRD §4.2 forbids substituting a zero
 * for unavailable data and §4.4 requires a gap to be drawn as a gap. The usual
 * way that requirement is violated is not by anyone deciding to violate it —
 * it is by a chart library's default (`connectNulls`, `spanGaps`) or by a
 * `?? 0` written to satisfy a type checker. Putting the gap rule in one
 * function, with a test that fails if the rendered path is continuous, means
 * there is exactly one place for it to be wrong and that place is pinned.
 *
 * `null` means unknown, everywhere in this product (CLAUDE.md §5). A missing
 * sample is a hole in the line, never a point on the floor and never a
 * straight segment joining the two sides.
 */

import { scaleLinear, scaleUtc } from 'd3-scale';
import { line } from 'd3-shape';

/** One sample. `value` is `null` when the source had nothing for that instant. */
export interface Sample {
  /** Milliseconds since the epoch, as the API returns timestamps. */
  readonly t: number;
  readonly value: number | null;
}

/** Pixel-space mappers. Plain functions, so the caller may supply any scale. */
export interface Scales {
  readonly x: (t: number) => number;
  readonly y: (value: number) => number;
}

/**
 * Build the two scales a metric chart needs.
 *
 * `scaleUtc` rather than `scaleTime`: ticks are chosen in UTC, so a chart does
 * not silently shift its gridlines with the viewer's timezone while the
 * timestamps beside it are rendered in their locale. The *formatting* of a
 * tick is the component's business (and `Intl` already does it); choosing
 * where the ticks fall over an arbitrary range is what d3-scale is here for.
 */
export function scalesFor(
  timeRange: readonly [number, number],
  valueDomain: readonly [number, number],
  size: { readonly width: number; readonly height: number },
): Scales {
  const x = scaleUtc()
    .domain([new Date(timeRange[0]), new Date(timeRange[1])])
    .range([0, size.width]);
  const y = scaleLinear()
    .domain([...valueDomain])
    .range([size.height, 0]);
  return { x: (t) => x(new Date(t)), y: (value) => y(value) };
}

/**
 * The SVG `d` attribute for a series, broken wherever the data is.
 *
 * `line().defined()` is d3-shape's gap mechanism: where the predicate is
 * false, the generator closes the current subpath and starts a new one at the
 * next defined point. So a hole produces a second `M` command, which is
 * exactly what `linePath.test.ts` asserts — and the reason the gap rule is
 * ours rather than a library default that could change in a minor release.
 *
 * Returns `''` for a series with nothing to draw, which a `<path d="">`
 * renders as nothing. Not a zero-height line, and not a thrown error: "this
 * server reported nothing in this window" is an ordinary answer.
 */
export function linePath(samples: readonly Sample[], scales: Scales): string {
  const generator = line<Sample>()
    .defined((sample) => sample.value !== null)
    .x((sample) => scales.x(sample.t))
    // The `?? 0` below is unreachable while `defined` is in place — d3 calls
    // this accessor only for samples the predicate admitted — and it is
    // deliberately `?? 0` rather than `as number`. An assertion would hide a
    // real null if `defined` were ever dropped; this way the null becomes a
    // vertex on the baseline, which is exactly the signature
    // `linePath.test.ts` fails on. Verified by mutation: removing `defined`
    // emits `L200,200` and six tests fail.
    .y((sample) => scales.y(sample.value ?? 0));
  return generator([...samples]) ?? '';
}
