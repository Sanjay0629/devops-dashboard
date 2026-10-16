import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { LineChart, type ChartSeries } from '@/components/chart/LineChart';

/**
 * The chart component, not only its geometry.
 *
 * `linePath.test.ts` pins the gap rule at the function. This file is the other
 * half M2.2 owes (IMPLEMENTATION_PLAN.md): a series containing `null`, rendered
 * through the real component into real SVG, must come out as a *gap* — the
 * single most likely way to violate PRD §4.4. Two failure modes are asserted,
 * because they look different in the output:
 *
 *  - **coercion** (`null → 0`): a vertex appears on the baseline (`y = 180`
 *    for the plot height used here);
 *  - **bridging** (nulls skipped): the line is one continuous subpath.
 */

const T0 = Date.UTC(2026, 9, 16, 8, 0, 0);
const STEP = 900_000;
const RANGE = [T0, T0 + 4 * STEP] as const;
/** The plot area's height in SVG units (`HEIGHT - MARGIN.top - MARGIN.bottom`). */
const BASELINE_Y = 180;

function samples(values: (number | null)[]): ChartSeries['samples'] {
  return values.map((value, i) => ({ t: T0 + i * STEP, value }));
}

function renderChart(values: (number | null)[], extra: Partial<ChartSeries>[] = []) {
  const series: ChartSeries[] = [
    { id: 'a', label: 'Web 1', samples: samples(values) },
    ...extra.map((one, i) => ({
      id: `extra${String(i)}`,
      label: `Extra ${String(i)}`,
      samples: samples(values),
      ...one,
    })),
  ];
  return render(<LineChart title="CPU utilisation" unit="percent" range={RANGE} series={series} />);
}

const dataPath = (container: HTMLElement, slot = 1) =>
  container.querySelector(`path[stroke="var(--series-${String(slot)})"]`)?.getAttribute('d') ?? '';

const subpaths = (d: string) => (d.match(/M/g) ?? []).length;

describe('a gap renders as a gap', () => {
  it('breaks the line at a null instead of bridging it', () => {
    const { container } = renderChart([10, 20, null, 40, 50]);
    // Two runs → two subpaths. Bridging would leave exactly one.
    expect(subpaths(dataPath(container))).toBe(2);
  });

  it('does not coerce a null to a zero on the baseline', () => {
    const { container } = renderChart([10, 20, null, 40, 50]);
    expect(dataPath(container)).not.toMatch(new RegExp(`,${String(BASELINE_Y)}(?![\\d.])`));
  });

  it('draws a run of several nulls as one gap, not several', () => {
    const { container } = renderChart([10, null, null, null, 50]);
    expect(subpaths(dataPath(container))).toBe(2);
  });

  it('keeps an isolated reading visible as a dot', () => {
    // A one-point subpath draws nothing, so without the dot this reading
    // would vanish — losing data the other way round.
    const { container } = renderChart([null, 30, null, null, null]);
    expect(container.querySelectorAll('circle[r="3"]')).toHaveLength(1);
  });

  it('draws one continuous line when there is no hole', () => {
    const { container } = renderChart([10, 20, 30, 40, 50]);
    expect(subpaths(dataPath(container))).toBe(1);
  });

  it('says so, and draws no line, when every value is null', () => {
    const { container } = renderChart([null, null, null, null, null]);
    expect(screen.getByText(/CPU utilisation: no data in this window/)).toBeInTheDocument();
    expect(container.querySelector('path')).toBeNull();
  });
});

describe('reading the values without a mouse', () => {
  it('steps a crosshair with the arrow keys and says "no data" at a hole', async () => {
    const { container } = renderChart([10, 20, null, 40, 50]);
    const chart = screen.getByRole('group', { name: /CPU utilisation/ });
    chart.focus();

    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByText(/10\.0 %/)).toBeInTheDocument();
    await userEvent.keyboard('{ArrowRight}{ArrowRight}');
    // Index 2 is the hole: never "0.0 %".
    const readout = container.querySelector('[aria-live]');
    expect(readout).toHaveTextContent('no data');
    // The axis legitimately prints "0.0 %"; the readout for a hole must not.
    expect(readout).not.toHaveTextContent(/0\.0 %/);
  });

  it('labels times as UTC and clears on Escape', async () => {
    renderChart([10, 20, 30, 40, 50]);
    screen.getByRole('group', { name: /CPU utilisation/ }).focus();
    await userEvent.keyboard('{Home}');
    expect(screen.getByText(/UTC/, { selector: 'span' })).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    expect(screen.getByText(/Hover or focus the chart/)).toBeInTheDocument();
  });

  it('follows the pointer to the nearest sample', () => {
    renderChart([10, 20, 30, 40, 50]);
    const chart = screen.getByRole('group', { name: /CPU utilisation/ });
    chart.getBoundingClientRect = () =>
      ({ left: 0, width: 720, top: 0, height: 220, right: 720, bottom: 220 }) as DOMRect;
    // 64 is the left margin, 640 the plot width; the right edge is the last sample.
    // jsdom has no PointerEvent, so `fireEvent.pointerMove` would drop
    // `clientX`; a MouseEvent of the same name carries it.
    fireEvent(chart, new MouseEvent('pointermove', { clientX: 64 + 640, bubbles: true }));
    expect(screen.getByText(/50\.0 %/)).toBeInTheDocument();
  });
});

describe('the table view', () => {
  it('is not rendered until opened, then lists every sample with holes as "no data"', async () => {
    renderChart([10, null, 30, 40, 50]);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('View as table'));
    const table = await screen.findByRole('table');
    expect(table).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(1 + 5);
    expect(screen.getByText('no data')).toBeInTheDocument();
  });
});

describe('multiple series', () => {
  it('shows a legend, and a colour slot stays with its entity', () => {
    const { container } = render(
      <LineChart
        title="CPU utilisation"
        unit="percent"
        range={RANGE}
        series={[
          { id: 'x', label: 'Only remaining', samples: samples([1, 2, 3, 4, 5]), slot: 3 },
          { id: 'y', label: 'Another', samples: samples([5, 4, 3, 2, 1]), slot: 5 },
        ]}
      />,
    );
    expect(screen.getByText('Only remaining')).toBeInTheDocument();
    // Slot 3 → --series-4, whatever its position in the list.
    expect(container.querySelector('path[stroke="var(--series-4)"]')).not.toBeNull();
    expect(container.querySelector('path[stroke="var(--series-6)"]')).not.toBeNull();
  });

  it('labels threshold lines with text, not colour alone', () => {
    render(
      <LineChart
        title="CPU utilisation"
        unit="percent"
        range={RANGE}
        thresholds={{ warning: 80, critical: 90 }}
        series={[{ id: 'a', label: 'Web 1', samples: samples([10, 20, 30, 40, 50]) }]}
      />,
    );
    expect(screen.getByText('Warning 80.0 %')).toBeInTheDocument();
    expect(screen.getByText('Critical 90.0 %')).toBeInTheDocument();
  });
});
