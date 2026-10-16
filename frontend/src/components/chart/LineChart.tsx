/**
 * A time-series line chart (M2.2; TS-01's geometry, docs/TECH_STACK.md §2.5).
 *
 * **This component never decides what a hole means.** A `null` value goes to
 * `linePath`, whose `defined()` predicate breaks the line there; nothing in
 * this file coalesces, interpolates or skips a sample (PRD §4.4). The one
 * thing it adds is a dot for a sample whose neighbours are both holes: a
 * one-point subpath draws nothing, and an isolated reading would otherwise
 * vanish — a different way of losing data.
 *
 * Marks, per the dataviz method: 2px lines, recessive grid, status colours
 * reserved for the threshold lines (which also carry a dash and a text label,
 * so they are never colour-alone), categorical hues assigned by series slot and
 * never cycled — the caller passes at most eight series.
 *
 * Reading it without a mouse is a requirement, not a nicety: the chart is a
 * focusable group whose arrow keys move a crosshair, and the same numbers are
 * in a table under "View as table" for a screen reader (the table is rendered
 * only when opened, so five charts on a page cost five SVGs and no rows).
 */

import { useState } from 'react';

import { formatValue } from '@/components/chart/format';
import { formatInstant, formatTick, timeTicks, valueTicks } from '@/components/chart/ticks';
import { linePath, scalesFor, type Sample } from '@/components/chart/linePath';

export interface ChartSeries {
  id: string;
  label: string;
  samples: readonly Sample[];
  /**
   * The categorical slot (0–7) this entity keeps. Colour follows the entity,
   * never its rank: the caller owns the assignment so that removing one
   * server from a comparison does not repaint the others. Defaults to the
   * series' position, which is right for a single fixed set.
   */
  slot?: number;
}

export interface ChartThresholds {
  warning: number;
  critical: number;
}

interface Props {
  title: string;
  unit: string;
  series: readonly ChartSeries[];
  /** The window shown, in epoch ms. Drawn even where a series has no points. */
  range: readonly [number, number];
  thresholds?: ChartThresholds | null | undefined;
}

const WIDTH = 720;
const HEIGHT = 220;
const MARGIN = { top: 12, right: 16, bottom: 28, left: 64 };
const INNER_W = WIDTH - MARGIN.left - MARGIN.right;
const INNER_H = HEIGHT - MARGIN.top - MARGIN.bottom;
/** One per slot, so identity is not colour alone. */
const DASHES = ['', '7 3', '2 3', '8 3 2 3', '4 2', '1 3', '10 3 2 3 2 3', '5 5'];

const colourOf = (slot: number): string => `var(--series-${String((slot % 8) + 1)})`;
const slotOf = (one: ChartSeries, index: number): number => one.slot ?? index;

function domainFor(
  series: readonly ChartSeries[],
  unit: string,
  thresholds: ChartThresholds | null | undefined,
): [number, number] {
  if (unit === 'percent') return [0, 100];
  let max = thresholds?.critical ?? 0;
  for (const one of series)
    for (const sample of one.samples)
      if (sample.value !== null && sample.value > max) max = sample.value;
  return [0, max > 0 ? max * 1.1 : 1];
}

/** Is the sample at `i` defined while both neighbours are holes (or edges)? */
function isIsolated(samples: readonly Sample[], i: number): boolean {
  const here = samples[i];
  if (here?.value == null) return false;
  return samples[i - 1]?.value == null && samples[i + 1]?.value == null;
}

export function LineChart({ title, unit, series, range, thresholds }: Props): React.JSX.Element {
  const [selected, setSelected] = useState<number | null>(null);
  const [viaKeyboard, setViaKeyboard] = useState(false);
  const [tableOpen, setTableOpen] = useState(false);

  const grid = series[0]?.samples ?? [];
  const hasData = series.some((one) => one.samples.some((sample) => sample.value !== null));
  if (!hasData) {
    return (
      <p
        className="rounded border px-3 py-6 text-center text-sm"
        style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
      >
        {title}: no data in this window.
      </p>
    );
  }

  const domain = domainFor(series, unit, thresholds);
  const scales = scalesFor(range, domain, { width: INNER_W, height: INNER_H });
  const span = range[1] - range[0];

  const nearest = (clientX: number, left: number, width: number): number | null => {
    if (grid.length === 0 || width === 0) return null;
    const t = range[0] + ((clientX - left) / width) * span;
    let best = 0;
    for (let i = 1; i < grid.length; i += 1) {
      const candidate = grid[i];
      const current = grid[best];
      if (candidate && current && Math.abs(candidate.t - t) < Math.abs(current.t - t)) best = i;
    }
    return best;
  };

  const move = (delta: number | 'start' | 'end') => {
    setViaKeyboard(true);
    setSelected((current) => {
      if (delta === 'start') return 0;
      if (delta === 'end') return grid.length - 1;
      const base = current ?? (delta > 0 ? -1 : grid.length);
      return Math.min(grid.length - 1, Math.max(0, base + delta));
    });
  };

  const when = selected === null ? undefined : grid[selected]?.t;

  return (
    <figure className="m-0">
      <figcaption className="mb-1 text-sm font-medium">{title}</figcaption>

      {/* A chart cannot be a native control, so the keyboard contract lives on
          a labelled group: arrows step the crosshair, Escape clears it. The
          same numbers are in the table below for anyone who cannot use it. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        role="group"
        aria-label={`${title}. Use the left and right arrow keys to read values.`}
        onKeyDown={(event) => {
          if (event.key === 'ArrowRight') move(1);
          else if (event.key === 'ArrowLeft') move(-1);
          else if (event.key === 'Home') move('start');
          else if (event.key === 'End') move('end');
          else if (event.key === 'Escape') setSelected(null);
          else return;
          event.preventDefault();
        }}
        onPointerMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          // The plot area is a fraction of the rendered box, because the SVG
          // scales with its container (viewBox) rather than being re-measured.
          const left = box.left + (MARGIN.left / WIDTH) * box.width;
          setViaKeyboard(false);
          setSelected(nearest(event.clientX, left, (INNER_W / WIDTH) * box.width));
        }}
        onPointerLeave={() => {
          setSelected(null);
        }}
        className="rounded outline-offset-2"
      >
        <svg
          viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
          className="w-full"
          aria-hidden="true"
          focusable="false"
        >
          <g transform={`translate(${String(MARGIN.left)},${String(MARGIN.top)})`}>
            {valueTicks(domain).map((tick) => (
              <g key={`y${String(tick)}`}>
                <line
                  x1={0}
                  x2={INNER_W}
                  y1={scales.y(tick)}
                  y2={scales.y(tick)}
                  stroke="var(--c-border)"
                  strokeWidth={1}
                />
                <text
                  x={-8}
                  y={scales.y(tick)}
                  textAnchor="end"
                  dominantBaseline="middle"
                  fontSize={11}
                  fill="var(--c-text-muted)"
                >
                  {formatValue(tick, unit)}
                </text>
              </g>
            ))}
            {timeTicks(range).map((tick) => (
              <text
                key={`x${String(tick)}`}
                x={scales.x(tick)}
                y={INNER_H + 18}
                textAnchor="middle"
                fontSize={11}
                fill="var(--c-text-muted)"
              >
                {formatTick(tick, span)}
              </text>
            ))}

            {thresholds != null &&
              (
                [
                  ['Warning', thresholds.warning, 'var(--c-warning)'],
                  ['Critical', thresholds.critical, 'var(--c-critical)'],
                ] as const
              ).map(([name, value, colour]) =>
                value <= domain[1] ? (
                  <g key={name}>
                    <line
                      x1={0}
                      x2={INNER_W}
                      y1={scales.y(value)}
                      y2={scales.y(value)}
                      stroke={colour}
                      strokeWidth={1}
                      strokeDasharray="3 3"
                    />
                    <text
                      x={INNER_W - 4}
                      y={scales.y(value) - 3}
                      textAnchor="end"
                      fontSize={10}
                      fill="var(--c-text-muted)"
                    >
                      {name} {formatValue(value, unit)}
                    </text>
                  </g>
                ) : null,
              )}

            {series.map((one, index) => (
              <g key={one.id}>
                <path
                  d={linePath(one.samples, scales)}
                  fill="none"
                  stroke={colourOf(slotOf(one, index))}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  strokeDasharray={series.length > 1 ? DASHES[slotOf(one, index) % 8] : undefined}
                />
                {one.samples.map((sample, i) =>
                  isIsolated(one.samples, i) ? (
                    <circle
                      key={sample.t}
                      cx={scales.x(sample.t)}
                      cy={scales.y(sample.value ?? 0)}
                      r={3}
                      fill={colourOf(slotOf(one, index))}
                    />
                  ) : null,
                )}
              </g>
            ))}

            {when !== undefined && (
              <g>
                <line
                  x1={scales.x(when)}
                  x2={scales.x(when)}
                  y1={0}
                  y2={INNER_H}
                  stroke="var(--c-text-muted)"
                  strokeWidth={1}
                />
                {series.map((one, index) => {
                  const value = selected === null ? null : (one.samples[selected]?.value ?? null);
                  return value === null ? null : (
                    <circle
                      key={one.id}
                      cx={scales.x(when)}
                      cy={scales.y(value)}
                      r={4}
                      fill={colourOf(slotOf(one, index))}
                      stroke="var(--c-surface)"
                      strokeWidth={2}
                    />
                  );
                })}
              </g>
            )}
          </g>
        </svg>
      </div>

      <div
        className="mt-1 min-h-5 text-xs"
        style={{ color: 'var(--c-text-muted)' }}
        aria-live={viaKeyboard ? 'polite' : 'off'}
      >
        {when === undefined ? (
          <span>Hover or focus the chart to read values. Times are UTC.</span>
        ) : (
          <>
            <span>{formatInstant(when)}</span>
            {series.map((one) => {
              const value = selected === null ? null : (one.samples[selected]?.value ?? null);
              return (
                <span key={one.id} className="ml-3">
                  {series.length > 1 && `${one.label}: `}
                  {value === null ? 'no data' : formatValue(value, unit)}
                </span>
              );
            })}
          </>
        )}
      </div>

      {series.length > 1 && (
        <ul className="m-0 mt-1 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-xs">
          {series.map((one, index) => (
            <li key={one.id} className="flex items-center gap-1">
              <svg width={24} height={8} aria-hidden="true">
                <line
                  x1={0}
                  x2={24}
                  y1={4}
                  y2={4}
                  stroke={colourOf(slotOf(one, index))}
                  strokeWidth={2}
                  strokeDasharray={DASHES[slotOf(one, index) % 8]}
                />
              </svg>
              <span>{one.label}</span>
            </li>
          ))}
        </ul>
      )}

      <details
        className="mt-1 text-xs"
        onToggle={(event) => {
          setTableOpen(event.currentTarget.open);
        }}
      >
        <summary className="cursor-pointer" style={{ color: 'var(--c-text-muted)' }}>
          View as table
        </summary>
        {tableOpen && (
          <div className="mt-1 max-h-64 overflow-auto">
            <table className="w-full text-left">
              <caption className="sr-only">{title} values, times in UTC</caption>
              <thead>
                <tr>
                  <th scope="col">Time (UTC)</th>
                  {series.map((one) => (
                    <th scope="col" key={one.id}>
                      {one.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {grid.map((sample, i) => (
                  <tr key={sample.t}>
                    <th scope="row" className="font-normal">
                      {formatInstant(sample.t)}
                    </th>
                    {series.map((one) => {
                      const value = one.samples[i]?.value ?? null;
                      return (
                        <td key={one.id}>
                          {value === null ? 'no data' : formatValue(value, unit)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </figure>
  );
}
