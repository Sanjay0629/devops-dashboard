/**
 * The TS-01 gap gate (docs/TECH_STACK.md §2.5 criterion 2).
 *
 * This is the test the charting decision was made with, kept as a standing
 * regression test rather than deleted once it had served its purpose. What it
 * guards against is not a mistake someone is about to make — it is a `?? 0`,
 * a dropped `defined`, or a dependency bump changing a default, any of which
 * would make the product draw a silent host as an idle one (PRD §4.2, §4.4).
 *
 * It asserts on the *geometry of the rendered path*, not on a snapshot, so it
 * stays meaningful if the rendering changes and would stay meaningful against
 * a different library. Two defects have to be caught and a naive test catches
 * only one:
 *
 *  - **Coercion** — `null` becomes `0`. The path stays continuous and *gains*
 *    a vertex on the baseline.
 *  - **Bridging** — the hole is dropped and its neighbours joined. The path
 *    stays continuous and *loses* a vertex.
 *
 * So the gate checks subpath count *and* vertex count. `describe('the gate
 * itself')` below proves it can fail, for the same reason
 * `tests/authz/test_route_coverage.py` plants a bypass against its own grep
 * gate: "no failures" is otherwise indistinguishable from a check that
 * matches nothing.
 */

import { describe, expect, it } from 'vitest';

import { linePath, type Sample, scalesFor } from './linePath';

const COMMAND = /([MLHVCSQTAZ])([^MLHVCSQTAZ]*)/gi;
const NUMBER = /-?\d*\.?\d+(?:e[-+]?\d+)?/gi;

/**
 * Split an SVG `d` attribute into subpaths, each with its vertices.
 *
 * Tolerant about which drawing command a generator uses — only `M` starts a
 * new subpath — because the gate is about whether the line is broken, not
 * about whether the generator chose `L` or `C`.
 */
function subpaths(d: string): [number, number][][] {
  const found: [number, number][][] = [];
  for (const match of d.matchAll(COMMAND)) {
    const letter = match[1] ?? '';
    const numbers = (match[2] ?? '').match(NUMBER)?.map(Number) ?? [];
    const points: [number, number][] = [];
    for (let index = 0; index + 1 < numbers.length; index += 2) {
      points.push([numbers[index] as number, numbers[index + 1] as number]);
    }
    if (letter.toUpperCase() === 'M') {
      found.push(points);
    } else {
      found[found.length - 1]?.push(...points);
    }
  }
  return found;
}

interface Verdict {
  readonly subpathCount: number;
  readonly vertexCount: number;
  readonly passed: boolean;
  readonly reason: string;
}

/** Judge a path against the gate. Returns a verdict so failures are showable. */
function judgeGap(d: string, expectedVertices: number): Verdict {
  const parsed = subpaths(d);
  const subpathCount = parsed.length;
  const vertexCount = parsed.reduce((total, part) => total + part.length, 0);
  if (subpathCount < 2) {
    return {
      subpathCount,
      vertexCount,
      passed: false,
      reason:
        vertexCount > expectedVertices
          ? `continuous line with ${String(vertexCount)} vertices for ${String(expectedVertices)} real ` +
            'points: the gap was coerced to a value (PRD §4.2)'
          : 'continuous line: the gap was bridged, not drawn as a gap (PRD §4.4)',
    };
  }
  if (vertexCount !== expectedVertices) {
    return {
      subpathCount,
      vertexCount,
      passed: false,
      reason:
        `${String(subpathCount)} runs plotting ${String(vertexCount)} vertices for ` +
        `${String(expectedVertices)} real ` +
        'points: something was invented or dropped',
    };
  }
  return {
    subpathCount,
    vertexCount,
    passed: true,
    reason: `${String(subpathCount)} runs plotting exactly ${String(expectedVertices)} real points`,
  };
}

const MINUTE = 60_000;
const START = Date.UTC(2026, 9, 16, 12, 0, 0);

/**
 * Flat either side of the hole, on purpose. A gap in a varying series can be
 * mistaken for the variation; at a constant 50 % with a 0–100 domain, a coerced
 * zero is a spike to the floor and a bridge is a dead-flat line that looks
 * exactly like real data — which is the point, and why this looks at the path.
 */
const SERIES: readonly Sample[] = [
  { t: START, value: 50 },
  { t: START + MINUTE, value: 50 },
  { t: START + 2 * MINUTE, value: null },
  { t: START + 3 * MINUTE, value: 50 },
  { t: START + 4 * MINUTE, value: 50 },
];
const REAL_POINTS = SERIES.filter((sample) => sample.value !== null).length;

const SCALES = scalesFor([START, START + 4 * MINUTE], [0, 100], { width: 400, height: 200 });

describe('linePath', () => {
  it('breaks the line at a missing sample', () => {
    const verdict = judgeGap(linePath(SERIES, SCALES), REAL_POINTS);
    expect(verdict.passed, `${verdict.reason} — path: ${linePath(SERIES, SCALES)}`).toBe(true);
    expect(verdict.subpathCount).toBe(2);
    expect(verdict.vertexCount).toBe(REAL_POINTS);
  });

  it('plots no vertex on the baseline', () => {
    // The coercion signature, checked directly as well as through the vertex
    // count: y(0) is the bottom of the plot, and no real value here is 0.
    const baseline = SCALES.y(0);
    const vertices = subpaths(linePath(SERIES, SCALES)).flat();
    expect(vertices).toHaveLength(REAL_POINTS);
    for (const [, y] of vertices) {
      expect(y).not.toBeCloseTo(baseline, 5);
    }
  });

  it('keeps the two runs on either side of the hole', () => {
    const [left, right] = subpaths(linePath(SERIES, SCALES));
    expect(left).toHaveLength(2);
    expect(right).toHaveLength(2);
    // The hole is a real horizontal distance, not a hairline: the right run
    // starts a scale step after the left one ends.
    expect((right?.[0]?.[0] ?? 0) - (left?.[1]?.[0] ?? 0)).toBeGreaterThan(50);
  });

  it('draws leading and trailing holes as holes too', () => {
    // A server that started reporting late, or stopped before the window
    // ended. Neither end may be extended to the edge of the chart.
    const series: readonly Sample[] = [
      { t: START, value: null },
      { t: START + MINUTE, value: 10 },
      { t: START + 2 * MINUTE, value: 20 },
      { t: START + 3 * MINUTE, value: null },
      { t: START + 4 * MINUTE, value: null },
    ];
    const parsed = subpaths(linePath(series, SCALES));
    expect(parsed).toHaveLength(1);
    expect(parsed[0]).toHaveLength(2);
    expect(parsed[0]?.[0]?.[0]).toBeGreaterThan(0);
  });

  it('draws every-other-sample loss as separate points, not a trend line', () => {
    // The worst case for bridging: alternating holes. Joining these would
    // invent a smooth line out of four isolated observations.
    const series: readonly Sample[] = [
      { t: START, value: 10 },
      { t: START + MINUTE, value: null },
      { t: START + 2 * MINUTE, value: 20 },
      { t: START + 3 * MINUTE, value: null },
      { t: START + 4 * MINUTE, value: 30 },
    ];
    const parsed = subpaths(linePath(series, SCALES));
    expect(parsed).toHaveLength(3);
    expect(parsed.every((run) => run.length === 1)).toBe(true);
  });

  it('renders nothing for a series with no data at all', () => {
    // "This server reported nothing in this window" is an ordinary answer, so
    // it is an empty path rather than a thrown error or a line along zero.
    const empty: readonly Sample[] = [
      { t: START, value: null },
      { t: START + MINUTE, value: null },
    ];
    expect(linePath(empty, SCALES)).toBe('');
    expect(linePath([], SCALES)).toBe('');
  });

  it('maps a value to the pixel the scale says, with y inverted', () => {
    // Guards the scale wiring: an un-inverted y range draws every chart
    // upside down, which is the kind of thing that looks plausible.
    expect(SCALES.y(0)).toBe(200);
    expect(SCALES.y(100)).toBe(0);
    expect(SCALES.y(50)).toBe(100);
    expect(SCALES.x(START)).toBe(0);
    expect(SCALES.x(START + 4 * MINUTE)).toBe(400);
  });
});

describe('the gate itself', () => {
  it('fails a path that coerced the gap to zero', () => {
    const verdict = judgeGap('M0,100L100,100L200,200L300,100L400,100', REAL_POINTS);
    expect(verdict.passed).toBe(false);
    expect(verdict.vertexCount).toBe(5);
    expect(verdict.reason).toContain('coerced');
  });

  it('fails a path that bridged the gap', () => {
    const verdict = judgeGap('M0,100L100,100L300,100L400,100', REAL_POINTS);
    expect(verdict.passed).toBe(false);
    expect(verdict.reason).toContain('bridged');
  });

  it('fails a broken path that still invented a point', () => {
    const verdict = judgeGap('M0,100L100,100L200,200M300,100L400,100', REAL_POINTS);
    expect(verdict.passed).toBe(false);
    expect(verdict.subpathCount).toBe(2);
    expect(verdict.reason).toContain('invented or dropped');
  });

  it('passes a genuinely broken path', () => {
    const verdict = judgeGap('M0,100L100,100M300,100L400,100', REAL_POINTS);
    expect(verdict.passed).toBe(true);
  });

  it('parses curves, so a generator is judged on geometry not on commands', () => {
    const parsed = subpaths('M0,100C5,100 5,100 10,100M300,100C305,100 305,100 400,100');
    expect(parsed).toHaveLength(2);
    expect(parsed[0]?.[0]).toEqual([0, 100]);
  });
});
